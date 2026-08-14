"""``cognitive_type`` client-side filtering tests.

``cognitive_type`` is a pushdown ONLY for current-state ``knn`` (the single
method that exposes ``cognitive_types``). For the PIT knn variants AND for ALL
BM25 methods (current-state + PIT) it is a CLIENT-SIDE filter via
``episode_repo.get(ep_id)``.

Signals:
- current-state knn + ``cognitive_type`` → ``cognitive_types=[ct]`` pushdown; NO
  ``episode_repo.get`` for the vector hits (the repo pre-filtered).
- PIT knn + ``cognitive_type`` → pushdown NOT available; client-side filter via
  ``episode_repo.get``; hits whose episode is missing or mismatches are dropped.
- BM25 + ``cognitive_type`` (any pit) → ALWAYS client-side (no BM25 pushdown).
- Robust to ``< k`` after filtering: returns what matches, NO padding.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.contracts.persistence import FullTextHit, VectorHit
from seahorse.disclosure.types import PITPoint
from seahorse.retrieval import recall

from .conftest import _ep


def _pit(kind: str, t: datetime) -> PITPoint:
    return PITPoint(kind=kind, t=t)  # type: ignore[arg-type]


NOW = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)


class TestVigentKnnPushdown:
    def test_cognitive_types_pushed_down_no_client_filter(
        self, embedder, vector_repo, fts_repo, episode_repo
    ):
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9), VectorHit("e2", 0.2, 0.83)]
        recall(
            "q",
            pit=None,
            cognitive_type="fact",
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        # Pushdown: the repo received cognitive_types=[ct].
        assert vector_repo.calls["knn"][0]["cognitive_types"] == ["fact"]
        # No client-side filter -> episode_repo.get NOT called for vector hits.
        assert episode_repo.get_calls == []

    def test_no_cognitive_type_no_pushdown(self, embedder, vector_repo, fts_repo, episode_repo):
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
        assert vector_repo.calls["knn"][0]["cognitive_types"] is None


class TestPitKnnClientSide:
    def test_state_at_knn_filters_client_side(self, embedder, vector_repo, fts_repo, episode_repo):
        vector_repo.knn_state_at_hits = [
            VectorHit("e1", 0.1, 0.9),
            VectorHit("e2", 0.2, 0.83),
            VectorHit("e3", 0.3, 0.77),
        ]
        # e1/e3 are "fact"; e2 is "preference" (dropped).
        episode_repo.add(_ep("e1", created_at=NOW, cognitive_type="fact", valid_at=NOW))
        episode_repo.add(_ep("e2", created_at=NOW, cognitive_type="preference", valid_at=NOW))
        episode_repo.add(_ep("e3", created_at=NOW, cognitive_type="fact", valid_at=NOW))
        result = recall(
            "q",
            pit=_pit("state_at", NOW),
            cognitive_type="fact",
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        ep_ids = {c.ep_id for c in result}
        assert "e1" in ep_ids and "e3" in ep_ids and "e2" not in ep_ids
        # PIT knn has no pushdown param -> client-side filter invoked get per hit.
        assert sorted(episode_repo.get_calls) == ["e1", "e2", "e3"]


class TestBm25AlwaysClientSide:
    def test_vigent_bm25_filters_client_side(self, embedder, vector_repo, fts_repo, episode_repo):
        fts_repo.search_hits = [
            FullTextHit("e1", 1.0, 0.37),
            FullTextHit("e2", 2.0, 0.13),
        ]
        episode_repo.add(_ep("e1", created_at=NOW, cognitive_type="fact"))
        episode_repo.add(_ep("e2", created_at=NOW, cognitive_type="preference"))
        result = recall(
            "q",
            pit=None,
            cognitive_type="fact",
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        assert {c.ep_id for c in result} == {"e1"}
        # BM25 never has pushdown -> always client-side via get.
        assert sorted(episode_repo.get_calls) == ["e1", "e2"]

    def test_state_at_bm25_filters_client_side(self, embedder, vector_repo, fts_repo, episode_repo):
        fts_repo.search_state_at_hits = [FullTextHit("e1", 1.0, 0.37), FullTextHit("e2", 2.0, 0.13)]
        episode_repo.add(_ep("e1", created_at=NOW, cognitive_type="fact", valid_at=NOW))
        episode_repo.add(_ep("e2", created_at=NOW, cognitive_type="preference", valid_at=NOW))
        result = recall(
            "q",
            pit=_pit("state_at", NOW),
            cognitive_type="fact",
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        assert {c.ep_id for c in result} == {"e1"}


class TestRobustToUnderkAfterFilter:
    def test_fewer_than_k_no_padding(self, embedder, vector_repo, fts_repo, episode_repo):
        # 5 BM25 hits, only 1 matches cognitive_type; k=10 -> result has 1, no padding.
        fts_repo.search_hits = [FullTextHit(f"e{i}", 1.0, 0.37) for i in range(5)]
        episode_repo.add(_ep("e3", created_at=NOW, cognitive_type="fact"))
        # other episodes absent -> get returns None -> dropped
        result = recall(
            "q",
            pit=None,
            cognitive_type="fact",
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        assert [c.ep_id for c in result] == ["e3"]


class TestMissingEpisodeDropped:
    def test_hit_without_episode_dropped(self, embedder, vector_repo, fts_repo, episode_repo):
        fts_repo.search_hits = [FullTextHit("eGhost", 1.0, 0.37), FullTextHit("e1", 2.0, 0.13)]
        episode_repo.add(_ep("e1", created_at=NOW, cognitive_type="fact"))
        result = recall(
            "q",
            pit=None,
            cognitive_type="fact",
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        # eGhost has no episode -> get returns None -> dropped (no KeyError).
        assert [c.ep_id for c in result] == ["e1"]
