"""Tests for the #4 cost cap (f5-04 §4.1) — the ≤ $0.002/episodio gate.

Local and free-tier cloud models price at $0 (the 2026-08-04 palanca), so the
cap never fires for them; paid cloud rows use verified cache-miss prices. The
sanity check from f5-04 §3.2 (deepseek on a 5KB episode ≈ $0.0007) is pinned
here so the budget math cannot drift.
"""

from __future__ import annotations

import pytest

from seahorse.llm import (
    PRICING,
    BudgetContext,
    BudgetExceeded,
    LLMError,
    PricePerMTok,
    check_budget,
    estimate_cost,
    record_actual_cost,
)


class TestPricingTable:
    def test_local_and_free_tier_price_zero(self) -> None:
        for model_id in (
            "ollama/qwen3:1.7b",
            "ollama/qwen3:0.6b",
            "gemini/gemini-2.5-flash",
            "groq/llama-3.3-70b-versatile",
            "openrouter/deepseek/deepseek-r1:free",
        ):
            assert PRICING[model_id] == PricePerMTok(0.0, 0.0), model_id

    def test_paid_rows_present(self) -> None:
        assert "deepseek/deepseek-chat" in PRICING
        assert "anthropic/claude-haiku-4-5" in PRICING
        assert "openai/gpt-5-mini" in PRICING


class TestEstimateCost:
    def test_free_model_estimates_zero(self) -> None:
        assert estimate_cost("ollama/qwen3:1.7b", 3000, 1000) == 0.0

    def test_deepseek_5kb_episode_sanity_check(self) -> None:
        # f5-04 §3.2 pinned check: 3000 in + 1000 out on deepseek-chat.
        est = estimate_cost("deepseek/deepseek-chat", 3000, 1000)
        assert est == pytest.approx(0.0007)  # (420 + 280) / 1e6

    def test_unknown_model_raises_loud(self) -> None:
        with pytest.raises(LLMError, match="No pricing for nosuch/x"):
            estimate_cost("nosuch/x", 100, 100)


class TestRecordActualCost:
    def test_accumulates_into_budget_context(self) -> None:
        ctx = BudgetContext()
        cost = record_actual_cost(ctx, "deepseek/deepseek-chat", 1000, 500)
        assert cost == pytest.approx(0.00028)  # (140 + 140) / 1e6
        assert ctx.spent_usd == pytest.approx(0.00028)
        assert ctx.tokens_spent == 1500

    def test_free_model_records_zero(self) -> None:
        ctx = BudgetContext()
        assert record_actual_cost(ctx, "ollama/qwen3:1.7b", 3000, 500) == 0.0
        assert ctx.spent_usd == 0.0
        assert ctx.tokens_spent == 3500  # tokens still count toward the budget

    def test_unknown_model_raises_loud(self) -> None:
        with pytest.raises(LLMError):
            record_actual_cost(BudgetContext(), "nosuch/x", 100, 100)


class TestCheckBudget:
    def test_under_cap_passes(self) -> None:
        ctx = BudgetContext(cap_usd=0.002)
        check_budget("deepseek/deepseek-chat", 3000, 1000, ctx)  # $0.0007 < cap
        assert ctx.spent_usd == 0.0  # pre-flight only, no mutation

    def test_free_model_never_exceeds(self) -> None:
        ctx = BudgetContext(cap_usd=0.0)  # even a zero cap is fine at $0
        check_budget("gemini/gemini-2.5-flash", 10_000, 10_000, ctx)

    def test_over_remaining_cap_raises_budget_exceeded(self) -> None:
        ctx = BudgetContext(cap_usd=0.0005)
        with pytest.raises(BudgetExceeded, match="degrade to skip"):
            check_budget("deepseek/deepseek-chat", 3000, 1000, ctx)

    def test_accounts_for_spent_so_far(self) -> None:
        ctx = BudgetContext(cap_usd=0.001)
        record_actual_cost(ctx, "deepseek/deepseek-chat", 2000, 2000)  # $0.00084
        with pytest.raises(BudgetExceeded):
            check_budget("deepseek/deepseek-chat", 3000, 1000, ctx)  # +$0.0007
