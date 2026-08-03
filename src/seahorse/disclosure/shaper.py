"""DisclosureShaper — the #8 shaper that projects #11's fused list into levels.

#8 SHAPES; it does not fuse or rank (f5-08 §1.2). The shaper reads ONLY via the
typed repository Protocols of #6 (``EpisodeIndexRepository`` — INDEX/TIMELINE,
no body) and #2 (``EpisodeRepository`` — FULL, the only level that hydrates
``body_md``). It never calls #7 and never emits raw SQL (ADR-04).

PIT composition (f5-08 §5.3): INDEX delegates to #6's PIT accessors
(``get_rows_state_at`` / ``get_rows_known_at``) so the bi-temporal predicate
lives in one place. TIMELINE composes PIT **client-side over the rows returned
by ``chain_rows_from`` / ``find_vigent_row_by_fact_id``** by delegating the
PIT check back to #6's same accessors (no predicate drift, ADR-03 axes never
mixed). FULL PIT is NOT supported in MVP-0 (``PitFullNotSupported``); INDEX and
TIMELINE are PIT-aware by construction.

References:
- f5-08 §2 (three levels), §3 (DisclosureShaper Protocol), §5 (PIT composition)
- contracts/engine.py freshness_of (FullDetail.freshness single source of truth)
- contracts/persistence.py EpisodeIndexRepository (the typed accessor #6 owns)
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from seahorse.contracts.engine import EpisodeRepository, freshness_of
from seahorse.contracts.episode import Episode
from seahorse.contracts.index import IndexRowData
from seahorse.contracts.persistence import EpisodeIndexRepository
from seahorse.contracts.retrieval import FusedCandidate
from seahorse.disclosure.types import (
    MAX_FULL_BATCH,
    MAX_TIMELINE_WINDOW,
    MVP0_AXES,
    SUBJECT_MAX_CHARS,
    SUMMARY_MAX_CHARS,
    EpisodeProvenance,
    FullBatchTooLarge,
    FullDetail,
    IndexRow,
    NotInMVP0,
    PitFullNotSupported,
    PITPoint,
    TimelineAxis,
    TimelineEntry,
    TimelineWindow,
)


def _trunc(value: str | None, limit: int) -> str | None:
    """Deterministic truncation (ADR-10). None passes through."""
    if value is None:
        return None
    return value[:limit]


def _row_to_index_row(row: IndexRowData, score: float, now: datetime) -> IndexRow:
    return IndexRow(
        ep_id=row.ep_id,
        fact_id=row.fact_id,
        subject=_trunc(row.subject, SUBJECT_MAX_CHARS) or "",
        title=row.title,
        summary=_trunc(row.summary, SUMMARY_MAX_CHARS),
        cognitive_type=row.cognitive_type,
        skip_extraction=row.skip_extraction,
        valid_at=row.valid_at,
        invalid_at=row.invalid_at,
        created_at=row.created_at,
        score=score,
        stale=row.invalid_at is not None,
        pending_ingest=row.valid_at is not None and row.valid_at > now,
    )


def _row_to_timeline_entry(row: IndexRowData) -> TimelineEntry:
    # score is ALWAYS None in MVP-0: timeline is anchor-based, not query-based.
    return TimelineEntry(
        ep_id=row.ep_id,
        fact_id=row.fact_id,
        subject=_trunc(row.subject, SUBJECT_MAX_CHARS) or "",
        title=row.title,
        summary=_trunc(row.summary, SUMMARY_MAX_CHARS),
        cognitive_type=row.cognitive_type,
        valid_at=row.valid_at,
        invalid_at=row.invalid_at,
        created_at=row.created_at,
        supersedes=row.supersedes,
        score=None,
    )


def _provenance_of(episode: Episode) -> EpisodeProvenance:
    """Typed provenance slice. ``source_type`` from the F3.1 field; the rest
    from the provenance dict (agent_id/session_id/extraction_mode/model_used)."""
    p = episode.provenance or {}
    return EpisodeProvenance(
        agent_id=p.get("agent_id"),
        session_id=p.get("session_id"),
        source_type=episode.source_type,
        extraction_mode=p.get("extraction_mode"),
        model_used=p.get("model_used"),
    )


@runtime_checkable
class DisclosureShaper(Protocol):
    """Materializes #11's fused ranked list into index/timeline/full levels.

    #8 owns level materialization + the transition protocol; #11 owns fusion +
    ranking. Reads ONLY via #2 + #6 typed repos; never raw SQL, never #7.
    """

    def materialize_index(
        self,
        candidates: Sequence[FusedCandidate],
        *,
        pit: PITPoint | None = None,
        now: datetime | None = None,
    ) -> list[IndexRow]: ...

    def materialize_timeline(
        self, anchor_ep_id: str, *, axis: TimelineAxis, pit: PITPoint | None = None
    ) -> TimelineWindow: ...

    def materialize_full(
        self, ep_ids: Sequence[str], *, pit: PITPoint | None = None, now: datetime | None = None
    ) -> list[FullDetail]: ...


class DisclosureShaperImpl:
    """Default ``DisclosureShaper`` over #6 (index) + #2 (episode) repositories."""

    def __init__(
        self, index_repo: EpisodeIndexRepository, episode_repo: EpisodeRepository
    ) -> None:
        self._index = index_repo
        self._repo = episode_repo

    @staticmethod
    def _now(now: datetime | None) -> datetime:
        return now if now is not None else datetime.now(UTC)

    # ---------- INDEX (1st call, within #11's 250ms budget) ------------------

    def materialize_index(
        self,
        candidates: Sequence[FusedCandidate],
        *,
        pit: PITPoint | None = None,
        now: datetime | None = None,
    ) -> list[IndexRow]:
        if not candidates:
            return []
        now_dt = self._now(now)
        ep_ids = [c.ep_id for c in candidates]
        rows = self._index_rows(ep_ids, pit)
        by_id = {r.ep_id: r for r in rows}
        # Preserve #11's candidate order (ranked); do NOT reorder by ep_id.
        # Candidates whose ep_id is not in the index (or filtered out by PIT)
        # are dropped — they have no row to project.
        return [
            _row_to_index_row(by_id[c.ep_id], c.score, now_dt)
            for c in candidates
            if c.ep_id in by_id
        ]

    def _index_rows(self, ep_ids: list[str], pit: PITPoint | None) -> list[IndexRowData]:
        if pit is None:
            return self._index.get_rows(ep_ids)
        if pit.kind == "state_at":
            return self._index.get_rows_state_at(ep_ids, pit.t)
        if pit.kind == "known_at":
            return self._index.get_rows_known_at(ep_ids, pit.t)
        # ADR-03 fixes exactly two PIT kinds; the facade validates pit.kind BEFORE
        # the shaper runs, so reaching here is an invariant violation — fail loud
        # rather than silently misroute an unknown kind to known_at (mirror of
        # sqlite_episode_index._pit_predicate, which raises on the same guard).
        raise ValueError(f"pit.kind must be 'state_at' | 'known_at'; got {pit.kind!r}")

    # ---------- TIMELINE (2nd call, anchor-based, no body) -------------------

    def materialize_timeline(
        self,
        anchor_ep_id: str,
        *,
        axis: TimelineAxis,
        pit: PITPoint | None = None,
    ) -> TimelineWindow:
        if axis not in MVP0_AXES:
            raise NotInMVP0(axis)
        rows = self._timeline_rows(anchor_ep_id, axis)
        if pit is not None:
            rows = self._pit_filter(rows, pit)
        entries = tuple(
            _row_to_timeline_entry(r) for r in rows[:MAX_TIMELINE_WINDOW]
        )
        return TimelineWindow(
            anchor_ep_id=anchor_ep_id, axis=axis, entries=entries, pit=pit
        )

    def _timeline_rows(self, anchor_ep_id: str, axis: TimelineAxis) -> list[IndexRowData]:
        if axis == "supersedes_chain":
            return self._index.chain_rows_from(anchor_ep_id)
        # fact_id_scope: the currently-vigent row for the anchor's fact_id.
        anchor_rows = self._index.get_rows([anchor_ep_id])
        if not anchor_rows:
            return []
        vigent = self._index.find_vigent_row_by_fact_id(anchor_rows[0].fact_id)
        return [vigent] if vigent is not None else []

    def _pit_filter(self, rows: list[IndexRowData], pit: PITPoint) -> list[IndexRowData]:
        """Client-side PIT composition by delegating to #6's typed accessors.

        Reuses #6's PIT predicate exactly (no drift, ADR-03 axes never mixed):
        re-query the same ep_ids through ``get_rows_state_at`` / ``get_rows_known_at``
        and keep only the rows that survive. This avoids re-implementing the
        bi-temporal predicate in #8 (f5-08 §5.3).
        """
        ep_ids = [r.ep_id for r in rows]
        if pit.kind == "state_at":
            surviving = self._index.get_rows_state_at(ep_ids, pit.t)
        elif pit.kind == "known_at":
            surviving = self._index.get_rows_known_at(ep_ids, pit.t)
        else:
            # ADR-03 fixes exactly two PIT kinds; the facade validates pit.kind
            # BEFORE the shaper runs, so reaching here is an invariant violation
            # — fail loud rather than silently misroute (mirror of
            # sqlite_episode_index._pit_predicate).
            raise ValueError(
                f"pit.kind must be 'state_at' | 'known_at'; got {pit.kind!r}"
            )
        keep = {r.ep_id for r in surviving}
        # Preserve the original order (e.g. chain sorted by created_at).
        return [r for r in rows if r.ep_id in keep]

    # ---------- FULL (3rd call, the ONLY level that hydrates body) ----------

    def materialize_full(
        self,
        ep_ids: Sequence[str],
        *,
        pit: PITPoint | None = None,
        now: datetime | None = None,
    ) -> list[FullDetail]:
        if len(ep_ids) > MAX_FULL_BATCH:
            raise FullBatchTooLarge(len(ep_ids), MAX_FULL_BATCH)
        if pit is not None:
            raise PitFullNotSupported()
        now_dt = self._now(now)
        details: list[FullDetail] = []
        for ep_id in ep_ids:
            ep = self._repo.get(ep_id)
            if ep is None:
                # Missing episode: skip rather than fail the whole batch. The
                # caller can detect a short result by length.
                continue
            details.append(
                FullDetail(
                    episode=ep,
                    provenance=_provenance_of(ep),
                    freshness=freshness_of(ep, now_dt),
                    pit=None,
                )
            )
        return details


__all__ = ["DisclosureShaper", "DisclosureShaperImpl"]