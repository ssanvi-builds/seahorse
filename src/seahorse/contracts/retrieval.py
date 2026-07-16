"""Fused candidate contract — the #11 → #8 boundary.

``FusedCandidate`` is the ranked, fused, body-less candidate emitted by
#11 Hybrid Retrieval and projected by #8 Progressive Disclosure into the
index/timeline/full levels.

Ownership (frontier pattern): ``FusedCandidate`` is **owned by #11**. It is
materialized here by #8 (the first consumer to ship) as a stable frontier so
#8 can compile and test against a fixed shape. When #11 ships, it IMPORTS
``FusedCandidate`` from here — it does NOT relocate or redefine it. A field
addition is additive/non-breaking; a rename or removal requires a new
sign-off. This mirrors ``IndexRowData`` (owned by #8, materialized by #6 in
``contracts/index.py``).

Reproducibility (ADR-10): the ``score`` is the RRF-fused reproducible score
from #11; #8 passes it through verbatim into ``IndexRow.score`` and never
recomputes or reorders by it. ``sources`` is provenance of which retrievers
contributed the candidate (``"vector"``/``"bm25"``/``"bfs"``/``"chain"``) —
NOT a reranking signal.

References:
- f5-08 §3.2 (FusedCandidate seam, #11 owns the type)
- f5-11 (Hybrid Retrieval Engine — fusion + RRF + reproducible scoring)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FusedCandidate:
    """Ranked fused candidate from #11, body-less. Owned by #11.

    ``ep_id`` is the absolute episode id (positional anchor for the 2nd/3rd
    disclosure calls). ``score`` is reproducible (ADR-10): the same query +
    index state yields the same score, independent of batch or arrival order.
    """

    ep_id: str
    score: float
    sources: tuple[str, ...]


__all__ = ["FusedCandidate"]