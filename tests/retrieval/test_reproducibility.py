"""Reproducibility tests (ADR-10: scores depend only on (query, episode, k)).

Two runs over the same state produce the SAME fused list. RRF is rank-based, the
tie-break is ``(-score, ep_id)`` asc, and ``pit=None`` resolves via an injectable
clock — so nothing depends on wall-clock time or arrival order. NO LLM in the
query path; scores are pure RRF over ranks (f5-11 §10, §16).

Signals:
- Two identical runs -> identical ``list[FusedCandidate]`` (structural equality).
- Tie-break by ``ep_id`` asc when RRF scores are equal.
- ``pit=None`` uses the injected clock; the same clock yields the same chain
  projection (active-now depends only on ``now``).
- ``embedder.embed_query`` called EXACTLY once per recall.
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


class TestIdenticalRuns:
    def test_two_runs_same_state_identical_output(
        self, embedder, vector_repo, fts_repo, episode_repo, fixed_clock
    ):
        vector_repo.knn_hits = [
            VectorHit("e3", 0.1, 0.9),
            VectorHit("e1", 0.2, 0.83),
            VectorHit("e2", 0.3, 0.77),
        ]
        fts_repo.search_hits = [FullTextHit("e1", 1.0, 0.37), FullTextHit("e4", 2.0, 0.13)]
        r1 = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
            clock=fixed_clock,
        )
        r2 = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
            clock=fixed_clock,
        )
        assert r1 == r2

    def test_scores_are_pure_rrf(self, embedder, vector_repo, fts_repo, episode_repo):
        # e1: vector rank1 + bm25 rank1 -> 2*_rrf(1). e2: vector rank2 -> _rrf(2).
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
        assert by_id["e1"].score == _rrf(1) * 2
        assert by_id["e2"].score == _rrf(2)


class TestTieBreak:
    def test_tie_broken_by_ep_id_asc(self, embedder, vector_repo, fts_repo, episode_repo):
        # eB vector rank1, eA bm25 rank1 -> equal score -> ep_id asc: eA first.
        vector_repo.knn_hits = [VectorHit("eB", 0.1, 0.9)]
        fts_repo.search_hits = [FullTextHit("eA", 1.0, 0.37)]
        result = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        assert result[0].ep_id == "eA"
        assert result[1].ep_id == "eB"
        assert result[0].score == result[1].score


class TestClockReproducibility:
    def test_same_clock_same_chain_projection(
        self, embedder, vector_repo, fts_repo, episode_repo, fixed_clock
    ):
        # active-now depends on `now`; with a fixed clock two runs project the
        # same chain member -> identical output.
        e_old = _ep(
            "eOld",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            valid_at=datetime(2024, 1, 1, tzinfo=UTC),
            invalid_at=datetime(2024, 2, 1, tzinfo=UTC),
        )
        e1 = _ep("e1", created_at=NOW, valid_at=NOW, supersedes="eOld")
        episode_repo.add(e_old)
        episode_repo.add(e1)
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9)]
        r1 = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
            clock=fixed_clock,
        )
        r2 = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
            clock=fixed_clock,
        )
        assert r1 == r2


class TestEmbedderCalledOnce:
    def test_embed_query_called_once_per_recall(
        self, embedder, vector_repo, fts_repo, episode_repo
    ):
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9)]
        recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        assert embedder.calls == ["q"]
