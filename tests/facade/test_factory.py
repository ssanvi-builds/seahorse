"""Tests for ``seahorse.facade.factory.build_facade`` (pre-work for #13/#14)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from seahorse.facade import build_facade
from seahorse.facade.facade import MemoryFacade
from seahorse.facade.types import RememberPayload
from seahorse.persistence.storage import Storage


def _advancing_clock(start: datetime, step: timedelta):
    state = {"t": start}

    def _now() -> datetime:
        t = state["t"]
        state["t"] = t + step
        return t

    return _now


def _agent_by() -> dict:
    return {"source_type": "agent", "agent_id": "a1", "session_id": "s1"}


class TestBuildFacade:
    def test_returns_memory_facade(self, tmp_path) -> None:
        facade, storage = build_facade(tmp_path / "f.db")
        try:
            assert isinstance(facade, MemoryFacade)
        finally:
            storage.close()

    def test_remember_then_recall_round_trip(self, tmp_path) -> None:
        facade, storage = build_facade(tmp_path / "f.db")
        try:
            r = facade.remember(
                RememberPayload(body="Sergio lives in Madrid", by=_agent_by())
            )
            assert r.status == "ACTIVE"
            assert r.ep_id is not None
            rows = facade.recall("madrid")
            assert r.ep_id in [row.ep_id for row in rows]
        finally:
            storage.close()

    def test_injected_clock_drives_engine_timestamps(self, tmp_path) -> None:
        clock = _advancing_clock(
            datetime(2026, 7, 16, 12, 0, tzinfo=UTC), timedelta(seconds=10)
        )
        facade, storage = build_facade(tmp_path / "f.db", clock=clock)
        try:
            r = facade.remember(
                RememberPayload(body="first episode", by=_agent_by())
            )
            full = facade.recall_full([r.ep_id])
            assert len(full) == 1
            # The clock's first tick (12:00:00) is the created_at.
            assert full[0].episode.created_at == datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
        finally:
            storage.close()

    def test_default_clock_is_utc_now(self, tmp_path) -> None:
        before = datetime.now(UTC)
        facade, storage = build_facade(tmp_path / "f.db")
        try:
            r = facade.remember(
                RememberPayload(body="default clock episode", by=_agent_by())
            )
            after = datetime.now(UTC)
            full = facade.recall_full([r.ep_id])
            created = full[0].episode.created_at
            assert before <= created <= after
            assert created.tzinfo is not None
        finally:
            storage.close()

    def test_reuses_existing_storage(self, tmp_path) -> None:
        storage = Storage(tmp_path / "shared.db")
        try:
            facade, storage_out = build_facade(tmp_path / "ignored.db", storage=storage)
            assert storage_out is storage
            facade.remember(RememberPayload(body="reused storage", by=_agent_by()))
            rows = facade.recall("reused")
            assert any(r.ep_id for r in rows)
        finally:
            storage.close()

    def test_config_is_propagated(self, tmp_path) -> None:
        from seahorse.facade.types import FacadeConfig

        config = FacadeConfig(default_extraction_mode="skip")
        facade, storage = build_facade(tmp_path / "f.db", config=config)
        try:
            assert facade._config is config
        finally:
            storage.close()