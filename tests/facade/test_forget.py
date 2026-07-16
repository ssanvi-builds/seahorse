"""Tests for ``MemoryFacade.forget`` — soft-delete via #2.

``forget`` delegates to #2 ``engine.forget`` directly. ``EngineError(
E_PENDING_CANNOT_INVALIDATE)``, ``InvalidationConflictError``, and ``NotFound``
are propagated verbatim. #12 does NOT call ``write_path.ingest`` and does NOT
touch ``expired_at`` (I7).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.contracts.engine import InvalidationConflictError, NotFound
from seahorse.engine.errors import E_PENDING_CANNOT_INVALIDATE, EngineError
from seahorse.facade.errors import E_EMPTY_BODY, E_MISSING_SOURCE_TYPE, SeahorseError
from tests.facade.conftest import make_episode


def _by() -> dict:
    return {"source_type": "agent", "agent_id": "a1"}


class TestForgetDelegation:
    def test_calls_engine_forget_once(self, facade, engine) -> None:
        engine.forget_result = make_episode("e1", invalid_at=datetime(2026, 1, 1, tzinfo=UTC))
        facade.forget("e1", reason="stale", by=_by())
        assert len(engine.forget_calls) == 1

    def test_does_not_call_write_path(self, facade, write_path, engine) -> None:
        engine.forget_result = make_episode("e1", invalid_at=datetime(2026, 1, 1, tzinfo=UTC))
        facade.forget("e1", reason="stale", by=_by())
        assert write_path.ingest_calls == []

    def test_returns_episode_verbatim(self, facade, engine) -> None:
        ep = make_episode("e1", invalid_at=datetime(2026, 1, 1, tzinfo=UTC))
        engine.forget_result = ep
        assert facade.forget("e1", reason="stale", by=_by()) is ep

    def test_forwards_reason_and_by(self, facade, engine) -> None:
        engine.forget_result = make_episode("e1")
        facade.forget("e1", reason="outdated", by={"source_type": "human", "agent_id": "z"})
        call = engine.forget_calls[0]
        assert call["reason"] == "outdated"
        assert call["by"]["source_type"] == "human"


class TestForgetPropagation:
    def test_pending_cannot_invalidate_propagated(self, facade, engine) -> None:
        engine.forget_raise = EngineError(E_PENDING_CANNOT_INVALIDATE)
        with pytest.raises(EngineError) as exc:
            facade.forget("e1", reason="x", by=_by())
        assert exc.value.code == E_PENDING_CANNOT_INVALIDATE

    def test_invalidation_conflict_propagated(self, facade, engine) -> None:
        engine.forget_raise = InvalidationConflictError()
        with pytest.raises(InvalidationConflictError):
            facade.forget("e1", reason="x", by=_by())

    def test_not_found_propagated(self, facade, engine) -> None:
        engine.forget_raise = NotFound("e1")
        with pytest.raises(NotFound):
            facade.forget("e1", reason="x", by=_by())


class TestForgetLog:
    def test_emits_forget_invalidated(self, engine) -> None:
        from tests.facade.conftest import make_facade as _mf

        f, log = _mf()
        f._engine.forget_result = make_episode("e1")
        f.forget("e1", reason="stale", by=_by())
        assert log == [("forget", "invalidated")]


class TestForgetBoundaryValidation:
    def test_empty_reason(self, facade) -> None:
        with pytest.raises(SeahorseError) as exc:
            facade.forget("e1", reason="  ", by=_by())
        assert exc.value.code == E_EMPTY_BODY

    def test_empty_ep_id(self, facade) -> None:
        with pytest.raises(SeahorseError) as exc:
            facade.forget("", reason="x", by=_by())
        assert exc.value.code == E_EMPTY_BODY

    def test_missing_source_type(self, facade) -> None:
        with pytest.raises(SeahorseError) as exc:
            facade.forget("e1", reason="x", by={})
        assert exc.value.code == E_MISSING_SOURCE_TYPE

    def test_validation_before_engine(self, facade, engine) -> None:
        with pytest.raises(SeahorseError):
            facade.forget("e1", reason="", by=_by())
        assert engine.forget_calls == []