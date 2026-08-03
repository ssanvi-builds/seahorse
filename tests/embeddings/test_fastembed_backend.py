"""FastEmbed ONNX backend (M1-B.2, f5-07 §3.2.2 + OQ-7-12).

The backend wraps fastembed's sync ``TextEmbedding`` behind the async
``Embedder`` Protocol via ``asyncio.to_thread``. These tests use a duck-typed
fake model (``query_embed`` / ``passage_embed`` generators) so the async routing
and L2 normalization are pinned WITHOUT the real model / onnxruntime. The real
factory is gated on ``SEAHORSE_RUN_MODEL_TESTS=1`` + network (OQ-7-12 bundle:
mE5-small fp32-O4, 235MB — int8 not published for arm64, verified live).
"""

from __future__ import annotations

import asyncio
import os

import numpy as np
import pytest

from seahorse.embeddings.fastembed_backend import (
    FastEmbedEmbedder,
    _prefix_drift_cosine,
)
from seahorse.embeddings.types import ModelIdentity


class _FakeFastEmbedModel:
    """Duck-typed fastembed model: query embeds are [1,0,...]/[0,1,...],
    passage embeds are [0,0,1,...]."""

    def __init__(self, dim: int = 384) -> None:
        self._dim = dim

    def query_embed(self, texts):
        for i, _ in enumerate(texts):
            vec = [0.0] * self._dim
            vec[0 if i == 0 else 1] = 1.0
            yield vec

    def passage_embed(self, texts):
        for _ in texts:
            vec = [0.0] * self._dim
            vec[2] = 1.0
            yield vec


def _identity() -> ModelIdentity:
    return ModelIdentity(
        backend="fastembed",
        model_name="intfloat/multilingual-e5-small",
        revision="a1b2c3d4e5f6",
        dim=384,
        quantization="fp32",
        normalized=True,
    )


def test_embed_routes_role_and_returns_unit_float32() -> None:
    embedder = FastEmbedEmbedder(_FakeFastEmbedModel(), _identity())
    vecs = asyncio.run(embedder.embed(["a", "b"], "query"))
    assert vecs.shape == (2, 384)
    assert vecs.dtype == np.float32
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0)  # L2-unit
    assert vecs[0, 0] > 0.9  # query role -> query_embed -> component 0


def test_embed_passage_role_routes_to_passage_embed() -> None:
    embedder = FastEmbedEmbedder(_FakeFastEmbedModel(), _identity())
    vecs = asyncio.run(embedder.embed(["a"], "passage"))
    assert vecs.shape == (1, 384)
    assert vecs[0, 2] > 0.9  # passage_embed -> component 2


def test_model_identity_and_dim_exposed() -> None:
    embedder = FastEmbedEmbedder(_FakeFastEmbedModel(), _identity())
    assert embedder.dim == 384
    assert embedder.model_identity() == _identity()


def test_prefix_drift_cosine_detects_wiring_missing() -> None:
    # A model whose query and passage embeddings are identical (prefix wiring
    # missing) yields cosine ~1.0 — the drift the startup self-test watches for.
    class _NoPrefixModel:
        def query_embed(self, texts):
            for _ in texts:
                yield [1.0, 0.0, 0.0]

        def passage_embed(self, texts):
            for _ in texts:
                yield [1.0, 0.0, 0.0]

    assert _prefix_drift_cosine(_NoPrefixModel()) == pytest.approx(1.0)


def test_prefix_drift_cosine_distinguishes_query_and_passage() -> None:
    # Real e5 wiring separates the roles (query: "query: ", passage: "passage: "),
    # so the cosine of the raw-text self-test lands strictly inside (0.5, 0.999).
    cos = _prefix_drift_cosine(_FakeFastEmbedModel())
    assert 0.0 <= cos < 0.5  # [1,0,0] vs [0,0,1] are orthogonal (not ~1.0)


@pytest.fixture()
def gate_model_tests() -> None:
    if os.environ.get("SEAHORSE_RUN_MODEL_TESTS") != "1":
        pytest.skip("model test gated; set SEAHORSE_RUN_MODEL_TESTS=1")


def test_build_fastembed_embedder_real(gate_model_tests) -> None:
    # OQ-7-12: real build registers the mE5-small custom model (fp32-O4 bundle)
    # and constructs the embedder; requires the extra 'embeddings' + network.
    from seahorse.embeddings.fastembed_backend import build_fastembed_embedder

    embedder = build_fastembed_embedder()
    assert embedder.dim == 384
    mid = embedder.model_identity()
    assert mid.model_name == "intfloat/multilingual-e5-small"
    assert mid.backend == "fastembed"
