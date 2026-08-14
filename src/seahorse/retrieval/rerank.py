"""Cross-encoder rerank as a stage-3 post-RRF step (opt-in).

A small, localized post-RRF step in the retrieval engine's ``recall``. The
cross-encoder reorders the RRF-fused candidates by relevance to the query,
REPLACING the RRF score with the cross-encoder score (the manifest records
``score_source: "rrf_rerank"`` for this variant). Default-OFF: ``recall``
applies it only when a ``QueryReranker`` is explicitly passed.

The text to score is hydrated by the caller (``index_repo.get_rows`` →
summary/subject, ~20×200 chars — NOT ``body_md``). This module is a pure
function over the already-fetched scores.

Honesty: a candidate with no score (the scores list is shorter than candidates)
is kept with its RRF score at the end — never invent a score. The deterministic
``(-score, ep_id)`` tie-break preserves reproducibility.
"""

from __future__ import annotations

from collections.abc import Sequence

from seahorse.contracts.retrieval import FusedCandidate


def apply_rerank(
    candidates: Sequence[FusedCandidate],
    scores: Sequence[float],
    *,
    k: int,
) -> list[FusedCandidate]:
    """Reorder candidates by cross-encoder scores, truncate to ``k``.

    Rebuilds each candidate with ``score = rerank_score`` (the cross-encoder
    relevance — the manifest records ``score_source: "rrf_rerank"``), sorts
    descending with the deterministic ``ep_id`` tie-break, truncates to ``k``.
    A candidate with no score (the scores list is shorter than candidates) is
    kept with its RRF score at the end (honest — never invent a score).
    """
    reranked: list[FusedCandidate] = []
    for i, c in enumerate(candidates):
        score = scores[i] if i < len(scores) else c.score
        reranked.append(FusedCandidate(ep_id=c.ep_id, score=score, sources=c.sources))
    reranked.sort(key=lambda c: (-c.score, c.ep_id))
    return reranked[:k]


__all__ = ["apply_rerank"]
