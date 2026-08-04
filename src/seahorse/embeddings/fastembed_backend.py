"""FastEmbed ONNX backend (#7, f5-07 §3.2.2 + OQ-7-12).

Wraps fastembed's sync ``TextEmbedding`` behind the async ``Embedder`` Protocol
via ``asyncio.to_thread``. fastembed + onnxruntime live in the optional
``embeddings`` extra; ``build_fastembed_embedder`` imports ``fastembed`` lazily
so the default ``uv sync --extra dev`` (G2 mode) never pulls the heavy stack.

OQ-7-12 (verified live 2026-08-03 via the HF API): ``intfloat/multilingual-e5-small``
publishes ``model.onnx`` (fp32, 470MB), ``model_O4.onnx`` (fp32-O4, 235MB) and
``model_qint8_avx512_vnni.onnx`` (int8, x86-only — unusable on Apple Silicon).
There is NO int8 or fp16 artifact portable to arm64, so the bundle defaults to
``model_O4.onnx`` (fp32-O4) — portable across Windows/Linux/macOS, which is the
standpoint for an open standard. The size deviation vs the SO-7c int8 claim
(~235MB vs ~113MB) is documented; a portable int8 bundle is a measured
follow-up (Optimum quantization + per-platform benchmark).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence
from typing import Protocol, runtime_checkable

import numpy as np  # core dep since M1-B.1

from seahorse.embeddings.types import ModelIdentity, Role, _l2_normalize

_logger = logging.getLogger("seahorse.embeddings.fastembed")

# Bundle pin (single source for model_file + the ModelIdentity stamp).
MODEL_NAME = "intfloat/multilingual-e5-small"
MODEL_FILE = "onnx/model_O4.onnx"  # OQ-7-12: fp32-O4 (portable), not int8
DIM = 384
_QUANTIZATION = "fp32"

_SELF_TEST_TEXT = "prueba de consistencia del embedder al startup"

# e5 role prefixes (paper: arXiv:2212.03533). fastembed's CustomTextEmbedding
# does NOT add these — the consumer must. Missing them collapses query/passage
# into the same vector (cosine 1.0), which the startup self-test detects.
_QUERY_PREFIX = "query: "
_PASSAGE_PREFIX = "passage: "


@runtime_checkable
class _FastEmbedModelLike(Protocol):
    """Duck-typed fastembed ``TextEmbedding`` surface (keeps tests model-free).

    The return covers both the test fakes (``Sequence[float]`` rows) and the
    real ONNX model (``np.ndarray`` rows, via fastembed's ``NumpyArray``);
    ``**kwargs`` mirrors fastembed's ``query_embed``/``passage_embed`` surface.
    """

    def query_embed(self, query, **kwargs) -> Iterable[Sequence[float] | np.ndarray]: ...

    def passage_embed(self, texts, **kwargs) -> Iterable[Sequence[float] | np.ndarray]: ...


class FastEmbedEmbedder:
    """Async adapter over a fastembed ``TextEmbedding`` (duck-typed for tests)."""

    def __init__(self, model: _FastEmbedModelLike, identity: ModelIdentity) -> None:
        self._model = model
        self._identity = identity

    async def embed(self, texts: Sequence[str], role: Role) -> np.ndarray:
        vecs = await asyncio.to_thread(self._embed_sync, texts, role)
        return _l2_normalize(vecs)

    def _embed_sync(self, texts: Sequence[str], role: Role) -> np.ndarray:
        # e5 role prefix (query/passage) is the consumer's job — fastembed's
        # CustomTextEmbedding embeds raw text. Without it query == passage.
        prefix = _PASSAGE_PREFIX if role == "passage" else _QUERY_PREFIX
        prefixed = [f"{prefix}{text}" for text in texts]
        gen = (
            self._model.passage_embed(prefixed)
            if role == "passage"
            else self._model.query_embed(prefixed)
        )
        return np.asarray(list(gen), dtype=np.float32)

    def model_identity(self) -> ModelIdentity:
        return self._identity

    @property
    def dim(self) -> int:
        return self._identity.dim


def _prefix_drift_cosine(embedder: FastEmbedEmbedder) -> float:
    """Cosine(query, passage) over the FULL path — the startup drift signal.

    Measured through ``_embed_sync`` (role prefixes applied): if the e5
    ``query: `` / ``passage: `` wiring is missing, query == passage and the
    cosine is ~1.0 (the drift). Separated roles land strictly inside
    (0.5, 0.999). Duck-typed for tests via the embedder's sync seam.
    """
    q = embedder._embed_sync([_SELF_TEST_TEXT], "query")[0]
    p = embedder._embed_sync([_SELF_TEST_TEXT], "passage")[0]
    denom = float(np.linalg.norm(q) * np.linalg.norm(p))
    if denom == 0.0:
        return 1.0
    return float(np.dot(q, p) / denom)


def build_fastembed_embedder(
    *,
    revision_sha: str | None = None,
    quantization: str = _QUANTIZATION,
) -> FastEmbedEmbedder:
    """Build the FastEmbed ONNX embedder for mE5-small (OQ-7-12 bundle).

    Requires the ``embeddings`` extra (``fastembed`` + ``onnxruntime``); the
    model downloads lazily on the first embed (not at build). Runs the startup
    prefix-drift self-test as a WARNING — never fail-loud (the G2 fallback
    covers a broken embedder).
    """
    from fastembed import (  # type: ignore[import-not-found]  # lazy: extra 'embeddings'
        TextEmbedding,
    )
    from fastembed.common.model_description import (  # type: ignore[import-not-found]
        ModelSource,
        PoolingType,
    )

    # fastembed >=0.8 requires a ModelSource dataclass (dict shorthand broke in
    # 0.8.0: model_management reads model.sources.hf). ModelSource exists with
    # the same shape across the declared >=0.6.0 range.
    TextEmbedding.add_custom_model(
        model=MODEL_NAME,
        pooling=PoolingType.MEAN,
        normalization=True,
        sources=ModelSource(hf=MODEL_NAME),
        dim=DIM,
        model_file=MODEL_FILE,
    )
    model = TextEmbedding(model_name=MODEL_NAME)
    identity = ModelIdentity(
        backend="fastembed",
        model_name=MODEL_NAME,
        revision=revision_sha or "latest",
        dim=DIM,
        quantization=quantization,
        normalized=True,
    )
    embedder = FastEmbedEmbedder(model, identity)
    _self_test(embedder)
    return embedder


def _self_test(embedder: FastEmbedEmbedder) -> None:
    """Startup prefix-drift check (f5-07 §3.4) — warning, never fail-loud.

    A broken self-test (or a model that fails to load) must not block boot: the
    facade falls back to G2 and the retrieval mode reports the embedder as
    unavailable.
    """
    try:
        cos = _prefix_drift_cosine(embedder)
    except Exception:  # noqa: BLE001 — a self-test failure is not a boot failure
        _logger.warning("embedder self-test failed; using the model as-is", exc_info=True)
        return
    if not (0.5 < cos < 0.999):
        _logger.warning(
            "embedder prefix-drift check suspicious: cosine(query, passage)=%.4f "
            "(expected in (0.5, 0.999)); retrieval may be degraded",
            cos,
        )


__all__ = [
    "FastEmbedEmbedder",
    "build_fastembed_embedder",
    "_prefix_drift_cosine",
    "MODEL_NAME",
    "MODEL_FILE",
    "DIM",
]
