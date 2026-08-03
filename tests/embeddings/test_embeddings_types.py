"""Embeddings core types (M1-B.1, f5-07 §3.1).

Pins the signed ``ModelIdentity`` (cache_key / major_version shapes), the async
``Embedder`` Protocol (``embed(texts, role) -> np.ndarray`` L2-unit-normalized,
``model_identity()``, ``dim``), and the defensive ``_l2_normalize`` (zero-vector
safe). The module must import WITHOUT numpy at top level (numpy is lazy inside
``_l2_normalize``) so ``seahorse.embeddings`` stays importable without the heavy
runtime path.
"""

from __future__ import annotations

import pytest

from seahorse.embeddings.types import Embedder, ModelIdentity, _l2_normalize


def test_model_identity_cache_key_and_major_version() -> None:
    mid = ModelIdentity(
        backend="fastembed",
        model_name="intfloat/multilingual-e5-small",
        revision="a1b2c3d4e5f6g7h8i9j0",
        dim=384,
        quantization="fp32",
        normalized=True,
    )
    assert (
        mid.cache_key()
        == "fastembed:intfloat/multilingual-e5-small:a1b2c3d4e5f6:384:fp32"
    )
    assert mid.major_version() == "intfloat/multilingual-e5-small:384:True"


def test_model_identity_cache_key_truncates_long_revision() -> None:
    mid = ModelIdentity(
        backend="ollama",
        model_name="m",
        revision="x",
        dim=4,
        quantization="fp32",
        normalized=True,
    )
    assert mid.cache_key() == "ollama:m:x:4:fp32"


def test_embedder_protocol_is_runtime_checkable() -> None:
    class _FakeEmbedder:
        async def embed(self, texts, role) -> object:  # noqa: ARG002
            return None

        def model_identity(self) -> ModelIdentity:
            return ModelIdentity(
                backend="b", model_name="m", revision="r", dim=4,
                quantization="fp32", normalized=True,
            )

        @property
        def dim(self) -> int:
            return 4

    assert isinstance(_FakeEmbedder(), Embedder)


def test_l2_normalize_produces_unit_norms_and_is_zero_safe() -> None:
    import numpy as np

    vecs = np.array([[3.0, 4.0], [0.0, 0.0]], dtype=np.float32)
    out = _l2_normalize(vecs)
    # [3,4] -> unit norm; the zero vector maps to itself (norm 0 -> safe 1.0).
    assert np.allclose(np.linalg.norm(out, axis=1), [1.0, 0.0])
    assert out[0, 0] == pytest.approx(0.6)
    assert out[0, 1] == pytest.approx(0.8)


def test_l2_normalize_returns_float32() -> None:
    import numpy as np

    out = _l2_normalize(np.array([[1.0, 0.0]], dtype=np.float64))
    assert out.dtype == np.float32
