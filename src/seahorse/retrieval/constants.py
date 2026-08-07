"""#11 Hybrid Retrieval — owned constants (f5-11 §0/§9.4).

``RRF_K`` and ``INDEX_P95_MS`` are OWNED by #11. ``TOP_K`` is owned by #8
(imported from ``seahorse.disclosure.types``); ``MAX_HOPS_MVP1`` is owned by #10
(imported from ``seahorse.contracts.index``). #11 never re-declares those.

References:
- f5-11 §5.2 (RRF_K=60, Cormack 2009, NOT learned — ADR-10)
- f5-11 §0/§10.1 (INDEX_P95_MS=250, owned by #11; the INDEX-call p95 budget)
"""

from __future__ import annotations

RRF_K: int = 60
"""RRF constant (Cormack 2009). Amortizes the top-1 advantage so rank 1 and rank 2
do not differ wildly. NOT learned (ADR-10): a single hyper-parameter fixed up
front, never tuned on a batch. ``rrf_fuse`` uses ``1/(RRF_K + rank)`` per source."""

INDEX_P95_MS: int = 250
"""p95 latency budget for the INDEX call, OWNED by #11 (f5-11 §10.1).

Covers embedding (#7) + stage-1 (kNN+BM25) + stage-2 (chain[+BFS] over 1 anchor,
hops=1) + RRF + handoff to ``materialize_index``. TIMELINE (150ms) / FULL (100ms)
are owned by #8 (separate anchor-based calls, outside this budget). Asserted by
the #16 benchmark harness, NOT by #11 unit tests (no wall-clock claims here)."""

RECENCY_GAMMA: float = 0.5
"""F1 recency — max multiplicative boost at age 0 (cerebras-f-feasibility §3).

``score' = score · (1 + γ·exp(-ln2·age_days/half_life))``, factor in ``[1, 1+γ]``.
A fresh-but-irrelevant candidate cannot outrank a relevant one by more than
``1+γ``. Pinned here (ADR-10: a single hyper-parameter fixed up front, NOT tuned
on a batch); the F7 recency experiment (LMEB) decides the real calibration.
Default-OFF: ``recall`` applies the boost only when a ``RecencyConfig`` is
explicitly passed AND ``pit is None`` (recency is a "now"-regime signal; PIT
queries reproduce state as-of-``t`` with pure RRF)."""

RECENCY_HALF_LIFE_DAYS: float = 30.0
"""F1 recency — exponential half-life of the signal, in days.

After ``half_life_days`` the boost halves; after ~5 half-lives it is ~3% of
``γ``. Pinned here (ADR-10); calibrated by the F7 experiment."""

__all__ = ["RRF_K", "INDEX_P95_MS", "RECENCY_GAMMA", "RECENCY_HALF_LIFE_DAYS"]
