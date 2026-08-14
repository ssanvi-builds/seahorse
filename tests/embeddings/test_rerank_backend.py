"""FastEmbed ONNX cross-encoder backend.

The backend wraps fastembed's sync ``TextCrossEncoder`` behind the
``QueryReranker`` extension point. These tests use a duck-typed fake model
(``rerank``
generator) so the adapter routing is pinned WITHOUT the real model / onnxruntime.
The real factory is gated on ``SEAHORSE_RUN_MODEL_TESTS=1`` + network (bundle:
bge-reranker-v2-m3 ONNX o4, ~1.1GB — validated on arm64 2026-08-08).
"""

from __future__ import annotations

import os

import pytest

from seahorse.contracts.rerank import QueryReranker
from seahorse.embeddings.rerank_backend import (
    ADDITIONAL_FILES,
    MODEL_FILE,
    MODEL_NAME,
    FastEmbedReranker,
)


class _FakeCrossEncoder:
    """Duck-typed fastembed ``TextCrossEncoder``: scores by doc length."""

    def rerank(self, query, documents, batch_size=64):
        for doc in documents:
            yield float(len(doc))


def test_fastembed_reranker_satisfies_query_reranker_protocol() -> None:
    assert isinstance(FastEmbedReranker(_FakeCrossEncoder()), QueryReranker)


def test_rerank_returns_scores_aligned_with_docs() -> None:
    reranker = FastEmbedReranker(_FakeCrossEncoder())
    docs = ["a", "bb", "ccc"]
    scores = reranker.rerank("query", docs)
    assert scores == [1.0, 2.0, 3.0]


def test_rerank_empty_docs_returns_empty() -> None:
    reranker = FastEmbedReranker(_FakeCrossEncoder())
    assert reranker.rerank("query", []) == []


def test_bundle_pin_constants() -> None:
    # The MIT multilingual bundle: the coherent Apache-2.0
    # choice over the cc-by-nc jina default.
    assert "bge-reranker-v2-m3" in MODEL_NAME
    assert MODEL_FILE == "model.onnx"
    assert "model.onnx.data" in ADDITIONAL_FILES


@pytest.fixture()
def gate_model_tests() -> None:
    if os.environ.get("SEAHORSE_RUN_MODEL_TESTS") != "1":
        pytest.skip("model test gated; set SEAHORSE_RUN_MODEL_TESTS=1")


def test_build_fastembed_reranker_real(gate_model_tests) -> None:
    # Real build registers the bge-reranker-v2-m3 custom model (ONNX o4 bundle)
    # and constructs the reranker; requires the extra 'embeddings' + network.
    from seahorse.embeddings.rerank_backend import build_fastembed_reranker

    reranker = build_fastembed_reranker()
    assert isinstance(reranker, FastEmbedReranker)
    scores = reranker.rerank(
        "What is the capital of France?",
        ["The capital of France is Paris.", "The weather is sunny."],
    )
    assert len(scores) == 2
    assert scores[0] > scores[1]  # the relevant doc scores higher


def test_build_fastembed_reranker_idempotent(gate_model_tests) -> None:
    # Warm-DB: a facade is built per variant, each calling this — the second
    # call must reuse the registered model, not raise "already registered".
    from seahorse.embeddings.rerank_backend import build_fastembed_reranker

    r1 = build_fastembed_reranker()
    r2 = build_fastembed_reranker()
    assert isinstance(r1, FastEmbedReranker)
    assert isinstance(r2, FastEmbedReranker)
