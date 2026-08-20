"""Hybrid Retrieval — owned constants.

``RRF_K`` and ``INDEX_P95_MS`` are owned here. ``TOP_K`` is owned by the
disclosure layer (imported from ``seahorse.disclosure.types``);
``MAX_HOPS_MVP1`` is owned by the BFS axis (imported from
``seahorse.contracts.index``). This module never re-declares those.
"""

from __future__ import annotations

from types import MappingProxyType

RRF_K: int = 60
"""RRF constant (Cormack 2009). Amortizes the top-1 advantage so rank 1 and rank 2
do not differ wildly. NOT learned: a single hyper-parameter fixed up front,
never tuned on a batch. ``rrf_fuse`` uses ``1/(RRF_K + rank)`` per source."""

INDEX_P95_MS: int = 250
"""p95 latency budget for the INDEX call.

Covers embedding + stage-1 (kNN+BM25) + stage-2 (chain[+BFS] over 1 anchor,
hops=1) + RRF + handoff to ``materialize_index``. TIMELINE (150ms) / FULL (100ms)
are owned by the disclosure layer (separate anchor-based calls, outside this
budget). Asserted by the benchmark harness, not by unit tests (no wall-clock
claims here)."""

RECENCY_GAMMA: float = 0.5
"""Max multiplicative boost at age 0 for the recency signal.

``score' = score · (1 + γ·exp(-ln2·age_days/half_life))``, factor in ``[1, 1+γ]``.
A fresh-but-irrelevant candidate cannot outrank a relevant one by more than
``1+γ``. Pinned here (a single hyper-parameter fixed up front, NOT tuned on a
batch); a future calibration pass decides the real values. Default-OFF:
``recall`` applies the boost only when a ``RecencyConfig`` is explicitly passed
AND ``pit is None`` (recency is a "now"-regime signal; PIT queries reproduce
state as-of-``t`` with pure RRF)."""

RECENCY_HALF_LIFE_DAYS: float = 30.0
"""Exponential half-life of the recency signal, in days.

After ``half_life_days`` the boost halves; after ~5 half-lives it is ~3% of
``γ``. Pinned here; calibrated by a future experiment."""

DECAY_DEFAULT_HALF_LIFE_DAYS: float = 347.0
"""Default half-life (days) for the decay bias when the episode's
``cognitive_type`` is unknown or missing.

The S₀ priors (R3) cover the four memlocal-derived types; ``project_doc``,
``working`` (reserved) and ``None`` fall back to this "general knowledge" prior
(the most conservative of the table). Pinned here (fixed up front, NOT tuned on
a batch); a future calibration pass decides the real values. Default-OFF:
``recall`` applies the bias only when a ``DecayConfig`` is explicitly passed
AND ``pit is None`` (decay is a "now"-regime signal; PIT queries reproduce state
as-of-``t`` with pure RRF)."""

DECAY_HALF_LIVES_BY_TYPE: MappingProxyType[str, float] = MappingProxyType(
    {
        "episodic": 139.0,
        "semantic": 347.0,
        "social": 231.0,
        "procedural": 347.0,
    }
)
"""S₀ half-life priors (days) for the decay bias, by ``cognitive_type``.

R3 priors — experiment priors, NOT grounding: converted from the memlocal
forgetting table (``S = ln(2)/λ``), mapping only the real memlocal types
(``working``/``project_doc`` are NOT memlocal types and are excluded). The
ordinal ``S_episodic < S_semantic`` is an assumption to validate in F7, not
grounded. Immutable (``MappingProxyType``); pinned, not tuned."""

RERANK_OVERFETCH_K: int = 20
"""The over-fetch size for the stage-3 cross-encoder.

The RRF fusion runs with ``k_rerank`` (NOT ``k``) when a ``QueryReranker`` is
wired, so the cross-encoder has ~20 candidates to reorder before truncating to
``k``. Fixed cost per query (O(top-k)), NOT per episode — changing the model
never requires a reindex (query-time pure)."""

INDEX_RERANK_P95_MS: int = 500
"""p95 latency budget for the INDEX call when rerank is enabled.

Owned here. The base path keeps its 250ms promise (``INDEX_P95_MS``); the rerank
path has its OWN budget (``p95_index_rerank_ms <= 500ms``) because the
cross-encoder adds a fixed O(k_rerank) scoring step. Measured by the benchmark
harness, not by unit tests."""

__all__ = [
    "RRF_K",
    "INDEX_P95_MS",
    "RECENCY_GAMMA",
    "RECENCY_HALF_LIFE_DAYS",
    "DECAY_DEFAULT_HALF_LIFE_DAYS",
    "DECAY_HALF_LIVES_BY_TYPE",
    "RERANK_OVERFETCH_K",
    "INDEX_RERANK_P95_MS",
]
