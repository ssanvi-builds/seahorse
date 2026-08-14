"""Query-reranking seam — owned by hybrid retrieval, materialized by the embeddings extra.

Hybrid Retrieval's stage-3 needs a cross-encoder to reorder the RRF-fused
candidates by relevance to the query. The FULL cross-encoder (fastembed
``TextCrossEncoder``) is ONNX + numpy — it is NOT materialized in the stdlib-only
core, and pulling numpy/ONNX into hybrid retrieval would violate the
stdlib-only-core contract. By the established frontier pattern (``QueryEmbedder``
is owned by hybrid retrieval and materialized by the embedder in
``contracts/embeddings.py``), this minimal query-path seam is OWNED by hybrid
retrieval and will be MATERIALIZED by the embeddings extra
(``seahorse.embeddings.rerank_backend``). It crosses the hybrid-retrieval ->
embeddings boundary, so it lives in ``contracts/`` — not in
``retrieval/engine.py``, which would invert the dependency direction.

The output is a ``Sequence[float]`` of relevance scores, one per doc, aligned
with the input ``docs``. Higher = more relevant. Hybrid retrieval never inspects
the model internals — it only consumes the scores to reorder the fused
candidates.

The cross-encoder is a DISCRIMINATIVE rank-only model (NOT a generative LLM),
pinned by model identity, with its own latency budget
(``p95_index_rerank_ms <= 500ms``). Default-OFF: hybrid retrieval applies it
only when a ``QueryReranker`` is explicitly passed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class QueryReranker(Protocol):
    """Sync query-reranking seam for hybrid retrieval's stage-3.

    ``rerank(query, docs)`` returns one relevance score per doc (higher = more
    relevant), aligned with ``docs``. Hybrid retrieval calls it ONCE per recall,
    AFTER the RRF fusion (over-fetched to ``k_rerank``), to reorder the
    candidates before truncating to ``k``. The scores are opaque floats — hybrid
    retrieval never inspects the model internals.
    """

    def rerank(self, query: str, docs: Sequence[str]) -> Sequence[float]: ...


__all__ = ["QueryReranker"]
