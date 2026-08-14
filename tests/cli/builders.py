"""Builders + doubles for the CLI tests (shared with ``conftest.py``).

Kept in a plain module (not ``conftest``) so test modules can import the
builders directly via ``from tests.cli.builders import ...`` — ``conftest``
is not importable as a package module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from seahorse.contracts.engine import Episode, FreshnessView, WriteResult
from seahorse.disclosure.types import (
    EpisodeProvenance,
    FullDetail,
    IndexRow,
    PITPoint,
    TimelineEntry,
    TimelineWindow,
)

T0 = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def make_write_result(status: str = "ACTIVE") -> WriteResult:
    return WriteResult(ep_id="ep-1", fact_id="fact-1", status=status, collisions_detected=[])


def make_episode(
    ep_id: str = "ep-1",
    *,
    supersedes: str | None = None,
    invalid_at: datetime | None = None,
    body: str = "Sergio lives in Madrid",
    subject: str | None = "Sergio",
    cognitive_type: str | None = "semantic",
) -> Episode:
    return Episode(
        id=ep_id,
        created_at=T0,
        schema_version="1.1",
        provenance={"source_type": "agent"},
        body=body,
        subject=subject,
        fact_id="fact-1",
        valid_at=None,
        invalid_at=invalid_at,
        expired_at=None,
        supersedes=supersedes,
        cognitive_type=cognitive_type,
        source_type="agent",
        title=None,
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
        created_at=T0,
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
        ),
        pit=None,
    )


def make_full_detail() -> FullDetail:
    return FullDetail(
        episode=make_episode(),
        provenance=EpisodeProvenance(
            agent_id="a1", session_id="s1", source_type="agent",
            extraction_mode="skip", model_used=None,
        ),
        freshness=FreshnessView(
            fact_id="fact-1", age_days=0, stale=False, pending_ingest=False, regime="agent"
        ),
        pit=None,
    )


class RecordingFacade:
    """Records the 7 facade calls the CLI makes; returns configurable results.

    The ``recall`` kwargs are captured verbatim (``**kwargs``) so absent keys
    are ABSENT in the recording — this structurally enforces the "forwarded
    only when present" invariant that outcome-only assertions cannot.
    """

    def __init__(self) -> None:
        self.remember_calls: list[dict[str, Any]] = []
        self.recall_calls: list[dict[str, Any]] = []
        self.recall_timeline_calls: list[dict[str, Any]] = []
        self.recall_full_calls: list[dict[str, Any]] = []
        self.improve_calls: list[dict[str, Any]] = []
        self.forget_calls: list[dict[str, Any]] = []
        self.build_pit_calls: list[dict[str, Any]] = []
        self.get_vigente_calls: list[dict[str, Any]] = []

        self.remember_result = make_write_result()
        self.recall_result: list = [make_index_row()]
        self.timeline_result = make_timeline_window()
        self.full_result: list = [make_full_detail()]
        self.improve_result = make_episode("ep-2", supersedes="ep-1")
        self.forget_result = make_episode("ep-1", invalid_at=T0)
        self.build_pit_result: PITPoint | None = None
        self.build_pit_raise: Exception | None = None
        self.vigente_result: list = []

    def remember(self, payload, *, skip_extraction=None, extraction_mode=None, now=None):
        self.remember_calls.append(
            {"payload": payload, "skip_extraction": skip_extraction,
             "extraction_mode": extraction_mode, "now": now}
        )
        return self.remember_result

    def recall(self, query, **kwargs):
        self.recall_calls.append({"query": query, **kwargs})
        return list(self.recall_result)

    def recall_timeline(self, anchor_ep_id, *, axis="supersedes_chain", pit=None, hops=1):
        self.recall_timeline_calls.append(
            {"anchor": anchor_ep_id, "axis": axis, "pit": pit, "hops": hops}
        )
        return self.timeline_result

    def recall_full(self, ep_ids, *, pit=None):
        self.recall_full_calls.append({"ep_ids": list(ep_ids), "pit": pit})
        return list(self.full_result)

    def improve(self, ep_id, new_body, *, by, valid_at=None, reason="correction", now=None):
        self.improve_calls.append(
            {"ep_id": ep_id, "new_body": new_body, "by": dict(by),
             "valid_at": valid_at, "reason": reason, "now": now}
        )
        return self.improve_result

    def forget(self, ep_id, *, reason, by, now=None):
        self.forget_calls.append({"ep_id": ep_id, "reason": reason, "by": dict(by), "now": now})
        return self.forget_result

    def build_pit(self, pit=None, *, pit_kind=None, t=None):
        self.build_pit_calls.append({"pit": pit, "pit_kind": pit_kind, "t": t})
        if self.build_pit_raise is not None:
            raise self.build_pit_raise
        return self.build_pit_result

    def get_vigente(self, subject=None, *, now=None):
        self.get_vigente_calls.append({"subject": subject, "now": now})
        return list(self.vigente_result)


__all__ = [
    "T0",
    "RecordingFacade",
    "make_write_result",
    "make_episode",
    "make_index_row",
    "make_timeline_window",
    "make_full_detail",
]