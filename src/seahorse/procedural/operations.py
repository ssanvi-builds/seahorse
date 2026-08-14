"""``record_procedure`` — deterministic skill creation.

A skill is an ``Episode`` in the canonical format with
``cognitive_type=procedural``. The agent or human writes the skill with
``extraction_mode=skip`` — zero LLM in the write path, cost ≈ 0. The canonical
body (``## Trigger`` / ``## Steps`` / ``## Validation`` / ``## Rationale``) is
validated BEFORE the facade call so a malformed skill never reaches storage
(fail-loud).

The procedural layer is a client of the primitives facade (MemoryFacade): it
delegates to ``facade.remember`` and never reaches the engine directly
(delegation purity — the engine only ever sees a ``RememberPayload``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from seahorse.facade.facade import MemoryFacade
from seahorse.facade.types import Provenance, RememberPayload
from seahorse.procedural.config import CANONICAL_SECTIONS, X_METADATA_KEYS


class ProceduralError(Exception):
    """A procedural-skill validation failure (canonical body, metadata)."""


def _validate_canonical_body(body: str) -> None:
    """Validate the canonical SKILL.md body.

    All four sections must be present. The canonical body is load-bearing: a
    skill whose body is not canonical cannot be reliably gated or versioned
    (supersession), so ``record_procedure`` refuses it before any write.
    """
    if not body or not body.strip():
        raise ProceduralError("skill body must be non-empty")
    missing = [s for s in CANONICAL_SECTIONS if f"## {s}" not in body]
    if missing:
        raise ProceduralError(
            f"skill body missing canonical sections: {', '.join(missing)}"
        )


def record_procedure(
    facade: MemoryFacade,
    *,
    body: str,
    by: Provenance,
    title: str | None = None,
    summary: str | None = None,
    valid_at: datetime | None = None,
    trigger: str | None = None,
    scope: str | None = None,
    version: str | None = None,
    now: datetime | None = None,
) -> Any:
    """Create a procedural skill deterministically (skip-first, near-zero cost).

    Validates the canonical body, then delegates to ``facade.remember`` with
    ``cognitive_type=procedural`` and ``extraction_mode=skip`` (cost ≈ 0). The
    ``x-*`` metadata (``x-seahorse-skill-trigger`` / ``-scope`` / ``-version``)
    is stored in provenance when provided — advisory; the versioning truth is
    ``supersedes`` (supersession), not the version field.

    Returns the facade ``WriteResult`` verbatim.
    """
    _validate_canonical_body(body)
    effective_by: dict[str, Any] = dict(by)
    for key, value in (
        ("x-seahorse-skill-trigger", trigger),
        ("x-seahorse-skill-scope", scope),
        ("x-seahorse-skill-version", version),
    ):
        if value is not None:
            effective_by[key] = value
    payload = RememberPayload(
        body=body,
        by=effective_by,  # type: ignore[arg-type]
        cognitive_type="procedural",
        title=title,
        summary=summary,
        valid_at=valid_at,
    )
    return facade.remember(payload, extraction_mode="skip", now=now)


__all__ = ["ProceduralError", "record_procedure", "X_METADATA_KEYS"]
