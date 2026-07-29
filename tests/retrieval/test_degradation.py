"""Degradation tests (f5-11 §11, §16: robustness to partial/empty sources).

RRF fuses whatever each source returned; it NEVER pads with invented scores
(ADR-10). A missing source (empty list) simply contributes nothing — the fused
result is the union of whatever the present sources returned.

Signals:
- D1: vector empty, BM25 returns -> result from BM25 only.
- D2: BM25 empty, vector returns -> result from vector only.
- D4: stage 1 empty but ``anchor_ep_id`` given -> stage 2 (chain) still runs.
- D5: stage 1 empty AND no anchor -> stage 2 skipped, result empty.
- D7: every source empty -> empty result (no padding).
- Partial ``< k`` from a source -> no padding, no crash.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.contracts.persistence import FullTextHit, VectorHit
from seahorse.retrieval import recall

from .conftest import _ep

NOW = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)


class TestD1VectorEmpty:
    def test_vector_empty_bm25_only(self, embedder, vector_repo, fts_repo, episode_repo):
        fts_repo.search_hits = [FullTextHit("e1", 1.0, 0.37), FullTextHit("e2", 2.0, 0.13)]
        result = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        assert [c.ep_id for c in result] == ["e1", "e2"]
        assert all(c.sources == ("bm25",) for c in result)


class TestD2Bm25Empty:
    def test_bm25_empty_vector_only(self, embedder, vector_repo, fts_repo, episode_repo):
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9)]
        result = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        assert [c.ep_id for c in result] == ["e1"]
        assert result[0].sources == ("vector",)


class TestD4Stage1EmptyAnchorGiven:
    def test_stage1_empty_but_anchor_runs_chain(
        self, embedder, vector_repo, fts_repo, episode_repo, fixed_clock
    ):
        # No vector/bm25 hits, but an explicit anchor -> chain_from(anchor) runs.
        e_old = _ep(
            "eOld",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            valid_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        e1 = _ep("e1", created_at=NOW, valid_at=NOW, supersedes="eOld")
        episode_repo.add(e_old)
        episode_repo.add(e1)
        result = recall(
            "q",
            pit=None,
            anchor_ep_id="e1",
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
            clock=fixed_clock,
        )
        # active-now picks e1; chain contributes e1.
        assert "e1" in {c.ep_id for c in result}
        assert episode_repo.chain_calls == ["e1"]


class TestD5Stage1EmptyNoAnchor:
    def test_stage1_empty_no_anchor_skips_stage2(
        self, embedder, vector_repo, fts_repo, episode_repo
    ):
        result = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        assert result == []
        assert episode_repo.chain_calls == []


class TestD7AllEmpty:
    def test_all_sources_empty(
        self, embedder, vector_repo, fts_repo, episode_repo, bfs_repo, fixed_clock
    ):
        result = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            graph_repo=bfs_repo,
            k=10,
            bfs_as_index_enabled=True,
            clock=fixed_clock,
        )
        assert result == []


class TestPartialUnderkNoPadding:
    def test_vector_returns_two_of_k(self, embedder, vector_repo, fts_repo, episode_repo):
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9), VectorHit("e2", 0.2, 0.83)]
        result = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        assert len(result) == 2  # no padding to k=10
