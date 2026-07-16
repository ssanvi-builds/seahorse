"""Tests for #5 ``StubWritePath`` — the MVP-0 write path (SO-5b).

MVP-0 materializes the skip-path for real (it is not dead code — it is the
production skip path). The ``llm`` path degrades to skip with
``reason='llm_not_implemented_mvp0'`` (the LLM path is MVP-1). ``run_skip_path``
constructs the effective provenance (``extraction_mode='skip'``,
``model_used=None``, ``prompt_hash=None``) and delegates to ``engine.remember``
— it does NOT replicate engine invariants (no gate, no deterministic_extract;
those are MVP-1).
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.contracts.engine import WriteResult
from seahorse.facade.types import RememberPayload
from seahorse.write_path.decide import decide_path
from seahorse.write_path.stub import StubWritePath, _degrade_to_skip, run_skip_path


def _payload(by: dict | None = None, **kw) -> RememberPayload:
    base = {"source_type": "agent"}
    if by:
        base.update(by)
    return RememberPayload(body="hello world", by=base, **kw)


class TestRunSkipPath:
    def test_delegates_to_engine_remember_once(self, engine) -> None:
        run_skip_path(_payload(), decide_path(_payload(), "skip"), engine)
        assert engine.remember_count == 1

    def test_effective_provenance_marks_skip(self, engine) -> None:
        p = _payload({"source_type": "agent", "agent_id": "a1"})
        run_skip_path(p, decide_path(p, "skip"), engine)
        by = engine.last_call.by
        assert by["extraction_mode"] == "skip"
        assert by["model_used"] is None
        assert by["prompt_hash"] is None

    def test_preserves_caller_provenance(self, engine) -> None:
        p = _payload({"source_type": "agent", "agent_id": "a1", "session_id": "s1"})
        run_skip_path(p, decide_path(p, "skip"), engine)
        by = engine.last_call.by
        assert by["source_type"] == "agent"
        assert by["agent_id"] == "a1"
        assert by["session_id"] == "s1"

    def test_forwards_body_and_fields(self, engine) -> None:
        t = datetime(2026, 1, 1, tzinfo=UTC)
        p = _payload(title="My Title", valid_at=t, cognitive_type="semantic", schema_version="1.2")
        run_skip_path(p, decide_path(p, "skip"), engine, now=t)
        call = engine.last_call
        assert call.body == "hello world"
        assert call.title == "My Title"
        assert call.valid_at == t
        assert call.cognitive_type == "semantic"
        assert call.schema_version == "1.2"
        assert call.now == t

    def test_returns_engine_write_result_verbatim(self, engine) -> None:
        p = _payload()
        result = WriteResult(ep_id="ep-9", fact_id="fac-9", status="ACTIVE", collisions_detected=[])
        engine._result = result
        assert run_skip_path(p, decide_path(p, "skip"), engine) is result

    def test_does_not_overwrite_caller_confidence(self, engine) -> None:
        # run_skip_path only sets extraction_mode/model_used/prompt_hash; it does
        # NOT touch confidence — the caller's value (if any) passes through.
        p = _payload({"source_type": "agent", "confidence": 0.42})
        run_skip_path(p, decide_path(p, "skip"), engine)
        assert engine.last_call.by["confidence"] == 0.42


class TestDegradeToSkip:
    def test_routes_to_skip_with_mvp0_reason(self, engine) -> None:
        p = _payload()
        decision = decide_path(p, "llm")  # path == "llm"
        assert decision.path == "llm"
        result = _degrade_to_skip(p, decision, engine, reason="llm_not_implemented_mvp0")
        assert engine.remember_count == 1
        assert engine.last_call.by["extraction_mode"] == "skip"
        assert engine.last_call.by["model_used"] is None
        assert result.status == "ACTIVE"

    def test_degrade_does_not_call_llm(self, engine) -> None:
        # MVP-0: the llm path degrades directly — it never reaches StubLLMClient.
        # Proven structurally: _degrade_to_skip takes no llm_client argument.
        p = _payload()
        _degrade_to_skip(p, decide_path(p, "llm"), engine)
        assert engine.remember_count == 1


class TestStubWritePathIngest:
    def test_skip_path_runs_skip(self, engine) -> None:
        wp = StubWritePath(engine)
        p = _payload()
        wp.ingest(p, "skip")
        assert engine.remember_count == 1
        assert engine.last_call.by["extraction_mode"] == "skip"

    def test_llm_path_degrades_to_skip(self, engine) -> None:
        wp = StubWritePath(engine)
        p = _payload()
        wp.ingest(p, "llm")
        assert engine.remember_count == 1
        assert engine.last_call.by["extraction_mode"] == "skip"
        assert engine.last_call.by["model_used"] is None

    def test_importer_ingest_uses_skip(self, engine) -> None:
        wp = StubWritePath(engine)
        p = _payload({"source_type": "importer"})
        wp.ingest(p, "llm")  # even with llm flag, importer guard -> skip
        assert engine.remember_count == 1
        assert engine.last_call.by["extraction_mode"] == "skip"

    def test_returns_write_result(self, engine) -> None:
        wp = StubWritePath(engine)
        result = WriteResult(ep_id="x", fact_id="y", status="ACTIVE", collisions_detected=[])
        engine._result = result
        assert wp.ingest(_payload(), "skip") is result

    def test_forwards_now(self, engine) -> None:
        wp = StubWritePath(engine)
        t = datetime(2026, 7, 16, tzinfo=UTC)
        wp.ingest(_payload(), "skip", now=t)
        assert engine.last_call.now == t