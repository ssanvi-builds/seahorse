"""End-to-end smoke for #12 MemoryFacade against the REAL #2 + #8 + #6 + #5.

This is not a unit test of the facade's routing (the recording doubles cover
that). It proves the wiring composes end-to-end: a single ``MemoryFacade`` over
a real ``BiTemporalEngine`` + real ``DisclosureShaperImpl`` + real SQLite
``Storage`` + real ``StubWritePath`` runs the full memory-native lifecycle and
the progressive-disclosure reads see what the writes did.

Lifecycle: remember → ACTIVE → recall shows it → improve → new Episode
(supersedes old) → recall shows new, old gone → forget(new) → freshness_view →
audit_log carries apply/improve/forget events.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seahorse.disclosure.shaper import DisclosureShaperImpl
from seahorse.engine.engine import BiTemporalEngine
from seahorse.facade.facade import MemoryFacade
from seahorse.facade.types import FacadeConfig, RememberPayload
from seahorse.persistence.storage import Storage
from seahorse.write_path.stub import StubWritePath


def _advancing_clock(start: datetime, step: timedelta):
    """A clock that advances ``step`` on every read so writes get distinct
    ``created_at`` values (deterministic, no wall-clock dependency)."""
    state = {"t": start}

    def _now() -> datetime:
        t = state["t"]
        state["t"] = t + step
        return t

    return _now


@pytest.fixture()
def facade(tmp_path):
    storage = Storage(tmp_path / "e2e.db")
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
        clock=clock,
        config=FacadeConfig(),
    )
    yield f
    storage.close()


def _agent_by() -> dict:
    return {"source_type": "agent", "agent_id": "a1", "session_id": "s1"}


class TestEndToEndLifecycle:
    def test_remember_returns_active(self, facade) -> None:
        result = facade.remember(
            RememberPayload(body="Sergio lives in Madrid", by=_agent_by())
        )
        assert result.status == "ACTIVE"
        assert result.ep_id is not None
        assert result.collisions_detected == []

    def test_full_lifecycle(self, facade) -> None:
        # 1. remember -> ACTIVE
        r1 = facade.remember(
            RememberPayload(body="Sergio lives in Madrid", by=_agent_by())
        )
        assert r1.status == "ACTIVE"
        old_id = r1.ep_id
        assert old_id is not None

        # 2. recall shows it (INDEX level — no body hydration at this level)
        rows = facade.recall("madrid")
        ids = [r.ep_id for r in rows]
        assert old_id in ids
        assert len(rows) >= 1

        # 3. improve -> new Episode superseding the old
        new_ep = facade.improve(
            old_id, "Sergio lives in Barcelona", by={"source_type": "human", "agent_id": "sergio"}
        )
        assert new_ep.id != old_id
        assert new_ep.supersedes == old_id

        # 4. recall shows the new one; the old one is no longer vigente
        rows_after = facade.recall("barcelona")
        ids_after = [r.ep_id for r in rows_after]
        assert new_ep.id in ids_after
        assert old_id not in ids_after

        # 5. forget the new episode
        forgotten = facade.forget(new_ep.id, reason="wrong", by=_agent_by())
        assert forgotten.invalid_at is not None

        # 6. recall no longer shows it
        rows_final = facade.recall("anything")
        assert new_ep.id not in [r.ep_id for r in rows_final]
        assert old_id not in [r.ep_id for r in rows_final]

        # 7. freshness_view of the forgotten episode reports stale
        fv = facade.freshness_view(new_ep.id)
        assert fv.stale is True

        # 8. audit_log of the old id carries apply + improve; the new id carries forget
        old_audit = facade.audit_log(old_id)
        primitives_old = {e.primitive for e in old_audit}
        assert "apply" in primitives_old
        assert "improve" in primitives_old
        new_audit = facade.audit_log(new_ep.id)
        primitives_new = {e.primitive for e in new_audit}
        assert "forget" in primitives_new

    def test_recall_full_hydrates_body(self, facade) -> None:
        r = facade.remember(
            RememberPayload(body="The project ships in Q4", by=_agent_by())
        )
        full = facade.recall_full([r.ep_id])
        assert len(full) == 1
        assert full[0].episode.body == "The project ships in Q4"
        assert full[0].freshness is not None

    def test_recall_timeline_follows_supersedes_chain(self, facade) -> None:
        r = facade.remember(
            RememberPayload(body="Sergio uses Python 3.12", by=_agent_by())
        )
        new_ep = facade.improve(
            r.ep_id, "Sergio uses Python 3.13", by={"source_type": "human", "agent_id": "sergio"}
        )
        window = facade.recall_timeline(new_ep.id, axis="supersedes_chain")
        chain_ids = {e.ep_id for e in window.entries}
        assert r.ep_id in chain_ids
        assert new_ep.id in chain_ids