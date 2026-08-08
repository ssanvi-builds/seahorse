"""Query-reranking seam — owned by #11, materialized by the embeddings extra.

#11 Hybrid Retrieval's stage-3 (f7-experimental-design §5b) needs a cross-encoder
to reorder the RRF-fused candidates by relevance to the query. The FULL
cross-encoder (fastembed ``TextCrossEncoder``) is ONNX + numpy — it is NOT
materialized in the stdlib-only core, and pulling numpy/ONNX into #11 would
violate the stdlib-only-core contract. By the established frontier pattern
(``QueryEmbedder`` is owned by #11 and materialized by #7 in
``contracts/embeddings.py``), this minimal query-path seam is OWNED by #11 and
will be MATERIALIZED by the embeddings extra (``seahorse.embeddings.rerank_backend``).
It crosses the #11 -> embeddings boundary, so it lives in ``contracts/`` — not in
``retrieval/engine.py``, which would invert the dependency direction.

The output is a ``Sequence[float]`` of relevance scores, one per doc, aligned
with the input ``docs``. Higher = more relevant. #11 never inspects the model
internals — it only consumes the scores to reorder the fused candidates.

F2 (cerebras-f-feasibility §4): the cross-encoder is a DISCRIMINATIVE rank-only
model (NOT a generative LLM — ADR-10 amendment), pinned by model identity, with
its own latency budget (``p95_index_rerank_ms <= 500ms``). Default-OFF: #11
applies it only when a ``QueryReranker`` is explicitly passed.

References:
- f5-11 §6.2 (cross-encoder mediano, opt-in, fuera del budget 250ms)
- cerebras-f-feasibility.md §4 (F2 — cross-encoder ONNX on-device, MIT bundle)
- f7-experimental-design.md §5b (rerank 4ª llamada — decide F2)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class QueryReranker(Protocol):
    """Sync query-reranking seam for #11's stage-3 (frontier pattern).

    ``rerank(query, docs)`` returns one relevance score per doc (higher = more
    relevant), aligned with ``docs``. #11 calls it ONCE per recall, AFTER the
    RRF fusion (over-fetched to ``k_rerank``), to reorder the candidates before
    truncating to ``k``. The scores are opaque floats — #11 never inspects the
    model internals.
    """

    def rerank(self, query: str, docs: Sequence[str]) -> Sequence[float]: ...


__all__ = ["QueryReranker"]
