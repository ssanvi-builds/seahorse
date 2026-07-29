"""PIT routing + axis-isolation tests (R13, ADR-03).

The two bi-temporal axes NEVER mix within one recall. ``pit.kind`` is validated
ONCE at the entrypoint and the SAME kind fans to ALL sources. ``pit=None``
resolves to the vigent path (kNN ``vigent_only=True``; BM25 ``vigent_only=True``;
BFS ``state_at`` at the injected ``now``).

Signals (f5-11 §7, §16):
- ``pit=None`` → vigent knn + vigent search (no ``_state_at``/``_known_at`` call).
- ``pit=state_at`` → ``knn_state_at`` + ``search_state_at`` ONLY.
- ``pit=known_at`` → ``knn_known_at`` + ``search_known_at`` ONLY.
- Invalid ``pit.kind`` raises ``InvalidPITKind`` BEFORE the embedder runs.
- ``subject_filter`` reaches ONLY the vigent BM25 search (PIT variants don't
  accept it — mediano, f5-06 §7a.3).
- ``pit=None`` BFS (when enabled) gets ``pit_kind="state_at"`` + ``t=clock_now``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.contracts.persistence import FullTextHit, VectorHit
from seahorse.disclosure.types import PITPoint
from seahorse.retrieval import InvalidPITKind, recall

from .conftest import _row


def _pit(kind: str, t: datetime) -> PITPoint:
    return PITPoint(kind=kind, t=t)  # type: ignore[arg-type]


def _v(ep_id: str) -> VectorHit:
    return VectorHit(ep_id=ep_id, distance=0.1, score=0.9)


class TestPitNoneRoutesToVigent:
    def test_vigent_knn_and_search_called(self, embedder, vector_repo, fts_repo, episode_repo):
        vector_repo.knn_hits = [_v("e1")]
        fts_repo.search_hits = [FullTextHit("e2", 1.0, 0.37)]
        recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        assert "knn" in vector_repo.calls
        assert "search" in fts_repo.calls
        assert "knn_state_at" not in vector_repo.calls
        assert "knn_known_at" not in vector_repo.calls
        assert "search_state_at" not in fts_repo.calls
        assert "search_known_at" not in fts_repo.calls
        assert vector_repo.calls["knn"][0]["vigent_only"] is True
        assert fts_repo.calls["search"][0]["vigent_only"] is True


class TestPitStateAtRoutes:
    def test_state_at_knn_and_search_only(
        self, embedder, vector_repo, fts_repo, episode_repo, clock_now
    ):
        t = clock_now
        recall(
            "q",
            pit=_pit("state_at", t),
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        assert "knn_state_at" in vector_repo.calls
        assert "search_state_at" in fts_repo.calls
        assert vector_repo.calls["knn_state_at"][0]["t"] == t
        assert fts_repo.calls["search_state_at"][0]["t"] == t
        # vigent + known_at never called (axis isolation R13).
        assert "knn" not in vector_repo.calls
        assert "knn_known_at" not in vector_repo.calls
        assert "search" not in fts_repo.calls
        assert "search_known_at" not in fts_repo.calls


class TestPitKnownAtRoutes:
    def test_known_at_knn_and_search_only(
        self, embedder, vector_repo, fts_repo, episode_repo, clock_now
    ):
        t = clock_now
        recall(
            "q",
            pit=_pit("known_at", t),
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        assert "knn_known_at" in vector_repo.calls
        assert "search_known_at" in fts_repo.calls
        assert "knn" not in vector_repo.calls
        assert "knn_state_at" not in vector_repo.calls
        assert "search" not in fts_repo.calls
        assert "search_state_at" not in fts_repo.calls


class TestValidateOnce:
    def test_invalid_pit_kind_raises_before_embedder(
        self, embedder, vector_repo, fts_repo, episode_repo
    ):
        with pytest.raises(InvalidPITKind):
            recall(
                "q",
                pit=_pit("future_at", datetime(2024, 1, 1, tzinfo=UTC)),
                embedder=embedder,
                vector_repo=vector_repo,
                fts_repo=fts_repo,
                episode_repo=episode_repo,
                k=10,
            )
        # Embedder never ran — validation fires before stage 1.
        assert embedder.calls == []
        assert vector_repo.calls == {}
        assert fts_repo.calls == {}


class TestSubjectFilterRouting:
    def test_subject_filter_passed_to_vigent_search_only(
        self, embedder, vector_repo, fts_repo, episode_repo
    ):
        recall(
            "q",
            pit=None,
            subject_filter="acct",
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        assert fts_repo.calls["search"][0]["subject_filter"] == "acct"

    def test_subject_filter_not_forwarded_to_state_at_search(
        self, embedder, vector_repo, fts_repo, episode_repo, clock_now
    ):
        # PIT search variants do NOT accept subject_filter (mediano); the engine
        # simply does not forward it.
        recall(
            "q",
            pit=_pit("state_at", clock_now),
            subject_filter="acct",
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        assert "subject_filter" not in fts_repo.calls["search_state_at"][0]


class TestPitNoneBfsUsesStateAtNow:
    def test_bfs_gets_state_at_and_clock_now(
        self, embedder, vector_repo, fts_repo, episode_repo, bfs_repo, clock_now, fixed_clock
    ):
        vector_repo.knn_hits = [_v("e1")]
        bfs_repo.rows = [_row("eB", created_at=clock_now)]
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
        assert bfs_repo.calls[0]["pit_kind"] == "state_at"
        assert bfs_repo.calls[0]["pit"] == clock_now


class TestKnownAtBfsDropped:
    def test_known_at_bfs_raised_and_caught_bfs_axis_dropped(
        self, embedder, vector_repo, fts_repo, episode_repo, bfs_repo, clock_now
    ):
        # known_at + bfs enabled + not signed off -> BfsKnownAtUnsupported raised
        # by _bfs and caught by the engine; the BFS axis is dropped (rows=[]).
        # Seed known_at kNN hits so stage 1 has an anchor (pit=known_at routes to
        # knn_known_at, not the vigent knn).
        vector_repo.knn_known_at_hits = [_v("e1")]
        result = recall(
            "q",
            pit=_pit("known_at", clock_now),
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            graph_repo=bfs_repo,
            k=10,
            bfs_as_index_enabled=True,
            bfs_known_at_supported=False,
        )
        # BFS repo never called (the engine raised before calling).
        assert bfs_repo.calls == []
        # No BFS source contributed; e1 still returned from vector.
        assert [c.ep_id for c in result] == ["e1"]

    def test_known_at_bfs_supported_calls_repo(
        self, embedder, vector_repo, fts_repo, episode_repo, bfs_repo, clock_now
    ):
        vector_repo.knn_known_at_hits = [_v("e1")]
        bfs_repo.rows = [_row("eB", created_at=clock_now)]
        recall(
            "q",
            pit=_pit("known_at", clock_now),
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
