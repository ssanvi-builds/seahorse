"""Validate BiTemporalEngine.forget — the soft-delete path.

Soft-delete bi-temporal: marks ``invalid_at = now`` (once null->now), preserves
the row, never touches ``expired_at``. The invalidation metadata (``reason``,
``agent_id``) lives ONLY in the ``AuditEvent``, never in the episode row or
frontmatter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seahorse.contracts.engine import InvalidationConflictError, NotFound
from seahorse.engine import errors
from seahorse.engine.engine import BiTemporalEngine
from tests.engine.conftest import _episode

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)
FUTURE = NOW + timedelta(days=3)
LATER = NOW + timedelta(hours=1)


@pytest.fixture()
def engine(storage):
    repo, audit = storage
    return BiTemporalEngine(repo, audit), repo, audit


def _apply(eng, ep_id="e1", **kw):
    return eng.apply_fact(
        _episode(ep_id, body="# Subject\n", title=None, source_type="human", **kw), now=NOW
    )


# --- happy path --------------------------------------------------------------


def test_forget_marks_invalid_at_and_returns_episode(engine):
    eng, repo, audit = engine
    _apply(eng)
    forgotten = eng.forget("e1", reason="obsolete", by={"agent_id": "agent-X"}, now=LATER)
    assert forgotten.invalid_at == LATER
    stored = repo.get("e1")
    assert stored is not None
    assert stored.invalid_at == LATER


def test_forget_expired_at_untouched(engine):
    eng, repo, audit = engine
    _apply(eng)
    eng.forget("e1", reason="r", by={"agent_id": "a"}, now=LATER)
    assert repo.get("e1").expired_at is None  # expired_at untouched


# --- not found / already invalidated ----------------------------------------


def test_forget_raises_not_found_for_unknown_id(engine):
    eng, repo, audit = engine
    with pytest.raises(NotFound):
        eng.forget("ghost", reason="r", by={"agent_id": "a"}, now=NOW)


def test_forget_raises_invalidation_conflict_if_already_invalidated(engine):
    eng, repo, audit = engine
    _apply(eng)
    eng.forget("e1", reason="first", by={"agent_id": "a"}, now=LATER)
    with pytest.raises(InvalidationConflictError):
        eng.forget("e1", reason="second", by={"agent_id": "a"}, now=LATER)


# --- PENDING cannot be invalidated ------------------------------------------


def test_forget_pending_ingest_cannot_be_invalidated(engine):
    eng, repo, audit = engine
    _apply(eng, valid_at=FUTURE)  # PENDING_INGEST
    with pytest.raises(errors.EngineError) as exc:
        eng.forget("e1", reason="r", by={"agent_id": "a"}, now=NOW)
    assert exc.value.code == errors.E_PENDING_CANNOT_INVALIDATE
    # Nothing invalidated.
    assert repo.get("e1").invalid_at is None


# --- reason/agent live ONLY in audit ----------------------------------------


def test_forget_reason_lives_in_audit_not_in_row(engine):
    eng, repo, audit = engine
    _apply(eng)
    eng.forget("e1", reason="obsolete", by={"agent_id": "agent-X"}, now=LATER)
    events = audit.query(target_id="e1")
    forget_events = [e for e in events if e.primitive == "forget"]
    assert len(forget_events) == 1
    fe = forget_events[0]
    assert fe.result == "invalidated"
    assert fe.reason == "obsolete"
    assert fe.agent_id == "agent-X"
    assert fe.transaction_time == LATER
    # The episode dataclass carries no reason field — only invalid_at changed.
    stored = repo.get("e1")
    assert not hasattr(stored, "reason") or getattr(stored, "reason", None) is None


def test_forget_preserves_body_and_subject(engine):
    eng, repo, audit = engine
    _apply(eng)
    eng.forget("e1", reason="r", by={"agent_id": "a"}, now=LATER)
    stored = repo.get("e1")
    assert stored.body == "# Subject\n"
    assert stored.subject == "subject"  # no overwrite of content