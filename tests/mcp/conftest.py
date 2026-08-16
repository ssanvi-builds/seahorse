"""Shared fixtures + payload builders for the MCP server tests.

Two layers:
- **Real-stack facade** (``real_facade``): a ``MemoryFacade`` over real
  ``BiTemporalEngine`` + ``DisclosureShaperImpl`` + SQLite ``Storage`` +
  ``StubWritePath`` with an advancing clock — used by ``test_e2e_smoke`` (the
  stdio loop drives the real lifecycle). Mirrors
  ``tests/facade/test_e2e_smoke.py``.
- **Payload builders** (``make_episode`` / ``make_index_row`` /
  ``make_timeline_window`` / ``make_full_detail`` / ``make_write_result``):
  construct realistic frozen dataclass instances for the pure codec tests
  (``test_serialize`` / ``test_deserialize``) without spinning up storage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from seahorse.contracts.engine import Episode, FreshnessView, WriteResult
from seahorse.disclosure.shaper import DisclosureShaperImpl
from seahorse.disclosure.types import (
    FullDetail,
    IndexRow,
    PITPoint,
    TimelineEntry,
    TimelineWindow,
)
from seahorse.engine.engine import BiTemporalEngine
from seahorse.facade.facade import MemoryFacade
from seahorse.facade.types import FacadeConfig
from seahorse.facade.vigente_retriever import VigenteListingRetriever
from seahorse.persistence.storage import Storage
from seahorse.write_path.stub import StubWritePath

# ---------------------------------------------------------------------------
# Real-stack facade (for the in-process stdio E2E smoke).
# ---------------------------------------------------------------------------


def _advancing_clock(start: datetime, step: timedelta):
    """Clock that advances ``step`` on every read → distinct ``created_at``."""
    state = {"t": start}

    def _now() -> datetime:
        t = state["t"]
        state["t"] = t + step
        return t

    return _now


@pytest.fixture()
def real_facade(tmp_path):
    storage = Storage(tmp_path / "mcp_e2e.db")
    engine = BiTemporalEngine(repo=storage.episodes, audit=storage.audit)
    shaper = DisclosureShaperImpl(
        index_repo=storage.episode_index, episode_repo=storage.episodes
    )
    write_path = StubWritePath(engine=engine)
    clock = _advancing_clock(datetime(2026, 7, 16, 12, 0, tzinfo=UTC), timedelta(seconds=10))
    f = MemoryFacade(
        engine=engine,
        write_path=write_path,
        shaper=shaper,
        retriever=VigenteListingRetriever(engine=engine, clock=clock, config=FacadeConfig()),
        clock=clock,
        config=FacadeConfig(),
    )
    yield f
    storage.close()


def agent_by() -> dict[str, Any]:
    """The standard agent provenance used across MCP tests."""
    return {"source_type": "agent", "agent_id": "a1", "session_id": "s1"}


# ---------------------------------------------------------------------------
# Payload builders (pure, no storage).
# ---------------------------------------------------------------------------


def make_episode(
    ep_id: str = "ep-1",
    *,
    created_at: datetime | None = None,
    body: str = "Sergio lives in Madrid",
    subject: str | None = "Sergio",
    fact_id: str | None = "fact-1",
    cognitive_type: str | None = "semantic",
    source_type: str | None = "agent",
    valid_at: datetime | None = None,
    invalid_at: datetime | None = None,
    supersedes: str | None = None,
    title: str | None = "Title",
    provenance: dict[str, Any] | None = None,
) -> Episode:
    return Episode(
        id=ep_id,
        created_at=created_at or datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC),
        schema_version="1.1",
        provenance=provenance if provenance is not None else {"source_type": source_type},
        body=body,
        subject=subject,
        fact_id=fact_id,
        valid_at=valid_at,
        invalid_at=invalid_at,
        expired_at=None,
        supersedes=supersedes,
        cognitive_type=cognitive_type,
        source_type=source_type,
        title=title,
    )


def make_index_row(ep_id: str = "ep-1") -> IndexRow:
    return IndexRow(
        ep_id=ep_id,
        fact_id="fact-1",
        subject="Sergio",
        title="Title",
        summary="a summary",
        cognitive_type="semantic",
        skip_extraction=False,
        valid_at=None,
        invalid_at=None,
        created_at=datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC),
        score=0.0,
        stale=False,
        pending_ingest=False,
    )


def make_timeline_window(anchor: str = "ep-2") -> TimelineWindow:
    return TimelineWindow(
        anchor_ep_id=anchor,
        axis="supersedes_chain",
        entries=(
            TimelineEntry(
                ep_id="ep-1",
                fact_id="fact-1",
                subject="Sergio",
                title="Title",
                summary="old",
                cognitive_type="semantic",
                valid_at=None,
                invalid_at=None,
                created_at=datetime(2026, 7, 16, 11, 0, 0, tzinfo=UTC),
                supersedes=None,
            ),
            TimelineEntry(
                ep_id="ep-2",
                fact_id="fact-1",
                subject="Sergio",
                title="Title",
                summary="new",
                cognitive_type="semantic",
                valid_at=None,
                invalid_at=None,
                created_at=datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC),
                supersedes="ep-1",
            ),
        ),
        pit=None,
    )


def make_full_detail() -> FullDetail:
    from seahorse.disclosure.types import EpisodeProvenance

    return FullDetail(
        episode=make_episode(),
        provenance=EpisodeProvenance(
            agent_id="a1",
            session_id="s1",
            source_type="agent",
            extraction_mode="skip",
            model_used=None,
        ),
        freshness=FreshnessView(
            fact_id="fact-1", age_days=0, stale=False, pending_ingest=False, regime="agent"
        ),
        pit=None,
    )


def make_freshness_view() -> FreshnessView:
    return FreshnessView(
        fact_id="fact-1", age_days=3, stale=True, pending_ingest=False, regime="agent"
    )


def make_write_result(status: str = "ACTIVE") -> WriteResult:
    return WriteResult(
        ep_id="ep-1", fact_id="fact-1", status=status, collisions_detected=[]
    )


def make_pit(kind: str = "state_at") -> PITPoint:
    return PITPoint(kind=kind, t=datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC))


# ---------------------------------------------------------------------------
# RecordingFacade — records the 7 facade method calls (delegation-purity double).
#
# This double structurally enforces the MCP server's delegation invariants that
# outcome-only tests cannot catch: it asserts WHAT facade method was called,
# with WHICH kwargs, in WHICH order — and that guards fire BEFORE any read
# (call counts stay zero on the wire-shape error path). It is NOT a real
# facade; it returns configurable results so handlers can be tested in
# isolation from the engine, write path, and disclosure shaper.
# ---------------------------------------------------------------------------


class RecordingFacade:
    """Records the 7 facade method calls; returns configurable results."""

    def __init__(self) -> None:
        self.remember_calls: list[dict[str, Any]] = []
        self.recall_calls: list[dict[str, Any]] = []
        self.recall_timeline_calls: list[dict[str, Any]] = []
        self.recall_full_calls: list[dict[str, Any]] = []
        self.improve_calls: list[dict[str, Any]] = []
        self.forget_calls: list[dict[str, Any]] = []
        self.build_pit_calls: list[dict[str, Any]] = []
        self.freshness_calls: list[dict[str, Any]] = []
        self.audit_calls: list[dict[str, Any]] = []
        self.chain_calls: list[dict[str, Any]] = []
        self.get_vigente_calls: list[dict[str, Any]] = []

        # configurable returns
        self.remember_result = make_write_result()
        self.recall_result: list = []
        self.timeline_result = make_timeline_window()
        self.full_result: list = []
        self.improve_result = make_episode("ep-2", supersedes="ep-1")
        self.forget_result = make_episode(
            "ep-1", invalid_at=datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
        )
        self.build_pit_result: PITPoint | None = None
        self.build_pit_raise: Exception | None = None
        self.remember_raise: Exception | None = None
        self.recall_raise: Exception | None = None
        self.recall_full_raise: Exception | None = None
        self.freshness_result = make_freshness_view()
        self.audit_result: list = []
        self.chain_result: list = []
        self.vigente_result: list = []

    # The facade method names + signatures the MCP server calls.
    def remember(self, payload, *, skip_extraction=None, extraction_mode=None, now=None):
        self.remember_calls.append(
            {
                "payload": payload,
                "skip_extraction": skip_extraction,
                "extraction_mode": extraction_mode,
                "now": now,
            }
        )
        if self.remember_raise is not None:
            raise self.remember_raise
        return self.remember_result

    def recall(self, query, **kwargs):
        # **kwargs capture: only the keys the handler actually forwarded are
        # recorded. This structurally enforces the "only forwarded when present"
        # invariant (absent keys are ABSENT in the recording, not collapsed to
        # None) — outcome-only dicts cannot distinguish the two.
        self.recall_calls.append({"query": query, **kwargs})
        if self.recall_raise is not None:
            raise self.recall_raise
        return list(self.recall_result)

    def recall_timeline(self, anchor_ep_id, *, axis="supersedes_chain", pit=None, hops=1):
        self.recall_timeline_calls.append(
            {"anchor": anchor_ep_id, "axis": axis, "pit": pit, "hops": hops}
        )
        return self.timeline_result

    def recall_full(self, ep_ids, *, pit=None):
        self.recall_full_calls.append({"ep_ids": list(ep_ids), "pit": pit})
        if self.recall_full_raise is not None:
            raise self.recall_full_raise
        return list(self.full_result)

    def improve(self, ep_id, new_body, *, by, valid_at=None, reason="correction", now=None):
        self.improve_calls.append(
            {
                "ep_id": ep_id,
                "new_body": new_body,
                "by": dict(by),
                "valid_at": valid_at,
                "reason": reason,
                "now": now,
            }
        )
        return self.improve_result

    def forget(self, ep_id, *, reason, by, now=None):
        self.forget_calls.append(
            {"ep_id": ep_id, "reason": reason, "by": dict(by), "now": now}
        )
        return self.forget_result

    def build_pit(self, pit=None, *, pit_kind=None, t=None):
        self.build_pit_calls.append({"pit": pit, "pit_kind": pit_kind, "t": t})
        if self.build_pit_raise is not None:
            raise self.build_pit_raise
        return self.build_pit_result

    # The deferred read-only facade tools.
    def freshness_view(self, ep_id, *, now=None):
        self.freshness_calls.append({"ep_id": ep_id, "now": now})
        return self.freshness_result

    def audit_log(self, ep_id):
        self.audit_calls.append({"ep_id": ep_id})
        return list(self.audit_result)

    def follow_supersedes_chain(self, ep_id):
        self.chain_calls.append({"ep_id": ep_id})
        return list(self.chain_result)

    def get_vigente(self, subject=None, *, now=None):
        self.get_vigente_calls.append({"subject": subject, "now": now})
        return list(self.vigente_result)


__all__ = [
    "_advancing_clock",
    "real_facade",
    "agent_by",
    "make_episode",
    "make_index_row",
    "make_timeline_window",
    "make_full_detail",
    "make_write_result",
    "make_pit",
    "make_freshness_view",
    "RecordingFacade",
]