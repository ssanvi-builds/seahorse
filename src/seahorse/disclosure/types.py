"""Progressive Disclosure payload types (#8).

The three disclosure levels (index → timeline → full) and the transition
protocol data. ``IndexRow``/``TimelineEntry``/``TimelineWindow``/``FullDetail``
are the SHAPED payloads #8 emits; #11 owns fusion+ranking, #8 only projects.

Load-bearing rules (f5-08 §1, §2):
- INDEX and TIMELINE carry NO body. Only FULL hydrates ``body_md``.
- ``IndexRow.score`` is passthrough from #11 (reproducible, ADR-10); #8 never
  recomputes it.
- ``TimelineEntry.score`` is ALWAYS ``None`` in MVP-0: timeline is
  anchor-based, not query-based, so timeline hits have no inherited fused
  score. #8 projects, it does not score (f5-08 §2.2).
- Deterministic truncation: ``subject[:SUBJECT_MAX_CHARS]`` and
  ``summary[:SUMMARY_MAX_CHARS]`` so the token target is reproducible (ADR-10).
- ``stale``/``pending_ingest`` reflect the CURRENT regime (not the PIT
  instant) — derived from ``FreshnessView`` semantics (f5-08 §5.4).

References:
- f5-08 §2 (three levels: shapes, budgets, generation)
- f5-08 §5.4 (stale/pending_ingest semantics)
- contracts/engine.py freshness_of (single source of truth for FullDetail)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from seahorse.contracts.engine import FreshnessView
from seahorse.contracts.episode import Episode
from seahorse.contracts.index import PITKind

# ---------------------------------------------------------------------------
# Pins / constants (declared, adjustable by config; f5-08 header).
# ---------------------------------------------------------------------------

TOP_K: int = 10
"""Default rows per INDEX recall (1st call, within #11's 250ms budget)."""

MAX_TIMELINE_WINDOW: int = 20
"""Max entries in a TIMELINE window (2nd call). Bounds the payload."""

MAX_FULL_BATCH: int = 5
"""Max episodes per FULL call (3rd call). Exceeding raises FullBatchTooLarge."""

SUMMARY_MAX_CHARS: int = 200
"""Deterministic truncation of the denormalized summary (ADR-10)."""

SUBJECT_MAX_CHARS: int = 160
"""Deterministic truncation of the subject in the INDEX payload (ADR-10)."""

TimelineAxis = Literal[
    "supersedes_chain",  # MVP-0 — chain_rows_from(anchor)
    "fact_id_scope",  # MVP-0 — find_vigent_row_by_fact_id(anchor.fact_id)
    "created_at",  # MVP-1 — range_rows_* around anchor (±Δt)
    "valid_at",  # MVP-1 — range over valid_at (PIT)
    "graph_bfs",  # MVP-1 — 1-2 hop temporal graph via #10
]
"""Timeline axis (Literal extensible so L2c procedural doesn't break the seam)."""

MVP0_AXES: frozenset[str] = frozenset({"supersedes_chain", "fact_id_scope"})
"""MVP-0 timeline axes (f5-08 §4.2). Others raise NotInMVP0."""


# ---------------------------------------------------------------------------
# PIT point (the disclosure-level carrier of a bi-temporal query; #8 owns it).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PITPoint:
    """A bi-temporal point-in-time carried through the disclosure calls.

    ``kind`` follows ADR-03: the two axes are never mixed. ``state_at`` filters
    the valid_time axis (valid_at/invalid_at); ``known_at`` filters the
    transaction_time axis (created_at/expired_at).
    """

    kind: PITKind
    t: datetime


# ---------------------------------------------------------------------------
# INDEX level (1st call) — list[IndexRow], NO body.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexRow:
    """INDEX payload row (1st call). NO body. ~50 tok/result target.

    ``score`` is passthrough from #11 (RRF-fused, reproducible ADR-10); #8 does
    not recompute or reorder. ``stale``/``pending_ingest`` reflect the current
    regime (f5-08 §5.4), NOT the PIT instant.
    """

    ep_id: str
    fact_id: str
    subject: str  # truncated: subject[:SUBJECT_MAX_CHARS]
    title: str | None
    summary: str | None  # truncated: summary[:SUMMARY_MAX_CHARS]
    cognitive_type: str
    skip_extraction: bool  # ADR-09 surfaced identically
    valid_at: datetime | None
    invalid_at: datetime | None
    created_at: datetime
    score: float  # passthrough from #11
    stale: bool  # invalid_at is not None (current regime)
    pending_ingest: bool  # valid_at is not None and valid_at > now


# ---------------------------------------------------------------------------
# TIMELINE level (2nd call) — TimelineWindow, NO body.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimelineEntry:
    """TIMELINE entry (2nd call). NO body. ``score`` ALWAYS None in MVP-0.

    Timeline is anchor-based, not query-based: its hits may not have been in
    #11's fused result, so there is no inherited fused score. #8 projects; it
    does not score (f5-08 §2.2).
    """

    ep_id: str
    fact_id: str
    subject: str
    title: str | None
    summary: str | None
    cognitive_type: str
    valid_at: datetime | None
    invalid_at: datetime | None
    created_at: datetime
    supersedes: str | None  # chain adjacency
    score: float | None = None  # ALWAYS None in MVP-0


@dataclass(frozen=True)
class TimelineWindow:
    """TIMELINE payload (2nd call). Bounded: len(entries) <= MAX_TIMELINE_WINDOW."""

    anchor_ep_id: str
    axis: TimelineAxis
    entries: tuple[TimelineEntry, ...]
    pit: PITPoint | None = None


# ---------------------------------------------------------------------------
# FULL level (3rd call) — list[FullDetail], the ONLY level that loads body.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EpisodeProvenance:
    """Typed provenance slice of an Episode (corrección Lens C ISS-07).

    Drawn from ``Episode.provenance`` (a dict in F3.1) into a typed shape so the
    FULL payload is self-describing. Missing keys map to ``None``.
    """

    agent_id: str | None
    session_id: str | None
    source_type: str | None
    extraction_mode: str | None
    model_used: str | None


@dataclass(frozen=True)
class FullDetail:
    """FULL payload (3rd call). The ONLY level that hydrates ``body_md``.

    ``episode`` is the complete F3.1 episode (with body). ``freshness`` is the
    pure ``FreshnessView`` derivation (single source of truth: freshness_of).
    """

    episode: Episode
    provenance: EpisodeProvenance
    freshness: FreshnessView
    pit: PITPoint | None = None


# ---------------------------------------------------------------------------
# Exceptions (typed, fail-loud; f5-08 §3.5, §5.3).
# ---------------------------------------------------------------------------


class FullBatchTooLarge(Exception):
    """Raised by ``materialize_full`` when ``len(ep_ids) > MAX_FULL_BATCH``.

    The agent asks full for a filtered subset after inspecting index/timeline,
    never for the whole K. Cap = 5 (f5-08 §3.5).
    """

    def __init__(self, requested: int, cap: int) -> None:
        self.requested = requested
        self.cap = cap
        super().__init__(f"full batch={requested} exceeds cap={cap}")


class PitFullNotSupported(Exception):
    """Raised by ``materialize_full`` when ``pit`` is provided in MVP-0.

    Full PIT semantics (resolving a ``fact_id``-as-of-``t``) are not contracted
    in MVP-0 (f5-08 §5.3). INDEX and TIMELINE ARE PIT-aware by construction; FULL
    is not. MVP-1 must define the version resolution before fixing the contract.
    """


class NotInMVP0(Exception):
    """Raised when a TimelineAxis beyond the MVP-0 set is requested.

    MVP-0 supports ``supersedes_chain``/``fact_id_scope``; ``created_at``/
    ``valid_at``/``graph_bfs`` are MVP-1 (f5-08 §4.2). Fail-loud, no silent
    degradation (ADR-10 honesty).
    """

    def __init__(self, axis: str) -> None:
        self.axis = axis
        super().__init__(f"timeline axis={axis!r} is not in MVP-0")


__all__ = [
    "TOP_K",
    "MAX_TIMELINE_WINDOW",
    "MAX_FULL_BATCH",
    "SUMMARY_MAX_CHARS",
    "SUBJECT_MAX_CHARS",
    "TimelineAxis",
    "MVP0_AXES",
    "PITPoint",
    "IndexRow",
    "TimelineEntry",
    "TimelineWindow",
    "EpisodeProvenance",
    "FullDetail",
    "FullBatchTooLarge",
    "PitFullNotSupported",
    "NotInMVP0",
]