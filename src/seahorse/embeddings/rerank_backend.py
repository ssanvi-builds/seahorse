"""FastEmbed ONNX cross-encoder backend (F2, f7 §5b).

Wraps fastembed's sync ``TextCrossEncoder`` behind the ``QueryReranker`` seam
(``contracts/rerank.py``). fastembed + onnxruntime live in the optional
``embeddings`` extra; ``build_fastembed_reranker`` imports ``fastembed`` lazily
so the default ``uv sync --extra dev`` (G2 mode) never pulls the heavy stack.

Bundle (validated 2026-08-08 on Apple Silicon): ``bge-reranker-v2-m3`` (MIT,
multilingual) converted to ONNX o4 — ``hooman650/bge-reranker-v2-m3-onnx-o4``
(``model.onnx`` + ``model.onnx.data``, ~1.1GB). A 20-pair rerank measured
~204ms on arm64 — within the ``p95_index_rerank_ms <= 500ms`` budget (f7 §5b).
The fastembed default ``jina-reranker-v2-base-multilingual`` is cc-by-nc-4.0
(incompatible with the Apache-2.0 standard, ADR-011) — the MIT
``bge-reranker-v2-m3`` is the coherent multilingual choice (cerebras-f §4.2).

The reranker is query-time pure: changing the model NEVER requires a reindex
(cerebras-f §4.2). Cost is fixed per query (O(k_rerank) pairs), not per episode.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

_logger = logging.getLogger("seahorse.embeddings.rerank")

# Bundle pin (single source for the model identity + the fingerprint).
MODEL_NAME = "hooman650/bge-reranker-v2-m3-onnx-o4"
MODEL_FILE = "model.onnx"
ADDITIONAL_FILES = ("model.onnx.data",)
_LICENSE = "mit"
_SIZE_GB = 1.1


class FastEmbedReranker:
    """Sync adapter over a fastembed ``TextCrossEncoder`` (duck-typed for tests).

    ``rerank(query, docs)`` returns one relevance score per doc (higher = more
    relevant), aligned with ``docs`` — the ``QueryReranker`` seam #11 consumes.
    The batch is sized to the full doc list (20 pairs in one ONNX run — the
    measured ~204ms on arm64, f7 §5b).
    """

    def __init__(self, model) -> None:
        self._model = model

    def rerank(self, query: str, docs: Sequence[str]) -> Sequence[float]:
        batch = len(docs) or 1
        return list(self._model.rerank(query, docs, batch_size=batch))


def build_fastembed_reranker() -> FastEmbedReranker:
    """Build the FastEmbed ONNX cross-encoder for bge-reranker-v2-m3 (F2).

    Requires the ``embeddings`` extra (``fastembed`` + ``onnxruntime``); the
    model downloads lazily on the first build (not at import). Idempotent
    registration: ``add_custom_model`` raises "already registered" on a second
    call in the same process — the F7 warm-DB experiment builds a facade per
    variant, so the second+ calls must reuse the registered model instead of
    failing (which would silently degrade the hybrid regime to G2).
    """
    from fastembed.common.model_description import (  # type: ignore[import-not-found]
        ModelSource,
    )
    from fastembed.rerank.cross_encoder import (  # type: ignore[import-not-found]  # lazy: extra 'embeddings'
        TextCrossEncoder,
    )

    try:
        TextCrossEncoder.add_custom_model(
            model=MODEL_NAME,
            sources=ModelSource(hf=MODEL_NAME),
            model_file=MODEL_FILE,
            additional_files=list(ADDITIONAL_FILES),
            description="bge-reranker-v2-m3 (MIT, multilingual) ONNX o4",
            license=_LICENSE,
            size_in_gb=_SIZE_GB,
        )
    except ValueError as exc:
        if "already registered" not in str(exc):
            raise
    model = TextCrossEncoder(model_name=MODEL_NAME)
    return FastEmbedReranker(model)


__all__ = [
    "FastEmbedReranker",
    "build_fastembed_reranker",
    "MODEL_NAME",
    "MODEL_FILE",
    "ADDITIONAL_FILES",
]
