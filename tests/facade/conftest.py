"""Shared fixtures + recording doubles for the primitives facade tests.

The recording doubles (``RecordingEngine`` / ``RecordingWritePath`` /
``RecordingShaper``) structurally enforce the primitives facade's delegation
invariants that outcome-only tests cannot catch (the structural-review
lesson): assert WHAT downstream method was called, with WHICH args, in WHICH
order — not just the return value. They also prove guards fire BEFORE any read
(call counts stay zero on the error path).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from seahorse.contracts.engine import (
    AuditEvent,
    Episode,
    FreshnessView,
    NotFound,
    WriteResult,
)
from seahorse.contracts.retrieval import FusedCandidate
from seahorse.disclosure.types import (
    FullBatchTooLarge,
    FullDetail,
    IndexRow,
    PitFullNotSupported,
    PITPoint,
    TimelineWindow,
)
from seahorse.engine import errors as engine_errors
from seahorse.facade.types import RememberPayload

# ---------------------------------------------------------------------------
# Episode builder (mirror of the engine test helper, kept local for isolation).
# ---------------------------------------------------------------------------


def make_episode(
    ep_id: str,
    *,
    created_at: datetime | None = None,
    body: str = "body",
    subject: str | None = "Sergio",
    fact_id: str | None = "fact-1",
    cognitive_type: str | None = None,
    source_type: str | None = "agent",
    valid_at: datetime | None = None,
    invalid_at: datetime | None = None,
    supersedes: str | None = None,
    title: str | None = "Title",
    provenance: dict[str, Any] | None = None,
) -> Episode:
    return Episode(
        id=ep_id,
        created_at=created_at if created_at is not None else datetime(2026, 1, 1, tzinfo=UTC),
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


# ---------------------------------------------------------------------------
# RecordingEngine — records every method the facade calls on the engine.
# ---------------------------------------------------------------------------


class RecordingEngine:
    """Engine double that records engine calls and returns configurable results."""

    def __init__(self) -> None:
        self.get_vigente_calls: list[dict[str, Any]] = []
        self.improve_calls: list[dict[str, Any]] = []
        self.forget_calls: list[dict[str, Any]] = []
        self.freshness_calls: list[dict[str, Any]] = []
        self.audit_calls: list[dict[str, Any]] = []
        self.chain_calls: list[dict[str, Any]] = []
        # configurable returns
        self.vigente: list[Episode] = []
        self.improve_result: Episode | None = None
        self.improve_raise: Exception | None = None
        self.forget_result: Episode | None = None
        self.forget_raise: Exception | None = None
        self.freshness_result: FreshnessView = FreshnessView(
            fact_id="f1", age_days=0, stale=False, pending_ingest=False, regime="agent"
        )
        self.audit_result: list[AuditEvent] = []
        self.chain_result: list[Episode] = []

    def get_vigente(
        self, subject: str | None = None, *, now: datetime | None = None
    ) -> list[Episode]:
        self.get_vigente_calls.append({"subject": subject, "now": now})
        return list(self.vigente)

    def improve(
        self,
        ep_id: str,
        new_body: str,
        *,
        by: dict,
        valid_at: datetime | None = None,
        reason: str = "correction",
        now: datetime | None = None,
    ) -> Episode:
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
        if self.improve_raise is not None:
            raise self.improve_raise
        assert self.improve_result is not None
        return self.improve_result

    def forget(
        self, ep_id: str, *, reason: str, by: dict, now: datetime | None = None
    ) -> Episode:
        self.forget_calls.append(
            {"ep_id": ep_id, "reason": reason, "by": dict(by), "now": now}
        )
        if self.forget_raise is not None:
            raise self.forget_raise
        assert self.forget_result is not None
        return self.forget_result

    def freshness_view(self, ep_id: str, *, now: datetime | None = None) -> FreshnessView:
        self.freshness_calls.append({"ep_id": ep_id, "now": now})
        return self.freshness_result

    def audit_log(self, ep_id: str) -> list[AuditEvent]:
        self.audit_calls.append({"ep_id": ep_id})
        return list(self.audit_result)

    def follow_supersedes_chain(self, ep_id: str) -> list[Episode]:
        self.chain_calls.append({"ep_id": ep_id})
        return list(self.chain_result)


# ---------------------------------------------------------------------------
# RecordingWritePath — records write-path ingest calls.
# ---------------------------------------------------------------------------


class RecordingWritePath:
    def __init__(self) -> None:
        self.ingest_calls: list[dict[str, Any]] = []
        self.result: WriteResult = WriteResult(
            ep_id="e1", fact_id="f1", status="ACTIVE", collisions_detected=[]
        )

    def ingest(
        self,
        payload: RememberPayload,
        extraction_mode: str,
        *,
        now: datetime | None = None,
    ) -> WriteResult:
        self.ingest_calls.append(
            {"payload": payload, "extraction_mode": extraction_mode, "now": now}
        )
        return self.result


# ---------------------------------------------------------------------------
# RecordingShaper — records the disclosure shaper's materialize_* calls.
# ---------------------------------------------------------------------------


class RecordingShaper:
    def __init__(self) -> None:
        self.index_calls: list[dict[str, Any]] = []
        self.timeline_calls: list[dict[str, Any]] = []
        self.full_calls: list[dict[str, Any]] = []
        self.index_result: list[IndexRow] = []
        self.timeline_result: TimelineWindow | None = None
        self.timeline_raise: Exception | None = None
        self.full_result: list[FullDetail] = []
        self.full_raise: Exception | None = None

    def materialize_index(
        self, candidates: Sequence[FusedCandidate], *, pit: PITPoint | None, now: datetime | None
    ) -> list[IndexRow]:
        self.index_calls.append(
            {"candidates": list(candidates), "pit": pit, "now": now}
        )
        return list(self.index_result)

    def materialize_timeline(
        self,
        anchor_ep_id: str,
        *,
        axis: Any,
        pit: PITPoint | None,
        hops: int = 1,
        now: datetime | None = None,
    ) -> TimelineWindow:
        self.timeline_calls.append(
            {"anchor": anchor_ep_id, "axis": axis, "pit": pit, "hops": hops, "now": now}
        )
        if self.timeline_raise is not None:
            raise self.timeline_raise
        assert self.timeline_result is not None
        return self.timeline_result

    def materialize_full(
        self, ep_ids: Sequence[str], *, pit: PITPoint | None, now: datetime | None
    ) -> list[FullDetail]:
        self.full_calls.append({"ep_ids": list(ep_ids), "pit": pit, "now": now})
        if self.full_raise is not None:
            raise self.full_raise
        return list(self.full_result)


# ---------------------------------------------------------------------------
# A primitive_log that records (op, result) tuples — assert the primitive was
# emitted with the right op/result.
# ---------------------------------------------------------------------------


def make_log() -> tuple[list[tuple[str, str]], Callable[[str, str], None]]:
    log: list[tuple[str, str]] = []

    def _log(op: str, result: str) -> None:
        log.append((op, result))

    return log, _log


def make_facade(
    *,
    engine: RecordingEngine | None = None,
    write_path: RecordingWritePath | None = None,
    shaper: RecordingShaper | None = None,
    retriever: object | None = None,
    clock: Callable[[], datetime] | None = None,
    on_episode_indexed: Callable[[str], None] | None = None,
):
    from seahorse.facade.facade import MemoryFacade
    from seahorse.facade.types import FacadeConfig
    from seahorse.facade.vigente_retriever import VigenteListingRetriever

    log, log_fn = make_log()
    eng = engine or RecordingEngine()
    clk = clock or (lambda: datetime(2026, 7, 16, tzinfo=UTC))
    config = FacadeConfig()
    # Recall policy is delegated to an injected Retriever. When none is
    # provided, the first-release current-state-listing impl wraps the recording
    # engine so the original behavior (engine.get_vigente driven by recall) is
    # preserved.
    ret = retriever if retriever is not None else VigenteListingRetriever(
        engine=eng, clock=clk, config=config
    )
    facade = MemoryFacade(
        engine=eng,
        write_path=write_path or RecordingWritePath(),
        shaper=shaper or RecordingShaper(),
        retriever=ret,
        clock=clk,
        config=config,
        primitive_log=log_fn,
        on_episode_indexed=on_episode_indexed,
    )
    return facade, log


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine() -> RecordingEngine:
    return RecordingEngine()


@pytest.fixture()
def write_path() -> RecordingWritePath:
    return RecordingWritePath()


@pytest.fixture()
def shaper() -> RecordingShaper:
    return RecordingShaper()


@pytest.fixture()
def clock() -> Callable[[], datetime]:
    return lambda: datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


@pytest.fixture()
def facade(engine, write_path, shaper, clock):
    f, _log = make_facade(
        engine=engine, write_path=write_path, shaper=shaper, clock=clock
    )
    return f


# Re-export doubles/symbols for downstream test modules.
__all__ = [
    "RecordingEngine",
    "RecordingWritePath",
    "RecordingShaper",
    "make_episode",
    "make_facade",
    "make_log",
    "engine_errors",
    "NotFound",
    "FullBatchTooLarge",
    "PitFullNotSupported",
]