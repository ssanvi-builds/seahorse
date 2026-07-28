"""Unit tests for ``seahorse.retrieval.fusion`` — pure-Python RRF over ranks.

Test signals (f5-11 §16, load-bearing):
- RRF operates on RANK, not the ``score`` magnitudes of #6 (correction LOW 6).
- Deduplication by ``ep_id`` BEFORE fusing; ``sources`` = sorted union.
- Deterministic tie-break by ``ep_id`` asc (reproducibility ADR-10).
- Truncation to ``k``; robust to ``<k`` and empty sources; NO padding (ADR-10).
- ``sources`` is provenance only, NOT a rerank signal (R11).

RRF_K = 60 (Cormack 2009): rank 1 -> 1/61, rank 2 -> 1/62, rank 3 -> 1/63.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from seahorse.retrieval.constants import RRF_K
from seahorse.retrieval.fusion import SourceList, rrf_fuse

# ---------------------------------------------------------------------------
# Helpers — fakes with an ``ep_id`` (VectorHit/FullTextHit/IndexRowData shape).
# A ``score`` field is carried but RRF MUST ignore it (rank-based only).
# ---------------------------------------------------------------------------


def _hit(ep_id: str, score: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(ep_id=ep_id, score=score)


def _src(name: str, ep_ids: list[str], scores: list[float] | None = None) -> SourceList:
    if scores is None:
        scores = [0.0] * len(ep_ids)
    items = [_hit(e, s) for e, s in zip(ep_ids, scores, strict=True)]
    return SourceList(name=name, items=items, key=lambda h: h.ep_id)


def _rrf(rank: int) -> float:
    return 1.0 / (RRF_K + rank)


# ---------------------------------------------------------------------------
# 1. RRF operates on RANK, not on score magnitudes (correction LOW 6).
# ---------------------------------------------------------------------------


class TestRankNotScore:
    def test_same_order_different_score_magnitudes_yields_same_rrf(self):
        # Same ep_id ordering; wildly different score fields. RRF ignores score.
        a = _src("vector", ["e1", "e2", "e3"], scores=[0.99, 0.5, 0.1])
        b = _src("vector", ["e1", "e2", "e3"], scores=[0.0001, 0.0002, 0.0003])
        fused_a = rrf_fuse([a], k=10)
        fused_b = rrf_fuse([b], k=10)
        assert [(c.ep_id, c.score) for c in fused_a] == [(c.ep_id, c.score) for c in fused_b]
        assert fused_a[0].score == pytest.approx(_rrf(1))

    def test_same_score_different_order_yields_different_rrf(self):
        # Same score magnitudes, but ranks differ -> different RRF scores.
        a = _src("vector", ["e1", "e2"], scores=[0.5, 0.5])
        b = _src("vector", ["e2", "e1"], scores=[0.5, 0.5])
        fused_a = rrf_fuse([a], k=10)
        fused_b = rrf_fuse([b], k=10)
        # e1 is rank 1 in a, rank 2 in b -> higher score in a
        a_e1 = next(c for c in fused_a if c.ep_id == "e1")
        b_e1 = next(c for c in fused_b if c.ep_id == "e1")
        assert a_e1.score > b_e1.score
        assert a_e1.score == pytest.approx(_rrf(1))
        assert b_e1.score == pytest.approx(_rrf(2))


# ---------------------------------------------------------------------------
# 2. Dedup by ep_id BEFORE fusing; sources = sorted union (provenance).
# ---------------------------------------------------------------------------


class TestDedup:
    def test_same_ep_id_in_two_sources_yields_one_candidate_union_sources(self):
        vector = _src("vector", ["e1", "e2"])
        bm25 = _src("bm25", ["e2", "e3"])
        fused = rrf_fuse([vector, bm25], k=10)
        by_id = {c.ep_id: c for c in fused}
        # e2 appears in both -> single candidate, sources sorted union
        assert by_id["e2"].sources == ("bm25", "vector")
        # e1 only vector, e3 only bm25
        assert by_id["e1"].sources == ("vector",)
        assert by_id["e3"].sources == ("bm25",)
        # e2 score = vector rank2 + bm25 rank1
        assert by_id["e2"].score == pytest.approx(_rrf(2) + _rrf(1))

    def test_first_occurrence_wins_within_a_source(self):
        # Duplicate ep_id within one source: first rank (best) counts.
        dup = SourceList(
            name="vector",
            items=[_hit("e1", 0.9), _hit("e1", 0.1), _hit("e2", 0.5)],
            key=lambda h: h.ep_id,
        )
        fused = rrf_fuse([dup], k=10)
        e1 = next(c for c in fused if c.ep_id == "e1")
        # First occurrence is rank 1, second occurrence ignored (not rank 2).
        assert e1.score == pytest.approx(_rrf(1))
        assert len(fused) == 2  # no duplicate candidates

    def test_sources_canonical_alphabetical(self):
        fused = rrf_fuse(
            [_src("chain", ["e1"]), _src("vector", ["e1"]), _src("bm25", ["e1"])],
            k=10,
        )
        assert fused[0].sources == ("bm25", "chain", "vector")


# ---------------------------------------------------------------------------
# 3. Deterministic tie-break by ep_id asc (ADR-10).
# ---------------------------------------------------------------------------


class TestTieBreak:
    def test_tie_broken_by_ep_id_asc(self):
        # Two candidates with identical RRF score (each rank 1 in its own source).
        a = _src("vector", ["eB"])
        b = _src("bm25", ["eA"])
        fused = rrf_fuse([a, b], k=10)
        # Same score (both rank 1) -> ep_id asc breaks the tie: eA before eB
        assert fused[0].ep_id == "eA"
        assert fused[1].ep_id == "eB"
        assert fused[0].score == pytest.approx(fused[1].score)

    def test_higher_score_ranks_first(self):
        a = _src("vector", ["e1", "e2"])  # e1 rank1
        b = _src("bm25", ["e1"])  # e1 rank1 -> e1 score = 2*_rrf(1) (top)
        fused = rrf_fuse([a, b], k=10)
        assert fused[0].ep_id == "e1"
        assert fused[0].score == pytest.approx(_rrf(1) * 2)


# ---------------------------------------------------------------------------
# 4. Truncation; robust to <k and empty; NO padding (ADR-10).
# ---------------------------------------------------------------------------


class TestTruncationAndRobustness:
    def test_truncate_to_k(self):
        fused = rrf_fuse([_src("vector", [f"e{i}" for i in range(20)])], k=5)
        assert len(fused) == 5

    def test_fewer_than_k_no_padding(self):
        fused = rrf_fuse([_src("vector", ["e1", "e2"])], k=10)
        assert len(fused) == 2

    def test_empty_sources_returns_empty(self):
        assert rrf_fuse([], k=10) == []
        assert rrf_fuse([_src("vector", [])], k=10) == []

    def test_k_zero_returns_empty(self):
        assert rrf_fuse([_src("vector", ["e1", "e2"])], k=0) == []


# ---------------------------------------------------------------------------
# 5. sources is provenance only, NOT a rerank signal (R11).
# ---------------------------------------------------------------------------


class TestSourcesNotRerankSignal:
    def test_sources_does_not_affect_order_when_scores_differ(self):
        # e1: rank1 in one source (score _rrf(1)). e2: rank1 in three sources (3*_rrf(1)).
        # e2 has more sources AND higher score -> ranks first by SCORE, not by source count.
        a = _src("vector", ["e1"])
        b = _src("bm25", ["e2"])
        c = _src("chain", ["e2"])
        d = _src("bfs", ["e2"])
        fused = rrf_fuse([a, b, c, d], k=10)
        assert fused[0].ep_id == "e2"  # higher score
        # e2 is in bm25/chain/bfs (NOT vector); e1 is alone in vector.
        # sorted alphabetical: 'bfs' < 'bm25' < 'chain'.
        assert fused[0].sources == ("bfs", "bm25", "chain")
        assert fused[1].ep_id == "e1"
        assert fused[1].sources == ("vector",)

    def test_tie_does_not_consider_sources(self):
        # eB in 2 sources (rank1 each) vs eA in 1 source (rank1). Different scores,
        # so order is by score. Then construct a genuine tie: eA rank1 in vector,
        # eB rank1 in bm25 -> equal score -> tie-break by ep_id, NOT by source count.
        a = _src("vector", ["eA"])
        b = _src("bm25", ["eB"])
        fused = rrf_fuse([a, b], k=10)
        assert fused[0].score == pytest.approx(fused[1].score)
        assert fused[0].ep_id == "eA"  # ep_id asc, not source-count
        assert fused[0].sources == ("vector",)
        assert fused[1].sources == ("bm25",)


# ---------------------------------------------------------------------------
# 6. Ranking order is reproducible across two identical runs (ADR-10).
# ---------------------------------------------------------------------------


class TestReproducibility:
    def test_two_identical_runs_produce_identical_output(self):
        sources = [
            _src("vector", ["e3", "e1", "e2"], scores=[0.9, 0.5, 0.1]),
            _src("bm25", ["e1", "e4", "e3"], scores=[3.0, 1.0, 0.5]),
        ]
        run1 = rrf_fuse(sources, k=10)
        run2 = rrf_fuse(sources, k=10)
        assert run1 == run2  # structural equality of FusedCandidate lists

    def test_consensus_candidate_ranks_high(self):
        # e1 appears rank2 in vector + rank1 in bm25 -> strong consensus.
        sources = [_src("vector", ["eX", "e1"]), _src("bm25", ["e1", "eY"])]
        fused = rrf_fuse(sources, k=10)
        assert fused[0].ep_id == "e1"
        assert fused[0].sources == ("bm25", "vector")
        assert fused[0].score == pytest.approx(_rrf(2) + _rrf(1))