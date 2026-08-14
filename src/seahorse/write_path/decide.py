"""``decide_path`` — the pure, LLM-free write-path decision.

The decision of which ingestion path to take (``skip`` vs ``llm``) is a pure
function of deterministic inputs. It NEVER spends an LLM call: using an LLM to
decide whether to use an LLM would be paradoxical and non-reproducible.

Authoritative decision rules:
- ``extraction_mode`` flag (caller) — authoritative for ``source_type == 'agent'``.
- ``source_type`` guard — authoritative: ONLY ``agent`` may spend an LLM call.
  Every non-agent value (``human`` / ``importer`` / ``system``, and any
  unknown/missing value as defense-in-depth) forces ``skip``. The facade rejects
  out-of-vocabulary ``source_type`` at its boundary; decide_path's unknown->skip
  is a belt-and-braces backstop, never the primary enforcement.
- ``cognitive_type`` + size/density heuristics — advisory, logged only, NEVER
  override the flag.

The ``is_valid_skip_path`` formal gate + ``deterministic_extract`` fallback
land in ``run_skip_path``; this module owns the decision only.

References:
- seahorse/facade/types.py (RememberPayload — the decision input shape)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from seahorse.facade.types import ExtractionMode, RememberPayload

Path = Literal["skip", "llm"]

_VALID_MODES: frozenset[str] = frozenset({"skip", "llm"})
# Reserved for the single-episode write path. ``llm_partial`` is fully reserved
# (not schema-valid). ``consolidated`` IS schema-valid and round-trippable
# (batch-distillation marker) but NOT routable here: the batch distillation
# (``distill_episodes``, a later milestone) writes via ``engine.remember``
# directly, bypassing ``decide_path``. Refusing both loudly is fail-loud honesty
# — a single-episode ingest can never honor them.
_RESERVED_MODES: frozenset[str] = frozenset({"llm_partial", "consolidated"})

# Non-agent source_types force skip. Only ``agent`` may take the llm path. Each
# known non-agent value carries a distinct reason for observability; any
# unknown/missing value falls back to ``non_agent_skip_guard`` (defense-in-depth
# — the facade is the primary source_type-vocabulary enforcer).
_SOURCE_GUARD_REASONS: dict[str, str] = {
    "importer": "importer_skip_guard",
    "human": "human_skip_guard",
    "system": "system_skip_guard",
}
_NON_AGENT_FALLBACK_REASON = "non_agent_skip_guard"

# Near-zero-cost LLM target for the skip-vs-llm advisory.
_LLM_COST_TARGET_BYTES = 5120  # 5 KB

_logger = logging.getLogger("seahorse.write_path.decide")


class InvalidExtractionMode(ValueError):
    """Raised by ``decide_path`` for an unsupported ``extraction_mode`` value.

    ``llm_partial`` is fully reserved; ``consolidated`` is schema-valid
    (round-trippable) but NOT routable by the single-episode write path — the
    batch distillation writes via ``engine.remember`` directly, bypassing
    ``decide_path``. Both are refused loudly rather than silently dropped to
    skip (fail-loud honesty).
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode
        super().__init__(
            f"extraction_mode={mode!r} is not supported "
            f"(valid: {'|'.join(sorted(_VALID_MODES))}; "
            f"reserved: {'|'.join(sorted(_RESERVED_MODES))})"
        )


@dataclass(frozen=True)
class PathDecision:
    """The deterministic path decision returned by ``decide_path``.

    ``path`` is the chosen ingestion path (``skip`` | ``llm``); ``requested_mode``
    is the caller's flag (preserved even when an authoritative guard overrides
    the path, e.g. importer->skip with ``requested_mode='llm'``); ``reason``
    explains why the path was chosen / degraded (observability for the write
    path and the MCP server).
    """

    path: Path
    requested_mode: ExtractionMode
    reason: str | None = None


def _validate_mode(mode: str) -> None:
    if mode not in _VALID_MODES:
        raise InvalidExtractionMode(mode)


def _looks_technical(line: str) -> bool:
    """Heuristic: a line looks technical (code/config/ADR) vs prose."""
    s = line.strip()
    if not s:
        return False
    technical_chars = set("=:;{}[]<>()&|/\\#@$%^*+-")
    if any(c in technical_chars for c in s):
        return True
    # Prose lines end in sentence punctuation; technical lines usually don't.
    return not s.endswith((".", "?", "!"))


def _density_proxy(body: str) -> Literal["dense", "prose"]:
    """Advisory density proxy: >50% technical lines => ``dense``."""
    if not body:
        return "prose"
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if not lines:
        return "prose"
    technical = sum(1 for ln in lines if _looks_technical(ln))
    return "dense" if technical / len(lines) > 0.5 else "prose"


def _advise_heuristics(payload: RememberPayload) -> None:
    """Log advisory warnings for the agent+llm case; NEVER override the flag.

    Fires only when the caller reached this point (``source_type == 'agent'``
    and ``mode == 'llm'``). The heuristics are reproducible (deterministic over
    ``payload.body``); they emit ``logging.warning`` only.
    """
    body = payload.body
    if len(body) > _LLM_COST_TARGET_BYTES:
        _logger.warning(
            "skip recommended: body is %d bytes (>5KB), exceeds the $0.002/ep "
            "LLM cost target",
            len(body),
        )
    if _density_proxy(body) == "dense":
        _logger.warning(
            "skip recommended: body looks dense (code/config/ADR), LLM "
            "extraction likely low-value"
        )


def decide_path(payload: RememberPayload, extraction_mode: ExtractionMode) -> PathDecision:
    """Decide the ingestion path deterministically (no LLM, no I/O).

    Pure function over its inputs: same ``(payload, extraction_mode)`` -> same
    ``PathDecision``. Reads ONLY the payload's ``source_type`` + ``body`` and the
    ``extraction_mode`` flag. Advisory heuristics emit reproducible
    ``logging.warning`` records (observable, not mutating).
    """
    _validate_mode(extraction_mode)
    source_type = payload.by.get("source_type")
    if source_type == "agent":
        # Only agent may spend an LLM call; the flag is authoritative here.
        if extraction_mode == "skip":
            return PathDecision(path="skip", requested_mode="skip", reason="flag_skip")
        _advise_heuristics(payload)
        return PathDecision(path="llm", requested_mode="llm", reason="flag_llm")
    # Non-agent (human/importer/system, or unknown/missing) -> skip.
    guard_reason = (
        _SOURCE_GUARD_REASONS.get(source_type)
        if isinstance(source_type, str)
        else None
    )
    return PathDecision(
        path="skip",
        requested_mode=extraction_mode,
        reason=guard_reason or _NON_AGENT_FALLBACK_REASON,
    )


__all__ = ["PathDecision", "Path", "InvalidExtractionMode", "decide_path"]