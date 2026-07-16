"""#4 Multi-LLM client contract — frozen from MVP-0 (SO-5a).

#5 (write-path) imports this contract so the ``llm`` extraction path compiles
and is testable without a real LLM. MVP-0 never calls it: ``StubWritePath``
degrades ``llm``→``skip`` and ``StubLLMClient`` raises ``NotImplementedError``.

Materialization note: the signed contract (f6-signoffs SO-5a) uses Pydantic
``BaseModel``. This MVP-0 materialization uses stdlib ``@dataclass`` to keep
the project runtime-dep-free (``dependencies = []``). The field sets are
identical to the signed contract; this mirrors ``contracts/episode.py``, where
#1 signs a Pydantic model and #6 materializes it as a dataclass. When #4 ships
the real client it may adopt Pydantic; the frontier shape (fields) is what is
signed and stable.

``BudgetContext`` is the documented immutability exception: a mutable execution
accumulator that advances across retries (``spent_usd`` / ``tokens_spent`` via
``record_actual_cost``). It is execution state, not domain data.

References:
- f6-signoffs.md SO-5a (signed contract — LLMClient, ExtractResult, BudgetContext, StubLLMClient)
- f5-04-multi-llm.md (Multi-LLM extraction design)
- seahorse/write_path/ (#5 — the consumer; StubWritePath degrades llm→skip)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class CompletionResult:
    """Result of ``LLMClient.complete`` (free-form completion)."""

    text: str
    prompt_hash: str
    model_used: str | None = None
    cost_usd: float = 0.0
    tokens_used: int = 0
    retries: int = 0


@dataclass(frozen=True)
class ExtractResult:
    """Result of ``LLMClient.extract`` (structured fact extraction).

    Field set is the signed SO-5a contract. ``model_used`` is ``None`` when
    ``degraded_to_skip`` is ``True`` (the write-path fell back to the
    deterministic skip-path).
    """

    data: dict[str, Any]
    prompt_hash: str
    model_used: str | None = None
    degraded_to_skip: bool = False
    cost_usd: float = 0.0
    confidence: float | None = None
    retries: int = 0


@dataclass
class BudgetContext:
    """Execution-time budget accumulator (mutable — documented exception).

    Advances across retries via ``record_actual_cost``. ``fallback_to_skip=True``
    lets the write-path degrade to the deterministic skip-path when the budget
    is exhausted (MVP-0 honesty: no silent overspend).
    """

    cap_usd: float = 0.002
    spent_usd: float = 0.0
    token_budget: int = 8000
    tokens_spent: int = 0
    repair_budget: int = 2
    fallback_to_skip: bool = True
    # Extra execution state (non-signed, additive, non-breaking): retry counter
    # and the reason of the last degradation, for observability by #5/#13.
    retries_used: int = 0
    last_degradation_reason: str | None = field(default=None)

    def record_actual_cost(self, *, cost_usd: float = 0.0, tokens: int = 0) -> None:
        """Accumulate the actual cost of one LLM call into the running totals."""
        self.spent_usd += cost_usd
        self.tokens_spent += tokens

    def would_exceed(self, *, cost_usd: float = 0.0, tokens: int = 0) -> bool:
        """Whether a prospective call would breach the USD or token cap."""
        return (self.spent_usd + cost_usd > self.cap_usd) or (
            self.tokens_spent + tokens > self.token_budget
        )


@runtime_checkable
class LLMClient(Protocol):
    """LLM client seam (signed SO-5a, stable MVP-0 → MVP-1).

    ``complete`` is the free-form completion entry; ``extract`` is the
    structured fact extraction used by the #5 write-path. Both take an optional
    ``BudgetContext`` so the caller can bound cost. MVP-0's ``StubLLMClient``
    refuses both — the ``llm`` path is MVP-1.
    """

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        role: str = "extraction",
        budget: BudgetContext | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> CompletionResult: ...

    def extract(
        self,
        content: str,
        schema_hint: str,
        *,
        role: str = "extraction",
        budget: BudgetContext | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> ExtractResult: ...


class StubLLMClient:
    """MVP-0 stub for ``LLMClient`` — refuses both methods.

    The ``llm`` extraction path is not implemented in MVP-0. ``StubWritePath``
    degrades ``extraction_mode='llm'`` to the deterministic skip-path before
    this is ever called, so a raise here is a fail-loud backstop: if a caller
    reaches the stub directly, it learns the path is MVP-1 rather than getting
    silent garbage.
    """

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        role: str = "extraction",
        budget: BudgetContext | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> CompletionResult:
        raise NotImplementedError(
            "LLM completion not implemented in MVP-0. "
            "Use extraction_mode='skip' (default). The LLM path is MVP-1."
        )

    def extract(
        self,
        content: str,
        schema_hint: str,
        *,
        role: str = "extraction",
        budget: BudgetContext | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> ExtractResult:
        raise NotImplementedError(
            "LLM extraction not implemented in MVP-0. "
            "Use extraction_mode='skip' (default). The LLM path is MVP-1."
        )


__all__ = [
    "LLMClient",
    "CompletionResult",
    "ExtractResult",
    "BudgetContext",
    "StubLLMClient",
]