"""Tests for the #4 LLM client contract freeze (SO-5a).

The contract is frozen from MVP-0 so #5 (write-path) can import it without
drift. The MVP-0 materialization uses stdlib ``@dataclass`` (NOT Pydantic
``BaseModel``) to keep the project runtime-dep-free — same field set as the
signed contract, mirroring the ``contracts/episode.py`` precedent (#1 signs
Pydantic, #6 materializes as dataclass). When #4 ships the real client it may
use Pydantic; the frontier shape (fields) is what is signed.

``StubLLMClient`` raises ``NotImplementedError`` — MVP-0 never calls the LLM
path (#5 degrades ``llm``→``skip``). ``BudgetContext`` is the documented
immutability exception: a mutable execution accumulator.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, is_dataclass

import pytest

from seahorse.llm.types import (
    BudgetContext,
    CompletionResult,
    ExtractResult,
    LLMClient,
    StubLLMClient,
)


class TestExtractResult:
    def test_required_fields(self) -> None:
        r = ExtractResult(data={"x": 1}, prompt_hash="h")
        assert r.data == {"x": 1}
        assert r.prompt_hash == "h"

    def test_defaults_match_signed_contract(self) -> None:
        r = ExtractResult(data={}, prompt_hash="h")
        assert r.model_used is None  # None when degraded_to_skip=True
        assert r.degraded_to_skip is False
        assert r.cost_usd == 0.0
        assert r.confidence is None
        assert r.retries == 0

    def test_frozen(self) -> None:
        r = ExtractResult(data={}, prompt_hash="h")
        with pytest.raises(FrozenInstanceError):
            r.retries = 5  # type: ignore[misc]


class TestCompletionResult:
    def test_required_fields(self) -> None:
        r = CompletionResult(text="out", prompt_hash="h")
        assert r.text == "out"
        assert r.prompt_hash == "h"

    def test_defaults(self) -> None:
        r = CompletionResult(text="out", prompt_hash="h")
        assert r.model_used is None
        assert r.cost_usd == 0.0
        assert r.tokens_used == 0
        assert r.retries == 0

    def test_frozen(self) -> None:
        r = CompletionResult(text="out", prompt_hash="h")
        with pytest.raises(FrozenInstanceError):
            r.text = "x"  # type: ignore[misc]


class TestBudgetContext:
    def test_defaults_match_signed_contract(self) -> None:
        b = BudgetContext()
        assert b.cap_usd == 0.002
        assert b.spent_usd == 0.0
        assert b.token_budget == 8000
        assert b.tokens_spent == 0
        assert b.repair_budget == 2
        assert b.fallback_to_skip is True

    def test_is_mutable_execution_accumulator(self) -> None:
        # Documented immutability exception: execution state, not domain data.
        b = BudgetContext()
        b.spent_usd = 0.001
        b.tokens_spent = 500
        assert b.spent_usd == 0.001
        assert b.tokens_spent == 500

    def test_record_actual_cost_accumulates(self) -> None:
        b = BudgetContext()
        b.record_actual_cost(cost_usd=0.001, tokens=400)
        assert b.spent_usd == pytest.approx(0.001)
        assert b.tokens_spent == 400
        b.record_actual_cost(cost_usd=0.002, tokens=600)
        assert b.spent_usd == pytest.approx(0.003)
        assert b.tokens_spent == 1000

    def test_record_actual_cost_defaults_zero(self) -> None:
        b = BudgetContext()
        b.record_actual_cost()
        assert b.spent_usd == 0.0
        assert b.tokens_spent == 0

    def test_is_dataclass_not_frozen(self) -> None:
        assert is_dataclass(BudgetContext)
        b = BudgetContext()
        # mutable: no FrozenInstanceError on assignment
        b.repair_budget = 3
        assert b.repair_budget == 3


class TestStubLLMClient:
    def test_extract_raises_not_implemented_with_skip_hint(self) -> None:
        client = StubLLMClient()
        with pytest.raises(NotImplementedError, match="skip"):
            client.extract("content", "schema_hint", role="extraction", budget=BudgetContext())

    def test_complete_raises_not_implemented(self) -> None:
        client = StubLLMClient()
        with pytest.raises(NotImplementedError):
            client.complete([], budget=BudgetContext())

    def test_mentions_mvp0_in_message(self) -> None:
        client = StubLLMClient()
        with pytest.raises(NotImplementedError, match="MVP-0"):
            client.extract("c", "s")


class TestLLMClientProtocol:
    def test_protocol_is_runtime_checkable(self) -> None:
        # StubLLMClient satisfies the LLMClient Protocol structurally.
        assert isinstance(StubLLMClient(), LLMClient)

    def test_protocol_has_extract_and_complete(self) -> None:
        assert hasattr(LLMClient, "extract")
        assert hasattr(LLMClient, "complete")