"""Procedural skills — deterministic, stdlib-only.

A skill is an ``Episode`` in the canonical format with
``cognitive_type=procedural``. The layer is a client of the primitives facade
(MemoryFacade) and the disclosure shaper (DisclosureShaper) — it never reaches
the engine directly (delegation purity).

- ``config`` — the canonical skill format (sections, ``x-*`` metadata, caps).
- ``operations`` — ``record_procedure`` (deterministic creation, skip-first).
- ``trust`` — the trust gate (prompt-injection mitigation). The consumers
  (``mcp/tools.py``, ``cli/skills.py``) call ``gate_skill`` directly; the
  ``ProceduralShaper`` wrapper was removed (dead code with a return type
  incompatible with the facade's ``_ShaperLike`` protocol).
"""

from seahorse.procedural.config import (
    CANONICAL_SECTIONS,
    SKILL_SUMMARY_MAX_CHARS,
    X_METADATA_KEYS,
    ProceduralConfig,
)
from seahorse.procedural.operations import ProceduralError, record_procedure
from seahorse.procedural.trust import SkillDelivery, TrustLevel, gate_skill, trust_level_of

__all__ = [
    "CANONICAL_SECTIONS",
    "SKILL_SUMMARY_MAX_CHARS",
    "X_METADATA_KEYS",
    "ProceduralConfig",
    "ProceduralError",
    "record_procedure",
    "SkillDelivery",
    "TrustLevel",
    "gate_skill",
    "trust_level_of",
]
