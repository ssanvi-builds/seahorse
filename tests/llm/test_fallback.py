"""Tests for the LLM retry + fallback chain.

Transient errors retry with backoff + jitter; content/permanent errors
propagate immediately; the fallback chain moves primary→secondary→tertiary
when a backend is exhausted, treating ``ContextWindowError`` like a transient,
and raises ``LLMError`` only when every backend is gone.
"""

from __future__ import annotations

import pytest

from seahorse.llm import (
    ContextWindowError,
    LLMError,
    ProviderError,
    RateLimitError,
    TransientHTTPError,
)
from seahorse.llm.fallback import call_with_fallback, call_with_retry


class TestCallWithRetry:
    def test_retries_transient_then_succeeds(self) -> None:
        calls: list[int] = []

        def fn() -> str:
            calls.append(1)
            if len(calls) < 3:
                raise RateLimitError("429")
            return "ok"

        assert call_with_retry(fn, base_delay_s=0.0) == "ok"
        assert len(calls) == 3  # 2 retries + 1 success

    def test_exhausts_retries_and_raises_last_error(self) -> None:
        def fn() -> None:
            raise RateLimitError("429")

        with pytest.raises(RateLimitError):
            call_with_retry(fn, max_retries=1, base_delay_s=0.0)

    def test_permanent_error_not_retried(self) -> None:
        calls: list[int] = []

        def fn() -> None:
            calls.append(1)
            raise ProviderError("401")

        with pytest.raises(ProviderError):
            call_with_retry(fn, base_delay_s=0.0)
        assert len(calls) == 1

    def test_sleeps_with_jitter(self, monkeypatch) -> None:
        slept: list[float] = []
        monkeypatch.setattr("seahorse.llm.fallback.time.sleep", slept.append)
        # jitter factor 0.5 + 1.0*0.5 = 1.0 → base delay unchanged.
        monkeypatch.setattr("seahorse.llm.fallback.random.random", lambda: 1.0)
        calls: list[int] = []

        def fn() -> str:
            calls.append(1)
            if len(calls) < 2:
                raise TransientHTTPError("500")
            return "ok"

        assert call_with_retry(fn, base_delay_s=1.0) == "ok"
        assert slept == [1.0]


class TestCallWithFallback:
    def test_moves_to_secondary_when_primary_exhausted(self) -> None:
        def one(model_id: str) -> str:
            if model_id == "primary":
                raise RateLimitError("429")
            return model_id

        res = call_with_fallback(("primary", "secondary"), one, base_delay_s=0.0)
        assert res == "secondary"

    def test_context_window_error_moves_to_next_backend(self) -> None:
        def one(model_id: str) -> str:
            if model_id == "a":
                raise ContextWindowError("too long")
            return model_id

        res = call_with_fallback(("a", "b"), one, base_delay_s=0.0)
        assert res == "b"

    def test_all_context_window_exhausts(self) -> None:
        def one(model_id: str) -> None:
            raise ContextWindowError("too long")

        with pytest.raises(LLMError, match="All backends exhausted"):
            call_with_fallback(("a", "b"), one, base_delay_s=0.0)

    def test_all_exhausted_raises_llm_error(self) -> None:
        def one(model_id: str) -> None:
            raise RateLimitError("429")

        with pytest.raises(LLMError, match="All backends exhausted"):
            call_with_fallback(("a", "b"), one, base_delay_s=0.0)

    def test_first_backend_success_does_not_touch_rest(self) -> None:
        seen: list[str] = []

        def one(model_id: str) -> str:
            seen.append(model_id)
            return model_id

        assert call_with_fallback(("a", "b", "c"), one) == "a"
        assert seen == ["a"]
