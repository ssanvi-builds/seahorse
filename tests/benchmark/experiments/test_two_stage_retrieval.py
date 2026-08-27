"""Tests for the two-stage session→episode experiment (``two_stage_retrieval.py``).

The experiment measures whether re-ranking within the golden session (hybrid:
vector + BM25 RRF over the episode bodies) raises episode recall above the
global top-10 baseline. The synthetic runs verify the MECHANICS (no model):
case A (two-stage hit), case B (within-session miss), case C (session miss);
the authoritative decision comes from an LMEB-S run.
"""

from __future__ import annotations

import pytest

from seahorse.benchmark.experiments.two_stage_retrieval import (
    TWO_STAGE_IMPROVEMENT_THRESHOLD,
    TwoStageExperimentResult,
    _default_embedder,
    _hybrid_rank_within_session,
    _stub_episode,
    decide_two_stage,
    render_two_stage_report,
    run_two_stage_experiment,
)


def _result(
    *,
    session_recall: float = 0.79,
    episode_recall: float = 0.533,
    within_top1: float = 0.4,
    within_top3: float = 0.6,
    within_top5: float = 0.8,
    two_stage_1: float = 0.3,
    two_stage_3: float = 0.5,
    two_stage_5: float = 0.6,
    n_queries: int = 100,
    n_localized: int = 92,
    n_session_hit: int = 70,
    n_session_miss: int = 22,
    n_within_hit_1: int = 30,
    n_within_hit_3: int = 50,
    n_within_hit_5: int = 60,
    regime: str = "hybrid",
) -> TwoStageExperimentResult:
    return TwoStageExperimentResult(
        session_recall_at_k=session_recall,
        episode_recall_at_k=episode_recall,
        within_session_top1=within_top1,
        within_session_top3=within_top3,
        within_session_top5=within_top5,
        two_stage_episode_recall_1=two_stage_1,
        two_stage_episode_recall_3=two_stage_3,
        two_stage_episode_recall_5=two_stage_5,
        n_queries=n_queries,
        n_localized=n_localized,
        n_session_hit=n_session_hit,
        n_session_miss=n_session_miss,
        n_within_hit_1=n_within_hit_1,
        n_within_hit_3=n_within_hit_3,
        n_within_hit_5=n_within_hit_5,
        regime=regime,
    )


class TestHybridRankWithinSession:
    def test_ranks_answer_episode_first(self) -> None:
        """The hybrid re-rank (vector + BM25 RRF) puts the episode sharing the
        query's distinctive tokens first."""
        embedder = _default_embedder("synthetic")
        episodes = [
            _stub_episode("answer", "The capital of Avalon is Amber Ridge."),
            _stub_episode("decoy", "The Avalon economy grew last quarter."),
        ]
        ranked = _hybrid_rank_within_session("capital of Avalon", episodes, embedder)
        assert ranked[0] == "answer"
        assert ranked[1] == "decoy"

    def test_ranks_decoy_first_when_it_shares_more_tokens(self) -> None:
        """A decoy sharing more query tokens outranks the answer episode."""
        embedder = _default_embedder("synthetic")
        episodes = [
            _stub_episode(
                "answer", "The Amber Ridge stands tall on the northern ridge."
            ),
            _stub_episode(
                "decoy", "The capital of Avalon is disputed by the Avalon court."
            ),
        ]
        ranked = _hybrid_rank_within_session("capital of Avalon", episodes, embedder)
        assert ranked[0] == "decoy"
        assert ranked[1] == "answer"

    def test_empty_bodies_return_empty(self) -> None:
        embedder = _default_embedder("synthetic")
        episodes = [_stub_episode("a", ""), _stub_episode("b", "")]
        assert _hybrid_rank_within_session("query", episodes, embedder) == []


class TestDecideTwoStage:
    def test_invalid_regime_on_fallback_g2(self) -> None:
        decision = decide_two_stage(_result(regime="fallback_g2"))
        assert decision["decision"] == "invalid_regime"
        assert decision["flip"] is False

    def test_two_stage_indicated_when_top5_beats_baseline(self) -> None:
        """two_stage@5 = 0.60 >= episode_recall 0.533 + 0.05 -> the session's
        top-5 (hybrid re-ranked) surfaces the answer more often than the global
        top-10; the two-stage is indicated."""
        decision = decide_two_stage(_result(two_stage_5=0.60, episode_recall=0.533))
        assert decision["decision"] == "two_stage_indicated"
        assert decision["flip"] is True

    def test_two_stage_not_indicated_when_below_threshold(self) -> None:
        """two_stage@5 = 0.55 < 0.533 + 0.05 -> the two-stage does not beat the
        baseline; document and close."""
        decision = decide_two_stage(_result(two_stage_5=0.55, episode_recall=0.533))
        assert decision["decision"] == "two_stage_not_indicated"
        assert decision["flip"] is False

    def test_threshold_boundary(self) -> None:
        """two_stage@5 == episode_recall + threshold exactly -> indicated (>=)."""
        decision = decide_two_stage(
            _result(
                two_stage_5=0.5 + TWO_STAGE_IMPROVEMENT_THRESHOLD,
                episode_recall=0.5,
            )
        )
        assert decision["decision"] == "two_stage_indicated"


class TestRenderTwoStageReport:
    def test_render_contains_metrics_and_decision(self) -> None:
        result = run_two_stage_experiment(corpus="synthetic")
        decision = decide_two_stage(result)
        report = render_two_stage_report(result, decision)
        assert "session recall@10" in report
        assert "episode recall@10" in report
        assert "two-stage episode recall@5" in report
        assert f"decision: {decision['decision']}" in report


class TestRunTwoStageSynthetic:
    def test_mechanics_three_cases(self) -> None:
        """Case A (two-stage hit) + case B (within-session miss) + case C
        (session miss): the counts and rates reproduce the expected mechanics."""
        result = run_two_stage_experiment(corpus="synthetic")
        assert result.regime == "hybrid"
        assert result.n_queries == 9
        assert result.n_localized == 9
        assert result.n_session_hit == 6
        assert result.n_session_miss == 3
        assert result.session_recall_at_k == pytest.approx(6 / 9)
        assert result.episode_recall_at_k == pytest.approx(3 / 9)
        assert result.within_session_top1 == pytest.approx(3 / 6)
        assert result.within_session_top3 == pytest.approx(3 / 6)
        assert result.within_session_top5 == pytest.approx(6 / 6)
        assert result.two_stage_episode_recall_1 == pytest.approx(3 / 9)
        assert result.two_stage_episode_recall_3 == pytest.approx(3 / 9)
        assert result.two_stage_episode_recall_5 == pytest.approx(6 / 9)
        # The invariant: session hits + misses == localized.
        assert result.n_session_hit + result.n_session_miss == result.n_localized

    def test_unknown_corpus_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown corpus"):
            run_two_stage_experiment(corpus="nope")
