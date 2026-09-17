"""Validate BiTemporalEngine.improve — the human-edit path.

Human edit = invalidate-then-append atomically. The old episode is invalidated
and a new one with ``supersedes=old`` is appended inside ``repo.atomic()``; if
the new body's subject collides with a THIRD current-state episode (not the
target, which is already invalidated), the whole transaction rolls back
fail-loud with ``E_COLLISION_EXISTS``. ``improve`` preserves the signed
``-> Episode`` return type.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seahorse.contracts.engine import InvalidationConflictError, NotFound
from seahorse.engine import errors
from seahorse.engine.engine import BiTemporalEngine
from seahorse.frontmatter.schema import SupersedesReason
from tests.engine.conftest import _episode

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=2)
FUTURE = NOW + timedelta(days=2)


@pytest.fixture()
def engine(storage):
    repo, audit = storage
    return BiTemporalEngine(repo, audit), repo, audit


def _apply(eng, ep_id, body, **kw):
    return eng.apply_fact(
        _episode(ep_id, body=body, title=None, source_type="human", **kw), now=NOW
    )


# --- happy path --------------------------------------------------------------


def test_improve_invalidates_old_appends_new_with_supersedes(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\noriginal\n")
    new_ep = eng.improve("e1", "# Madrid\nupdated\n", by={"source_type": "human"}, now=LATER)
    assert new_ep.supersedes == "e1"
    assert new_ep.invalid_at is None
    assert new_ep.valid_at == LATER  # valid_at or now
    # old is invalidated; new is current-state.
    assert repo.get("e1").invalid_at == LATER
    assert repo.get(new_ep.id).invalid_at is None


def test_improve_preserves_schema_and_cognitive_type(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n", cognitive_type="fact")
    new_ep = eng.improve("e1", "# Madrid\nv2\n", by={"source_type": "human"}, now=LATER)
    assert new_ep.schema_version == repo.get("e1").schema_version
    assert new_ep.cognitive_type == "fact"


def test_improve_audit_has_successor_id(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n")
    new_ep = eng.improve(
        "e1", "# Madrid\nv2\n", by={"source_type": "human"}, reason="correction", now=LATER
    )
    improve_events = [e for e in audit.query(target_id="e1") if e.primitive == "improve"]
    assert len(improve_events) == 1
    ev = improve_events[0]
    assert ev.successor_id == new_ep.id
    assert ev.result == "updated"
    assert ev.reason == "correction"
    assert ev.transaction_time == LATER


# --- improve successor carries the portable correction enum -------------------
#
# The successor of an improve carries ``supersedes_reason: "correction"`` (the
# portable enum, surviving export/import), NOT the free-text ``reason`` (which
# is observability-only and lives in the AuditEvent). Mixing the two would be
# type-confusion (free text -> enum). This pins both: the enum lands on the
# successor and round-trips through storage; the free-text reason stays in the
# audit and never leaks into supersedes_reason.


def test_improve_successor_carries_correction_supersedes_reason(engine):
    # The successor of an improve is a CORRECTION — it carries the portable
    # ``SupersedesReason.CORRECTION`` enum, which round-trips through storage
    # (migration 009) so it survives export/import. The default-``reason`` path
    # (no explicit reason) still stamps the enum.
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n")
    new_ep = eng.improve("e1", "# Madrid\nv2\n", by={"source_type": "human"}, now=LATER)
    assert new_ep.supersedes_reason == SupersedesReason.CORRECTION
    # round-trips through storage (not just the in-memory return value)
    assert repo.get(new_ep.id).supersedes_reason == SupersedesReason.CORRECTION


def test_improve_free_text_reason_does_not_leak_into_supersedes_reason(engine):
    # Type-confusion guard: the free-text ``reason`` (observability) must NOT be
    # copied into ``supersedes_reason`` (the portable enum). Even with a custom
    # human reason, the successor carries the enum "correction", not the free text.
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n")
    new_ep = eng.improve(
        "e1",
        "# Madrid\nv2\n",
        by={"source_type": "human"},
        reason="fixed a typo in the second paragraph",
        now=LATER,
    )
    assert new_ep.supersedes_reason == SupersedesReason.CORRECTION  # enum, not free text
    assert repo.get(new_ep.id).supersedes_reason == SupersedesReason.CORRECTION
    # the free-text reason is traced in the AuditEvent (observability channel), not
    # in the episode's portable supersedes_reason.
    ev = next(e for e in audit.query(target_id="e1") if e.primitive == "improve")
    assert ev.reason == "fixed a typo in the second paragraph"


# --- not found / state guards on the target --------------------------------


def test_improve_not_found(engine):
    eng, repo, audit = engine
    with pytest.raises(NotFound):
        eng.improve("ghost", "# X\n", by={"source_type": "human"}, now=NOW)


def test_improve_already_invalidated_target_raises(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n")
    eng.forget("e1", reason="r", by={"agent_id": "a"}, now=LATER)
    with pytest.raises(InvalidationConflictError):
        eng.improve("e1", "# Madrid\nv2\n", by={"source_type": "human"}, now=LATER)


def test_improve_pending_target_cannot_be_edited(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n", valid_at=FUTURE)  # PENDING_INGEST
    with pytest.raises(errors.EngineError) as exc:
        eng.improve("e1", "# Madrid\nv2\n", by={"source_type": "human"}, now=NOW)
    assert exc.value.code == errors.E_PENDING_CANNOT_INVALIDATE
    # target untouched.
    assert repo.get("e1").invalid_at is None


# --- collision with a third current-state episode -> raise + rollback ----------


def test_improve_collision_with_third_raises_and_rolls_back(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n")     # fact_id X (subject "madrid")
    _apply(eng, "e3", "# Python\n")     # fact_id Y (subject "python"), unrelated current-state
    with pytest.raises(errors.EngineError) as exc:
        # new body's subject "python" collides with e3 (fact_id Y), not the chain of e1.
        eng.improve("e1", "# Python\nedited\n", by={"source_type": "human"}, now=LATER)
    assert exc.value.code == errors.E_COLLISION_EXISTS
    # Rollback: e1 NOT invalidated, e3 untouched, no new episode appended.
    assert repo.get("e1").invalid_at is None
    assert repo.get("e3").invalid_at is None
    assert {e.id for e in repo.query_vigent()} == {"e1", "e3"}


def test_improve_same_subject_no_collision(engine):
    # Editing the body while keeping the same subject is NOT a collision:
    # the target is invalidated first, so find_vigent returns None.
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\nv1\n")
    new_ep = eng.improve("e1", "# Madrid\nv2\n", by={"source_type": "human"}, now=LATER)
    assert repo.get("e1").invalid_at == LATER
    assert repo.get(new_ep.id).invalid_at is None
    # exactly one current-state now (the successor).
    assert {e.id for e in repo.query_vigent()} == {new_ep.id}


def test_improve_new_body_without_heading_inherits_old_subject(engine):
    # Identity inheritance (design review post-v1.0, decision 2 option C): when
    # the new body derives no subject (no H1, no title), the successor inherits
    # the old episode's subject and fact_id instead of silently leaving the
    # subject un-keyed (which used to break the consolidated-note regime: the
    # successor vanished from _is_consolidated matching and from the vault).
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\nv1\n")
    new_ep = eng.improve(
        "e1", "plain text with no heading\n", by={"source_type": "human"}, now=LATER
    )
    stored = repo.get(new_ep.id)
    assert stored.subject == repo.get("e1").subject
    assert stored.fact_id == repo.get("e1").fact_id


def test_improve_new_body_without_heading_and_no_old_subject_stores_none(engine):
    # Nothing to inherit: when the old episode has no subject either, the
    # successor still stores subject/fact_id None (the old un-keyed contract).
    eng, repo, audit = engine
    _apply(eng, "e1", "no heading at all\n")
    assert repo.get("e1").subject is None
    new_ep = eng.improve(
        "e1", "still no heading\n", by={"source_type": "human"}, now=LATER
    )
    stored = repo.get(new_ep.id)
    assert stored.subject is None
    assert stored.fact_id is None


def test_improve_new_body_with_new_heading_rekeys_explicitly(engine):
    # An explicit new H1 is a deliberate re-key: the successor derives the NEW
    # subject (and may collide with a third episode — tested above), it does
    # NOT inherit the old subject.
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\nv1\n")
    new_ep = eng.improve("e1", "# Barcelona\nv2\n", by={"source_type": "human"}, now=LATER)
    stored = repo.get(new_ep.id)
    assert stored.subject == "barcelona"
    assert stored.fact_id is not None  # derived from the new subject, not inherited


def test_improve_collision_rollback_emits_no_audit(engine):
    # A rolled-back improve emits NO improve AuditEvent (audit is written only
    # after the atomic block succeeds).
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n")
    _apply(eng, "e3", "# Python\n")
    with pytest.raises(errors.EngineError):
        eng.improve("e1", "# Python\nedited\n", by={"source_type": "human"}, now=LATER)
    assert all(e.primitive != "improve" for e in audit.query(target_id="e1"))
    assert all(e.primitive != "improve" for e in audit.query(target_id="e3"))

# --- retroactive valid_at (VESTIGIA finding, 2026-09-17) ---------------------
#
# F3.1 defines invalid_at as "real-world time until which the fact was true".
# A retroactive valid_at on the successor asserts the fact changed on that
# date, so the old interval must close THERE, not on the correction clock:
# closing at wall clock leaves both records in force on the state axis for the
# retroactive window (state_at hands back two rows for the same fact).


MAR_1 = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
SEP_1 = datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)
SEP_10 = datetime(2026, 9, 10, 0, 0, 0, tzinfo=UTC)
SEP_17 = datetime(2026, 9, 17, 5, 26, 33, tzinfo=UTC)


def test_improve_retroactive_valid_at_closes_old_at_successor_valid_at(engine):
    # The exact VESTIGIA repro: fact in force since March, corrected on the
    # 17th with "the correction held since the 1st".
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\noriginal\n", valid_at=MAR_1)
    new_ep = eng.improve(
        "e1", "# Madrid\ncorrected\n", by={"source_type": "human"}, valid_at=SEP_1, now=SEP_17
    )
    # The old interval closes at the successor's valid_at, NOT at the wall
    # clock of the correction.
    assert repo.get("e1").invalid_at == SEP_1
    assert new_ep.valid_at == SEP_1
    # The state axis tiles: exactly one in-force record at any state time.
    assert [e.id for e in repo.query_state_at(SEP_10)] == [new_ep.id]
    before = repo.query_state_at(MAR_1 + timedelta(days=1))
    assert [e.id for e in before] == ["e1"]
    after = repo.query_state_at(SEP_17 + timedelta(days=1))
    assert [e.id for e in after] == [new_ep.id]
    # known_at is untouched: the correction is only known from its own
    # created_at on — the successor cannot leak into the past.
    known_before = [e.id for e in repo.query_known_at(SEP_10)]
    assert known_before == ["e1"]
    known_after = [e.id for e in repo.query_known_at(SEP_17 + timedelta(seconds=1))]
    assert set(known_after) == {"e1", new_ep.id}


def test_improve_retroactive_valid_at_keeps_wall_clock_as_created_at(engine):
    # The retroactive window lives on the STATE axis only; created_at stays
    # engine-owned at the correction clock.
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\noriginal\n", valid_at=MAR_1)
    new_ep = eng.improve(
        "e1", "# Madrid\ncorrected\n", by={"source_type": "human"}, valid_at=SEP_1, now=SEP_17
    )
    assert new_ep.created_at == SEP_17


def test_improve_future_valid_at_rejected_loud(engine):
    # "This fact starts being true on X" is NOT a correction — it is a
    # future-dated fact, which belongs to remember (the PENDING_INGEST
    # regime). Accepting it on improve would also close the old interval at
    # a future date: a state the current-state listing cannot represent (its
    # partial index is ``invalid_at IS NULL``) while the state predicate
    # says in-force — the two would silently disagree. Reject loud, store
    # untouched.
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\nv1\n")
    with pytest.raises(errors.EngineError) as exc:
        eng.improve(
            "e1", "# Madrid\nv2\n", by={"source_type": "human"}, valid_at=FUTURE, now=LATER
        )
    assert exc.value.code == errors.E_IMPROVE_VALID_AT_FUTURE
    # Nothing happened: target valid, no successor, no audit row.
    assert repo.get("e1").invalid_at is None
    assert {e.id for e in repo.query_vigent()} == {"e1"}
    assert all(e.primitive != "improve" for e in audit.query(target_id="e1"))


def test_improve_valid_at_before_target_valid_at_rejected_loud(engine):
    # "The replacement was true before the thing it replaced" breaks the
    # valid_at <= invalid_at invariant on the target row: reject loud, leave
    # the store untouched.
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\noriginal\n", valid_at=MAR_1)
    before = datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC)
    with pytest.raises(errors.EngineError) as exc:
        eng.improve(
            "e1", "# Madrid\ncorrected\n", by={"source_type": "human"}, valid_at=before, now=SEP_17
        )
    assert exc.value.code == errors.E_MONOTONICITY_VIOLATED
    # Nothing happened: target valid, no successor, no audit row.
    assert repo.get("e1").invalid_at is None
    assert {e.id for e in repo.query_vigent()} == {"e1"}
    assert all(e.primitive != "improve" for e in audit.query(target_id="e1"))


def test_improve_valid_at_equal_to_target_valid_at_allowed(engine):
    # Equal is coherent ("the record was wrong from the start"): the old
    # interval is empty, the successor takes over from the target's own start.
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\noriginal\n", valid_at=MAR_1)
    new_ep = eng.improve(
        "e1", "# Madrid\ncorrected\n", by={"source_type": "human"}, valid_at=MAR_1, now=SEP_17
    )
    assert repo.get("e1").invalid_at == MAR_1
    assert [e.id for e in repo.query_state_at(MAR_1 + timedelta(days=1))] == [new_ep.id]
