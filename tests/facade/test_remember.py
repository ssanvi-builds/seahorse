"""Tests for ``MemoryFacade.remember`` — boundary validation + delegation to #5.

#12.remember validates shape only (body, source_type, tags-empty, resolved
extraction_mode) and delegates to #5 ``WritePath.ingest``. It does NOT call
``engine.remember`` (the write-path owns that) and does NOT replicate the
``source_type → skip`` guard (that lives in #5 ``decide_path``).
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
        # #12 never reaches the engine directly for remember — #5 owns the write.
        facade.remember(_payload())
        assert engine.improve_calls == []
        assert engine.forget_calls == []

    def test_returns_write_result_verbatim(self, facade, write_path) -> None:
        from seahorse.contracts.engine import WriteResult

        write_path.result = WriteResult(
            ep_id="ep-7", fact_id="fac-7", status="ACTIVE", collisions_detected=[]
        )
        assert facade.remember(_payload()).ep_id == "ep-7"

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
        # Empty body raises BEFORE #5 is touched (no ingest call).
        with pytest.raises(SeahorseError):
            facade.remember(_payload(body=""))
        assert write_path.ingest_calls == []

    def test_does_not_validate_cognitive_type_enum(self, facade, write_path) -> None:
        # #12 does NOT enforce COGNITIVE_TYPES — engine/#1 are the authority.
        # 'fact' is outside the f5-01 enum but passes the facade (engine tests use it).
        facade.remember(_payload(cognitive_type="fact"))
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
        # The source_type→skip guard lives in #5 decide_path, NOT in #12.
        # #12 passes 'llm' through; #5 would then degrade. Here we only assert
        # #12 does not rewrite the mode.
        p = RememberPayload(body="hi", by={"source_type": "importer"})
        facade.remember(p, extraction_mode="llm")
        assert write_path.ingest_calls[0]["extraction_mode"] == "llm"