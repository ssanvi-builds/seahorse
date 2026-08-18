"""LiteLLM backend — the real ``LLMClient``.

Implements the ``LLMClient`` Protocol over LiteLLM: a single SDK for the 100+
provider surface, so swapping ``ollama/qwen3:1.7b`` for
``gemini/gemini-2.5-flash`` is a config change, not code. LiteLLM lives in the
optional ``llm`` extra and is imported LAZILY inside the call — importing the
package never requires it (mirrors the ``embeddings`` extra pattern).

Sync boundary: the contract is sync; LiteLLM's ``completion`` is used
directly. ``extract`` orchestrates the full pipeline: pre-flight budget gate →
plain-prompt build (injection-delimited) → fallback chain with retries →
Pydantic validate → repair loop (1 repair per model) → ``degraded_to_skip``
when the chain is exhausted or the budget/repair cap fires. The write path
never sees a raised LLM error from ``extract``; ``complete`` propagates instead
(the complete-path caller expects the text).

Native structured outputs (``response_format``) are OPT-IN and gated on
``ProviderConfig.supports_json_schema``; the default is the plain prompt +
validator path that works on every model including the weakest. ``model_used``
is normalized to ``{provider}/{resp.model}`` (LiteLLM can return a bare model
id), and ``prompt_hash`` hashes the prompt that produced the VALID output
(after a repair, the repair prompt).
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any, NoReturn

from pydantic import BaseModel

from seahorse.llm import cost
from seahorse.llm.errors import (
    BudgetExceeded,
    ContextWindowError,
    ExtractionValidationError,
    LLMError,
    ProviderError,
    RateLimitError,
    TransientHTTPError,
)
from seahorse.llm.fallback import call_with_fallback
from seahorse.llm.parser import (
    build_extract_prompt,
    build_repair_prompt,
    hash_prompt,
    parse_and_validate,
)
from seahorse.llm.providers import resolve_provider
from seahorse.llm.routing import RoleRoute
from seahorse.llm.types import BudgetContext, CompletionResult, ExtractResult, Messages

_logger = logging.getLogger("seahorse.llm.litellm")

_DEFAULT_MAX_TOKENS = 2048
_DEFAULT_TIMEOUT_S = 20.0  # extraction role timeout


def _count_tokens(text: str) -> int:
    """Rough pre-flight token estimate (content ≤5KB typical).

    A ~4-char-per-token heuristic is enough for the cost gate; the real usage
    comes from the response after the call.
    """
    return max(1, len(text) // 4)


def _translate_litellm_exc(exc: Exception) -> NoReturn:
    """Map a LiteLLM vendor exception to the error taxonomy by class name.

    Matching by name keeps the translation robust without importing litellm's
    exception module (which is only present when the extra is installed).
    """
    name = type(exc).__name__
    if "RateLimit" in name:
        raise RateLimitError(str(exc)) from exc
    if "Context" in name or "LengthExceeded" in name:
        raise ContextWindowError(str(exc)) from exc
    if name in ("AuthenticationError", "BadRequestError", "NotFoundError") or (
        "Authentication" in name or "NotFound" in name or "Permission" in name
    ):
        raise ProviderError(str(exc)) from exc
    if "APIConnection" in name or "APIError" in name or "Timeout" in name:
        raise TransientHTTPError(str(exc)) from exc
    raise LLMError(f"litellm call failed: {exc}") from exc


class LiteLLMBackend:
    """The real ``LLMClient`` — LiteLLM + fallback chain + cost cap + repair.

    ``route`` is the extraction ``RoleRoute`` (primary→secondary→tertiary).
    When it is ``None`` (no provider configured yet) every call raises
    ``LLMError`` with a setup hint; the write path turns that into an honest
    llm→skip degrade.
    """

    def __init__(
        self,
        route: RoleRoute | None = None,
        *,
        use_native_structured: bool = False,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        max_retries: int = 2,
        base_delay_s: float = 0.5,
        max_delay_s: float = 8.0,
    ) -> None:
        self._route = route
        self._use_native_structured = use_native_structured
        self._timeout_s = timeout_s
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._base_delay_s = base_delay_s
        self._max_delay_s = max_delay_s

    # -- LLMClient protocol -------------------------------------------------

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        budget: BudgetContext | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> CompletionResult:
        """Free-form completion over the fallback chain.

        Propagates ``LLMError`` when the chain is exhausted — the complete-path
        caller expects text and must handle failure; ``extract`` is the
        degrade-to-skip path.
        """
        ctx = budget or BudgetContext()
        return self._complete_with_fallback(
            messages,
            ctx,
            schema_hint=None,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
        )

    def extract(
        self,
        content: str,
        schema_hint: type[BaseModel],
        *,
        budget: BudgetContext | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
        prompt_builder: Callable[[str, type[BaseModel]], Messages] | None = None,
    ) -> ExtractResult:
        """Structured extraction: pre-flight budget → prompt → validate → repair.

        ``prompt_builder`` is an optional custom prompt builder (default: the
        extraction prompt). It lets callers reuse the full pipeline — schema
        hint + repair + degrade + fallback chain + cost cap — with a different
        prompt (e.g. the distillation synthesis prompt).

        Never raises for a failed extraction — returns ``degraded_to_skip=True``
        with ``model_used=None`` so the write path degrades honestly instead of
        crashing.
        """
        ctx = budget or BudgetContext()
        timeout = timeout_s or self._timeout_s
        try:
            # Pre-flight cost gate: degrade when the primary's worst-case
            # estimate exceeds the remaining episode cap.
            if self._route is not None:
                try:
                    cost.check_budget(
                        self._route.primary,
                        _count_tokens(content),
                        max_tokens or self._max_tokens,
                        ctx,
                    )
                except BudgetExceeded:
                    ctx.last_degradation_reason = "budget_pre_flight_exceeded"
                    _logger.warning(
                        "llm.extract.preflight_skip cap_usd=%s", ctx.cap_usd
                    )
                    return ExtractResult(
                        data={}, prompt_hash="", degraded_to_skip=True
                    )
            return self._extract_with_repair(
                content, schema_hint, ctx, max_tokens, timeout, prompt_builder
            )
        except LLMError as exc:  # chain exhausted / pricing unknown / setup
            ctx.last_degradation_reason = f"llm_exception: {exc}"
            _logger.error("llm.extract.failed: %s", exc)
            return ExtractResult(
                data={}, prompt_hash="", degraded_to_skip=True,
                cost_usd=ctx.spent_usd,
            )

    # -- internals ----------------------------------------------------------

    def _extract_with_repair(
        self,
        content: str,
        schema_hint: type[BaseModel],
        ctx: BudgetContext,
        max_tokens: int | None,
        timeout_s: float,
        prompt_builder: Callable[[str, type[BaseModel]], Messages] | None = None,
    ) -> ExtractResult:
        builder = prompt_builder or build_extract_prompt
        first_messages = builder(content, schema_hint)
        messages = first_messages
        retries_total = 0
        for round in range(ctx.repair_budget + 1):  # 1 initial + repairs
            res = self._complete_with_fallback(
                messages, ctx, schema_hint=schema_hint,
                max_tokens=max_tokens, timeout_s=timeout_s,
            )
            retries_total += res.retries
            try:
                data = parse_and_validate(res.text, schema_hint)
                return ExtractResult(
                    data=data,
                    prompt_hash=hash_prompt(messages),  # effective prompt
                    model_used=res.model_used,
                    cost_usd=ctx.spent_usd,
                    confidence=None,
                    retries=retries_total,
                )
            except ExtractionValidationError as exc:
                if round >= ctx.repair_budget:
                    ctx.last_degradation_reason = "repair_exhausted"
                    _logger.warning(
                        "llm.extract.repair_exhausted model=%s", res.model_used
                    )
                    return ExtractResult(
                        data={},
                        prompt_hash=hash_prompt(first_messages),
                        degraded_to_skip=True,
                        cost_usd=ctx.spent_usd,
                        retries=retries_total,
                    )
                messages = build_repair_prompt(res.text, exc, schema_hint)
        raise LLMError("repair loop exhausted unexpectedly")  # pragma: no cover

    def _complete_with_fallback(
        self,
        messages: Sequence[Mapping[str, str]],
        ctx: BudgetContext,
        *,
        schema_hint: type[BaseModel] | None,
        max_tokens: int | None,
        timeout_s: float | None,
    ) -> CompletionResult:
        chain = self._route.chain() if self._route is not None else ()
        if not chain:
            raise LLMError(
                "no LLM route configured; run `seahorse init --llm` to pick a provider"
            )
        calls = 0

        def _one(model_id: str) -> CompletionResult:
            nonlocal calls
            calls += 1
            return self._complete_one(
                model_id, messages, ctx,
                schema_hint=schema_hint, max_tokens=max_tokens,
                timeout_s=timeout_s or self._timeout_s,
            )

        res = call_with_fallback(
            chain, _one,
            max_retries=self._max_retries,
            base_delay_s=self._base_delay_s,
            max_delay_s=self._max_delay_s,
        )
        return dataclasses.replace(res, retries=max(0, calls - 1))

    def _complete_one(
        self,
        model_id: str,
        messages: Sequence[Mapping[str, str]],
        ctx: BudgetContext,
        *,
        schema_hint: type[BaseModel] | None,
        max_tokens: int | None,
        timeout_s: float,
    ) -> CompletionResult:
        try:
            from litellm import (  # type: ignore[import-not-found]  # lazy: extra 'llm'
                completion,
            )
        except ImportError:
            raise LLMError(
                "LiteLLM is not installed; run `uv sync --extra llm` "
                "(or `uv pip install litellm` in this venv)"
            ) from None
        kwargs = self._kwargs_for(
            model_id, messages, schema_hint=schema_hint,
            max_tokens=max_tokens, timeout_s=timeout_s,
        )
        try:
            resp = completion(**kwargs)
        except Exception as exc:  # noqa: BLE001 — vendor error translation
            _translate_litellm_exc(exc)
        text = resp.choices[0].message.content
        usage = getattr(resp, "usage", None)
        tokens_in = getattr(usage, "prompt_tokens", 0) or 0
        tokens_out = getattr(usage, "completion_tokens", 0) or 0
        actual_cost = cost.record_actual_cost(ctx, model_id, tokens_in, tokens_out)
        provider = model_id.split("/", 1)[0]
        raw_model = getattr(resp, "model", None)
        if raw_model and raw_model.startswith(f"{provider}/"):
            model_used = raw_model  # litellm already prefixed it
        elif raw_model:
            model_used = f"{provider}/{raw_model}"  # bare id → normalize
        else:
            model_used = model_id
        return CompletionResult(
            text=text,
            prompt_hash=hash_prompt(messages),
            model_used=model_used,
            cost_usd=actual_cost,
            tokens_used=tokens_in + tokens_out,
        )

    def _kwargs_for(
        self,
        model_id: str,
        messages: Sequence[Mapping[str, str]],
        *,
        schema_hint: type[BaseModel] | None,
        max_tokens: int | None,
        timeout_s: float,
    ) -> dict[str, Any]:
        """Select the backend features. Default: plain prompt.

        Native ``json_schema`` is an OPT-IN optimization only when the provider
        advertises support (hard dependence is avoided); tool-use is reserved
        for a medium-term goal and not emitted here.
        """
        prov = resolve_provider(model_id)
        kwargs: dict[str, Any] = {
            "model": model_id,
            "messages": list(messages),
            "max_tokens": max_tokens or self._max_tokens,
            "timeout": timeout_s,
        }
        if (
            self._use_native_structured
            and prov.supports_json_schema
            and schema_hint is not None
        ):
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_hint.__name__,
                    "schema": schema_hint.model_json_schema(),
                },
            }
        return kwargs


__all__ = ["LiteLLMBackend"]
