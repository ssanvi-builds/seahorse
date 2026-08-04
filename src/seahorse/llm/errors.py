"""#4 error taxonomy (f5-04 §4.4) — retry vs content vs permanent.

The fallback chain and the repair loop need to classify a failure before
deciding what to do with it:

- **Transient** (``TimeoutError``, ``RateLimitError``, ``TransientHTTPError``,
  ``ContextWindowError``) → retry with exponential backoff + jitter, then move
  to the next backend in the chain (``fallback.py``).
- **Content** (``ExtractionValidationError``) → the model output failed to
  parse/validate; the repair loop re-prompts (1 repair per model, f5-04 §4.4
  "repair_per_model_limit"), then falls back or degrades to skip.
- **Permanent** (``ProviderError``, unknown model/config) → no retry; move to
  the next backend, never retry the same call.
- **Budget** (``BudgetExceeded``) → pre-flight cost gate (ADR-09 ≤ $0.002/ep);
  the caller decides truncate-via-progressive-disclosure or degrade to skip.

The ``TimeoutError`` retry case reuses the stdlib ``TimeoutError`` rather than
a #4 subclass — it is already a builtin and ``call_with_retry`` cazes it by
identity with the transient family.

References:
- f5-04-multi-llm.md §4.4 (error/retry matrix), §4.1 (cost cap)
- f5-04-multi-llm.md §4.3 (fallback chain exception set)
"""

from __future__ import annotations


class LLMError(Exception):
    """Base error for the #4 Multi-LLM layer. All backend failures derive."""


class ProviderError(LLMError):
    """Permanent provider failure: 401/403, unknown model, invalid config.

    Not retryable — the same call would fail identically. The fallback chain
    moves to the next backend without retrying (f5-04 §4.4 "Permanente").
    """


class RateLimitError(LLMError):
    """Transient 429 / rate-limit hit. Safe to retry with backoff + jitter."""


class TransientHTTPError(LLMError):
    """Transient 5xx / network failure. Safe to retry with backoff + jitter."""


class ContextWindowError(LLMError):
    """Input + max output exceeds the provider context window.

    Treated like ``ProviderError`` in the fallback chain: move to the next
    backend (typically one with a larger context or local Ollama) or degrade to
    skip (f5-04 §4.3). A token-count pre-check against
    ``ProviderConfig.max_context_tokens`` prevents it when possible.
    """


class BudgetExceeded(LLMError):
    """Pre-flight cost estimate exceeds the remaining episode cap (ADR-09).

    Raised by ``cost.check_budget``. The caller (#5) decides: truncate via
    progressive disclosure and retry, or degrade to skip. Not a provider error.
    """


class ExtractionValidationError(LLMError):
    """Model output did not parse/validate against the ``schema_hint``.

    Content error (not retryable as-is): the repair loop re-prompts with the
    validation error (1 repair per model, f5-04 §4.4). When the repair budget
    is exhausted the backend returns ``ExtractResult(degraded_to_skip=True)``
    instead of raising — the write path never crashes on bad model output.
    """


__all__ = [
    "LLMError",
    "ProviderError",
    "RateLimitError",
    "TransientHTTPError",
    "ContextWindowError",
    "BudgetExceeded",
    "ExtractionValidationError",
]
