"""``audit.append`` is atomic with the episode mutation.

The engine writes the audit event INSIDE the episode mutation's ``repo.atomic()``
(``apply_fact`` / ``improve`` / ``forget``). A failure between the episode commit
and the audit INSERT would leave a persisted episode with NO audit row — a torn
audit trail, breaking the write-path invariant that every mutation emits a
matching ``AuditEvent``. ``audit.append`` lives INSIDE the mutation's
``atomic()`` (the reentrant ``ConnectionManager.atomic`` nests clean —
``audit.append`` opens its own ``cm.atomic()`` which just bumps the depth
counter and reuses the outer tx), so an audit failure rolls the episode write
back too. These tests pin that by failing the audit INSERT after the episode
write and asserting the episode mutation did NOT persist.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from seahorse.engine.engine import BiTemporalEngine
from tests.engine.conftest import _episode

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=2)


@pytest.fixture()
def engine(storage):
    repo, audit = storage
    return BiTemporalEngine(repo, audit), repo, audit


def _apply(eng, ep_id, body, **kw):
    return eng.apply_fact(
        _episode(ep_id, body=body, title=None, source_type="human", **kw), now=NOW
    )


def _fail_audit(audit, monkeypatch) -> None:
    """Make ``audit.append`` raise after the episode write would commit."""

    def _boom(event) -> None:
        raise sqlite3.OperationalError("simulated audit failure")

    monkeypatch.setattr(audit, "append", _boom)


def test_apply_fact_rolls_back_episode_if_audit_fails(engine, monkeypatch) -> None:
    eng, repo, audit = engine
    _fail_audit(audit, monkeypatch)
    with pytest.raises(sqlite3.OperationalError):
        _apply(eng, "e1", "# Madrid\n")
    # The episode append AND its episode_index row rolled back with the failed
    # audit — no persisted episode without an audit row.
    assert repo.get("e1") is None
    with repo._cm.read() as w:  # noqa: SLF001 — assert the index row rolled back too
        idx = w.execute("SELECT 1 FROM episode_index WHERE ep_id = ?", ("e1",)).fetchone()
    assert idx is None


def test_improve_rolls_back_both_writes_if_audit_fails(engine, monkeypatch) -> None:
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\noriginal\n")  # establish the target, audit working
    _fail_audit(audit, monkeypatch)  # fail the audit on the improve only
    with pytest.raises(sqlite3.OperationalError):
        eng.improve("e1", "# Madrid\nupdated\n", by={"source_type": "human"}, now=LATER)
    # BOTH the new episode append AND the old invalidation rolled back — the
    # target is still current-state with its original body.
    e1 = repo.get("e1")
    assert e1 is not None
    assert e1.invalid_at is None
    assert e1.body == "# Madrid\noriginal\n"


def test_forget_rolls_back_invalidation_if_audit_fails(engine, monkeypatch) -> None:
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n")  # e1 current-state, audit working
    _fail_audit(audit, monkeypatch)  # fail the audit on the forget only
    with pytest.raises(sqlite3.OperationalError):
        eng.forget("e1", reason="wrong", by={"source_type": "human"}, now=LATER)
    # The invalidation rolled back with the failed audit — e1 still current-state.
    e1 = repo.get("e1")
    assert e1 is not None
    assert e1.invalid_at is None


def test_apply_fact_does_not_mask_audit_integrity_error_as_collision(
    engine, monkeypatch
) -> None:
    # apply_fact's ``except sqlite3.IntegrityError`` also wraps audit.append
    # (moved inside the atomic). An IntegrityError from the audit INSERT (NOT
    # the collision index) must surface — not be translated to a bogus
    # COLLISION/ACTIVE result for an episode the atomic rolled back.
    eng, repo, audit = engine

    def _boom(event) -> None:
        raise sqlite3.IntegrityError("simulated audit constraint failure")

    monkeypatch.setattr(audit, "append", _boom)
    with pytest.raises(sqlite3.IntegrityError):
        _apply(eng, "e1", "# Madrid\n")
    # the episode was rolled back with the failed audit; no persisted row.
    assert repo.get("e1") is None