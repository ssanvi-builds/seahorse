"""RRF fusion — the heart of #11 Hybrid Retrieval (pure Python, no I/O, ADR-10).

Owns the fusion + the final ranking + the ``FusedCandidate`` construction.
``FusedCandidate`` itself is OWNED by #11 and lives in
``seahorse.contracts.retrieval`` (materialized by #8 as the first consumer to
ship); #11 IMPORTS it — it does not redefine it.

What RRF is (f5-11 §5): rank-based, score-scale-agnostic, deterministic fusion.
The ``score`` magnitudes that #6 produces (``1/(1+distance)``, ``exp(-bm25)``)
are DISCARDED by RRF — they only drove #6's internal over-fetch ``ORDER BY`` /
``LIMIT``. RRF inputs the per-source 1-based RANK (position in the ordered list).
``FusedCandidate.score = Σ over sources where c appears of 1/(RRF_K + rank)``.

Spec-inconsistency notes (resolved here, documented for review):
- f5-11 §5.3/§5.4 use the field name ``source`` (singular); the SIGNED contract
  ``contracts.retrieval.FusedCandidate`` uses ``sources`` (plural). The signed
  contract is authoritative — this module builds ``sources``. The pseudocode
  ``source`` is illustrative only.
- f5-11 §5.3 pseudocode uses 4 named params (``vector_hits``/``bm25_hits``/
  ``chain_eps``/``bfs_rows``). The 4 source types carry DIFFERENT id fields
  (``VectorHit``/``FullTextHit``/``IndexRowData`` expose ``.ep_id``; ``Episode``
  exposes ``.id``). Per-source key callables are therefore required, so the
  signature is generalized to ``Sequence[SourceList]`` with an explicit ``key``
  extractor per source. This preserves ALL RRF semantics from §5.3 (dedup-before-
  fuse, first-occurrence-wins within a source, ``sources = sorted(union)``,
  ``(-score, ep_id)`` ranking, truncate to ``k``, no padding) while being
  type-safe and extensible (a 5th source does not change the signature).

References:
- f5-11 §5 (RRF strategy), §5.3 (pseudocode), §16 (test signals)
- f6-signoffs.md SO-6 (RRF in Python puro en #11; #6 returns raw lists per source)
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
    reproducible ``ORDER BY`` of the owning component (#6 for vector/bm25,
    #2 for chain by ``created_at`` asc, #10 for bfs by hop order).
    ``key`` extracts the episode id (``VectorHit``/``FullTextHit``/``IndexRowData``
    -> ``.ep_id``; ``Episode`` -> ``.id``).

    Not generic (PEP 695 ``class SourceList[T]`` needs py3.12; the project targets
    py3.11). ``items: Sequence[Any]`` + ``key: Callable[[Any], str]`` keeps mypy
    satisfied and the surface 3.11-compatible.
    """

    name: str
    items: Sequence[Any]
    key: Callable[[Any], str]


def rrf_fuse(sources: Sequence[SourceList], *, k: int) -> list[FusedCandidate]:
    """Pure-Python Reciprocal Rank Fusion over 1-based ranks (ADR-10).

    Steps (f5-11 §5.3, semantics preserved):
      1. Dedup by ``ep_id`` BEFORE fusing. Within ONE source, the FIRST occurrence
         wins (best rank); subsequent duplicates in the same source are ignored
         (defensive — #6 should not return duplicates, but if it does, the best
         rank counts, never the worst).
      2. Accumulate ``score[ep] += 1/(RRF_K + rank)`` and ``provenance[ep].add(name)``
         for every (source, rank) where ``ep`` appears.
      3. Build ``FusedCandidate(ep, score, sources=tuple(sorted(provenance)))`` —
         ``sources`` is the sorted-union provenance, a read-only audit field
         (NOT a rerank signal, R11).
      4. Rank: descending ``score`` with deterministic tie-break by ``ep_id`` asc
         (UUIDv7 is time-ordenable; two runs with the same state yield the same
         order — reproducibility ADR-10).
      5. Truncate to ``k``. Robust to ``< k`` and empty sources: returns fewer than
         ``k`` (or ``[]``); NEVER pads with invented scores (ADR-10).

    ``k <= 0`` returns ``[]`` (truncate-to-0). No validation of ``k`` — the
    caller owns wire-shape validation (#13/#14); #11 is robust to whatever it gets.
    """
    scores: dict[str, float] = defaultdict(float)
    provenance: dict[str, set[str]] = defaultdict(set)

    for src in sources:
        seen_in_src: set[str] = set()
        for rank, item in enumerate(src.items, start=1):  # 1-based rank
            ep_id = src.key(item)
            if ep_id in seen_in_src:
                continue  # first occurrence wins (best rank) within a source
            seen_in_src.add(ep_id)
            scores[ep_id] += 1.0 / (RRF_K + rank)
            provenance[ep_id].add(src.name)

    candidates = [
        FusedCandidate(
            ep_id=ep_id,
            score=score,
            sources=tuple(sorted(provenance[ep_id])),  # canonical alphabetical
        )
        for ep_id, score in scores.items()
    ]
    # Deterministic ranking: descending score, tie-break by ep_id asc (ADR-10).
    candidates.sort(key=lambda c: (-c.score, c.ep_id))
    return candidates[:k]


__all__ = ["SourceList", "rrf_fuse"]