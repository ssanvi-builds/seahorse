"""Fused candidate contract — the hybrid-retrieval → progressive-disclosure boundary.

``FusedCandidate`` is the ranked, fused, body-less candidate emitted by Hybrid
Retrieval and projected by Progressive Disclosure into the index/timeline/full
levels.

Ownership (frontier pattern): ``FusedCandidate`` is **owned by hybrid
retrieval**. It is materialized here by progressive disclosure (the first
consumer to ship) as a stable frontier so it can compile and test against a
fixed shape. When hybrid retrieval ships, it IMPORTS ``FusedCandidate`` from
here — it does NOT relocate or redefine it. A field addition is
additive/non-breaking; a rename or removal requires a new sign-off. This mirrors
``IndexRowData`` (owned by progressive disclosure, materialized by the
persistence layer in ``contracts/index.py``).

Reproducibility: the ``score`` is the RRF-fused reproducible score from hybrid
retrieval; progressive disclosure passes it through verbatim into
``IndexRow.score`` and never recomputes or reorders by it. ``sources`` is
provenance of which retrievers contributed the candidate
(``"vector"``/``"bm25"``/``"bfs"``/``"chain"``) — NOT a reranking signal.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FusedCandidate:
    """Ranked fused candidate from hybrid retrieval, body-less. Owned by hybrid retrieval.

    ``ep_id`` is the absolute episode id (positional anchor for the 2nd/3rd
    disclosure calls). ``score`` is reproducible: the same query + index state
    yields the same score, independent of batch or arrival order.
    """

    ep_id: str
    score: float
    sources: tuple[str, ...]


__all__ = ["FusedCandidate"]