"""Procedural skill format documentation.

A skill is an ``Episode`` in the canonical format with
``cognitive_type=procedural`` — NO schema change. The body is a canonical
SKILL.md-style markdown with fixed sections; machine-readable metadata lives in
``x-*`` keys. The source of truth for versioning is ``supersedes``
(supersession), NOT the advisory ``x-seahorse-skill-version`` field.
"""

from __future__ import annotations

from dataclasses import dataclass

# Canonical SKILL.md body sections. ``record_procedure`` validates that ALL four
# are present — the canonical body is load-bearing (a skill whose body is not
# canonical cannot be reliably gated or versioned).
CANONICAL_SECTIONS: tuple[str, ...] = (
    "Trigger",
    "Steps",
    "Validation",
    "Rationale",
)

# Machine-readable skill metadata (advisory; the versioning truth is
# ``supersedes``). Stored in ``Episode.provenance`` as ``x-*`` keys so the
# frontmatter round-trip preserves them.
X_METADATA_KEYS: tuple[str, ...] = (
    "x-seahorse-skill-trigger",
    "x-seahorse-skill-scope",
    "x-seahorse-skill-version",
)

# Discovery-level summary cap: the skill's INDEX row carries a summary ≤ 280
# chars so the agent can decide relevance without the body.
SKILL_SUMMARY_MAX_CHARS: int = 280


@dataclass(frozen=True)
class ProceduralConfig:
    """Documentation dataclass for the procedural skill format.

    Not a runtime config — it documents the canonical format so the CLI and the
    agent share one definition of what a skill is. ``record_procedure`` and
    ``gate_skill`` reads these constants directly.
    """

    canonical_sections: tuple[str, ...] = CANONICAL_SECTIONS
    x_metadata_keys: tuple[str, ...] = X_METADATA_KEYS
    summary_max_chars: int = SKILL_SUMMARY_MAX_CHARS


__all__ = [
    "CANONICAL_SECTIONS",
    "X_METADATA_KEYS",
    "SKILL_SUMMARY_MAX_CHARS",
    "ProceduralConfig",
]
