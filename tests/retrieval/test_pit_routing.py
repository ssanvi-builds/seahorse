"""PIT routing + axis-isolation tests.

The two bi-temporal axes NEVER mix within one recall. ``pit.kind`` is validated
ONCE at the entrypoint and the SAME kind fans to ALL sources. ``pit=None``
resolves to the current-state path (kNN ``vigent_only=True``; BM25
``vigent_only=True``; BFS ``state_at`` at the injected ``now``).

Signals:
- ``pit=None`` → current-state knn + current-state search (no ``_state_at``/
  ``_known_at`` call).
- ``pit=state_at`` → ``knn_state_at`` + ``search_state_at`` ONLY.
- ``pit=known_at`` → ``knn_known_at`` + ``search_known_at`` ONLY.
- Invalid ``pit.kind`` raises ``RetrievalInvalidPITKind`` BEFORE the embedder runs.
- ``subject_filter`` reaches ONLY the current-state BM25 search (PIT variants
  don't accept it — a medium-term goal).
- ``pit=None`` BFS (when enabled) gets ``pit_kind="state_at"`` + ``t=clock_now``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.contracts.persistence import FullTextHit, VectorHit
from seahorse.disclosure.types import PITPoint
from seahorse.retrieval import RetrievalInvalidPITKind, recall

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
        # current-state + known_at never called (axis isolation).
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
        with pytest.raises(RetrievalInvalidPITKind):
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
        # PIT search variants do NOT accept subject_filter (a medium-term goal);
        # the engine simply does not forward it.
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
        # knn_known_at, not the current-state knn).
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


class TestRetrievalInvalidPITKindAttribution:
    """The retrieval entrypoint error attributes to the engine, not the facade.

    Before the rename, ``retrieval.errors.InvalidPITKind`` (a plain ``Exception``
    raised by the engine's recall) shared its ``__name__`` with
    ``facade.errors.InvalidPITKind`` (a ``SeahorseError`` carrying
    ``.code = E_INVALID_PIT_KIND``, owned by the facade). Both
    ``mcp.errors._ORIGIN_BY_CLASS`` and ``cli.exit_codes._ORIGIN_BY_CLASS`` match
    on ``type(exc).__name__``, so both mapped to the facade — mis-attributing the
    engine's error to the facade. The rename to ``RetrievalInvalidPITKind`` gives
    the two classes distinct ``__name__`` so each table attributes to its real
    owner. These tests pin the engine attribution through BOTH projections
    (MCP + CLI).
    """

    def test_mcp_translate_attributes_to_component_11(self) -> None:
        from seahorse.mcp.errors import translate

        exc = RetrievalInvalidPITKind("bogus")
        resp = translate(exc, request_id=1)
        # Plain Exception (no .code) → generic -32603, exception_class set, engine.
        assert resp["error"]["code"] == -32603
        assert resp["error"]["data"]["exception_class"] == "RetrievalInvalidPITKind"
        assert resp["error"]["data"]["component"] == "#11"

    def test_cli_translate_attributes_to_component_11(self) -> None:
        from seahorse.cli.exit_codes import translate

        exc = RetrievalInvalidPITKind("bogus")
        exit_code, info = translate(exc)
        # Plain Exception (no .code, not in CAT_B) → generic fallback, engine.
        from seahorse.cli.exit_codes import EXIT_GENERAL

        assert exit_code == EXIT_GENERAL
        assert info["exception_class"] == "RetrievalInvalidPITKind"
        assert info["component"] == "#11"

    def test_facade_invalid_pit_kind_still_attributes_to_12(self) -> None:
        # Regression guard: the facade's InvalidPITKind (SeahorseError, .code) MUST
        # keep attributing to the facade — the rename must not bleed into the facade path.
        from seahorse.facade.errors import E_INVALID_PIT_KIND, InvalidPITKind
        from seahorse.mcp.errors import CAT_A, translate

        resp = translate(InvalidPITKind("bogus"), request_id=1)
        assert resp["error"]["code"] == CAT_A[E_INVALID_PIT_KIND]
        assert resp["error"]["data"]["seahorse_code"] == E_INVALID_PIT_KIND
        assert resp["error"]["data"]["component"] == "#12"
