"""#11 Hybrid Retrieval Engine (owned by #11; f5-11).

The cima do fosso competitivo MVP-1: RRF fusion of vector + BM25 (+ optional
chain / BFS mediano) over 1-based ranks, with reproducible ranking (ADR-10) and
NO LLM in the query path. #11 OWNS fusion + final ranking + PIT routing; it does
NOT own storage (#6), embeddings (#7), or the BFS axis (#10) — those are injected
as typed Protocols. The query-embedding seam (``QueryEmbedder``) lives in
``seahorse.contracts.embeddings`` (owned by #11, materialized by #7).

MVP-1 (this module) ships RRF over vector + BM25 (stage 1) with chain as an
optional anchor-driven 3rd source (stage 2). BFS is TIMELINE-only by default
(``bfs_as_index_enabled=False``); using it as an INDEX fusion source is a mediano
extension pending #8/#10 sign-off. ``known_at`` BFS raises
``BfsKnownAtUnsupported`` unless ``bfs_known_at_supported=True`` (TD-2 sign-off).

Honest deviations from the spec (documented in ``engine.py``):
- Stage 1 runs SEQUENTIALLY (no ``asyncio.gather``). The spec's parallelism is an
  MVP-1 micro-opt for the real async #7 Embedder + real SQLite readers. #11 is
  stdlib-only sync; ADR-10 is preserved because RRF is rank-based and the
  ``(-score, ep_id)`` tie-break is independent of arrival order.

References:
- f5-11 (Hybrid Retrieval Engine — the load-bearing spec, 1376 lines)
- f6-signoffs.md SO-6 (RRF in Python puro en #11)
- seahorse.contracts.retrieval (FusedCandidate — owned by #11, materialized by #8)
"""

from __future__ import annotations

from seahorse.retrieval.constants import INDEX_P95_MS, RRF_K
from seahorse.retrieval.engine import recall
from seahorse.retrieval.errors import BfsKnownAtUnsupported, InvalidPITKind
from seahorse.retrieval.fusion import SourceList, rrf_fuse

__all__ = [
    "BfsKnownAtUnsupported",
    "INDEX_P95_MS",
    "InvalidPITKind",
    "RRF_K",
    "SourceList",
    "recall",
    "rrf_fuse",
]
