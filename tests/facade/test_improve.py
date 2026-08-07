"""Tests for ``MemoryFacade.improve`` — manual supersede edit via #2.

``improve`` delegates to #2 ``engine.improve`` directly (NOT #5 — #5 owns the
first-ingestion write-path, not the supersede edit). The effective provenance
marks the edit as a skip-path extraction (``extraction_mode='skip'``,
``model_used=None``, ``prompt_hash=None``, ``confidence=1.0``) while preserving
the caller's ``source_type``. #12 does NOT call ``write_path.ingest`` and does
NOT open ``repo.atomic()`` (#2 owns the I8 atomic invalidate-then-append).

``EngineError(E_COLLISION_EXISTS)`` is propagated verbatim — the gap from
f5-12 §5.5 is resolved in the engine (atomic rollback).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.engine.errors import E_COLLISION_EXISTS, EngineError
from seahorse.facade.errors import E_EMPTY_BODY, E_MISSING_SOURCE_TYPE, SeahorseError
from tests.facade.conftest import make_episode


def _by() -> dict:
    return {"source_type": "human", "agent_id": "a1"}


class TestImproveDelegation:
    def test_calls_engine_improve_once(self, facade, engine) -> None:
        engine.improve_result = make_episode("e2", supersedes="e1")
        facade.improve("e1", "new body", by=_by())
        assert len(engine.improve_calls) == 1

    def test_does_not_call_write_path(self, facade, write_path, engine) -> None:
        engine.improve_result = make_episode("e2")
        facade.improve("e1", "new body", by=_by())
        assert write_path.ingest_calls == []

    def test_returns_episode_verbatim(self, facade, engine) -> None:
        new_ep = make_episode("e2", supersedes="e1")
        engine.improve_result = new_ep
        assert facade.improve("e1", "new body", by=_by()) is new_ep

    def test_forwards_ep_id_and_new_body(self, facade, engine) -> None:
        engine.improve_result = make_episode("e2")
        facade.improve("e1", "new body", by=_by())
        call = engine.improve_calls[0]
        assert call["ep_id"] == "e1"
        assert call["new_body"] == "new body"


class TestImproveEffectiveProvenance:
    def test_sets_four_skip_keys(self, facade, engine) -> None:
        engine.improve_result = make_episode("e2")
        facade.improve("e1", "new body", by=_by())
        by = engine.improve_calls[0]["by"]
        assert by["extraction_mode"] == "skip"
        assert by["model_used"] is None
        assert by["prompt_hash"] is None
        assert by["confidence"] == 1.0

    def test_preserves_caller_source_type(self, facade, engine) -> None:
        engine.improve_result = make_episode("e2")
        facade.improve("e1", "new body", by={"source_type": "agent", "agent_id": "x"})
        assert engine.improve_calls[0]["by"]["source_type"] == "agent"

    def test_preserves_caller_agent_id(self, facade, engine) -> None:
        engine.improve_result = make_episode("e2")
        facade.improve("e1", "new body", by={"source_type": "human", "agent_id": "a9"})
        assert engine.improve_calls[0]["by"]["agent_id"] == "a9"

    def test_forwards_reason(self, facade, engine) -> None:
        engine.improve_result = make_episode("e2")
        facade.improve("e1", "new body", by=_by(), reason="typo")
        assert engine.improve_calls[0]["reason"] == "typo"

    def test_forwards_valid_at(self, facade, engine) -> None:
        engine.improve_result = make_episode("e2")
        t = datetime(2026, 1, 1, tzinfo=UTC)
        facade.improve("e1", "new body", by=_by(), valid_at=t)
        assert engine.improve_calls[0]["valid_at"] == t

    def test_overrides_caller_supplied_skip_keys(self, facade, engine) -> None:
        engine.improve_result = make_episode("e2")
        caller_by = {
            "source_type": "human",
            "agent_id": "a1",
            "extraction_mode": "llm",
            "model_used": "gpt-4",
            "prompt_hash": "abc",
            "confidence": 0.3,
        }
        facade.improve("e1", "new body", by=caller_by)
        by = engine.improve_calls[0]["by"]
        assert by["extraction_mode"] == "skip"
        assert by["model_used"] is None
        assert by["prompt_hash"] is None
        assert by["confidence"] == 1.0
        # caller-supplied non-skip keys are still preserved
        assert by["source_type"] == "human"
        assert by["agent_id"] == "a1"


class TestImproveCollisionPropagation:
    def test_collision_exists_propagated_verbatim(self, facade, engine) -> None:
        # f5-12 §5.5 gap is resolved in the engine: E_COLLISION_EXISTS + rollback.
        # #12 does NOT catch it.
        engine.improve_raise = EngineError(E_COLLISION_EXISTS, collisions=["c1"])
        with pytest.raises(EngineError) as exc:
            facade.improve("e1", "new body", by=_by())
        assert exc.value.code == E_COLLISION_EXISTS

    def test_log_not_emitted_on_collision(self, facade, engine) -> None:
        from tests.facade.conftest import make_facade as _mf

        f, log = _mf()
        f._engine.improve_raise = EngineError(E_COLLISION_EXISTS, collisions=["c1"])
        with pytest.raises(EngineError):
            f.improve("e1", "new body", by=_by())
        assert log == []


class TestImproveLog:
    def test_emits_improve_updated(self, engine) -> None:
        from tests.facade.conftest import make_facade as _mf

        f, log = _mf()
        f._engine.improve_result = make_episode("e2")
        f.improve("e1", "new body", by=_by())
        assert log == [("improve", "updated")]


class TestImproveIndexesSuccessor:
    """F7 experiment enabler — improve must make the successor retrievable.

    f5-16 §4.6: ``knowledge_update_accuracy`` = fraction where the new version
    post-``improve`` is in top-k. In the hybrid regime the new version is only
    retrievable if the composition root indexes it — a pure ``engine.improve``
    write leaves it in the ``episodes`` table but not in vec0/FTS. The facade
    exposes an optional ``on_episode_improved`` callback (dependency injection —
    the facade never knows the indexer); the composition root wires it.
    """

    def test_callback_fires_with_successor_id(self, engine) -> None:
        from tests.facade.conftest import make_facade as _mf

        fired: list[str] = []
        f, _log = _mf(on_episode_improved=fired.append)
        f._engine.improve_result = make_episode("e2")
        f.improve("e1", "new body", by=_by())
        assert fired == ["e2"]

    def test_callback_not_fired_without_hook(self, facade, engine) -> None:
        engine.improve_result = make_episode("e2")
        facade.improve("e1", "new body", by=_by())  # default hook=None → no-op

    def test_callback_not_fired_on_collision(self, engine) -> None:
        from tests.facade.conftest import make_facade as _mf

        fired: list[str] = []
        f, _log = _mf(on_episode_improved=fired.append)
        f._engine.improve_raise = EngineError(E_COLLISION_EXISTS, collisions=["c1"])
        with pytest.raises(EngineError):
            f.improve("e1", "new body", by=_by())
        assert fired == []  # the callback fires only on a successful supersede


class TestImproveBoundaryValidation:
    def test_empty_new_body(self, facade) -> None:
        with pytest.raises(SeahorseError) as exc:
            facade.improve("e1", "  ", by=_by())
        assert exc.value.code == E_EMPTY_BODY

    def test_empty_ep_id(self, facade) -> None:
        with pytest.raises(SeahorseError) as exc:
            facade.improve("", "new body", by=_by())
        assert exc.value.code == E_EMPTY_BODY

    def test_missing_source_type(self, facade) -> None:
        with pytest.raises(SeahorseError) as exc:
            facade.improve("e1", "new body", by={})
        assert exc.value.code == E_MISSING_SOURCE_TYPE

    def test_validation_before_engine(self, facade, engine) -> None:
        with pytest.raises(SeahorseError):
            facade.improve("e1", "", by=_by())
        assert engine.improve_calls == []

    def test_does_not_validate_source_type_enum(self, facade, engine) -> None:
        engine.improve_result = make_episode("e2")
        facade.improve("e1", "new body", by={"source_type": "robot"})
        assert len(engine.improve_calls) == 1


class TestImproveDoesNotValidateValidAt:
    def test_far_future_valid_at_passed_through(self, facade, engine) -> None:
        # The valid_at guard is #2-owned. #12 must NOT reject a far-future
        # value at the border — it forwards it untouched to engine.improve.
        engine.improve_result = make_episode("e2")
        t = datetime(9999, 1, 1, tzinfo=UTC)
        facade.improve("e1", "new body", by=_by(), valid_at=t)
        assert engine.improve_calls[0]["valid_at"] == t

    def test_none_valid_at_passed_through(self, facade, engine) -> None:
        engine.improve_result = make_episode("e2")
        facade.improve("e1", "new body", by=_by(), valid_at=None)
        assert engine.improve_calls[0]["valid_at"] is None


class TestImproveForwardsNow:
    def test_forwards_now_to_engine(self, facade, engine) -> None:
        engine.improve_result = make_episode("e2")
        t = datetime(2026, 1, 1, tzinfo=UTC)
        facade.improve("e1", "new body", by=_by(), now=t)
        assert engine.improve_calls[0]["now"] == t

    def test_clock_used_when_now_none(self, facade, engine) -> None:
        engine.improve_result = make_episode("e2")
        facade.improve("e1", "new body", by=_by())
        assert engine.improve_calls[0]["now"] == datetime(2026, 7, 16, 12, 0, tzinfo=UTC)