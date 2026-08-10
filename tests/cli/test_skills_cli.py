"""``seahorse skill`` CLI — delegation purity + R5 trust gate.

The skill surface is a client of #12 (MemoryFacade) and the procedural layer:
``add`` delegates to ``record_procedure`` (canonical body validated before any
write), ``list`` uses ``get_vigente`` filtered to procedural, ``search`` uses
``recall`` with the procedural filter, ``show`` uses ``recall_full`` + the R5
trust gate at the CLI layer (never reaches into the facade's internals).
"""

from __future__ import annotations

import io

import pytest

from seahorse.cli.errors import CliUsageError
from seahorse.cli.skills import (
    run_skill_add,
    run_skill_list,
    run_skill_search,
    run_skill_show,
)
from seahorse.procedural.operations import ProceduralError
from tests.cli.builders import T0, RecordingFacade, make_episode

CANONICAL = """## Trigger

When the user asks how to do X.

## Steps

1. Do A.

## Validation

Check C.

## Rationale

Because D.
"""


def _out() -> io.StringIO:
    return io.StringIO()


class TestSkillAdd:
    def test_delegates_to_record_procedure_skip(self, recording: RecordingFacade):
        run_skill_add(
            recording,
            body=CANONICAL,
            source_type="agent",
            agent_id="a1",
            session_id="s1",
            title="How to do X",
            fmt="human",
            out=_out(),
        )
        assert len(recording.remember_calls) == 1
        call = recording.remember_calls[0]
        assert call["extraction_mode"] == "skip"
        assert call["payload"].cognitive_type == "procedural"
        assert call["payload"].body == CANONICAL

    def test_rejects_non_canonical_body(self, recording: RecordingFacade):
        with pytest.raises(ProceduralError):
            run_skill_add(recording, body="## Trigger\n\nOnly trigger.", fmt="human", out=_out())
        # No write reached the facade (fail-loud before any call).
        assert recording.remember_calls == []

    def test_stores_x_metadata(self, recording: RecordingFacade):
        run_skill_add(
            recording,
            body=CANONICAL,
            trigger="user asks X",
            scope="personal",
            version="1.0",
            fmt="human",
            out=_out(),
        )
        prov = recording.remember_calls[0]["payload"].by
        assert prov["x-seahorse-skill-trigger"] == "user asks X"
        assert prov["x-seahorse-skill-scope"] == "personal"
        assert prov["x-seahorse-skill-version"] == "1.0"


class TestSkillList:
    def test_filters_vigente_to_procedural(self, recording: RecordingFacade):
        recording.vigente_result = [
            make_episode("ep-1", cognitive_type="procedural"),
            make_episode("ep-2", cognitive_type="semantic"),
        ]
        out = _out()
        run_skill_list(recording, fmt="human", out=out)
        assert len(recording.get_vigente_calls) == 1
        assert "ep-1" in out.getvalue()
        assert "ep-2" not in out.getvalue()

    def test_empty_vault_honest_message(self, recording: RecordingFacade):
        out = _out()
        run_skill_list(recording, fmt="human", out=out)
        assert "no skills" in out.getvalue()


class TestSkillSearch:
    def test_delegates_recall_with_procedural_filter(self, recording: RecordingFacade):
        run_skill_search(recording, query="how to", fmt="human", out=_out())
        assert len(recording.recall_calls) == 1
        call = recording.recall_calls[0]
        assert call["query"] == "how to"
        assert call["cognitive_type"] == "procedural"


class TestSkillShow:
    def test_gates_high_trust_as_instruction(self, recording: RecordingFacade):
        recording.full_result = [
            make_full_detail_procedural(source_type="human", provenance={"source_type": "human"})
        ]
        out = _out()
        run_skill_show(recording, ep_id="ep-1", fmt="human", out=out)
        assert "instruction" in out.getvalue()
        assert "citation" not in out.getvalue()

    def test_gates_low_trust_as_citation(self, recording: RecordingFacade):
        recording.full_result = [
            make_full_detail_procedural(
                source_type="importer", provenance={"source_type": "importer"}
            )
        ]
        out = _out()
        run_skill_show(recording, ep_id="ep-1", fmt="human", out=out)
        assert "citation/context" in out.getvalue()

    def test_min_trust_high_gates_medium(self, recording: RecordingFacade):
        recording.full_result = [
            make_full_detail_procedural(
                source_type="agent", provenance={"source_type": "agent", "extraction_mode": "skip"}
            )
        ]
        out = _out()
        run_skill_show(recording, ep_id="ep-1", min_trust="high", fmt="human", out=out)
        assert "citation/context" in out.getvalue()

    def test_invalid_min_trust_usage_error(self, recording: RecordingFacade):
        with pytest.raises(CliUsageError):
            run_skill_show(recording, ep_id="ep-1", min_trust="bogus", fmt="human", out=_out())


def make_full_detail_procedural(*, source_type: str, provenance: dict):
    from seahorse.contracts.engine import FreshnessView
    from seahorse.contracts.episode import Episode
    from seahorse.disclosure.types import FullDetail

    ep = Episode(
        id="ep-1",
        created_at=T0,
        schema_version="1.1",
        provenance=provenance,
        body="## Trigger\n\nT\n\n## Steps\n\nS",
        subject="skill",
        fact_id="fact-1",
        valid_at=None,
        invalid_at=None,
        expired_at=None,
        supersedes=None,
        cognitive_type="procedural",
        source_type=source_type,
        title="Skill",
    )
    return FullDetail(
        episode=ep,
        provenance=ep.provenance,
        freshness=FreshnessView(
            fact_id=ep.fact_id, age_days=0, stale=False, pending_ingest=False, regime="agent"
        ),
        pit=None,
    )
