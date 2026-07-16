"""#5 ``decide_path`` — the pure, LLM-free write-path decision (SO-5b).

The decision of which ingestion path to take (``skip`` vs ``llm``) is a pure
function of deterministic inputs. It NEVER spends an LLM call: using an LLM to
decide whether to use an LLM would be paradoxical and non-deterministic
(f5-05 §2).

MVP-0 authoritativeness:
- ``extraction_mode`` flag (caller) — authoritative.
- ``source_type == 'importer'`` guard — authoritative (importers carry F3.3-
  migrated frontmatter, so they always skip).
- ``cognitive_type`` + size/density heuristics — advisory, logged only (MVP-1).

The first-class decision tree of #5 (``is_valid_skip_path`` formal gate +
``deterministic_extract`` fallback) is MVP-1 (SO-5b). MVP-0 operates the skip
path directly via the F3.3 migrador frontier — ``run_skip_path`` just builds
the effective provenance and delegates to ``engine.remember``.

References:
- f6-signoffs.md SO-5b (decide_path + run_skip_path real from MVP-0)
- f5-05-skip-extraction.md §2 (decision inputs), §4.2 (authoritativeness table)
- seahorse/facade/types.py (RememberPayload — the decision input shape)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from seahorse.facade.types import ExtractionMode, RememberPayload

Path = Literal["skip", "llm"]

_VALID_MODES: frozenset[str] = frozenset({"skip", "llm"})
# Reserved modes refused in MVP-0/MVP-1 (over-reach vs ADR-09; f5-05 §4.2).
_RESERVED_MODES: frozenset[str] = frozenset({"llm_partial", "consolidated"})


class InvalidExtractionMode(ValueError):
    """Raised by ``decide_path`` for an unsupported ``extraction_mode`` value.

    ``llm_partial`` / ``consolidated`` are reserved (MVP-1+) and refused loud
    rather than silently dropped to skip (ADR-10 honesty).
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
    the path, e.g. importer→skip with ``requested_mode='llm'``); ``reason``
    explains why the path was chosen / degraded (observability for #5/#13).
    """

    path: Path
    requested_mode: ExtractionMode
    reason: str | None = None


def _validate_mode(mode: str) -> None:
    if mode not in _VALID_MODES:
        raise InvalidExtractionMode(mode)


def decide_path(payload: RememberPayload, extraction_mode: ExtractionMode) -> PathDecision:
    """Decide the ingestion path deterministically (no LLM, no I/O).

    Pure function: same inputs → same output. Reads ONLY the payload's
    ``source_type`` and the ``extraction_mode`` flag.
    """
    _validate_mode(extraction_mode)
    source_type = payload.by.get("source_type")
    if source_type == "importer":
        # Authoritative importer guard (f5-05 §4.2): importers always skip.
        return PathDecision(
            path="skip", requested_mode=extraction_mode, reason="importer_skip_guard"
        )
    if extraction_mode == "skip":
        return PathDecision(path="skip", requested_mode="skip", reason="flag_skip")
    return PathDecision(path="llm", requested_mode="llm", reason="flag_llm")


__all__ = ["PathDecision", "Path", "InvalidExtractionMode", "decide_path"]