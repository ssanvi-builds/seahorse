"""Stubs for primitives deferred to a later release.

These primitives are SIGNED in the timeline (the accessors are part of the
``BiTemporalEngine`` surface) but revisable until a later release. In the
current release they fail loud with ``EngineError("E_NOT_IN_MVP_0")`` rather
than over-claiming a behavior that depends on a deeper conflict policy
(fail-loud honesty). The ``mvp1_axis`` marker keeps them visible to the runner
without gating the current-release green suite.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.engine import errors
from seahorse.engine.engine import BiTemporalEngine
from seahorse.engine.policy import DefaultConflictPolicyMVP1
from tests.engine.conftest import _episode

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)

pytestmark = pytest.mark.mvp1_axis


@pytest.fixture()
def engine(storage):
    repo, audit = storage
    return BiTemporalEngine(repo, audit)


# --- deferred primitive stubs ----------------------------------------------


def test_state_at_raises_not_in_mvp_0(engine):
    with pytest.raises(errors.EngineError) as exc:
        engine.state_at(NOW)
    assert exc.value.code == errors.E_NOT_IN_MVP_0


def test_recall_pit_raises_not_in_mvp_0(engine):
    with pytest.raises(errors.EngineError) as exc:
        engine.recall_pit("e1", NOW)
    assert exc.value.code == errors.E_NOT_IN_MVP_0


def test_detect_collisions_public_raises_not_in_mvp_0(engine):
    candidate = _episode("e1", body="# Madrid\n")
    with pytest.raises(errors.EngineError) as exc:
        engine.detect_collisions(candidate)
    assert exc.value.code == errors.E_NOT_IN_MVP_0


def test_resolve_conflict_raises_not_in_mvp_0(engine):
    with pytest.raises(errors.EngineError) as exc:
        engine.resolve_conflict(collision=object())
    assert exc.value.code == errors.E_NOT_IN_MVP_0


def test_revalidate_raises_not_in_mvp_0(engine):
    with pytest.raises(errors.EngineError) as exc:
        engine.revalidate("e1", by={"agent_id": "a"})
    assert exc.value.code == errors.E_NOT_IN_MVP_0


def test_expire_raises_not_in_mvp_0(engine):
    with pytest.raises(errors.EngineError) as exc:
        engine.expire("e1")
    assert exc.value.code == errors.E_NOT_IN_MVP_0


# --- deferred conflict policy --------------------------------------------


def test_default_conflict_policy_mvp1_resolve_raises_not_in_mvp_0():
    policy = DefaultConflictPolicyMVP1()
    with pytest.raises(errors.EngineError) as exc:
        policy.resolve(object())
    assert exc.value.code == errors.E_NOT_IN_MVP_0