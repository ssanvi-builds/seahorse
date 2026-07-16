"""#4 Multi-LLM client contract (frozen MVP-0, SO-5a).

Re-exports the signed ``LLMClient`` seam, the result/budget dataclasses, and
the MVP-0 ``StubLLMClient``. ``#5`` imports the client contract from here; the
real client lands in MVP-1.
"""

from __future__ import annotations

from seahorse.llm.types import (
    BudgetContext,
    CompletionResult,
    ExtractResult,
    LLMClient,
    StubLLMClient,
)

__all__ = [
    "LLMClient",
    "CompletionResult",
    "ExtractResult",
    "BudgetContext",
    "StubLLMClient",
]