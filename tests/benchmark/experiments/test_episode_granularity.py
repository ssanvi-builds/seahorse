"""Tests for the episode-granularity experiment (``episode_granularity.py``).

The experiment measures session-level vs episode-level recall@10 — whether the
ANSWER-BEARING episode (not just its golden session) reaches the top-k, the
remaining A4 suspect. The synthetic runs verify the MECHANICS (no model): case
A (recoverable episode) vs case B (session retrieved, episode not); the
authoritative decision comes from an LMEB-S run.
"""

from __future__ import annotations

import pytest

from seahorse.benchmark.experiments.episode_granularity import (
    EPISODE_LEVEL_RECALL_THRESHOLD,
    WITHIN_SESSION_RANK_THRESHOLD,
    EpisodeGranularityExperimentResult,
    decide_episode_granularity,
    render_episode_granularity_report,
    run_episode_granularity_experiment,
)


def _result(
    *,
    session: float = 1.0,
    episode: float = 0.4,
    top1: float = 0.5,
    top3: float = 0.8,
    top5: float = 0.9,
    context: float = 0.5,
    regime: str = "hybrid",
) -> EpisodeGranularityExperimentResult:
    return EpisodeGranularityExperimentResult(
        session_level_recall_at_k=session,
        episode_level_recall_at_k=episode,
        within_session_top1=top1,
        within_session_top3=top3,
        within_session_top5=top5,
        answer_in_context_rate=context,
        n_queries=10,
        n_episodes=100,
        n_localized=10,
        n_unlocalized=0,
        regime=regime,
    )


class TestRunSynthetic:
    def test_mechanics_case_a_and_b(self) -> None:
        """Case A (answer episode retrievable) + case B (session only): session
        recall 1.0, episode recall 0.5, within-session top-1 only half."""
        result = run_episode_granularity_experiment(corpus="synthetic")
        assert result.regime == "hybrid"
        assert result.n_queries == 10
        assert result.n_localized == 10
        assert result.n_unlocalized == 0
        assert result.session_level_recall_at_k == 1.0
        assert result.episode_level_recall_at_k == 0.5
        assert result.within_session_top1 == 0.5
        assert result.within_session_top3 == 1.0
        assert result.within_session_top5 == 1.0
        assert result.answer_in_context_rate == 0.5

    def test_unknown_corpus_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown corpus"):
            run_episode_granularity_experiment(corpus="nope")


class TestDecideEpisodeGranularity:
    def test_reader_bottleneck_when_episode_recall_high(self) -> None:
        """episode-level recall >= 0.5 → the episode IS retrieved → reader."""
        decision = decide_episode_granularity(_result(episode=0.6))
        assert decision["decision"] == "reader_bottleneck"
        assert decision["flip"] is False

    def test_two_stage_retrieval_when_within_session_good(self) -> None:
        decision = decide_episode_granularity(_result(episode=0.3, top3=0.8))
        assert decision["decision"] == "two_stage_retrieval"
        assert decision["flip"] is True

    def test_episode_not_retrievable_when_both_low(self) -> None:
        decision = decide_episode_granularity(_result(episode=0.2, top3=0.3))
        assert decision["decision"] == "episode_not_retrievable"
        assert decision["flip"] is False

    def test_threshold_boundary_reader_bottleneck(self) -> None:
        decision = decide_episode_granularity(
            _result(episode=EPISODE_LEVEL_RECALL_THRESHOLD)
        )
        assert decision["decision"] == "reader_bottleneck"

    def test_threshold_boundary_two_stage(self) -> None:
        decision = decide_episode_granularity(
            _result(episode=0.4, top3=WITHIN_SESSION_RANK_THRESHOLD)
        )
        assert decision["decision"] == "two_stage_retrieval"

    def test_invalid_regime_on_fallback_g2(self) -> None:
        decision = decide_episode_granularity(_result(regime="fallback_g2"))
        assert decision["decision"] == "invalid_regime"
        assert decision["flip"] is False


class TestRenderEpisodeGranularityReport:
    def test_render_contains_metrics_and_decision(self) -> None:
        result = run_episode_granularity_experiment(corpus="synthetic")
        decision = decide_episode_granularity(result)
        report = render_episode_granularity_report(result, decision)
        assert "session-level recall@10" in report
        assert "episode-level recall@10" in report
        assert "within-session top-3" in report
        assert f"decision: {decision['decision']}" in report


class TestRunExperimentWiring:
    def test_episode_granularity_via_run_experiment(self) -> None:
        """``run_experiment(experiment='episode_granularity')`` delegates to the
        module and renders the report."""
        from seahorse.benchmark.experiments.runner import (
            render_experiment_report,
            run_experiment,
        )

        report = run_experiment(experiment="episode_granularity", corpus="synthetic")
        assert report.experiment == "episode_granularity"
        assert report.decision["decision"] in (
            "reader_bottleneck",
            "two_stage_retrieval",
            "episode_not_retrievable",
        )
        rendered = render_experiment_report(report)
        assert "Episode-granularity experiment" in rendered

    def test_episode_granularity_rejects_claude_mem_corpus(self) -> None:
        from seahorse.benchmark.experiments.runner import run_experiment

        with pytest.raises(ValueError, match="'synthetic' or 'lmeb-s'"):
            run_experiment(experiment="episode_granularity", corpus="claude-mem")
