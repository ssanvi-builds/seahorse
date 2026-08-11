"""Tests for ``ProceduralShaper`` — skill progressive disclosure (L2c §6.1).

The three levels map to the disclosure levels:
- Discovery (INDEX): summary ≤ 280 chars (the skill's Discovery level).
- Activation (TIMELINE): passthrough to the inner shaper.
- Execution (FULL): the gated body (R5) — low-trust skills are delivered as
  citation/context (``as_instruction=False``), never as instructions.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.contracts.engine import Episode
from seahorse.contracts.retrieval import FusedCandidate
from seahorse.disclosure.types import (
    FullDetail,
    IndexRow,
    TimelineWindow,
)
from seahorse.procedural.shaper import ProceduralShaper
from seahorse.procedural.trust import TrustLevel

from .conftest import make_episode

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def _candidate(ep_id: str, score: float = 0.5) -> FusedCandidate:
    return FusedCandidate(ep_id=ep_id, score=score, sources=("vector",))


def _index_row(ep_id: str, summary: str | None = "s") -> IndexRow:
    return IndexRow(
        ep_id=ep_id,
        fact_id="f1",
        subject="skill",
        title="Skill",
        summary=summary,
        cognitive_type="procedural",
        skip_extraction=True,
        valid_at=None,
        invalid_at=None,
        created_at=NOW,
        score=0.5,
        stale=False,
        pending_ingest=False,
    )


def _full_detail(ep: Episode) -> FullDetail:
    from seahorse.contracts.engine import FreshnessView

    return FullDetail(
        episode=ep,
        provenance=ep.provenance,
        freshness=FreshnessView(
            fact_id=ep.fact_id, age_days=0, stale=False, pending_ingest=False, regime="agent"
        ),
        pit=None,
    )


class _Inner:
    """Minimal inner DisclosureShaper double."""

    def __init__(self) -> None:
        self.index_result: list[IndexRow] = []
        self.timeline_result: TimelineWindow | None = None
        self.full_result: list[FullDetail] = []
        self.timeline_calls: list[dict] = []

    def materialize_index(self, candidates, *, pit=None, now=None):
        return list(self.index_result)

    def materialize_timeline(self, anchor_ep_id, *, axis, pit=None, hops=1, now=None):
        self.timeline_calls.append(
            {"anchor": anchor_ep_id, "axis": axis, "pit": pit, "hops": hops, "now": now}
        )
        assert self.timeline_result is not None
        return self.timeline_result

    def materialize_full(self, ep_ids, *, pit=None, now=None):
        return list(self.full_result)


class TestProceduralShaperDiscovery:
    def test_index_passthrough(self):
        inner = _Inner()
        inner.index_result = [_index_row("e1", summary="short")]
        shaper = ProceduralShaper(inner)
        rows = shaper.materialize_index([_candidate("e1")])
        assert rows == inner.index_result

    def test_discovery_summary_capped_at_280(self):
        # The inner shaper already truncates to SUMMARY_MAX_CHARS=200; the
        # Discovery level guarantees the ≤280 skill cap is never exceeded.
        inner = _Inner()
        inner.index_result = [_index_row("e1", summary="x" * 300)]
        shaper = ProceduralShaper(inner)
        rows = shaper.materialize_index([_candidate("e1")])
        assert len(rows[0].summary or "") <= 280


class TestProceduralShaperActivation:
    def test_timeline_delegates_with_hops(self):
        inner = _Inner()
        inner.timeline_result = TimelineWindow(
            anchor_ep_id="e1", axis="supersedes_chain", entries=(), pit=None
        )
        shaper = ProceduralShaper(inner)
        win = shaper.materialize_timeline("e1", axis="supersedes_chain", hops=2)
        assert win is inner.timeline_result
        assert inner.timeline_calls[0]["hops"] == 2


class TestProceduralShaperExecution:
    def test_full_gates_high_trust_as_instruction(self):
        inner = _Inner()
        ep = make_episode(
            source_type="human", provenance={"source_type": "human"}, body="## Trigger\n\nT"
        )
        inner.full_result = [_full_detail(ep)]
        shaper = ProceduralShaper(inner)
        out = shaper.materialize_full(["e1"])
        assert len(out) == 1
        assert out[0].trust is TrustLevel.HIGH
        assert out[0].as_instruction is True
        assert out[0].body == ep.body

    def test_full_gates_low_trust_as_citation(self):
        inner = _Inner()
        ep = make_episode(
            source_type="importer",
            provenance={"source_type": "importer", "importer_vendor": "claude-mem"},
            body="## Trigger\n\nT",
        )
        inner.full_result = [_full_detail(ep)]
        shaper = ProceduralShaper(inner)
        out = shaper.materialize_full(["e1"])
        assert out[0].trust is TrustLevel.LOW
        assert out[0].as_instruction is False

    def test_min_trust_high_gates_medium_skill(self):
        inner = _Inner()
        ep = make_episode(
            source_type="agent", provenance={"source_type": "agent", "extraction_mode": "skip"}
        )
        inner.full_result = [_full_detail(ep)]
        shaper = ProceduralShaper(inner, min_trust=TrustLevel.HIGH)
        out = shaper.materialize_full(["e1"])
        assert out[0].as_instruction is False

    def test_empty_full_returns_empty(self):
        inner = _Inner()
        shaper = ProceduralShaper(inner)
        assert shaper.materialize_full([]) == []
