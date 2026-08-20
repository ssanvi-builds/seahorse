"""Hybrid Retrieval Engine.

RRF fusion of vector + BM25 (+ optional chain) over 1-based ranks, with
reproducible ranking and NO LLM in the query path. This module owns fusion +
final ranking + PIT routing; it does NOT own storage, embeddings, or the BFS
axis — those are injected as typed Protocols. The query-embedding extension
point (``QueryEmbedder``) lives in ``seahorse.contracts.embeddings``.

This module ships RRF over vector + BM25 (stage 1) with chain as an optional
anchor-driven 3rd source (stage 2). The BFS-as-index axis was removed
(unreachable dead code; the F7 (e) multi-hop experiment recommends a physical
graph with typed edge traversal — a different construct — and the ``graph_bfs``
timeline axis already covers the user-facing graph traversal).

Honest deviations from the spec (documented in ``engine.py``):
- Stage 1 runs SEQUENTIALLY (no ``asyncio.gather``). The spec's parallelism is a
  micro-optimization deferred to a later release for the real async embedder +
  real SQLite readers. This module is stdlib-only sync; reproducibility is
  preserved because RRF is rank-based and the ``(-score, ep_id)`` tie-break is
  independent of arrival order.
"""

from __future__ import annotations

from seahorse.retrieval.constants import (
    DECAY_DEFAULT_HALF_LIFE_DAYS,
    DECAY_HALF_LIVES_BY_TYPE,
    INDEX_P95_MS,
    RECENCY_GAMMA,
    RECENCY_HALF_LIFE_DAYS,
    RRF_K,
)
from seahorse.retrieval.decay import DecayConfig, apply_decay_bias
from seahorse.retrieval.engine import recall
from seahorse.retrieval.errors import RetrievalInvalidPITKind
from seahorse.retrieval.fusion import SourceList, rrf_fuse
from seahorse.retrieval.recency import RecencyConfig, apply_recency_boost

__all__ = [
    "DECAY_DEFAULT_HALF_LIFE_DAYS",
    "DECAY_HALF_LIVES_BY_TYPE",
    "INDEX_P95_MS",
    "RECENCY_GAMMA",
    "RECENCY_HALF_LIFE_DAYS",
    "RRF_K",
    "DecayConfig",
    "RecencyConfig",
    "RetrievalInvalidPITKind",
    "SourceList",
    "apply_decay_bias",
    "apply_recency_boost",
    "recall",
    "rrf_fuse",
]
