"""Tests for #5 ``run_llm_path`` (f5-05 §4.1) over the C8.7 seam.

The real LLM path: ``StubWritePath.ingest`` routes an ``llm`` decision to
``run_llm_path`` when a client is wired, otherwise it degrades (MVP-0
behaviour). Every failure degrades HONESTLY with the durable marker
(``degraded_from``/``degrade_reason``, ADR-10); success builds the effective
LLM provenance and delegates to ``engine.remember`` with the validated fields.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.contracts.engine import WriteResult
from seahorse.facade.types import RememberPayload
from seahorse.llm import BudgetContext, ExtractResult, LLMClient
from seahorse.write_path.decide import PathDecision, decide_path
from seahorse.write_path.llm import EpisodeFrontmatter, run_llm_path
from seahorse.write_path.stub import StubWritePath


class _RecordingLLMClient:
    """Recording double for the ``LLMClient`` Protocol (M4-C.3)."""

    def __init__(
        self,
        result: ExtractResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[dict] = []
        self._result = result
        self._error = error

    def extract(
        self,
        content: str,
        schema_hint: type,
        *,
        role: str = "extraction",
        budget: BudgetContext | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> ExtractResult:
        self.calls.append(
            {"content": content, "schema_hint": schema_hint, "role": role, "budget": budget}
        )
        if self._error is not None:
            raise self._error
        return self._result

    def complete(
        self,
        messages,
        *,
        role: str = "extraction",
        budget: BudgetContext | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> WriteResult:
        raise NotImplementedError


def _ok_result(**data) -> ExtractResult:
    payload = {"subject": "llm subject"}
    payload.update(data)
    return ExtractResult(
        data=payload,
        prompt_hash="h" * 64,
        model_used="ollama/qwen3:1.7b",
        confidence=0.9,
    )


def _payload(**kw) -> RememberPayload:
    base = {
        "body": "# Titulo\ncontenido",
        "by": {"source_type": "agent"},
        "valid_at": None,
        "cognitive_type": None,
        "title": None,
        "tags": (),
        "schema_version": "1.1",
    }
    base.update(kw)
    return RememberPayload(**base)


def _llm_decision(payload: RememberPayload) -> PathDecision:
    return decide_path(payload, "llm")


class TestRunLlmPathSuccess:
    def test_delegates_remember_with_effective_provenance(self, engine) -> None:
        client = _RecordingLLMClient(
            result=_ok_result(subject="llm subject", cognitive_type="episodic")
        )
        p = _payload()
        run_llm_path(p, _llm_decision(p), engine, client)
        call = engine.last_call
        assert call.body == p.body
        assert call.by["extraction_mode"] == "llm"
        assert call.by["model_used"] == "ollama/qwen3:1.7b"
        assert call.by["prompt_hash"] == "h" * 64
        assert call.by["confidence"] == 0.9
        assert call.subject == "llm subject"
        assert call.cognitive_type == "episodic"
        assert call.schema_version == "1.1"

    def test_extract_receives_body_schema_hint_and_budget(self, engine) -> None:
        client = _RecordingLLMClient(result=_ok_result())
        p = _payload()
        run_llm_path(p, _llm_decision(p), engine, client)
        call = client.calls[0]
        assert call["content"] == p.body
        assert call["schema_hint"] is EpisodeFrontmatter
        assert isinstance(call["budget"], BudgetContext)
        assert call["budget"].cap_usd == 0.002  # ADR-09 per-episode cap

    def test_missing_subject_degrades_final_validation(self, engine) -> None:
        # subject is REQUIRED (smoke 2026-08-04): a model that omits it fails
        # the final validation → honest degrade to skip (the skip path derives
        # from title/H1 instead).
        client = _RecordingLLMClient(result=_ok_result(subject=None))
        p = _payload()
        run_llm_path(p, _llm_decision(p), engine, client)
        assert engine.last_call.by["extraction_mode"] == "skip"
        assert engine.last_call.by["degrade_reason"] == "final_validation_failed"

    def test_payload_values_fall_back_when_model_omits(self, engine) -> None:
        client = _RecordingLLMClient(
            result=_ok_result(subject="s", cognitive_type=None, valid_at=None)
        )
        now = datetime(2026, 1, 1, tzinfo=UTC)
        p = _payload(valid_at=now, cognitive_type="semantic")
        run_llm_path(p, _llm_decision(p), engine, client)
        call = engine.last_call
        assert call.valid_at == now  # caller's explicit value wins (I2)
        assert call.cognitive_type == "semantic"


class TestRunLlmPathDegrades:
    def test_stub_client_degrades_not_implemented(self, engine) -> None:
        client = _RecordingLLMClient(error=NotImplementedError("MVP-0"))
        p = _payload()
        run_llm_path(p, _llm_decision(p), engine, client)
        by = engine.last_call.by
        assert client.calls  # the stub was invoked (it raises on call)
        assert by["extraction_mode"] == "skip"
        assert by["model_used"] is None
        assert by["prompt_hash"] is None
        assert by["degraded_from"] == "llm"
        assert by["degrade_reason"] == "llm_not_implemented_mvp0"

    def test_backend_exception_degrades(self, engine) -> None:
        client = _RecordingLLMClient(error=RuntimeError("boom"))
        p = _payload()
        run_llm_path(p, _llm_decision(p), engine, client)
        by = engine.last_call.by
        assert by["extraction_mode"] == "skip"
        assert by["degraded_from"] == "llm"
        assert by["degrade_reason"] == "llm_exception"

    def test_degraded_result_degrades(self, engine) -> None:
        client = _RecordingLLMClient(
            result=ExtractResult(data={}, prompt_hash="", degraded_to_skip=True)
        )
        p = _payload()
        run_llm_path(p, _llm_decision(p), engine, client)
        by = engine.last_call.by
        assert by["extraction_mode"] == "skip"
        assert by["degrade_reason"] == "llm_degraded"

    def test_schema_drift_degrades_final_validation(self, engine) -> None:
        client = _RecordingLLMClient(result=_ok_result(subject="s", bogus="x"))
        p = _payload()
        run_llm_path(p, _llm_decision(p), engine, client)
        by = engine.last_call.by
        assert by["extraction_mode"] == "skip"
        assert by["degrade_reason"] == "final_validation_failed"

    def test_unknown_cognitive_type_degrades_final_validation(self, engine) -> None:
        client = _RecordingLLMClient(result=_ok_result(cognitive_type="bogus"))
        p = _payload()
        run_llm_path(p, _llm_decision(p), engine, client)
        assert engine.last_call.by["degrade_reason"] == "final_validation_failed"


class TestIngestWiring:
    def test_ingest_routes_llm_when_client_wired(self, engine) -> None:
        client = _RecordingLLMClient(result=_ok_result(subject="s"))
        wp = StubWritePath(engine=engine, llm_client=client)
        wp.ingest(_payload(), "llm")
        assert client.calls
        assert engine.last_call.by["extraction_mode"] == "llm"

    def test_ingest_without_client_still_degrades(self, engine) -> None:
        wp = StubWritePath(engine=engine)
        wp.ingest(_payload(), "llm")
        by = engine.last_call.by
        assert by["extraction_mode"] == "skip"
        assert by["degrade_reason"] == "llm_not_implemented_mvp0"

    def test_ingest_skip_path_untouched_by_client(self, engine) -> None:
        client = _RecordingLLMClient(result=_ok_result())
        wp = StubWritePath(engine=engine, llm_client=client)
        p = _payload()
        wp.ingest(p, "skip")
        assert client.calls == []  # skip path never touches the LLM
        assert engine.last_call.by["extraction_mode"] == "skip"


class TestProtocolConformance:
    def test_recording_client_satisfies_llmclient_protocol(self) -> None:
        assert isinstance(_RecordingLLMClient(), LLMClient)
