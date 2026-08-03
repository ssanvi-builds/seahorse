"""Validate BiTemporalEngine.improve (Phase 8, owned #2).

Op 3 — human edit = invalidate-then-append atomically (I8). The old episode is
invalidated and a new one with ``supersedes=old`` is appended inside
``repo.atomic()``; if the new body's subject collides with a THIRD vigente
episode (not the target, which is already invalidated), the whole transaction
rolls back (TD #8 fail-loud: ``E_COLLISION_EXISTS``). ``improve`` preserves the
signed ``-> Episode`` return type.
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
    # old is invalidated; new is vigente.
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


# --- CC-3 (C8.9): improve successor carries the portable correction enum -------
#
# Spec memory_architecture §2.8: the successor of an improve carries
# ``supersedes_reason: "correction"`` (the portable enum, surviving export/import),
# NOT the free-text ``reason`` (which is observability-only and lives in the
# AuditEvent). Mixing the two would be type-confusion (free text -> enum). This
# pins both: the enum lands on the successor and round-trips through storage;
# the free-text reason stays in the audit and never leaks into supersedes_reason.


def test_improve_successor_carries_correction_supersedes_reason(engine):
    # CC-3: the successor of an improve is a CORRECTION — it carries the portable
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
    # CC-3 type-confusion guard: the free-text ``reason`` (observability) must NOT
    # be copied into ``supersedes_reason`` (the portable enum). Even with a custom
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


# --- TD #8: collision with a third vigente -> raise + rollback ----------------


def test_improve_collision_with_third_raises_and_rolls_back(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n")     # fact_id X (subject "madrid")
    _apply(eng, "e3", "# Python\n")     # fact_id Y (subject "python"), unrelated vigente
    with pytest.raises(errors.EngineError) as exc:
        # new body's subject "python" collides with e3 (fact_id Y), not the chain of e1.
        eng.improve("e1", "# Python\nedited\n", by={"source_type": "human"}, now=LATER)
    assert exc.value.code == errors.E_COLLISION_EXISTS
    # I8 rollback: e1 NOT invalidated, e3 untouched, no new episode appended.
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
    # exactly one vigente now (the successor).
    assert {e.id for e in repo.query_vigent()} == {new_ep.id}


def test_improve_new_body_without_heading_stores_fact_id_none(engine):
    # The successor derives subject/fact_id from the new body only (LWW regime):
    # a new body with no H1 and no title -> fact_id None. improve must still
    # derive them (not store the constructor default), which this pins.
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\nv1\n")
    new_ep = eng.improve(
        "e1", "plain text with no heading\n", by={"source_type": "human"}, now=LATER
    )
    stored = repo.get(new_ep.id)
    assert stored.subject is None
    assert stored.fact_id is None


def test_improve_collision_rollback_emits_no_audit(engine):
    # TD #8: a rolled-back improve emits NO improve AuditEvent (audit is
    # written only after the atomic block succeeds).
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n")
    _apply(eng, "e3", "# Python\n")
    with pytest.raises(errors.EngineError):
        eng.improve("e1", "# Python\nedited\n", by={"source_type": "human"}, now=LATER)
    assert all(e.primitive != "improve" for e in audit.query(target_id="e1"))
    assert all(e.primitive != "improve" for e in audit.query(target_id="e3"))