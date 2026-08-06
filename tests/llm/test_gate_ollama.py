"""#4 CI gate integration tests (f5-04 §2.2) — real Ollama, weakest model.

The adversarial local-first guarantee: if the extraction path works on the
SMALLEST model of the family (``ollama/qwen3:0.6b``), it works on all of them.
The weak model follows instructions poorly, so the Pydantic validator + retry +
repair loop has to carry the load — that is what proves the path does not
silently depend on native structured outputs (``response_format``) or on the
quality of a strong model (ADR-05, f5-04 §6.1). A dev who opts into
``json_schema`` without the plain-prompt fallback makes THIS gate red (f5-04
§7.2), so the coupling is caught in CI, not in production.

These tests are GATED off by default (they hit a live Ollama on ``:11434``):
set ``SEAHORSE_RUN_LLM_TESTS=1`` to run, e.g.
``SEAHORSE_RUN_LLM_TESTS=1 uv run pytest tests/llm/test_gate_ollama.py --no-cov``.
The ``ci-llm-gate.yml`` workflow runs them with ``pytest -m llm_gate --reruns 2``
against a pinned Ollama service container + ``qwen3:0.6b`` (CPU, GitHub Actions).
The reruns absorb the weak model's stochastic variance (a single bad streak must
not turn a path-correct run red); a deterministic regression still fails every
rerun (f5-04 §2.2).

The main ``ci.yml`` never installs the ``llm`` extra, so a top-level litellm
import is forbidden here — the tests drive the real ``LiteLLMBackend`` public
API, which imports litellm lazily inside the call (same pattern as the
embeddings model tests and the install smoke).

References:
- f5-04-multi-llm.md §2.2 (CI gate), §4.4 (errors/retries), §5.5 (provenance), §6.1 (ADR-05)
- stack-f4.md §1.7 (testing: pin de versión Ollama obligatorio)
"""

from __future__ import annotations

import os

import pytest

from seahorse.llm import (
    BudgetContext,
    CompletionResult,
    LiteLLMBackend,
    RateLimitError,
    RoleRoute,
)
from seahorse.write_path.llm import EpisodeFrontmatter

pytestmark = [
    pytest.mark.llm_gate,
    pytest.mark.skipif(
        os.environ.get("SEAHORSE_RUN_LLM_TESTS") != "1",
        reason="llm gate gated; set SEAHORSE_RUN_LLM_TESTS=1 to run against a live Ollama",
    ),
]

# The weakest family member the gate is contractually run against (f5-04 §2.2).
# Overridable for a local smoke with any local model, e.g.
# ``LLM_GATE_MODEL=ollama/qwen3:14b-q4_K_M``.
GATE_MODEL = os.environ.get("LLM_GATE_MODEL", "ollama/qwen3:0.6b")

# A local model that is NOT the gate model; the fallback test forces it to fail.
# It is a LOCAL id, so ``cost._price_for`` prices it $0 by convention and the
# pre-flight budget never fires on it (no download happens — the mock raises
# before the real call).
_PRIMARY_DOUBLE = "ollama/qwen3:14b"

# Weak-model inference is slow on CI CPU; keep the response short and the
# timeout generous (the 2026-08-04 smoke: a 14b local exceeded the 20s default).
#
# max_tokens must leave room for the qwen3 THINKING tokens (2026-08-05 gate
# finding): qwen3:0.6b thinks by default and burns the output budget on
# reasoning — a tight cap (256) starved the final JSON and the path degraded
# honestly to skip. 1024 is enough for thinking + the (small) schema answer.
_GATE_MAX_TOKENS = 1024
_GATE_TIMEOUT_S = 90.0

# Date-free on purpose (2026-08-05 gate finding): qwen3:0.6b eagerly extracts
# a bare date from the content as ``valid_at``, which the I2 validator rejects
# (naive datetimes are not allowed) — and the weak model's repair is flaky at
# fixing it, which made the gate red on model variance, not on a path defect.
# The success-path tests use content the weakest model can extract reliably;
# the repair test below covers the repair MECHANISM + honest degrade.
SAMPLE_CONTENT = (
    "Meeting with the memory team: we agreed the episode subject should be a "
    "short topic phrase, the cognitive type for this note is episodic, and "
    "the tags should list the key concepts discussed."
)


def _gate_backend() -> LiteLLMBackend:
    return LiteLLMBackend(
        route=RoleRoute(primary=GATE_MODEL),
        max_tokens=_GATE_MAX_TOKENS,
        timeout_s=_GATE_TIMEOUT_S,
    )


def _family_and_tag(model_id: str) -> tuple[str, str]:
    """``ollama/qwen3:0.6b`` → (``qwen3``, ``0.6b``) — the provenance tag pin."""
    family, tag = model_id.split("/", 1)[1].split(":", 1)
    return family, tag


def _assert_gate_model_provenance(model_used: str) -> None:
    """Provenance must carry the effective model WITH its tag (f5-04 §5.5)."""
    family, tag = _family_and_tag(GATE_MODEL)
    assert family in model_used, f"expected {family} in {model_used}"
    assert tag in model_used, f"expected tag {tag} in {model_used}"


class TestExtractFlowReal:
    def test_weak_model_extracts_subject_with_plain_prompt(self, monkeypatch) -> None:
        # Spy on the real completion path to prove the DEFAULT kwargs carry no
        # native structured output (ADR-05): the gate is RED if extraction only
        # works with ``response_format`` (f5-04 §6.1/§7.2).
        backend = _gate_backend()
        real = backend._complete_one
        kwargs_seen: dict = {}

        def _spy(
            model_id, messages, ctx, *, schema_hint, max_tokens, timeout_s
        ) -> CompletionResult:
            kwargs_seen.update(
                backend._kwargs_for(
                    model_id,
                    messages,
                    schema_hint=schema_hint,
                    max_tokens=max_tokens,
                    timeout_s=timeout_s,
                )
            )
            return real(
                model_id, messages, ctx,
                schema_hint=schema_hint, max_tokens=max_tokens, timeout_s=timeout_s,
            )

        monkeypatch.setattr(backend, "_complete_one", _spy)
        res = backend.extract(SAMPLE_CONTENT, EpisodeFrontmatter, budget=BudgetContext())

        assert res.degraded_to_skip is False
        assert res.data["subject"].strip(), "weak model must extract a non-empty subject"
        assert len(res.prompt_hash) == 64
        assert all(c in "0123456789abcdef" for c in res.prompt_hash)
        assert res.model_used is not None
        _assert_gate_model_provenance(res.model_used)
        assert "response_format" not in kwargs_seen  # plain prompt default


class TestFallbackChainReal:
    def test_moves_to_gate_model_when_primary_fails(self, monkeypatch) -> None:
        backend = LiteLLMBackend(
            route=RoleRoute(primary=_PRIMARY_DOUBLE, secondary=GATE_MODEL),
            max_retries=0,
            max_tokens=_GATE_MAX_TOKENS,
            timeout_s=_GATE_TIMEOUT_S,
        )
        real = backend._complete_one

        def _force_primary_fail(
            model_id, messages, ctx, *, schema_hint, max_tokens, timeout_s
        ) -> CompletionResult:
            if model_id == _PRIMARY_DOUBLE:
                raise RateLimitError("forced 429: gate fallback test")
            return real(
                model_id, messages, ctx,
                schema_hint=schema_hint, max_tokens=max_tokens, timeout_s=timeout_s,
            )

        monkeypatch.setattr(backend, "_complete_one", _force_primary_fail)
        res = backend.extract(SAMPLE_CONTENT, EpisodeFrontmatter, budget=BudgetContext())

        assert res.degraded_to_skip is False
        assert res.data["subject"].strip()
        assert res.model_used is not None
        _assert_gate_model_provenance(res.model_used)  # the secondary answered


class TestRepairFlowReal:
    def test_repair_loop_runs_against_real_model_and_degrades_honestly(
        self, monkeypatch
    ) -> None:
        from seahorse.llm.parser import hash_prompt

        backend = _gate_backend()
        real = backend._complete_one
        calls: list[tuple[str, list]] = []

        def _spy(
            model_id, messages, ctx, *, schema_hint, max_tokens, timeout_s
        ) -> CompletionResult:
            calls.append((model_id, list(messages)))
            if len(calls) == 1:
                # The weak model's most common failure: prose, not JSON.
                return CompletionResult(
                    text="I cannot produce JSON. Here is a summary of the note instead.",
                    prompt_hash="x" * 64,
                    model_used=GATE_MODEL,
                )
            return real(
                model_id, messages, ctx,
                schema_hint=schema_hint, max_tokens=max_tokens, timeout_s=timeout_s,
            )

        monkeypatch.setattr(backend, "_complete_one", _spy)
        # repair_budget=1 → exactly one repair re-prompt (f5-04 §4.4 "1 per model").
        res = backend.extract(
            SAMPLE_CONTENT, EpisodeFrontmatter, budget=BudgetContext(repair_budget=1)
        )

        # The repair re-prompt was actually sent to the real model.
        assert len(calls) == 2  # initial + one repair
        repair_messages = calls[1][1]
        assert "Previous output" in repair_messages[1]["content"]

        if res.degraded_to_skip:
            # Honest degrade (ADR-10): the weak model could not fix its output
            # within the repair budget — never a crash, never garbage. The
            # provenance hashes the FIRST prompt (the backend's degraded path).
            assert res.data == {}
            assert res.model_used is None
            assert res.prompt_hash == hash_prompt(calls[0][1])
        else:
            # The repair succeeded: provenance hashes the EFFECTIVE (repair)
            # prompt that produced the valid output (f5-04 §5.5).
            assert res.data["subject"].strip()
            assert res.prompt_hash == hash_prompt(repair_messages)


class TestRunLlmPathEndToEnd:
    def test_write_path_produces_llm_provenance_on_weak_model(self) -> None:
        from seahorse.facade.types import RememberPayload
        from seahorse.write_path.decide import decide_path
        from seahorse.write_path.llm import run_llm_path
        from tests.write_path.conftest import RecordingEngine

        engine = RecordingEngine()
        payload = RememberPayload(
            body=SAMPLE_CONTENT,
            by={"source_type": "agent"},
            schema_version="1.1",
        )
        run_llm_path(payload, decide_path(payload, "llm"), engine, _gate_backend())

        call = engine.last_call
        assert call.by["extraction_mode"] == "llm"
        assert call.subject, "weak model extraction must reach engine.remember (M4-C.3)"
        assert call.by["model_used"] is not None
        _assert_gate_model_provenance(call.by["model_used"])
        assert len(call.by["prompt_hash"]) == 64
