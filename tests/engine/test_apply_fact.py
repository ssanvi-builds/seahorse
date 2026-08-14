"""Validate BiTemporalEngine.apply_fact — the engine's critical path.

Fail-loud on collision: no append, WriteResult ep_id/fact_id = None,
status="COLLISION". WriteResult separates ep_id (UUID) from fact_id
(SHA-256(subject)[:32]). WriteResult.fact_id == stored fact_id by
construction (the bridge to the benchmark harness). PENDING_INGEST when
valid_at > now, else ACTIVE.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seahorse.engine import errors
from seahorse.engine.collision import fact_id_for
from seahorse.engine.engine import BiTemporalEngine
from tests.engine.conftest import _episode

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)
FUTURE = NOW + timedelta(days=5)
PAST = NOW - timedelta(days=5)


@pytest.fixture()
def engine(storage):
    repo, audit = storage
    return BiTemporalEngine(repo, audit), repo, audit


# --- clean append -----------------------------------------------------------


def test_apply_fact_clean_returns_active(engine):
    eng, repo, audit = engine
    wr = eng.apply_fact(
        _episode("e1", body="# Sergio lives in Madrid\n", source_type="human"), now=NOW
    )
    assert wr.status == "ACTIVE"
    assert wr.ep_id == "e1"
    assert wr.fact_id is not None
    assert wr.collisions_detected == []
    assert repo.get("e1") is not None


def test_apply_fact_pending_when_valid_at_in_future(engine):
    eng, repo, audit = engine
    wr = eng.apply_fact(
        _episode("e1", body="# Future fact\n", source_type="human", valid_at=FUTURE),
        now=NOW,
    )
    assert wr.status == "PENDING_INGEST"


def test_apply_fact_sets_created_at_to_now_and_expired_at_none(engine):
    eng, repo, audit = engine
    eng.apply_fact(_episode("e1", body="# X\n", source_type="human"), now=NOW)
    stored = repo.get("e1")
    assert stored is not None
    assert stored.created_at == NOW  # created_at is engine-owned
    assert stored.expired_at is None  # expired_at stays None in the current release


def test_apply_fact_subject_and_fact_id_derived_and_stored(engine):
    eng, repo, audit = engine
    eng.apply_fact(
        _episode("e1", body="# Madrid\ncontent", title=None, source_type="human"), now=NOW
    )
    stored = repo.get("e1")
    assert stored is not None
    assert stored.subject == "madrid"
    assert stored.fact_id == fact_id_for("# Madrid\ncontent", title=None)


# --- ep_id != fact_id -------------------------------------------------------


def test_apply_fact_ep_id_distinct_from_fact_id(engine):
    eng, repo, audit = engine
    wr = eng.apply_fact(_episode("e1", body="# X\n", source_type="human"), now=NOW)
    assert wr.ep_id != wr.fact_id  # UUID vs subject hash


# --- bridge equality --------------------------------------------------------


def test_apply_fact_bridge_equality_write_result_equals_stored_fact_id(engine):
    eng, repo, audit = engine
    wr = eng.apply_fact(
        _episode("e1", body="# Bridge subject\n", title=None, source_type="human"), now=NOW
    )
    stored = repo.get("e1")
    assert stored is not None
    # WriteResult.fact_id == IndexRow.fact_id (storage-derived) by construction.
    assert wr.fact_id == stored.fact_id == fact_id_for("# Bridge subject\n", title=None)


# --- fail-loud on collision -------------------------------------------------


def test_apply_fact_fail_loud_no_append_on_collision(engine):
    eng, repo, audit = engine
    first = eng.apply_fact(
        _episode("e1", body="# Same subject\n", source_type="human"), now=NOW
    )
    assert first.status == "ACTIVE"
    second = eng.apply_fact(
        _episode("e2", body="# Same subject\n", source_type="human"), now=NOW
    )
    assert second.status == "COLLISION"
    assert second.ep_id is None
    assert second.fact_id is None
    assert len(second.collisions_detected) == 1
    assert second.collisions_detected[0].existing_id == "e1"
    # The candidate was NOT appended; storage still has only e1.
    assert repo.get("e1") is not None
    assert repo.get("e2") is None


def test_apply_fact_collision_does_not_relax_unique_index(engine):
    eng, repo, audit = engine
    # Two failed collisions still leave exactly one current-state row (the first).
    eng.apply_fact(_episode("e1", body="# Topic\n", source_type="human"), now=NOW)
    eng.apply_fact(_episode("e2", body="# Topic\n", source_type="human"), now=NOW)
    eng.apply_fact(_episode("e3", body="# Topic\n", source_type="human"), now=NOW)
    vigent = repo.query_vigent()
    assert {e.id for e in vigent} == {"e1"}


def test_apply_fact_collision_emits_no_audit(engine):
    # A rejected candidate emits NO apply AuditEvent.
    eng, repo, audit = engine
    eng.apply_fact(_episode("e1", body="# Same\n", source_type="human"), now=NOW)
    eng.apply_fact(_episode("e2", body="# Same\n", source_type="human"), now=NOW)  # COLLISION
    assert audit.query(target_id="e2") == []


def test_apply_fact_force_sets_invalid_at_none_at_ingest(engine):
    # invalid_at is written null at ingest. A direct apply_fact call with a
    # candidate that already carries invalid_at must NOT bypass the lifecycle —
    # the stored row has invalid_at None.
    eng, repo, audit = engine
    eng.apply_fact(
        _episode("e1", body="# Madrid\n", source_type="human", invalid_at=NOW),
        now=NOW,
    )
    stored = repo.get("e1")
    assert stored is not None
    assert stored.invalid_at is None


# --- audit ------------------------------------------------------------------


def test_apply_fact_emits_audit_event(engine):
    eng, repo, audit = engine
    wr = eng.apply_fact(_episode("e1", body="# Audited\n", source_type="human"), now=NOW)
    events = audit.query(target_id=wr.ep_id)
    assert len(events) == 1
    assert events[0].primitive == "apply"
    assert events[0].result == "added"
    assert events[0].transaction_time == NOW


# --- guard rejection propagates --------------------------------------------


def test_apply_fact_guard_rejects_agent_custom_valid_at(engine):
    eng, repo, audit = engine
    with pytest.raises(errors.EngineError) as exc:
        eng.apply_fact(
            _episode("e1", body="# X\n", source_type="agent", valid_at=PAST),
            now=NOW,
        )
    assert exc.value.code == errors.E_VALID_AT_HUMAN_ONLY
    assert repo.get("e1") is None  # nothing appended