"""Tests for #5 ``run_skip_path`` — the first-class skip path (f5-05 sec 3.1/3.2).

First-class #5 (commit 3) wires the engine's ``is_valid_skip_path`` formal
gate and the ``deterministic_extract`` fallback into ``run_skip_path``:

- **Gate valid** -> use-as-is: ``confidence=1.0`` in the effective provenance,
  the engine ``WriteResult`` is returned verbatim.
- **Gate raises ``E_SKIP_CONTRACT_VIOLATED``** -> log ``skip_path.populated_invalid``
  and fall back to ``deterministic_extract`` (zero-LLM editorial pass);
  ``confidence=None``.
- **Gate raises AND no subject derivable** -> ``SubjectDerivationError``
  propagates loud (ADR-10); the engine is NOT called.

``#5`` owns ``confidence`` (f5-05 sec 11.5): it OVERWRITES the caller's value
(``1.0`` gate-valid, ``None`` fallback) — it does not pass it through.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from seahorse.contracts.engine import WriteResult
from seahorse.facade.types import RememberPayload
from seahorse.write_path.decide import decide_path
from seahorse.write_path.extract import SubjectDerivationError
from seahorse.write_path.stub import run_skip_path


def _payload(by: dict | None = None, *, title: str | None = "T",
             body: str = "hello world", **kw) -> RememberPayload:
    base: dict = {"source_type": "agent"}
    if by:
        base.update(by)
    return RememberPayload(body=body, by=base, title=title, **kw)


class TestRunSkipPathGateValid:
    def test_calls_gate_then_engine_each_once(self, engine) -> None:
        run_skip_path(_payload(), decide_path(_payload(), "skip"), engine)
        assert engine.gate_count == 1
        assert engine.remember_count == 1

    def test_confidence_is_1_when_gate_valid(self, engine) -> None:
        run_skip_path(_payload(), decide_path(_payload(), "skip"), engine)
        assert engine.last_call.by["confidence"] == 1.0

    def test_returns_write_result_verbatim(self, engine) -> None:
        p = _payload()
        result = WriteResult(ep_id="ep-9", fact_id="fac-9", status="ACTIVE", collisions_detected=[])
        engine._result = result
        assert run_skip_path(p, decide_path(p, "skip"), engine) is result

    def test_candidate_created_at_injected_as_now(self, engine) -> None:
        t = datetime(2026, 7, 16, tzinfo=UTC)
        p = _payload()
        run_skip_path(p, decide_path(p, "skip"), engine, now=t)
        # I1: created_at is engine-owned; #5 injects it ONLY for gate validation.
        assert engine.last_gate_call.episode.created_at == t

    def test_candidate_extraction_mode_skip_injected(self, engine) -> None:
        p = _payload()  # caller by has no extraction_mode
        run_skip_path(p, decide_path(p, "skip"), engine)
        assert engine.last_gate_call.episode.provenance["extraction_mode"] == "skip"

    def test_candidate_no_engine_owned_temporal_fields(self, engine) -> None:
        p = _payload()
        run_skip_path(p, decide_path(p, "skip"), engine)
        cand = engine.last_gate_call.episode
        assert cand.invalid_at is None
        assert cand.expired_at is None
        assert cand.supersedes is None

    def test_effective_provenance_four_added_keys(self, engine) -> None:
        p = _payload({"agent_id": "a1"})  # 2 caller keys (source_type, agent_id)
        run_skip_path(p, decide_path(p, "skip"), engine)
        by = engine.last_call.by
        assert by["extraction_mode"] == "skip"
        assert by["model_used"] is None
        assert by["prompt_hash"] is None
        assert by["confidence"] == 1.0
        # caller keys preserved
        assert by["source_type"] == "agent"
        assert by["agent_id"] == "a1"

    def test_overwrites_caller_confidence_with_1_when_gate_valid(self, engine) -> None:
        # spec 11.5: #5 owns confidence and OVERWRITES the caller's value.
        p = _payload({"confidence": 0.42})
        run_skip_path(p, decide_path(p, "skip"), engine)
        assert engine.last_call.by["confidence"] == 1.0


class TestRunSkipPathGateInvalidFallback:
    def test_falls_back_with_confidence_none(self) -> None:
        from tests.write_path.conftest import RecordingEngine
        eng = RecordingEngine(gate_raises=True)
        p = _payload(title="T")  # subject derivable -> fallback OK
        run_skip_path(p, decide_path(p, "skip"), eng)
        assert eng.remember_count == 1
        assert eng.last_call.by["confidence"] is None
        assert eng.last_call.by["extraction_mode"] == "skip"

    def test_fallback_still_delegates_to_engine(self) -> None:
        from tests.write_path.conftest import RecordingEngine
        eng = RecordingEngine(gate_raises=True)
        run_skip_path(_payload(title="T"), decide_path(_payload(title="T"), "skip"), eng)
        assert eng.remember_count == 1

    def test_fallback_overwrites_caller_confidence_with_none(self) -> None:
        from tests.write_path.conftest import RecordingEngine
        eng = RecordingEngine(gate_raises=True)
        p = _payload({"confidence": 0.42}, title="T")
        run_skip_path(p, decide_path(p, "skip"), eng)
        assert eng.last_call.by["confidence"] is None

    def test_logs_populated_invalid_via_caplog(self, caplog: pytest.LogCaptureFixture) -> None:
        from tests.write_path.conftest import RecordingEngine
        eng = RecordingEngine(gate_raises=True)
        p = _payload(title="T")
        with caplog.at_level(logging.WARNING, logger="seahorse.write_path.stub"):
            run_skip_path(p, decide_path(p, "skip"), eng)
        assert any(
            "skip_path" in r.message and "invalid" in r.message.lower()
            for r in caplog.records
        ), [r.message for r in caplog.records]

    def test_no_subject_raises_and_does_not_delegate(self) -> None:
        from tests.write_path.conftest import RecordingEngine
        eng = RecordingEngine(gate_raises=True)
        p = _payload(title=None, body="just prose, no heading")  # no subject
        with pytest.raises(SubjectDerivationError):
            run_skip_path(p, decide_path(p, "skip"), eng)
        assert eng.remember_count == 0  # loud reject; engine never called

    def test_gate_returns_false_routes_to_fallback(self) -> None:
        # extraction_mode != "skip" -> gate returns False (not a skip payload).
        # run_skip_path always injects extraction_mode="skip", so this branch is
        # only reachable if the gate returns False without raising — treat it as
        # fallback (confidence=None), symmetric with the raising branch.
        from tests.write_path.conftest import RecordingEngine
        eng = RecordingEngine(skip_gate=False)
        p = _payload(title="T")
        run_skip_path(p, decide_path(p, "skip"), eng)
        assert eng.remember_count == 1
        assert eng.last_call.by["confidence"] is None

    def test_non_skip_engine_error_propagates_verbatim(self) -> None:
        # A gate error with a code OTHER than E_SKIP_CONTRACT_VIOLATED is not a
        # skip-contract rejection — run_skip_path must re-raise it verbatim
        # (no fallback, no swallow). ADR-10: never silently mask an engine error.
        from seahorse.engine.errors import E_NOT_IN_MVP_0, EngineError
        from tests.write_path.conftest import RecordingEngine
        eng = RecordingEngine(gate_error_code=E_NOT_IN_MVP_0)
        p = _payload(title="T")
        with pytest.raises(EngineError) as excinfo:
            run_skip_path(p, decide_path(p, "skip"), eng)
        assert excinfo.value.code == E_NOT_IN_MVP_0
        assert eng.remember_count == 0  # no fallback delegation; re-raised