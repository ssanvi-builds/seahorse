"""Multi-LLM client contract — frozen from the first release.

The write path imports this contract so the ``llm`` extraction path compiles
and is testable without a real LLM. The first release never calls it:
``StubWritePath`` degrades ``llm``→``skip`` and ``StubLLMClient`` raises
``NotImplementedError``.

Materialization note: the signed contract uses Pydantic ``BaseModel``. The
result/budget types are materialized as stdlib ``@dataclass`` (the field sets
are identical to the signed contract; this mirrors ``contracts/episode.py``,
where the engine signs a Pydantic model and persistence materializes it as a
dataclass). ``LLMClient.extract`` reconciles ``schema_hint`` to
``type[BaseModel]`` — the earlier materialization typed it ``str``, a drift
corrected when the real client landed. Pydantic is already a core dependency,
so this does not relax the dependency posture.

``BudgetContext`` is the documented immutability exception: a mutable execution
accumulator that advances across retries (``spent_usd`` / ``tokens_spent`` via
``record_actual_cost``). It is execution state, not domain data.

Sync/async boundary: this contract is deliberately SYNC (``complete`` /
``extract`` return results, not coroutines). The real LLM adapter will almost
certainly be async under the hood (HTTP LLM clients are async-first), so the
adapter owns the async→sync bridge — it runs its event loop internally and
returns a plain ``CompletionResult`` / ``ExtractResult``. Keeping the boundary
sync means the write path (``StubWritePath`` / the future real write-path)
calls ``extract`` synchronously with no ``await`` ripple: the facade, the MCP
server and the CLI stay sync and do not need to become async just because the
LLM client is. The cost of the bridge (a thread or a ``loop.run_until_complete``)
is encapsulated in the LLM layer and is invisible to every caller above. This is
the single-point swap the sync boundary preserves: when the real adapter ships,
only the adapter changes; the write path and everything above it are untouched.
The first release never reaches this boundary — ``StubWritePath`` degrades
``llm``→``skip`` before ``StubLLMClient`` is ever called — but the contract is
frozen now so the swap is a single component's work, not a cross-cutting async
refactor.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

# The message shape the prompt builders produce (system/user turns).
Messages = Sequence[Mapping[str, str]]


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

    Field set matches the signed contract. ``model_used`` is ``None`` when
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
    is exhausted (no silent overspend).
    """

    cap_usd: float = 0.002
    spent_usd: float = 0.0
    token_budget: int = 8000
    tokens_spent: int = 0
    repair_budget: int = 2
    fallback_to_skip: bool = True
    # Extra execution state (additive, non-breaking): retry counter and the
    # reason of the last degradation, for observability by the write path and
    # the MCP server.
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
    """LLM client contract (signed, stable across releases).

    ``complete`` is the free-form completion entry; ``extract`` is the
    structured fact extraction used by the write path. Both take an optional
    ``BudgetContext`` so the caller can bound cost. The ``StubLLMClient`` stub
    refuses both — the ``llm`` path is a later release.
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
        schema_hint: type[BaseModel],
        *,
        role: str = "extraction",
        budget: BudgetContext | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
        prompt_builder: Callable[[str, type[BaseModel]], Messages] | None = None,
    ) -> ExtractResult: ...


class StubLLMClient:
    """First-release stub for ``LLMClient`` — refuses both methods.

    The ``llm`` extraction path is not implemented in the first release.
    ``StubWritePath`` degrades ``extraction_mode='llm'`` to the deterministic
    skip-path before this is ever called, so a raise here is a fail-loud
    backstop: if a caller reaches the stub directly, it learns the path is not
    yet implemented rather than getting silent garbage.
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
            "LLM completion not implemented in the current release. "
            "Use extraction_mode='skip' (default). The LLM path is a later release."
        )

    def extract(
        self,
        content: str,
        schema_hint: type[BaseModel],
        *,
        role: str = "extraction",
        budget: BudgetContext | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
        prompt_builder: Callable[[str, type[BaseModel]], Messages] | None = None,
    ) -> ExtractResult:
        raise NotImplementedError(
            "LLM extraction not implemented in the current release. "
            "Use extraction_mode='skip' (default). The LLM path is a later release."
        )


__all__ = [
    "LLMClient",
    "CompletionResult",
    "ExtractResult",
    "BudgetContext",
    "StubLLMClient",
]