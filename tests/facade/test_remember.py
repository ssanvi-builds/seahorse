"""Tests for ``MemoryFacade.remember`` — boundary validation + delegation to
the write path.

The primitives facade's ``remember`` validates shape only (body, source_type,
tags-empty, resolved extraction_mode) and delegates to the write path's
``WritePath.ingest``. It does NOT call ``engine.remember`` (the write path owns
that) and does NOT replicate the ``source_type → skip`` guard (that lives in the
write path's ``decide_path``).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.facade.errors import (
    E_EMPTY_BODY,
    E_INVALID_EXTRACTION_MODE,
    E_MISSING_SOURCE_TYPE,
    E_NOT_IN_MVP_0_1,
    SeahorseError,
)
from seahorse.facade.types import RememberPayload


def _payload(**kw) -> RememberPayload:
    base = {"source_type": "agent"}
    base.update(kw.pop("by_extra", {}))
    return RememberPayload(body=kw.pop("body", "hello world"), by=base, **kw)


class TestRememberDelegation:
    def test_delegates_to_write_path_ingest_once(self, facade, write_path) -> None:
        facade.remember(_payload())
        assert len(write_path.ingest_calls) == 1

    def test_does_not_call_engine_remember(self, facade, engine) -> None:
        # The primitives facade never reaches the engine directly for remember —
        # the write path owns the write.
        facade.remember(_payload())
        assert engine.improve_calls == []
        assert engine.forget_calls == []

    def test_returns_write_result_verbatim(self, facade, write_path) -> None:
        from seahorse.contracts.engine import WriteResult

        write_path.result = WriteResult(
            ep_id="ep-7", fact_id="fac-7", status="ACTIVE", collisions_detected=[]
        )
        assert facade.remember(_payload()).ep_id == "ep-7"

    def test_forwards_payload_verbatim(self, facade, write_path) -> None:
        p = _payload(body="hi", cognitive_type="semantic")
        facade.remember(p)
        assert write_path.ingest_calls[0]["payload"] is p  # identity — no swap

    def test_emits_primitive_log(self, write_path) -> None:
        from tests.facade.conftest import make_facade as _mf

        f, log = _mf()
        f.remember(_payload())
        assert log == [("remember", "active")]


class TestRememberResolveMode:
    def test_explicit_skip(self, facade, write_path) -> None:
        facade.remember(_payload(), extraction_mode="skip")
        assert write_path.ingest_calls[0]["extraction_mode"] == "skip"

    def test_explicit_llm(self, facade, write_path) -> None:
        facade.remember(_payload(), extraction_mode="llm")
        assert write_path.ingest_calls[0]["extraction_mode"] == "llm"

    def test_skip_extraction_true_forces_skip(self, facade, write_path) -> None:
        facade.remember(_payload(), skip_extraction=True)
        assert write_path.ingest_calls[0]["extraction_mode"] == "skip"

    def test_skip_extraction_false_forces_llm(self, facade, write_path) -> None:
        facade.remember(_payload(), skip_extraction=False)
        assert write_path.ingest_calls[0]["extraction_mode"] == "llm"

    def test_default_uses_config(self, write_path) -> None:
        from tests.facade.conftest import make_facade as _mf

        f, _log = _mf(write_path=write_path)
        # default config: default_extraction_mode="skip"
        f.remember(_payload())
        assert write_path.ingest_calls[0]["extraction_mode"] == "skip"

    def test_explicit_mode_wins_over_skip_extraction(self, facade, write_path) -> None:
        facade.remember(_payload(), skip_extraction=True, extraction_mode="llm")
        assert write_path.ingest_calls[0]["extraction_mode"] == "llm"

    def test_invalid_mode_rejected(self, facade) -> None:
        with pytest.raises(SeahorseError) as exc:
            facade.remember(_payload(), extraction_mode="llm_partial")  # type: ignore[arg-type]
        assert exc.value.code == E_INVALID_EXTRACTION_MODE

    def test_invalid_mode_before_write_path(self, facade, write_path) -> None:
        # Invalid extraction_mode raises BEFORE the write path is touched (no
        # ingest call).
        with pytest.raises(SeahorseError) as exc:
            facade.remember(_payload(), extraction_mode="llm_partial")  # type: ignore[arg-type]
        assert exc.value.code == E_INVALID_EXTRACTION_MODE
        assert write_path.ingest_calls == []

    def test_consolidated_schema_valid_but_not_routable(self, facade, write_path) -> None:
        # ``consolidated`` is schema-valid (wire round-trips it) but NOT routable
        # by single-episode ``remember`` — the batch distillation writes via
        # ``engine.remember`` directly, bypassing ``decide_path``. The facade
        # refuses loud (fail-loud honesty) BEFORE touching the write path, so
        # the write path never receives a mode it cannot honor.
        with pytest.raises(SeahorseError) as exc:
            facade.remember(_payload(), extraction_mode="consolidated")
        assert exc.value.code == E_INVALID_EXTRACTION_MODE
        assert write_path.ingest_calls == []


class TestRememberBoundaryValidation:
    def test_empty_body_rejected(self, facade) -> None:
        with pytest.raises(SeahorseError) as exc:
            facade.remember(_payload(body="   "))
        assert exc.value.code == E_EMPTY_BODY

    def test_missing_source_type_rejected(self, facade) -> None:
        p = RememberPayload(body="hello", by={})
        with pytest.raises(SeahorseError) as exc:
            facade.remember(p)
        assert exc.value.code == E_MISSING_SOURCE_TYPE

    def test_tags_nonempty_rejected_mvp0(self, facade) -> None:
        p = RememberPayload(body="hello", by={"source_type": "agent"}, tags=("a", "b"))
        with pytest.raises(SeahorseError) as exc:
            facade.remember(p)
        assert exc.value.code == E_NOT_IN_MVP_0_1

    def test_validation_fires_before_write_path(self, facade, write_path) -> None:
        # Empty body raises BEFORE the write path is touched (no ingest call).
        with pytest.raises(SeahorseError):
            facade.remember(_payload(body=""))
        assert write_path.ingest_calls == []

    def test_does_not_validate_cognitive_type_enum(self, facade, write_path) -> None:
        # The primitives facade does NOT enforce COGNITIVE_TYPES — the engine is
        # the authority. 'fact' is outside the canonical enum but passes the
        # facade (engine tests use it).
        facade.remember(_payload(cognitive_type="fact"))
        assert len(write_path.ingest_calls) == 1

    def test_does_not_validate_source_type_enum(self, facade, write_path) -> None:
        # The primitives facade does NOT enforce SOURCE_TYPES — the engine is
        # the authority. 'robot' is outside the SOURCE_TYPES vocabulary but
        # passes the facade (presence-only check, not membership).
        p = RememberPayload(body="hi", by={"source_type": "robot"})
        facade.remember(p)
        assert len(write_path.ingest_calls) == 1


class TestRememberForwardsNow:
    def test_explicit_now_forwarded(self, facade, write_path) -> None:
        t = datetime(2026, 1, 1, tzinfo=UTC)
        facade.remember(_payload(), now=t)
        assert write_path.ingest_calls[0]["now"] == t

    def test_clock_used_when_now_none(self, write_path) -> None:
        from tests.facade.conftest import make_facade as _mf

        fixed = datetime(2025, 12, 31, tzinfo=UTC)
        f, _log = _mf(write_path=write_path, clock=lambda: fixed)
        f.remember(_payload())
        assert write_path.ingest_calls[0]["now"] == fixed


class TestRememberDoesNotReplicateGuard:
    def test_importer_llm_not_forced_skip_by_facade(self, facade, write_path) -> None:
        # The source_type→skip guard lives in the write path's decide_path, NOT
        # in the primitives facade. The primitives facade passes 'llm' through;
        # the write path would then degrade. Here we only assert the primitives
        # facade does not rewrite the mode.
        p = RememberPayload(body="hi", by={"source_type": "importer"})
        facade.remember(p, extraction_mode="llm")
        assert write_path.ingest_calls[0]["extraction_mode"] == "llm"

    def test_remember_does_not_add_effective_provenance_to_payload_by(
        self, facade, write_path
    ) -> None:
        # remember forwards the caller's by untouched — it does NOT build
        # effective provenance (the write path's run_skip_path owns that).
        # improve is the only primitive that injects
        # extraction_mode/model_used/prompt_hash/confidence.
        by = {"source_type": "agent", "agent_id": "a1"}
        p = RememberPayload(body="hello world", by=by)
        facade.remember(p)
        forwarded_by = write_path.ingest_calls[0]["payload"].by
        # The exact same dict object is forwarded (no copy-with-extras).
        assert forwarded_by is by
        # No effective-provenance keys injected.
        assert set(forwarded_by.keys()) == {"source_type", "agent_id"}
        assert "extraction_mode" not in forwarded_by
        assert "model_used" not in forwarded_by
        assert "prompt_hash" not in forwarded_by
        assert "confidence" not in forwarded_by


class TestRememberDoesNotValidateValidAt:
    def test_far_future_valid_at_passed_through(self, facade, write_path) -> None:
        # The valid_at guard is engine/write-path-owned. The primitives facade
        # must NOT reject a far-future value at the border — it forwards it
        # untouched.
        t = datetime(9999, 1, 1, tzinfo=UTC)
        p = RememberPayload(body="hi", by={"source_type": "agent"}, valid_at=t)
        facade.remember(p)
        assert len(write_path.ingest_calls) == 1
        assert write_path.ingest_calls[0]["payload"].valid_at == t

    def test_none_valid_at_passed_through(self, facade, write_path) -> None:
        p = RememberPayload(body="hi", by={"source_type": "agent"}, valid_at=None)
        facade.remember(p)
        assert write_path.ingest_calls[0]["payload"].valid_at is None


class TestRememberLogHonesty:
    def test_logs_real_status_on_collision(self) -> None:
        from seahorse.contracts.engine import WriteResult
        from tests.facade.conftest import make_facade as _mf

        f, log = _mf()
        f._write_path.result = WriteResult(
            ep_id=None, fact_id=None, status="COLLISION", collisions_detected=[{"k": 1}]
        )
        f.remember(_payload())
        assert log == [("remember", "collision")]

    def test_logs_real_status_on_noop(self) -> None:
        from seahorse.contracts.engine import WriteResult
        from tests.facade.conftest import make_facade as _mf

        f, log = _mf()
        f._write_path.result = WriteResult(
            ep_id=None, fact_id=None, status="NOOP", collisions_detected=[]
        )
        f.remember(_payload())
        assert log == [("remember", "noop")]