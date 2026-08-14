"""Tests for the LiteLLM backend — no network.

``litellm`` is a lazy optional import, so the tests install a fake module in
``sys.modules`` (the same trick CI relies on: ``uv sync --extra dev`` has no
litellm and the backend still imports). The fake ``completion`` is exercised
through the real orchestration: pre-flight budget → fallback chain → validate →
repair → ``degraded_to_skip``.
"""

from __future__ import annotations

import sys
import types

import pytest
from pydantic import BaseModel, ConfigDict

from seahorse.llm import (
    BudgetContext,
    LiteLLMBackend,
    LLMClient,
    LLMError,
    RateLimitError,
    RoleRoute,
)


class _Frontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    tags: list[str] = []


class _FakeUsage:
    def __init__(self, prompt_tokens: int = 100, completion_tokens: int = 50) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, text: str) -> None:
        self.message = _FakeMessage(text)


class _FakeResp:
    def __init__(self, text: str, model: str = "fake-model", usage=None) -> None:
        self.choices = [_FakeChoice(text)]
        self.model = model
        self.usage = usage or _FakeUsage()


def _install_litellm(monkeypatch, completion_fn) -> None:
    fake = types.ModuleType("litellm")
    fake.completion = completion_fn
    monkeypatch.setitem(sys.modules, "litellm", fake)


def _route(*models: str) -> RoleRoute:
    return RoleRoute(
        primary=models[0],
        secondary=models[1] if len(models) > 1 else None,
        tertiary=models[2] if len(models) > 2 else None,
    )


class TestExtractValid:
    def test_valid_json_returns_validated_data(self, monkeypatch) -> None:
        _install_litellm(
            monkeypatch,
            lambda **kw: _FakeResp('{"subject": "seahorse", "tags": ["memoria"]}'),
        )
        backend = LiteLLMBackend(route=_route("ollama/qwen3:1.7b"))
        res = backend.extract("body", _Frontmatter)
        assert res.degraded_to_skip is False
        assert res.data == {"subject": "seahorse", "tags": ["memoria"]}
        assert res.model_used == "ollama/fake-model"  # provider prefix normalized
        assert len(res.prompt_hash) == 64
        assert res.confidence is None  # extractor does not emit confidence

    def test_model_used_avoids_double_prefix_when_litellm_prefixed(self, monkeypatch) -> None:
        # litellm may already return "ollama/qwen3:..." (with prefix); the
        # normalizer must not prepend a second "ollama/" (smoke-tested 2026-08-04).
        _install_litellm(
            monkeypatch,
            lambda **kw: _FakeResp('{"subject": "x", "tags": []}', model="ollama/qwen3:14b-q4_K_M"),
        )
        backend = LiteLLMBackend(route=_route("ollama/qwen3:1.7b"))
        res = backend.extract("body", _Frontmatter)
        assert res.model_used == "ollama/qwen3:14b-q4_K_M"

    def test_usage_records_into_budget_context(self, monkeypatch) -> None:
        _install_litellm(
            monkeypatch,
            lambda **kw: _FakeResp('{"subject": "x", "tags": []}', usage=_FakeUsage(120, 40)),
        )
        backend = LiteLLMBackend(route=_route("ollama/qwen3:1.7b"))
        ctx = BudgetContext()
        backend.extract("body", _Frontmatter, budget=ctx)
        assert ctx.tokens_spent == 160  # ollama is $0 but tokens still count


class TestRepair:
    def test_repair_flow_uses_second_prompt(self, monkeypatch) -> None:
        calls: list[list[dict]] = []

        def completion_fn(**kw) -> _FakeResp:
            calls.append(kw["messages"])
            if len(calls) == 1:
                return _FakeResp("no json here")
            return _FakeResp('{"subject": "fixed", "tags": []}')

        _install_litellm(monkeypatch, completion_fn)
        backend = LiteLLMBackend(route=_route("ollama/qwen3:1.7b"))
        res = backend.extract("body", _Frontmatter)
        assert res.degraded_to_skip is False
        assert res.data == {"subject": "fixed", "tags": []}
        assert len(calls) == 2
        assert "Previous output" in calls[1][1]["content"]  # repair prompt

    def test_repair_exhausted_degrades_to_skip(self, monkeypatch) -> None:
        _install_litellm(monkeypatch, lambda **kw: _FakeResp("still not json"))
        backend = LiteLLMBackend(route=_route("ollama/qwen3:1.7b"))
        ctx = BudgetContext(repair_budget=1)
        res = backend.extract("body", _Frontmatter, budget=ctx)
        assert res.degraded_to_skip is True
        assert res.model_used is None  # explicit None when degraded
        assert ctx.last_degradation_reason == "repair_exhausted"


class TestFallback:
    def test_moves_to_secondary_on_rate_limit(self, monkeypatch) -> None:
        def completion_fn(**kw) -> _FakeResp:
            if kw["model"] == "groq/llama-3.3-70b-versatile":
                raise RateLimitError("429")
            return _FakeResp('{"subject": "x", "tags": []}')

        _install_litellm(monkeypatch, completion_fn)
        backend = LiteLLMBackend(
            route=_route("groq/llama-3.3-70b-versatile", "ollama/qwen3:1.7b"),
            max_retries=0,
        )
        res = backend.extract("body", _Frontmatter)
        assert res.degraded_to_skip is False
        assert res.model_used == "ollama/fake-model"

    def test_chain_exhausted_degrades_not_raises(self, monkeypatch) -> None:
        def completion_fn(**kw) -> _FakeResp:
            raise RateLimitError("429")

        _install_litellm(monkeypatch, completion_fn)
        backend = LiteLLMBackend(
            route=_route("groq/llama-3.3-70b-versatile"), max_retries=0
        )
        res = backend.extract("body", _Frontmatter)
        assert res.degraded_to_skip is True
        assert res.model_used is None

    def test_complete_propagates_when_chain_exhausted(self, monkeypatch) -> None:
        def completion_fn(**kw) -> _FakeResp:
            raise RateLimitError("429")

        _install_litellm(monkeypatch, completion_fn)
        backend = LiteLLMBackend(
            route=_route("groq/llama-3.3-70b-versatile"), max_retries=0
        )
        with pytest.raises(LLMError, match="All backends exhausted"):
            backend.complete([{"role": "user", "content": "hi"}])


class TestBudgetAndSetup:
    def test_preflight_budget_exceeded_degrades(self, monkeypatch) -> None:
        called: list[bool] = []

        def completion_fn(**kw) -> _FakeResp:
            called.append(True)
            return _FakeResp('{"subject": "x", "tags": []}')

        _install_litellm(monkeypatch, completion_fn)
        backend = LiteLLMBackend(route=_route("deepseek/deepseek-chat"))
        ctx = BudgetContext(cap_usd=0.0001)
        res = backend.extract("very long body " * 100, _Frontmatter, budget=ctx)
        assert res.degraded_to_skip is True
        assert ctx.last_degradation_reason == "budget_pre_flight_exceeded"
        assert called == []  # the LLM was never hit

    def test_no_route_degrades_with_setup_hint(self, monkeypatch) -> None:
        _install_litellm(monkeypatch, lambda **kw: _FakeResp('{"subject": "x"}'))
        backend = LiteLLMBackend()  # no route configured yet
        ctx = BudgetContext()
        res = backend.extract("body", _Frontmatter, budget=ctx)
        assert res.degraded_to_skip is True
        assert "no LLM route" in (ctx.last_degradation_reason or "")


class TestNativeStructuredOptIn:
    def test_plain_prompt_default_has_no_response_format(self, monkeypatch) -> None:
        captured: dict = {}

        def completion_fn(**kw) -> _FakeResp:
            captured.update(kw)
            return _FakeResp('{"subject": "x", "tags": []}')

        _install_litellm(monkeypatch, completion_fn)
        backend = LiteLLMBackend(route=_route("groq/llama-3.3-70b-versatile"))
        backend.extract("body", _Frontmatter)
        assert "response_format" not in captured  # plain prompt default

    def test_native_structured_only_when_opted_in(self, monkeypatch) -> None:
        captured: dict = {}

        def completion_fn(**kw) -> _FakeResp:
            captured.update(kw)
            return _FakeResp('{"subject": "x", "tags": []}')

        _install_litellm(monkeypatch, completion_fn)
        backend = LiteLLMBackend(
            route=_route("groq/llama-3.3-70b-versatile"),
            use_native_structured=True,
        )
        backend.extract("body", _Frontmatter)
        assert captured["response_format"]["type"] == "json_schema"


class TestProtocolConformance:
    def test_backend_satisfies_llmclient_protocol(self) -> None:
        assert isinstance(
            LiteLLMBackend(route=_route("ollama/qwen3:1.7b")), LLMClient
        )
