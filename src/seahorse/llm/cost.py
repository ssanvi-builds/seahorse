"""Cost cap — the operational ≤ $0.002/episode gate.

The target applies to the LLM path with an episode ≤5KB; the skip path is zero
LLM and ~$0. The cap is OPERATIVE, not declarative: the LLM layer is stateless
between episodes, but inside one episode the caller (the write path) injects a
``BudgetContext`` that accumulates spend. ``check_budget`` runs pre-flight
(estimate based on price × max_tokens, worst-case cache-miss); the real cost
is known post-call and accumulated via ``record_actual_cost``.

Known limitation of the first release: the cap is pre-flight best-effort — a
long output can exceed the remaining cap without abort mid-stream. The excess
is recorded post-call and the NEXT call of the same episode is blocked.
Streaming abort is a medium-term goal.

Local and free-tier cloud models price at ``$0`` (the 2026-08-04 free-tier
decision): ``estimate_cost`` returns ``0.0`` and the cap never fires. The paid
cloud rows are the cache-miss prices verified in July 2026.
"""

from __future__ import annotations

from dataclasses import dataclass

from seahorse.llm.errors import BudgetExceeded, LLMError
from seahorse.llm.types import BudgetContext


@dataclass(frozen=True)
class PricePerMTok:
    """Cache-miss USD prices per million tokens (worst case for budgeting).

    ``input_usd`` is the cache-miss input price; ``output_usd`` the output
    price. Cache hits are a bonus, never a budgeting assumption.
    """

    input_usd: float
    output_usd: float


PRICING: dict[str, PricePerMTok] = {
    # Local (Ollama) — always $0. These are the factory-default models.
    "ollama/qwen3:0.6b": PricePerMTok(0.0, 0.0),
    "ollama/qwen3:1.7b": PricePerMTok(0.0, 0.0),
    "ollama/qwen3:4b": PricePerMTok(0.0, 0.0),
    "ollama/qwen3:8b": PricePerMTok(0.0, 0.0),
    # Free-tier cloud providers (2026-08-04) — $0 on their free tiers.
    "gemini/gemini-2.5-flash": PricePerMTok(0.0, 0.0),
    "groq/llama-3.3-70b-versatile": PricePerMTok(0.0, 0.0),
    "openrouter/deepseek/deepseek-r1:free": PricePerMTok(0.0, 0.0),
    # Paid cloud (verified Jul 2026, cache-miss).
    "anthropic/claude-haiku-4-5": PricePerMTok(1.00, 5.00),
    "anthropic/claude-sonnet-4-6": PricePerMTok(3.00, 15.00),
    "openai/gpt-5-mini": PricePerMTok(0.25, 2.00),
    "deepseek/deepseek-chat": PricePerMTok(0.14, 0.28),
}


# Local providers are ALWAYS $0 by convention (the user pays their own
# electricity, not per token): ANY model id under these prefixes prices at
# zero without a PRICING row — the user can pull qwen3:14b or qwen2.5:7b and
# the cost cap must not crash on an unlisted model (smoke-tested 2026-08-04).
_LOCAL_ZERO_PROVIDERS = frozenset({"ollama", "vllm"})


def _price_for(model_id: str) -> PricePerMTok:
    """Resolve the price row: explicit PRICING entry, else $0 if local.

    Cloud models MUST have a PRICING row (a config typo / unlisted paid model
    surfaces here as ``LLMError``, fail loud — never a silent $0 budget).
    """
    p = PRICING.get(model_id)
    if p is not None:
        return p
    provider = model_id.split("/", 1)[0]
    if provider in _LOCAL_ZERO_PROVIDERS:
        return PricePerMTok(0.0, 0.0)
    raise LLMError(f"No pricing for {model_id}; add to PRICING table")


def estimate_cost(model_id: str, tokens_in: int, max_tokens_out: int) -> float:
    """Pre-flight worst-case estimate (cache-miss) for one call.

    Raises ``LLMError`` when a NON-local model has no pricing row — a config
    typo surfaces here (fail loud), not as a silent $0 budget.
    """
    p = _price_for(model_id)
    return (tokens_in * p.input_usd + max_tokens_out * p.output_usd) / 1_000_000


def record_actual_cost(
    ctx: BudgetContext, model_id: str, tokens_in: int, tokens_out: int
) -> float:
    """Post-call: compute the real cost and accumulate it into ``ctx``.

    Returns the call's USD cost (the caller may log it); the accumulated
    ``ctx.spent_usd`` / ``ctx.tokens_spent`` drive the next ``check_budget``.
    """
    p = _price_for(model_id)
    cost = (tokens_in * p.input_usd + tokens_out * p.output_usd) / 1_000_000
    ctx.record_actual_cost(cost_usd=cost, tokens=tokens_in + tokens_out)
    return cost


def check_budget(
    model_id: str,
    estimated_tokens_in: int,
    max_tokens_out: int,
    ctx: BudgetContext,
) -> None:
    """Pre-flight gate. Raises ``BudgetExceeded`` when the estimate exceeds the
    remaining cap, so the caller can degrade to skip (fail-loud honesty).
    """
    remaining = ctx.cap_usd - ctx.spent_usd
    est = estimate_cost(model_id, estimated_tokens_in, max_tokens_out)
    if est > remaining:
        raise BudgetExceeded(
            f"estimated {est:.6f} > remaining {remaining:.6f}; degrade to skip"
        )


__all__ = [
    "PricePerMTok",
    "PRICING",
    "estimate_cost",
    "record_actual_cost",
    "check_budget",
]
