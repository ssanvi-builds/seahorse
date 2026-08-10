"""Tests for the R5 trust gate (L2c §6.1, prompt-injection).

A skill body is read as instructions by the LLM during trigger evaluation → it
is a persistent prompt-injection vector. The trust level is derived from
``provenance.agent_id`` + origin (manual/import/distilled); the gate decides
whether the body is delivered as an instruction or as a citation/context.
"""

from __future__ import annotations

from seahorse.procedural.trust import TrustLevel, gate_skill, trust_level_of

from .conftest import make_episode


class TestTrustLevelOf:
    def test_manual_human_is_high(self):
        ep = make_episode(
            source_type="human",
            provenance={"source_type": "human", "agent_id": "sergio"},
        )
        assert trust_level_of(ep) is TrustLevel.HIGH

    def test_agent_skip_is_medium(self):
        ep = make_episode(
            source_type="agent",
            provenance={"source_type": "agent", "extraction_mode": "skip"},
        )
        assert trust_level_of(ep) is TrustLevel.MEDIUM

    def test_importer_is_low(self):
        ep = make_episode(
            source_type="importer",
            provenance={"source_type": "importer", "importer_vendor": "claude-mem"},
        )
        assert trust_level_of(ep) is TrustLevel.LOW

    def test_distilled_is_low(self):
        ep = make_episode(
            source_type="system",
            provenance={"source_type": "system", "extraction_mode": "consolidated"},
        )
        assert trust_level_of(ep) is TrustLevel.LOW

    def test_unknown_source_defaults_medium(self):
        ep = make_episode(
            source_type="system",
            provenance={"source_type": "system", "extraction_mode": "skip"},
        )
        assert trust_level_of(ep) is TrustLevel.MEDIUM


class TestGateSkill:
    def test_high_trust_delivered_as_instruction(self):
        ep = make_episode(
            source_type="human",
            provenance={"source_type": "human"},
            body="## Trigger\n\nT\n\n## Steps\n\nS",
        )
        delivery = gate_skill(ep)
        assert delivery.as_instruction is True
        assert delivery.trust is TrustLevel.HIGH
        assert delivery.body == ep.body

    def test_low_trust_delivered_as_citation(self):
        ep = make_episode(
            source_type="importer",
            provenance={"source_type": "importer"},
            body="## Trigger\n\nT\n\n## Steps\n\nS",
        )
        delivery = gate_skill(ep)
        assert delivery.as_instruction is False
        assert delivery.trust is TrustLevel.LOW

    def test_min_trust_high_gates_medium_skill(self):
        ep = make_episode(
            source_type="agent",
            provenance={"source_type": "agent", "extraction_mode": "skip"},
        )
        delivery = gate_skill(ep, min_trust=TrustLevel.HIGH)
        assert delivery.as_instruction is False

    def test_min_trust_medium_passes_medium_skill(self):
        ep = make_episode(
            source_type="agent",
            provenance={"source_type": "agent", "extraction_mode": "skip"},
        )
        delivery = gate_skill(ep, min_trust=TrustLevel.MEDIUM)
        assert delivery.as_instruction is True

    def test_empty_body_gates_to_empty(self):
        ep = make_episode(
            source_type="human", provenance={"source_type": "human"}, body=""
        )
        delivery = gate_skill(ep)
        assert delivery.body == ""
