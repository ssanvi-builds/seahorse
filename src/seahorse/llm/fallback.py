"""Retry + fallback chain — transient-error resilience.

Two layers:

- ``call_with_retry`` — exponential backoff + jitter for ONE backend call.
  Retries the TRANSIENT family (``TimeoutError``, ``RateLimitError``,
  ``TransientHTTPError``); content and permanent errors propagate immediately
  (they would fail identically on retry).
- ``call_with_fallback`` — walks the role's ``primary → secondary → tertiary``
  chain, retrying each model, and moves on when a backend is exhausted.
  ``ContextWindowError`` is treated like a transient here (move to a
  bigger-context backend or local Ollama). When the whole chain is exhausted
  it raises ``LLMError``; the extractor turns that into ``degraded_to_skip``
  so the write path never crashes on LLM failure.

The jitter keeps a fleet of machines from hammering a rate-limited endpoint in
lockstep. Retry budgets are small by design: max_retries=2 per call, and one
repair per model — a model that already failed its repair moves on.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from functools import partial
from typing import ParamSpec, TypeVar

from seahorse.llm.errors import (
    ContextWindowError,
    LLMError,
    RateLimitError,
    TransientHTTPError,
)

_logger = logging.getLogger("seahorse.llm.fallback")

# Transient family: retry with backoff. ContextWindowError is added at the
# fallback layer but NOT retried in-place — see below.
_TRANSIENT = (TimeoutError, RateLimitError, TransientHTTPError)

T = TypeVar("T")
P = ParamSpec("P")


def call_with_retry(
    fn: Callable[[], T],
    *,
    max_retries: int = 2,
    base_delay_s: float = 0.5,
    max_delay_s: float = 8.0,
    retry_on: tuple[type[BaseException], ...] = _TRANSIENT,
) -> T:
    """Call ``fn`` with exponential backoff + jitter on transient failures.

    Raises the last transient exception when ``max_retries`` are exhausted
    (the fallback layer decides whether to try the next backend). Content and
    permanent errors propagate immediately.
    """
    last_exc: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except retry_on as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            delay = min(base_delay_s * (2**attempt), max_delay_s)
            delay *= 0.5 + random.random() * 0.5  # jitter: desync a fleet
            _logger.warning(
                "llm.transient_retry attempt=%d delay=%.2fs err=%s",
                attempt + 1,
                delay,
                exc,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def call_with_fallback(
    route_chain: tuple[str, ...],
    call_one: Callable[[str], T],
    *,
    max_retries: int = 2,
    base_delay_s: float = 0.5,
    max_delay_s: float = 8.0,
) -> T:
    """Walk ``primary → secondary → tertiary``, retrying each backend.

    A backend that exhausts its retries OR raises ``ContextWindowError`` moves
    the chain on. When every backend is exhausted, raises ``LLMError`` with the
    last error — the extractor converts this to ``degraded_to_skip``.
    """
    last_err: Exception | None = None
    for model_id in route_chain:
        try:
            return call_with_retry(
                partial(call_one, model_id),  # bind now, not late
                max_retries=max_retries,
                base_delay_s=base_delay_s,
                max_delay_s=max_delay_s,
            )
        except (TimeoutError, RateLimitError, TransientHTTPError,
                ContextWindowError) as exc:
            last_err = exc
            _logger.warning(
                "llm.backend_exhausted model=%s err=%s", model_id, exc
            )
            continue
    raise LLMError(f"All backends exhausted: {last_err}")


__all__ = ["call_with_fallback", "call_with_retry"]
