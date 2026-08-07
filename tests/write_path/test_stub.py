"""Tests for #5 ``StubWritePath`` — the MVP-0 write path (SO-5b).

MVP-0 materializes the skip-path for real (it is the production skip path).
The ``llm`` path degrades to skip with ``reason='llm_not_implemented_mvp0'``
(the LLM path is MVP-1). ``run_skip_path`` is first-class: it runs the engine
``is_valid_skip_path`` gate, falls back to ``deterministic_extract`` when the
gate rejects, and owns ``confidence`` (1.0 gate-valid, None fallback).
Gate-branch coverage lives in ``test_skip.py``; this file pins the provenance
shape and the degrade/ingest wiring.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from seahorse.contracts.engine import WriteResult
from seahorse.facade.types import RememberPayload
from seahorse.write_path.decide import decide_path
from seahorse.write_path.stub import StubWritePath, _degrade_to_skip, run_skip_path


def _payload(by: dict | None = None, **kw) -> RememberPayload:
    base = {"source_type": "agent"}
    if by:
        base.update(by)
    body = kw.pop("body", "hello world")
    return RememberPayload(body=body, by=base, **kw)


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

    def test_effective_provenance_has_four_added_keys(self, engine) -> None:
        # First-class #5: confidence joins the 3 MVP-0 keys (spec sec 11.5).
        p = _payload({"source_type": "agent", "agent_id": "a1"})
        run_skip_path(p, decide_path(p, "skip"), engine)
        by = engine.last_call.by
        assert by["extraction_mode"] == "skip"
        assert by["model_used"] is None
        assert by["prompt_hash"] is None
        assert by["confidence"] == 1.0

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

    def test_overwrites_caller_confidence_with_1_when_gate_valid(self, engine) -> None:
        # First-class #5: #5 owns confidence (spec sec 11.5) and OVERWRITES the
        # caller's value — 1.0 when the gate is valid (use-as-is), None on fallback.
        p = _payload({"source_type": "agent", "confidence": 0.42})
        run_skip_path(p, decide_path(p, "skip"), engine)
        assert engine.last_call.by["confidence"] == 1.0

    def test_genuine_skip_carries_no_degrade_marker(self, engine) -> None:
        # C8.7 [55] / ADR-10: a genuine skip (caller asked for skip) must NOT carry
        # the degrade marker — the marker distinguishes a REAL skip from a degraded
        # llm→skip. Without this, a degraded episode is indistinguishable from a
        # genuine skip in stored provenance (the "permanent lie" C8.7 fixes).
        p = _payload({"source_type": "agent"})
        run_skip_path(p, decide_path(p, "skip"), engine)
        by = engine.last_call.by
        assert "degraded_from" not in by
        assert "degrade_reason" not in by


class TestSummaryEnabler:
    """OQ3 enabler (f5-09 §6.2): the write path always supplies a summary to
    ``engine.remember`` — the caller's value or a deterministic zero-LLM
    fallback (first sentence of the body, skipping the H1). Covers 100% of
    episodes including the skip path."""

    def test_skip_path_derives_fallback_summary(self, engine) -> None:
        p = _payload(body="# Title\n\nFirst sentence of content. Second.")
        run_skip_path(p, decide_path(p, "skip"), engine)
        assert engine.last_call.summary == "First sentence of content."

    def test_skip_path_forwards_caller_summary(self, engine) -> None:
        p = _payload(body="# Title\n\nContent.", summary="Caller summary")
        run_skip_path(p, decide_path(p, "skip"), engine)
        assert engine.last_call.summary == "Caller summary"

    def test_fallback_skips_h1(self, engine) -> None:
        # The H1 is the subject; the summary must never be the tagged subject
        # line (obsiforge §15.2 redesign 4).
        p = _payload(body="# [session_tag:prompt_number]\n\nReal content here.")
        run_skip_path(p, decide_path(p, "skip"), engine)
        assert engine.last_call.summary == "Real content here."

    def test_fallback_branch_derives_summary(self, engine) -> None:
        # Gate-invalid -> deterministic_extract fallback still supplies a summary.
        p = _payload(body="# Title\n\nFallback content sentence.")
        run_skip_path(p, decide_path(p, "skip"), engine, now=datetime(2026, 1, 1, tzinfo=UTC))
        assert engine.last_call.summary == "Fallback content sentence."

    def test_degrade_to_skip_derives_summary(self, engine) -> None:
        p = _payload(body="# Title\n\nDegraded content sentence.")
        _degrade_to_skip(p, decide_path(p, "llm"), engine)
        assert engine.last_call.summary == "Degraded content sentence."

    def test_ingest_skip_derives_summary(self, engine) -> None:
        wp = StubWritePath(engine=engine)
        p = _payload(body="# Title\n\nIngest content sentence.")
        wp.ingest(p, "skip")
        assert engine.last_call.summary == "Ingest content sentence."


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

    def test_degrade_marks_degraded_from_and_reason_in_provenance(self, engine) -> None:
        # C8.7 [55] / ADR-10: the degrade is marked EXPLICITLY in provenance —
        # ``degraded_from`` (the requested mode that was degraded) + ``degrade_reason``
        # (why). This is the durable marker that distinguishes a degraded llm→skip
        # from a genuine skip (logging alone is non-durable; logs rotate).
        p = _payload()
        _degrade_to_skip(p, decide_path(p, "llm"), engine, reason="llm_not_implemented_mvp0")
        by = engine.last_call.by
        assert by["degraded_from"] == "llm"
        assert by["degrade_reason"] == "llm_not_implemented_mvp0"

    def test_degrade_core_model_used_and_prompt_hash_stay_none(self, engine) -> None:
        # Spec f5-05 sec 5 line 111: on an llm→skip degrade the EFFECTIVE mode is
        # skip, so provenance core carries ``model_used=None`` / ``prompt_hash=None``
        # (no model ran on this episode). Even when the caller CLAIMED an LLM intent
        # (model_used/prompt_hash in their by), #5 overwrites them to None — #5 does
        # not lie about the effective mode. The claimed intent is LOGGED, not stored.
        p = _payload({"source_type": "agent", "model_used": "gpt-test", "prompt_hash": "ph-1"})
        _degrade_to_skip(p, decide_path(p, "llm"), engine)
        by = engine.last_call.by
        assert by["model_used"] is None
        assert by["prompt_hash"] is None

    def test_degrade_logs_caller_llm_intent_for_traceability(
        self, engine, caplog
    ) -> None:
        # Spec f5-05 sec 5 line 111: the caller's CLAIMED model_used/prompt_hash
        # (the LLM intent that was degraded) are LOGUED at INFO for traceability of
        # the failed intent — they do NOT go to provenance core. Pins the log seam.
        p = _payload({"source_type": "agent", "model_used": "gpt-test", "prompt_hash": "ph-1"})
        with caplog.at_level(logging.INFO, logger="seahorse.write_path.stub"):
            _degrade_to_skip(p, decide_path(p, "llm"), engine, reason="llm_not_implemented_mvp0")
        # core stays None (spec line 111) — the intent is traced only in the log.
        assert engine.last_call.by["model_used"] is None
        assert engine.last_call.by["prompt_hash"] is None
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "gpt-test" in joined  # intent model_used logged
        assert "ph-1" in joined  # intent prompt_hash logged
        assert "llm_not_implemented_mvp0" in joined  # reason logged


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
        # C8.7 [55]: the degrade is marked — ingest(llm) carries degraded_from.
        assert engine.last_call.by["degraded_from"] == "llm"
        assert engine.last_call.by["degrade_reason"] == "llm_not_implemented_mvp0"

    def test_skip_ingest_carries_no_degrade_marker(self, engine) -> None:
        # C8.7 [55] / ADR-10: ingest(skip) is a genuine skip — no degrade marker.
        # The marker is degrade-only; its absence on genuine skip is what makes it
        # a truthful signal (presence == degraded, absence == real skip).
        wp = StubWritePath(engine)
        p = _payload()
        wp.ingest(p, "skip")
        by = engine.last_call.by
        assert "degraded_from" not in by
        assert "degrade_reason" not in by

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