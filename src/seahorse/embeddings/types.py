"""Embeddings core types.

``ModelIdentity`` stamps every vector (cache keys, drift detection, audit);
``Embedder`` is the async backend Protocol (the sync ``QueryEmbedder`` adapter
bridges it for sync callers); ``_l2_normalize`` is the defensive L2 normalizer
every backend applies (zero-vector safe).

Import-laziness: ``numpy`` is imported ONLY inside ``_l2_normalize`` (and via
``TYPE_CHECKING`` for annotations) so importing this module never pulls the
heavy numpy runtime into the core import path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np

# Embedding-mode experiment: what text the passage embedder receives. ``body``
# is the baseline; ``body+summary`` folds the editorial summary in front of the
# body. Single source for the facade factory and the indexer (this module stays
# numpy-light — import-laziness).
EMBED_MODES = ("body", "body+summary")

Role = Literal["query", "passage"]


@dataclass(frozen=True)
class ModelIdentity:
    """Immutable identity of an embedding model."""

    backend: str  # 'sentence-transformers' | 'fastembed' | 'ollama' | ...
    model_name: str  # e.g. 'intfloat/multilingual-e5-small'
    revision: str  # 40-char commit hash, or 'latest' for opaque APIs
    dim: int  # 384 for mE5-small
    quantization: str  # 'fp32' | 'fp16' | 'int8'
    normalized: bool  # True if the backend already L2-normalizes

    def cache_key(self) -> str:
        """Stable cache key: ``backend:model:rev12:dim:quant``."""
        return (
            f"{self.backend}:{self.model_name}:{self.revision[:12]}:"
            f"{self.dim}:{self.quantization}"
        )

    def major_version(self) -> str:
        """Coarse identity (model + dim + normalization) for drift detection."""
        return f"{self.model_name}:{self.dim}:{self.normalized}"


@runtime_checkable
class Embedder(Protocol):
    """Async embedding backend.

    ``embed`` returns a new ``(N, dim)`` float32 array, L2-unit-normalized, and
    never mutates state. Backends with ``normalized=True`` already L2-normalize;
    the wrapper forces it otherwise via ``_l2_normalize``.
    """

    async def embed(self, texts: Sequence[str], role: Role) -> np.ndarray: ...

    def model_identity(self) -> ModelIdentity:
        """Sync and cheap — used in cache keys, mismatch detection, audit."""
        ...

    @property
    def dim(self) -> int: ...


def _l2_normalize(vecs: np.ndarray) -> np.ndarray:
    """L2-unit-normalize along the last axis (zero-vector safe).

    ``norms == 0`` maps to 1.0 so a zero vector stays a zero vector (no NaN).
    numpy is imported lazily here — this module imports without numpy.
    """
    import numpy as np

    arr = np.asarray(vecs, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


__all__ = ["ModelIdentity", "Embedder", "Role", "EMBED_MODES", "_l2_normalize"]
