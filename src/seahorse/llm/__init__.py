"""Multi-LLM layer — the single frontier between the core and LLMs.

The signed ``LLMClient`` contract plus the result/budget dataclasses (frozen
from the first release), and the current materialization: providers registry,
role routing, cost cap, parser/validator, retry/fallback chain and the LiteLLM
backend. The write path imports the ``LLMClient`` Protocol from here and never
touches a vendor SDK; everything else is the LLM layer's internals or config
surface (providers/routing/cost are also re-exported for the CLI wizard and
`seahorse doctor`).
"""

from __future__ import annotations

from seahorse.llm.cost import (
    PRICING,
    PricePerMTok,
    check_budget,
    estimate_cost,
    record_actual_cost,
)
from seahorse.llm.errors import (
    BudgetExceeded,
    ContextWindowError,
    ExtractionValidationError,
    LLMError,
    ProviderError,
    RateLimitError,
    TransientHTTPError,
)
from seahorse.llm.lite_llm_backend import LiteLLMBackend
from seahorse.llm.providers import PROVIDERS, ProviderConfig, resolve_provider
from seahorse.llm.routing import RoleRoute
from seahorse.llm.types import (
    BudgetContext,
    CompletionResult,
    ExtractResult,
    LLMClient,
    StubLLMClient,
)

__all__ = [
    # Contract.
    "LLMClient",
    "CompletionResult",
    "ExtractResult",
    "BudgetContext",
    "StubLLMClient",
    "LiteLLMBackend",
    # Errors.
    "LLMError",
    "ProviderError",
    "RateLimitError",
    "TransientHTTPError",
    "ContextWindowError",
    "BudgetExceeded",
    "ExtractionValidationError",
    # Providers / routing / cost.
    "ProviderConfig",
    "PROVIDERS",
    "resolve_provider",
    "RoleRoute",
    "PricePerMTok",
    "PRICING",
    "estimate_cost",
    "record_actual_cost",
    "check_budget",
]