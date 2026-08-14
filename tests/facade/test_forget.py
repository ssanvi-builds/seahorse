"""Tests for ``MemoryFacade.forget`` — soft-delete via the engine.

``forget`` delegates to ``engine.forget`` directly. ``EngineError(
E_PENDING_CANNOT_INVALIDATE)``, ``InvalidationConflictError``, and ``NotFound``
are propagated verbatim. The primitives facade does NOT call
``write_path.ingest`` and does NOT touch ``expired_at``.
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

    def test_forwards_ep_id(self, facade, engine) -> None:
        engine.forget_result = make_episode("e1")
        facade.forget("e1", reason="stale", by=_by())
        assert engine.forget_calls[0]["ep_id"] == "e1"


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


class TestForgetLogSuppression:
    def test_log_not_emitted_on_pending_cannot_invalidate(self) -> None:
        from tests.facade.conftest import make_facade as _mf

        f, log = _mf()
        f._engine.forget_raise = EngineError(E_PENDING_CANNOT_INVALIDATE)
        with pytest.raises(EngineError):
            f.forget("e1", reason="x", by=_by())
        assert log == []

    def test_log_not_emitted_on_invalidation_conflict(self) -> None:
        from tests.facade.conftest import make_facade as _mf

        f, log = _mf()
        f._engine.forget_raise = InvalidationConflictError()
        with pytest.raises(InvalidationConflictError):
            f.forget("e1", reason="x", by=_by())
        assert log == []

    def test_log_not_emitted_on_not_found(self) -> None:
        from tests.facade.conftest import make_facade as _mf

        f, log = _mf()
        f._engine.forget_raise = NotFound("e1")
        with pytest.raises(NotFound):
            f.forget("e1", reason="x", by=_by())
        assert log == []


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

    def test_does_not_validate_source_type_enum(self, facade, engine) -> None:
        engine.forget_result = make_episode("e1")
        facade.forget("e1", reason="done", by={"source_type": "robot"})
        assert len(engine.forget_calls) == 1

    def test_validation_before_engine(self, facade, engine) -> None:
        with pytest.raises(SeahorseError):
            facade.forget("e1", reason="", by=_by())
        assert engine.forget_calls == []


class TestForgetForwardsNow:
    def test_forwards_now_to_engine(self, facade, engine) -> None:
        engine.forget_result = make_episode(
            "e1", invalid_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
        t = datetime(2026, 1, 1, tzinfo=UTC)
        facade.forget("e1", reason="stale", by=_by(), now=t)
        assert engine.forget_calls[0]["now"] == t


class TestForgetDoesNotMutateCallerBy:
    def test_forget_does_not_mutate_caller_by(self, facade, engine) -> None:
        """Guard the facade's defensive dict(by) copy: the caller's by-dict must
        not be mutated even when the engine mutates the dict it receives."""
        engine.forget_result = make_episode("e1")
        real_forget = engine.forget

        def mutating_forget(ep_id, *, reason, by, now=None):  # type: ignore[no-untyped-def]
            by["extraction_mode"] = "skip"
            by["confidence"] = 1.0
            return real_forget(ep_id, reason=reason, by=by, now=now)

        engine.forget = mutating_forget  # type: ignore[method-assign]

        caller_by = {"source_type": "agent", "agent_id": "a1"}
        snapshot = dict(caller_by)
        facade.forget("e1", reason="stale", by=caller_by)
        assert caller_by == snapshot
        assert "extraction_mode" not in caller_by
        assert "confidence" not in caller_by