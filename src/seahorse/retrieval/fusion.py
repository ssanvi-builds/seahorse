"""RRF fusion — the heart of Hybrid Retrieval (pure Python, no I/O).

Owns the fusion + the final ranking + the ``FusedCandidate`` construction.
``FusedCandidate`` itself is owned here and lives in
``seahorse.contracts.retrieval``; this module imports it — it does not redefine
it.

What RRF is: rank-based, score-scale-agnostic, deterministic fusion. The ``score``
magnitudes that the source repositories produce (``1/(1+distance)``,
``exp(-bm25)``) are DISCARDED by RRF — they only drove the sources' internal
over-fetch ``ORDER BY`` / ``LIMIT``. RRF inputs the per-source 1-based RANK
(position in the ordered list). ``FusedCandidate.score = Σ over sources where c
appears of 1/(RRF_K + rank)``.

Implementation notes:
- The design draft used the field name ``source`` (singular); the signed contract
  ``contracts.retrieval.FusedCandidate`` uses ``sources`` (plural). The signed
  contract is authoritative — this module builds ``sources``.
- The design draft used 4 named params (``vector_hits``/``bm25_hits``/
  ``chain_eps``/``bfs_rows``). The 4 source types carry DIFFERENT id fields
  (``VectorHit``/``FullTextHit``/``IndexRowData`` expose ``.ep_id``; ``Episode``
  exposes ``.id``). Per-source key callables are therefore required, so the
  signature is generalized to ``Sequence[SourceList]`` with an explicit ``key``
  extractor per source. This preserves ALL RRF semantics (dedup-before-fuse,
  first-occurrence-wins within a source, ``sources = sorted(union)``,
  ``(-score, ep_id)`` ranking, truncate to ``k``, no padding) while being
  type-safe and extensible (a 5th source does not change the signature).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from seahorse.contracts.retrieval import FusedCandidate
from seahorse.retrieval.constants import RRF_K


@dataclass(frozen=True)
class SourceList:
    """A named, ranked list from one retrieval source.

    ``name`` is the provenance tag (``"vector"``/``"bm25"``/``"chain"``/``"bfs"``).
    ``items`` is ordered best-first (rank 1 = ``items[0]``); the order is the
    reproducible ``ORDER BY`` of the owning component (the source repositories
    for vector/bm25, the engine for chain by ``created_at`` asc, the BFS axis
    for bfs by hop order).
    ``key`` extracts the episode id (``VectorHit``/``FullTextHit``/``IndexRowData``
    -> ``.ep_id``; ``Episode`` -> ``.id``).

    Not generic (PEP 695 ``class SourceList[T]`` needs py3.12; the project targets
    py3.11). ``items: Sequence[Any]`` + ``key: Callable[[Any], str]`` keeps mypy
    satisfied and the surface 3.11-compatible.
    """

    name: str
    items: Sequence[Any]
    key: Callable[[Any], str]


def rrf_fuse(
    sources: Sequence[SourceList], *, k: int, rrf_k: int | None = None
) -> list[FusedCandidate]:
    """Pure-Python Reciprocal Rank Fusion over 1-based ranks.

    Steps (semantics preserved):
      1. Dedup by ``ep_id`` BEFORE fusing. Within ONE source, the FIRST occurrence
         wins (best rank); subsequent duplicates in the same source are ignored
         (defensive — the source repositories should not return duplicates, but
         if they do, the best rank counts, never the worst).
      2. Accumulate ``score[ep] += 1/(rrf_k + rank)`` and ``provenance[ep].add(name)``
         for every (source, rank) where ``ep`` appears.
      3. Build ``FusedCandidate(ep, score, sources=tuple(sorted(provenance)))`` —
         ``sources`` is the sorted-union provenance, a read-only audit field
         (NOT a rerank signal).
      4. Rank: descending ``score`` with deterministic tie-break by ``ep_id`` asc
         (UUIDv7 is time-orderable; two runs with the same state yield the same
         order — reproducibility).
      5. Truncate to ``k``. Robust to ``< k`` and empty sources: returns fewer than
         ``k`` (or ``[]``); NEVER pads with invented scores.

    ``rrf_k`` overrides the module constant ``RRF_K`` (the benchmark sweep
    A5 measures the fusion across ``{10, 20, 40, 60}``); ``None`` keeps the
    production default. ``k <= 0`` returns ``[]`` (truncate-to-0). No validation
    of ``k`` — the caller owns wire-shape validation (the MCP server and the
    CLI); this module is robust to whatever it gets.
    """
    k_eff = RRF_K if rrf_k is None else rrf_k
    scores: dict[str, float] = defaultdict(float)
    provenance: dict[str, set[str]] = defaultdict(set)

    for src in sources:
        seen_in_src: set[str] = set()
        for rank, item in enumerate(src.items, start=1):  # 1-based rank
            ep_id = src.key(item)
            if ep_id in seen_in_src:
                continue  # first occurrence wins (best rank) within a source
            seen_in_src.add(ep_id)
            scores[ep_id] += 1.0 / (k_eff + rank)
            provenance[ep_id].add(src.name)

    candidates = [
        FusedCandidate(
            ep_id=ep_id,
            score=score,
            sources=tuple(sorted(provenance[ep_id])),  # canonical alphabetical
        )
        for ep_id, score in scores.items()
    ]
    # Deterministic ranking: descending score, tie-break by ep_id asc.
    candidates.sort(key=lambda c: (-c.score, c.ep_id))
    return candidates[:k]


__all__ = ["SourceList", "rrf_fuse"]
