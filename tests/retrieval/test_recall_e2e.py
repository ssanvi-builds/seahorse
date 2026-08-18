"""End-to-end ``recall`` tests — the full 2-stage flow + the disclosure extension point.

``recall`` runs stage 1 (kNN + BM25), picks the stage-1 top-1 as the anchor (or
an explicit ``anchor_ep_id``), runs stage 2 (chain) off that anchor, and fuses
the union with RRF. The output is ``list[FusedCandidate]`` — the exact extension
point the progressive-disclosure ``materialize_index`` consumes; it projects and
does NOT re-rank (``score`` is passthrough).

Signals:
- The three sources (vector/bm25/chain) contribute to the union.
- An ep_id present in multiple sources -> ONE candidate with the union ``sources``.
- The anchor (stage-1 top-1) drives the chain.
- ``FusedCandidate.score`` is the RRF score the progressive-disclosure layer
  reads verbatim (passthrough).
- Ordering by ``(-score, ep_id)``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.contracts.persistence import FullTextHit, VectorHit
from seahorse.retrieval import recall
from seahorse.retrieval.constants import RRF_K

from .conftest import _ep

NOW = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)


def _rrf(rank: int) -> float:
    return 1.0 / (RRF_K + rank)


class TestFullTwoStage:
    def test_all_three_sources_contribute(
        self, embedder, vector_repo, fts_repo, episode_repo, fixed_clock
    ):
        # vector: e1 (rank1), e2 (rank2). bm25: e1 (rank1), e3 (rank2).
        # anchor = e1 (stage-1 top-1). chain_from(e1) -> [e1, eOld] (active-now: e1).
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9), VectorHit("e2", 0.2, 0.83)]
        fts_repo.search_hits = [FullTextHit("e1", 1.0, 0.37), FullTextHit("e3", 2.0, 0.13)]
        e_old = _ep(
            "eOld",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            valid_at=datetime(2024, 1, 1, tzinfo=UTC),
            invalid_at=datetime(2024, 2, 1, tzinfo=UTC),
        )
        e1 = _ep("e1", created_at=NOW, valid_at=NOW, supersedes="eOld")
        episode_repo.add(e_old)
        episode_repo.add(e1)
        result = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
            clock=fixed_clock,
        )
        by_id = {c.ep_id: c for c in result}
        # e1 is in vector + bm25 + chain (active-now anchor).
        assert set(by_id["e1"].sources) == {"bm25", "chain", "vector"}
        # e2 only vector; e3 only bm25.
        assert by_id["e2"].sources == ("vector",)
        assert by_id["e3"].sources == ("bm25",)

    def test_anchor_drives_chain(
        self, embedder, vector_repo, fts_repo, episode_repo, fixed_clock
    ):
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9)]
        episode_repo.add(_ep("e1", created_at=NOW, valid_at=NOW))
        recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
            clock=fixed_clock,
        )
        # The stage-1 top-1 (e1) is the anchor for the chain.
        assert episode_repo.chain_calls == ["e1"]


class TestDedupAcrossSources:
    def test_same_ep_id_in_vector_and_chain_one_candidate_union_sources(
        self, embedder, vector_repo, fts_repo, episode_repo, fixed_clock
    ):
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9)]
        e1 = _ep("e1", created_at=NOW, valid_at=NOW)
        episode_repo.add(e1)
        result = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
            clock=fixed_clock,
        )
        # e1 in vector (rank1) + chain (active-now) -> single candidate, union sources.
        assert len(result) == 1
        assert result[0].ep_id == "e1"
        assert set(result[0].sources) == {"chain", "vector"}
        # score = vector rank1 + chain rank1 = 2*_rrf(1)
        assert result[0].score == _rrf(1) * 2


class TestScorePassthrough:
    def test_score_is_rrf_value_seam_eight_reads(
        self, embedder, vector_repo, fts_repo, episode_repo
    ):
        # The FusedCandidate.score the progressive-disclosure layer reads verbatim
        # is the RRF score (no rerank).
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9), VectorHit("e2", 0.2, 0.83)]
        fts_repo.search_hits = [FullTextHit("e1", 1.0, 0.37)]
        result = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        by_id = {c.ep_id: c for c in result}
        # e1: vector rank1 + bm25 rank1.
        assert by_id["e1"].score == _rrf(1) + _rrf(1)
        # e2: vector rank2 only.
        assert by_id["e2"].score == _rrf(2)
        # Ordering: e1 (2*_rrf(1)) before e2 (_rrf(2)).
        assert result[0].ep_id == "e1"


class TestKTruncation:
    def test_result_truncated_to_k(self, embedder, vector_repo, fts_repo, episode_repo):
        vector_repo.knn_hits = [VectorHit(f"e{i}", 0.01 * i, 1.0) for i in range(20)]
        result = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=5,
        )
        assert len(result) == 5
