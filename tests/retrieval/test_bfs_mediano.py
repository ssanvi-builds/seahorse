"""BFS-as-INDEX extension tests (a medium-term goal).

BFS is TIMELINE-only by default (``bfs_as_index_enabled=False``); using it as an
INDEX fusion source is a medium-term extension pending sign-off. ``known_at``
BFS raises ``BfsKnownAtUnsupported`` (no silent ``state_at`` fallback) unless
``bfs_known_at_supported=True``. Hops are capped to ``MAX_HOPS_MVP1`` before the
call (no dead try/except retry).

Signals:
- ``bfs_as_index_enabled=False`` (default) -> BFS axis NOT invoked.
- ``bfs_as_index_enabled=True`` + ``graph_repo=None`` -> graceful skip, no crash.
- ``hops > MAX_HOPS_MVP1`` -> capped to ``MAX_HOPS_MVP1`` in the call.
- ``known_at`` + unsupported -> ``BfsKnownAtUnsupported`` raised internally and
  caught; BFS axis dropped; the repo is NOT called.
- ``known_at`` + supported -> repo called with ``pit_kind="known_at"``.
- ``pit=None`` BFS -> ``pit_kind="state_at"`` at the injected ``now``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.contracts.index import MAX_HOPS_MVP1
from seahorse.contracts.persistence import VectorHit
from seahorse.disclosure.types import PITPoint
from seahorse.retrieval import recall

from .conftest import _row


def _pit(kind: str, t: datetime) -> PITPoint:
    return PITPoint(kind=kind, t=t)  # type: ignore[arg-type]


NOW = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)


def _seed_vector(vector_repo, ep_id="e1"):
    vector_repo.knn_hits = [VectorHit(ep_id, 0.1, 0.9)]


class TestBfsGating:
    def test_disabled_by_default(
        self, embedder, vector_repo, fts_repo, episode_repo, bfs_repo, fixed_clock
    ):
        _seed_vector(vector_repo)
        recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            graph_repo=bfs_repo,
            k=10,
            clock=fixed_clock,
        )
        assert bfs_repo.calls == []

    def test_enabled_without_graph_repo_skips_gracefully(
        self, embedder, vector_repo, fts_repo, episode_repo, fixed_clock
    ):
        _seed_vector(vector_repo)
        # graph_repo omitted (None) + bfs_as_index_enabled=True -> no crash, no BFS.
        result = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            graph_repo=None,
            k=10,
            bfs_as_index_enabled=True,
            clock=fixed_clock,
        )
        assert [c.ep_id for c in result] == ["e1"]


class TestHopsCap:
    def test_hops_capped_to_max(
        self, embedder, vector_repo, fts_repo, episode_repo, bfs_repo, fixed_clock
    ):
        _seed_vector(vector_repo)
        bfs_repo.rows = [_row("eB", created_at=NOW)]
        recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            graph_repo=bfs_repo,
            k=10,
            bfs_as_index_enabled=True,
            hops=99,
            clock=fixed_clock,
        )
        assert bfs_repo.calls[0]["hops"] == MAX_HOPS_MVP1

    def test_hops_within_cap_passes_through(
        self, embedder, vector_repo, fts_repo, episode_repo, bfs_repo, fixed_clock
    ):
        _seed_vector(vector_repo)
        recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            graph_repo=bfs_repo,
            k=10,
            bfs_as_index_enabled=True,
            hops=1,
            clock=fixed_clock,
        )
        assert bfs_repo.calls[0]["hops"] == 1

    def test_include_tags_soft_always_false_in_mvp(
        self, embedder, vector_repo, fts_repo, episode_repo, bfs_repo, fixed_clock
    ):
        _seed_vector(vector_repo)
        recall(
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
        assert bfs_repo.calls[0]["include_tags_soft"] is False


class TestKnownAtBfsSignoff:
    def test_unsupported_raises_and_drops_axis(
        self, embedder, vector_repo, fts_repo, episode_repo, bfs_repo
    ):
        # Seed the known_at kNN hits so stage 1 has an anchor (pit=known_at routes
        # to knn_known_at, not the current-state knn).
        vector_repo.knn_known_at_hits = [VectorHit("e1", 0.1, 0.9)]
        result = recall(
            "q",
            pit=_pit("known_at", NOW),
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            graph_repo=bfs_repo,
            k=10,
            bfs_as_index_enabled=True,
            bfs_known_at_supported=False,
        )
        assert bfs_repo.calls == []  # raised before the call
        assert [c.ep_id for c in result] == ["e1"]

    def test_supported_calls_repo_with_known_at(
        self, embedder, vector_repo, fts_repo, episode_repo, bfs_repo
    ):
        vector_repo.knn_known_at_hits = [VectorHit("e1", 0.1, 0.9)]
        bfs_repo.rows = [_row("eB", created_at=NOW)]
        recall(
            "q",
            pit=_pit("known_at", NOW),
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            graph_repo=bfs_repo,
            k=10,
            bfs_as_index_enabled=True,
            bfs_known_at_supported=True,
        )
        assert bfs_repo.calls[0]["pit_kind"] == "known_at"


class TestBfsCognitiveFilter:
    def test_bfs_rows_filtered_by_cognitive_type(
        self, embedder, vector_repo, fts_repo, episode_repo, bfs_repo, fixed_clock
    ):
        _seed_vector(vector_repo)
        bfs_repo.rows = [
            _row("eB", created_at=NOW, cognitive_type="fact"),
            _row("eC", created_at=NOW, cognitive_type="preference"),
        ]
        result = recall(
            "q",
            pit=None,
            cognitive_type="fact",
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            graph_repo=bfs_repo,
            k=10,
            bfs_as_index_enabled=True,
            clock=fixed_clock,
        )
        ids = {c.ep_id for c in result}
        assert "eB" in ids and "eC" not in ids
