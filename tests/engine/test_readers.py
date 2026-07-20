"""Validate the Bi-temporal Engine readers (Phase 9, owned #2).

Readers never mutate storage and never move frontier symbols. They project the
#6 repo Protocol into the read surfaces callers consume:

- ``get_vigente(subject=None)`` — *activo ahora*: vigente rows with
  ``valid_at IS NULL OR valid_at <= now``. Excludes ``PENDING_INGEST``.
- ``follow_supersedes_chain(ep_id)`` — bidirectional supersedes closure.
- ``is_valid_at(ep_id, t)`` / ``is_known_at(ep_id, t)`` — NULL-safe PIT
  predicates (F3.1 §15.1, l.880-892). ``None`` episode -> ``False``.
- ``audit_log(ep_id)`` — audit events whose ``target_id`` is ``ep_id``.
- ``freshness_view(ep_id)`` — pure ``FreshnessView`` derivation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seahorse.contracts.engine import FreshnessView, NotFound
from seahorse.engine.engine import BiTemporalEngine
from tests.engine.conftest import _episode

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=2)
FUTURE = NOW + timedelta(days=3)
PAST = NOW - timedelta(days=1)


@pytest.fixture()
def engine(storage):
    repo, audit = storage
    return BiTemporalEngine(repo, audit), repo, audit


def _apply(eng, ep_id, body, *, valid_at=None, source_type="human"):
    return eng.apply_fact(
        _episode(ep_id, body=body, title=None, source_type=source_type, valid_at=valid_at),
        now=NOW,
    )


# --- get_vigente (activo ahora: excludes PENDING_INGEST) --------------------


def test_get_vigente_excludes_pending_ingest(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n")                 # ACTIVE (valid_at=None)
    _apply(eng, "e2", "# Python\n", valid_at=FUTURE)  # PENDING_INGEST
    # Pass ``now=NOW`` explicitly: the engine's default clock is the wall clock,
    # so once the real date passes FUTURE the "pending" episode would correctly
    # become vigente and rot this assertion. The writes already pin ``now=NOW``.
    vigent = {e.id for e in eng.get_vigente(now=NOW)}
    assert vigent == {"e1"}


def test_get_vigente_filters_by_subject(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n")
    _apply(eng, "e2", "# Python\n")
    madrid = eng.get_vigente(subject="madrid")
    assert [e.id for e in madrid] == ["e1"]


def test_get_vigente_empty_after_invalidation(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n")
    eng.forget("e1", reason="r", by={"agent_id": "a"}, now=LATER)
    assert eng.get_vigente() == []


# --- follow_supersedes_chain (bidirectional closure) -----------------------


def test_follow_chain_is_bidirectional_after_improve(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\nv1\n")
    new_ep = eng.improve("e1", "# Madrid\nv2\n", by={"source_type": "human"}, now=LATER)
    from_old = {e.id for e in eng.follow_supersedes_chain("e1")}
    from_new = {e.id for e in eng.follow_supersedes_chain(new_ep.id)}
    assert from_old == {"e1", new_ep.id}
    assert from_new == {"e1", new_ep.id}


def test_follow_chain_single_episode(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n")
    assert {e.id for e in eng.follow_supersedes_chain("e1")} == {"e1"}


def test_follow_chain_unknown_returns_empty(engine):
    eng, repo, audit = engine
    assert eng.follow_supersedes_chain("ghost") == []


# --- is_valid_at (NULL-safe, F3.1 l.880-885) --------------------------------


def test_is_valid_at_active_episode_now(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n", valid_at=PAST)
    assert eng.is_valid_at("e1", NOW) is True


def test_is_valid_at_pending_future_is_false_now(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n", valid_at=FUTURE)
    assert eng.is_valid_at("e1", NOW) is False


def test_is_valid_at_valid_at_none_is_valid_always(engine):
    # valid_at=None means "from forever": valid at any t while not invalidated.
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n")  # valid_at=None
    assert eng.is_valid_at("e1", NOW) is True
    assert eng.is_valid_at("e1", PAST) is True


def test_is_valid_at_after_invalidation_is_false(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n", valid_at=PAST)
    eng.forget("e1", reason="r", by={"agent_id": "a"}, now=LATER)
    assert eng.is_valid_at("e1", LATER + timedelta(hours=1)) is False
    # still valid BEFORE the invalidation instant (pit < invalid_at).
    assert eng.is_valid_at("e1", PAST) is True


def test_is_valid_at_unknown_episode_is_false(engine):
    eng, repo, audit = engine
    assert eng.is_valid_at("ghost", NOW) is False


# --- is_known_at (NULL-safe, F3.1 l.887-892) -------------------------------


def test_is_known_at_after_creation(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n")  # created_at=NOW
    assert eng.is_known_at("e1", NOW + timedelta(minutes=1)) is True
    assert eng.is_known_at("e1", NOW - timedelta(minutes=1)) is False


def test_is_known_at_unknown_episode_is_false(engine):
    eng, repo, audit = engine
    assert eng.is_known_at("ghost", NOW) is False


# --- audit_log -------------------------------------------------------------


def test_audit_log_returns_events_for_target(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n")
    eng.forget("e1", reason="obsolete", by={"agent_id": "a"}, now=LATER)
    events = eng.audit_log("e1")
    primitives = [e.primitive for e in events]
    assert primitives == ["apply", "forget"]


def test_audit_log_empty_for_unknown(engine):
    eng, repo, audit = engine
    assert eng.audit_log("ghost") == []


# --- freshness_view --------------------------------------------------------


def test_freshness_view_active_episode(engine):
    eng, repo, audit = engine
    wr = _apply(eng, "e1", "# Madrid\n")  # ACTIVE
    fv = eng.freshness_view("e1", now=NOW + timedelta(days=5))
    assert isinstance(fv, FreshnessView)
    assert fv.fact_id == wr.fact_id
    assert fv.age_days == 5
    assert fv.stale is False
    assert fv.pending_ingest is False
    assert fv.regime == "human"


def test_freshness_view_stale_after_forget(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n")
    eng.forget("e1", reason="r", by={"agent_id": "a"}, now=LATER)
    fv = eng.freshness_view("e1", now=LATER + timedelta(hours=1))
    assert fv.stale is True


def test_freshness_view_pending_ingest(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n", valid_at=FUTURE)
    fv = eng.freshness_view("e1", now=NOW)
    assert fv.pending_ingest is True
    assert fv.stale is False


def test_freshness_view_unknown_raises_not_found(engine):
    eng, repo, audit = engine
    with pytest.raises(NotFound):
        eng.freshness_view("ghost", now=NOW)


def test_freshness_view_regime_falls_back_to_unknown(engine):
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n", source_type=None)
    fv = eng.freshness_view("e1", now=NOW)
    assert fv.regime == "unknown"


def test_freshness_view_fact_id_none_when_no_subject(engine):
    # An episode whose body has no H1 and no title carries fact_id=None.
    eng, repo, audit = engine
    _apply(eng, "e1", "plain text with no heading\n")
    fv = eng.freshness_view("e1", now=NOW + timedelta(days=1))
    assert fv.fact_id is None
    assert fv.age_days == 1
    assert fv.stale is False
    assert fv.regime == "human"


# --- boundaries: strict < / inclusive <= are load-bearing -------------------


def test_is_valid_at_boundary_pit_equals_invalid_at_is_false(engine):
    # F3.1 uses pit < invalid_at (strict) -> pit == invalid_at is NOT valid.
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n", valid_at=PAST)
    eng.forget("e1", reason="r", by={"agent_id": "a"}, now=LATER)
    assert eng.is_valid_at("e1", LATER) is False  # pit == invalid_at -> False
    # just before the invalidation instant it is still valid.
    assert eng.is_valid_at("e1", LATER - timedelta(microseconds=1)) is True


def test_is_known_at_boundary_pit_equals_created_at_is_true(engine):
    # F3.1 uses created_at <= pit (inclusive) -> pit == created_at is known.
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n")  # created_at == NOW
    assert eng.is_known_at("e1", NOW) is True


def test_get_vigente_boundary_valid_at_equals_now_is_included(engine):
    # get_vigente uses valid_at <= now (inclusive).
    eng, repo, audit = engine
    _apply(eng, "e1", "# Madrid\n", valid_at=NOW)  # exactly now
    assert {e.id for e in eng.get_vigente(now=NOW)} == {"e1"}
    # one microsecond in the future is still PENDING -> excluded.
    _apply(eng, "e2", "# Python\n", valid_at=NOW + timedelta(microseconds=1))
    assert {e.id for e in eng.get_vigente(now=NOW)} == {"e1"}