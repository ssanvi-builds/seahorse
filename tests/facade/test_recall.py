"""Tests for ``MemoryFacade.recall`` — MVP-0 G2 vigente listing via #8.

Canonical MVP-0 recall: ``engine.get_vigente`` → client-side ``cognitive_type``
filter → deterministic sort (``created_at`` desc, ``ep_id`` asc tie-break) →
truncate ``k`` → synthetic ``FusedCandidate(ep_id, score=0.0, sources=())`` →
#8 ``materialize_index``. #12 NEVER constructs ``IndexRow``. PIT recall is
MVP-1 (#11) and is refused before any read (ADR-03 axes never mixed).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.facade.errors import (
    E_EMPTY_QUERY,
    EmptyQueryError,
    PitRecallNotSupportedMVP0,
)
from tests.facade.conftest import make_episode


def _eps_at(times: list[datetime]) -> list:
    # ep_id encodes the desired tie-break / order label.
    return [make_episode(f"e{i}", created_at=t) for i, t in enumerate(times)]


class TestRecallDelegation:
    def test_calls_get_vigente_once(self, facade, engine) -> None:
        facade.recall("sergio")
        assert len(engine.get_vigente_calls) == 1

    def test_calls_materialize_index_once(self, facade, shaper) -> None:
        facade.recall("sergio")
        assert len(shaper.index_calls) == 1

    def test_never_calls_materialize_full_or_timeline(self, facade, shaper) -> None:
        facade.recall("sergio")
        assert shaper.full_calls == []
        assert shaper.timeline_calls == []

    def test_forwards_clock_now_to_get_vigente(self, facade, engine) -> None:
        facade.recall("sergio")
        assert engine.get_vigente_calls[0]["now"] == datetime(2026, 7, 16, 12, 0, tzinfo=UTC)

    def test_forwards_clock_now_to_materialize_index(self, facade, shaper) -> None:
        facade.recall("sergio")
        assert shaper.index_calls[0]["now"] == datetime(2026, 7, 16, 12, 0, tzinfo=UTC)

    def test_returns_shaper_result_verbatim(self, facade, shaper) -> None:
        from seahorse.disclosure.types import IndexRow

        row = IndexRow(
            ep_id="e1",
            fact_id="f1",
            subject="S",
            title=None,
            summary=None,
            cognitive_type="semantic",
            skip_extraction=True,
            valid_at=None,
            invalid_at=None,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            score=0.0,
            stale=False,
            pending_ingest=False,
        )
        shaper.index_result = [row]
        result = facade.recall("sergio")
        assert result == [row]
        # #12 did NOT construct the IndexRow itself — it came from #8.
        assert result[0] is row


class TestRecallSyntheticCandidates:
    def test_fused_candidate_score_zero_empty_sources(self, facade, engine, shaper) -> None:
        engine.vigente = _eps_at([datetime(2026, 1, 1, tzinfo=UTC)])
        facade.recall("sergio")
        cands = shaper.index_calls[0]["candidates"]
        assert len(cands) == 1
        assert cands[0].score == 0.0
        assert cands[0].sources == ()

    def test_pit_forwarded_as_none(self, facade, shaper) -> None:
        # #12 forwards pit=None to #8 even though the caller gave no pit.
        facade.recall("sergio")
        assert shaper.index_calls[0]["pit"] is None


class TestRecallDeterministicOrder:
    def test_created_at_desc(self, facade, engine, shaper) -> None:
        t1 = datetime(2026, 1, 1, tzinfo=UTC)
        t2 = datetime(2026, 2, 1, tzinfo=UTC)
        t3 = datetime(2026, 3, 1, tzinfo=UTC)
        engine.vigente = _eps_at([t1, t2, t3])  # e0,e1,e2
        facade.recall("sergio")
        cands = shaper.index_calls[0]["candidates"]
        # newest first: e2 (Mar), e1 (Feb), e0 (Jan)
        assert [c.ep_id for c in cands] == ["e2", "e1", "e0"]

    def test_tie_break_ep_id_asc(self, facade, engine, shaper) -> None:
        t = datetime(2026, 1, 1, tzinfo=UTC)
        # same created_at, ids out of order to prove the tie-break
        engine.vigente = [
            make_episode("zebra", created_at=t),
            make_episode("alpha", created_at=t),
            make_episode("mike", created_at=t),
        ]
        facade.recall("sergio")
        cands = shaper.index_calls[0]["candidates"]
        assert [c.ep_id for c in cands] == ["alpha", "mike", "zebra"]

    def test_subject_filter_forwarded(self, facade, engine) -> None:
        facade.recall("sergio", subject_filter="Sergio")
        assert engine.get_vigente_calls[0]["subject"] == "Sergio"


class TestRecallCognitiveFilter:
    def test_client_side_cognitive_filter(self, facade, engine, shaper) -> None:
        t = datetime(2026, 1, 1, tzinfo=UTC)
        engine.vigente = [
            make_episode("e1", created_at=t, cognitive_type="semantic"),
            make_episode("e2", created_at=t, cognitive_type="episodic"),
            make_episode("e3", created_at=t, cognitive_type="semantic"),
        ]
        facade.recall("sergio", cognitive_type="semantic")
        cands = shaper.index_calls[0]["candidates"]
        assert {c.ep_id for c in cands} == {"e1", "e3"}

    def test_no_filter_passes_all(self, facade, engine, shaper) -> None:
        t = datetime(2026, 1, 1, tzinfo=UTC)
        engine.vigente = [
            make_episode("e1", created_at=t, cognitive_type="semantic"),
            make_episode("e2", created_at=t, cognitive_type=None),
        ]
        facade.recall("sergio")
        assert len(shaper.index_calls[0]["candidates"]) == 2


class TestRecallTruncate:
    def test_truncates_to_k(self, facade, engine, shaper) -> None:
        t = datetime(2026, 1, 1, tzinfo=UTC)
        engine.vigente = [make_episode(f"e{i}", created_at=t) for i in range(20)]
        facade.recall("sergio", k=3)
        assert len(shaper.index_calls[0]["candidates"]) == 3

    def test_k_clamped_to_config_top_k(self, facade, engine, shaper) -> None:
        t = datetime(2026, 1, 1, tzinfo=UTC)
        engine.vigente = [make_episode(f"e{i}", created_at=t) for i in range(50)]
        # default config top_k = TOP_K = 10
        facade.recall("sergio", k=100)
        assert len(shaper.index_calls[0]["candidates"]) == 10


class TestRecallPitRefused:
    def test_pit_raises_before_any_read(self, facade, engine, shaper) -> None:
        from seahorse.disclosure.types import PITPoint

        pit = PITPoint(kind="state_at", t=datetime(2026, 1, 1, tzinfo=UTC))
        with pytest.raises(PitRecallNotSupportedMVP0):
            facade.recall("sergio", pit=pit)
        # ADR-03: no read happened.
        assert engine.get_vigente_calls == []
        assert shaper.index_calls == []


class TestRecallEmptyQuery:
    def test_empty_string_rejected(self, facade) -> None:
        with pytest.raises(EmptyQueryError):
            facade.recall("")

    def test_whitespace_rejected(self, facade) -> None:
        with pytest.raises(EmptyQueryError):
            facade.recall("   ")

    def test_empty_query_code(self, facade) -> None:
        with pytest.raises(EmptyQueryError) as exc:
            facade.recall("")
        assert exc.value.code == E_EMPTY_QUERY

    def test_empty_query_before_read(self, facade, engine) -> None:
        with pytest.raises(EmptyQueryError):
            facade.recall("")
        assert engine.get_vigente_calls == []