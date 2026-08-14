"""Trust gate for procedural skills (prompt-injection mitigation).

A skill body is read as instructions by the LLM during trigger evaluation → it
is a persistent prompt-injection vector. Skills arrive via import, LLM
distillation of sessions (potentially injected content), or the observer. The
mitigation: a per-skill trust level derived from ``provenance.agent_id`` +
origin (manual/import/distilled), and a trust gate BEFORE the body reaches the
agent's context — low-trust skills are delivered as citation/context, not as
instruction. The trigger is evaluated in the agent but with the body treated as
low-trust data until the gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from seahorse.contracts.engine import Episode


class TrustLevel(IntEnum):
    """Per-skill trust level. Higher rank = more trusted as instruction.

    ``IntEnum`` so ordering is numeric (``HIGH >= MEDIUM``), not lexicographic
    on the string value.
    """

    LOW = 1  # imported or distilled — deliver as citation/context
    MEDIUM = 2  # agent-authored skip — safe as instruction by default
    HIGH = 3  # manual (human-authored) — safe as instruction


def trust_level_of(episode: Episode) -> TrustLevel:
    """Derive the skill trust level from provenance.

    Origin rules:
    - ``source_type=human`` (manual) → HIGH.
    - ``source_type=importer`` (import) → LOW (content potentially injected).
    - ``extraction_mode=consolidated`` (LLM distillation) → LOW.
    - ``source_type=agent`` (skip) → MEDIUM.
    - Unknown → MEDIUM (conservative default, never HIGH).
    """
    p = episode.provenance or {}
    source_type = episode.source_type
    extraction_mode = p.get("extraction_mode")
    if source_type == "human":
        return TrustLevel.HIGH
    if source_type == "importer":
        return TrustLevel.LOW
    if extraction_mode == "consolidated":
        return TrustLevel.LOW
    if source_type == "agent":
        return TrustLevel.MEDIUM
    return TrustLevel.MEDIUM


@dataclass(frozen=True)
class SkillDelivery:
    """The gated delivery of a skill body.

    ``as_instruction`` is True only when the skill's trust meets the gate's
    ``min_trust``. A low-trust skill is delivered with ``as_instruction=False``:
    the caller (agent) treats the body as citation/context (data to verify),
    never as instructions to follow.
    """

    ep_id: str
    trust: TrustLevel
    as_instruction: bool
    body: str


def gate_skill(
    episode: Episode, *, min_trust: TrustLevel = TrustLevel.MEDIUM
) -> SkillDelivery:
    """Gate a skill before its body reaches the agent's context.

    The gate is the single decision point: it derives the trust level and
    decides whether the body is safe to treat as an instruction. The body is
    always delivered (so the agent can evaluate the skill), but the
    ``as_instruction`` flag tells the caller how to present it.
    """
    trust = trust_level_of(episode)
    as_instruction = trust.value >= min_trust.value
    return SkillDelivery(
        ep_id=episode.id,
        trust=trust,
        as_instruction=as_instruction,
        body=episode.body or "",
    )


__all__ = ["TrustLevel", "SkillDelivery", "trust_level_of", "gate_skill"]
