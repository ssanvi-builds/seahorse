"""Procedural skills (L2c §6.1) — deterministic, stdlib-only.

A skill is an ``Episode`` F3.1 with ``cognitive_type=procedural``. The layer is
a client of #12 (MemoryFacade) and #8 (DisclosureShaper) — it never reaches the
engine directly (delegation purity).

- ``config`` — the canonical skill format (sections, ``x-*`` metadata, caps).
- ``operations`` — ``record_procedure`` (deterministic creation, ADR-09 skip).
- ``trust`` — the R5 trust gate (prompt-injection mitigation).
- ``shaper`` — ``ProceduralShaper`` (3-level progressive disclosure).
"""

from seahorse.procedural.config import (
    CANONICAL_SECTIONS,
    SKILL_SUMMARY_MAX_CHARS,
    X_METADATA_KEYS,
    ProceduralConfig,
)
from seahorse.procedural.operations import ProceduralError, record_procedure
from seahorse.procedural.shaper import ProceduralShaper, SkillDetail
from seahorse.procedural.trust import SkillDelivery, TrustLevel, gate_skill, trust_level_of

__all__ = [
    "CANONICAL_SECTIONS",
    "SKILL_SUMMARY_MAX_CHARS",
    "X_METADATA_KEYS",
    "ProceduralConfig",
    "ProceduralError",
    "record_procedure",
    "ProceduralShaper",
    "SkillDetail",
    "SkillDelivery",
    "TrustLevel",
    "gate_skill",
    "trust_level_of",
]
