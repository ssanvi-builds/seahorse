"""Session-restricted two-stage recall — the engine's session boost stage.

The two-stage fix (WI-4): the engine has no session-restricted recall; this
stage identifies the top session as the session owning the MAJORITY of the
fused top-k candidates (aggregates evidence — the single top-1 candidate's
session only identifies the golden session ~49% of the time), fetches ALL of
its episodes (SQL ``WHERE session_id = ?``, denormalized by migration 012),
re-ranks them by hybrid (vector + BM25 over the bodies), and APPENDS the
session's FRESH episodes (those outside the fused top-k) by their hybrid
score. Fused candidates are never re-scored — they keep their exact RRF scores
and positions — so the baseline is preserved verbatim: a wrong identification
can only displace the weakest bottom candidates, never a mid-list golden
episode. Honest degrade: no session_id resolvable or no session episodes →
keep the fused order (never invented).

Signals:
- ``_normalize_tokens`` — the BM25 tokenizer (lower-case, non-alphanumeric stripped).
- ``_hybrid_rank_episodes`` — vector + BM25 RRF over the bodies, ep_id tie-break.
- ``_apply_session_boost`` — majority session identification + merge by hybrid
  score; honest no-op on no resolvable session / empty session episodes.
- ``recall`` integration — the boost runs when ``session_boost=True`` (explicit
  — the seam is default-OFF: the authoritative LMEB-S run proved the automatic
  two-stage net-harmful) AND ``index_repo`` is present AND ``pit is None``; PIT
  queries reproduce state as-of-t with pure RRF (never boosted); no
  ``index_repo`` → no session_ids to resolve → no boost.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.contracts.persistence import FullTextHit, VectorHit
from seahorse.contracts.retrieval import FusedCandidate
from seahorse.disclosure.types import PITPoint
from seahorse.retrieval import recall
from seahorse.retrieval.constants import RRF_K
from seahorse.retrieval.engine import (
    _apply_session_boost,
    _hybrid_rank_episodes,
    _normalize_tokens,
)

from .conftest import _ep, _row

NOW = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)


def _pit(kind: str, t: datetime) -> PITPoint:
    return PITPoint(kind=kind, t=t)  # type: ignore[arg-type]


def _candidate(ep_id: str, score: float, sources: tuple[str, ...] = ("vector",)) -> FusedCandidate:
    return FusedCandidate(ep_id=ep_id, score=score, sources=sources)


class TestNormalizeTokens:
    def test_lowercases_and_strips_non_alphanumeric(self) -> None:
        assert _normalize_tokens("Alpha-Beta, gamma!") == ["alpha", "beta", "gamma"]

    def test_empty_and_punctuation_only(self) -> None:
        assert _normalize_tokens("") == []
        assert _normalize_tokens("!!! ...") == []


class TestHybridRankEpisodes:
    def test_ranks_by_vector_plus_bm25_rrf(self, embedder) -> None:
        # Vector agrees with BM25: e2 is top in both sources.
        embedder.similarity_scores = [0.5, 0.9, 0.1]
        episodes = [
            _ep("e1", created_at=NOW, body="alpha"),
            _ep("e2", created_at=NOW, body="alpha beta"),
            _ep("e3", created_at=NOW, body="gamma"),
        ]
        ranked = _hybrid_rank_episodes("alpha beta", "VEC", episodes, embedder)
        assert ranked == ["e2", "e1", "e3"]

    def test_tie_break_by_ep_id(self, embedder) -> None:
        # Vector ranks e1 first, BM25 ranks e2 first → RRF tie → ep_id asc.
        embedder.similarity_scores = [0.1, 0.9]  # e2, e1
        episodes = [
            _ep("e2", created_at=NOW, body="alpha beta"),
            _ep("e1", created_at=NOW, body="alpha"),
        ]
        ranked = _hybrid_rank_episodes("alpha beta", "VEC", episodes, embedder)
        assert ranked == ["e1", "e2"]

    def test_empty_bodies_return_empty(self, embedder) -> None:
        episodes = [_ep("e1", created_at=NOW, body=None)]
        assert _hybrid_rank_episodes("q", "VEC", episodes, embedder) == []


class TestApplySessionBoost:
    def test_appends_fresh_session_episodes_without_rescoring_fused(
        self, embedder, index_repo, episode_repo
    ) -> None:
        # fused: e1(S1, 0.030), e2(S1, 0.028), e3(S2, 0.033). S1 majority.
        # Session S1 has e1, e2, e4 (e4 fresh, outside the fused top-k).
        # Hybrid re-rank of S1 gives e2/e4 1/61+1/62 and e1 2/63 — HIGHER than
        # the fused estimates — but append-only NEVER re-scores fused
        # candidates: e1/e2 keep their exact fused scores, only e4 (fresh)
        # enters with its hybrid score. The strong non-session e3 stays top.
        fused = [
            _candidate("e1", 0.030),
            _candidate("e2", 0.028, ("bm25",)),
            _candidate("e3", 0.033),
        ]
        for ep_id, session in (("e1", "S1"), ("e2", "S1"), ("e3", "S2")):
            index_repo.add(_row(ep_id, created_at=NOW, session_id=session))
        index_repo.add(_row("e4", created_at=NOW, session_id="S1"))
        for ep_id, body in (
            ("e1", "alpha"),
            ("e2", "alpha beta"),
            ("e3", "gamma"),
            ("e4", "alpha beta gamma"),
        ):
            episode_repo.add(_ep(ep_id, created_at=NOW, body=body))
        embedder.similarity_scores = [1.0, 2.0, 3.0]  # e1, e2, e4

        result = _apply_session_boost(
            fused, "alpha beta", "VEC", index_repo, episode_repo, embedder, k=4
        )
        # e3 (non-session) keeps its top spot; e4 (fresh) enters on hybrid
        # score; e1/e2 keep their fused scores AND positions relative to fused
        # (e2's hybrid 0.0325 would have jumped it above e4 in a re-scoring
        # design — append-only keeps it last).
        assert [c.ep_id for c in result] == ["e3", "e4", "e1", "e2"]
        assert result[0].score == pytest.approx(0.033)
        assert result[0].sources == ("vector",)
        # e4 is fresh — hybrid score, only the session marker.
        assert result[1].score == pytest.approx(1 / (RRF_K + 1) + 1 / (RRF_K + 2))
        assert result[1].sources == ("session",)
        # e1/e2 are session episodes NOT re-scored — fused scores and sources
        # preserved verbatim (no session marker: nothing was changed).
        assert result[2].score == pytest.approx(0.030)
        assert result[2].sources == ("vector",)
        assert result[3].score == pytest.approx(0.028)
        assert result[3].sources == ("bm25",)

    def test_identifies_session_by_majority_not_top1(
        self, embedder, index_repo, episode_repo
    ) -> None:
        # fused top-1 (e1) is S1, but S2 owns the MAJORITY (e2 + e3). The
        # boost targets S2 — the single top-1 candidate would target S1 (which
        # has only e1 → no fresh episodes → no-op). Session S2 has e2, e3, e4
        # (e4 fresh); the majority identification recovers e4.
        fused = [
            _candidate("e1", 0.030),
            _candidate("e2", 0.028, ("bm25",)),
            _candidate("e3", 0.026),
        ]
        for ep_id, session in (("e1", "S1"), ("e2", "S2"), ("e3", "S2")):
            index_repo.add(_row(ep_id, created_at=NOW, session_id=session))
        index_repo.add(_row("e4", created_at=NOW, session_id="S2"))
        for ep_id, body in (
            ("e1", "alpha"),
            ("e2", "alpha"),
            ("e3", "gamma"),
            ("e4", "alpha beta gamma"),
        ):
            episode_repo.add(_ep(ep_id, created_at=NOW, body=body))
        embedder.similarity_scores = [2.0, 1.0, 3.0]  # e2, e3, e4

        result = _apply_session_boost(
            fused, "alpha beta", "VEC", index_repo, episode_repo, embedder, k=3
        )
        # S2's fresh e4 enters (majority identification); e1 (top-1's S1) is
        # NOT demoted — append-only never touches non-session candidates.
        assert [c.ep_id for c in result] == ["e4", "e1", "e2"]
        assert result[0].score == pytest.approx(2 / (RRF_K + 1))
        assert result[0].sources == ("session",)  # e4 fresh
        assert result[1].score == pytest.approx(0.030)
        assert result[1].sources == ("vector",)  # e1 untouched
        assert result[2].score == pytest.approx(0.028)
        assert result[2].sources == ("bm25",)  # e2 untouched

    def test_fused_order_preserved_when_no_fresh_episodes(
        self, embedder, index_repo, episode_repo
    ) -> None:
        # All session episodes are already in the fused top-k → nothing fresh
        # to append → the fused order (by score) is preserved verbatim.
        fused = [_candidate("e1", 0.5), _candidate("e2", 0.4)]
        for ep_id in ("e1", "e2"):
            index_repo.add(_row(ep_id, created_at=NOW, session_id="S1"))
        for ep_id, body in (("e1", "alpha"), ("e2", "alpha beta")):
            episode_repo.add(_ep(ep_id, created_at=NOW, body=body))
        embedder.similarity_scores = [1.0, 2.0]

        result = _apply_session_boost(
            fused, "alpha beta", "VEC", index_repo, episode_repo, embedder, k=2
        )
        assert [c.ep_id for c in result] == ["e1", "e2"]
        assert result[0].score == 0.5 and result[1].score == 0.4
        assert all("session" not in c.sources for c in result)

    def test_truncates_to_k(self, embedder, index_repo, episode_repo) -> None:
        # The fresh session episode e2 enters by hybrid score, but k=1 keeps
        # only the top fused candidate (e1) — the append is truncated.
        fused = [_candidate("e1", 0.5)]
        index_repo.add(_row("e1", created_at=NOW, session_id="S1"))
        index_repo.add(_row("e2", created_at=NOW, session_id="S1"))
        for ep_id, body in (("e1", "alpha"), ("e2", "alpha beta")):
            episode_repo.add(_ep(ep_id, created_at=NOW, body=body))
        embedder.similarity_scores = [1.0, 2.0]

        result = _apply_session_boost(
            fused, "alpha beta", "VEC", index_repo, episode_repo, embedder, k=1
        )
        assert len(result) == 1
        assert result[0].ep_id == "e1"

    def test_honest_noop_when_no_session_resolvable(
        self, embedder, index_repo, episode_repo
    ) -> None:
        fused = [_candidate("e1", 0.5), _candidate("e2", 0.4)]
        index_repo.add(_row("e1", created_at=NOW, session_id=None))
        index_repo.add(_row("e2", created_at=NOW, session_id=None))
        result = _apply_session_boost(
            fused, "q", "VEC", index_repo, episode_repo, embedder, k=3
        )
        assert result is fused  # unchanged (no session to boost)

    def test_honest_noop_when_session_has_no_bodies(
        self, embedder, index_repo, episode_repo
    ) -> None:
        fused = [_candidate("e1", 0.5)]
        index_repo.add(_row("e1", created_at=NOW, session_id="S1"))
        episode_repo.add(_ep("e1", created_at=NOW, body=None))
        result = _apply_session_boost(
            fused, "q", "VEC", index_repo, episode_repo, embedder, k=3
        )
        assert result is fused

    def test_honest_noop_on_empty_fused(self, embedder, index_repo, episode_repo) -> None:
        assert (
            _apply_session_boost([], "q", "VEC", index_repo, episode_repo, embedder, k=3) == []
        )


class TestRecallSessionBoost:
    def _setup(self, embedder, vector_repo, fts_repo, episode_repo, index_repo) -> None:
        # Stage 1: vector + bm25 both surface e1 (session S1) and e3 (session S2).
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9), VectorHit("e3", 0.2, 0.8)]
        fts_repo.search_hits = [FullTextHit("e1", 1.0, 0.37), FullTextHit("e3", 2.0, 0.13)]
        for ep_id, session in (("e1", "S1"), ("e3", "S2")):
            index_repo.add(_row(ep_id, created_at=NOW, session_id=session))
        # Session S1 has e1 + e2 (e2 fresh, not in the fused top-k).
        index_repo.add(_row("e2", created_at=NOW, session_id="S1"))
        for ep_id, body in (("e1", "alpha"), ("e2", "alpha beta"), ("e3", "gamma")):
            episode_repo.add(_ep(ep_id, created_at=NOW, body=body))
        embedder.similarity_scores = [1.0, 2.0, 0.0]  # e1, e2, e3

    def test_boost_applied_when_index_repo_present_and_pit_none(
        self, embedder, vector_repo, fts_repo, episode_repo, index_repo, fixed_clock
    ) -> None:
        self._setup(embedder, vector_repo, fts_repo, episode_repo, index_repo)
        result = recall(
            "alpha beta",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            index_repo=index_repo,
            k=3,
            clock=fixed_clock,
            # Explicit: the boost is default-OFF (net-harmful on LMEB-S); this
            # test exercises the boost path on demand.
            session_boost=True,
        )
        # e1 keeps its fused top position (never re-scored); e2 (fresh session
        # episode, recovered by the boost) enters at position 2; e3 stays last.
        assert [c.ep_id for c in result] == ["e1", "e2", "e3"]
        assert "session" in result[1].sources  # e2 fresh — the recovered episode

    def test_no_boost_when_pit_is_not_none(
        self, embedder, vector_repo, fts_repo, episode_repo, index_repo, fixed_clock
    ) -> None:
        self._setup(embedder, vector_repo, fts_repo, episode_repo, index_repo)
        result = recall(
            "alpha beta",
            pit=_pit("state_at", NOW),
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            index_repo=index_repo,
            k=3,
            clock=fixed_clock,
        )
        # PIT reproduces state as-of-t with pure RRF — never boosted.
        assert all("session" not in c.sources for c in result)

    def test_no_boost_when_index_repo_is_none(
        self, embedder, vector_repo, fts_repo, episode_repo, fixed_clock
    ) -> None:
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9)]
        fts_repo.search_hits = [FullTextHit("e1", 1.0, 0.37)]
        episode_repo.add(_ep("e1", created_at=NOW, body="alpha"))
        result = recall(
            "alpha",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            index_repo=None,
            k=3,
            clock=fixed_clock,
        )
        assert all("session" not in c.sources for c in result)
