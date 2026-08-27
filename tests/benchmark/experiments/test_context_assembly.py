"""Tests for the context-assembly experiment (``context_assembly.py``).

The experiment decomposes the answer-in-context gap (episode recall 0.533 ->
answer-in-context 0.350) into disjoint per-query buckets: ``context_hit`` /
``hydration_failure`` / ``retrieval_miss`` / ``single_token`` / ``unlocalized``.
The synthetic runs verify the MECHANICS (no model): case A (context hit), case
B (retrieval miss), case C (single-token metric ceiling); the authoritative
decision comes from an LMEB-S run.
"""

from __future__ import annotations

import pytest

from seahorse.benchmark.experiments.context_assembly import (
    ContextAssemblyExperimentResult,
    _classify_query,
    decide_context_assembly,
    render_context_assembly_report,
    run_context_assembly_experiment,
)
from seahorse.benchmark.experiments.episode_locator import (
    STATUS_SINGLE_TOKEN,
    STATUS_UNLOCALIZED,
    STATUS_VERBATIM,
)


def _result(
    *,
    episode_recall: float = 0.5,
    answer_in_context: float = 0.3,
    answer_in_context_summary: float = 0.2,
    n_queries: int = 10,
    n_episodes: int = 100,
    n_localized: int = 9,
    n_unlocalized: int = 1,
    n_verbatim: int = 5,
    n_fragment: int = 2,
    n_single_token: int = 1,
    n_context_hit: int = 3,
    n_hydration_failure: int = 1,
    n_retrieval_miss: int = 4,
    regime: str = "hybrid",
) -> ContextAssemblyExperimentResult:
    return ContextAssemblyExperimentResult(
        episode_recall_at_k=episode_recall,
        answer_in_context_rate=answer_in_context,
        answer_in_context_summary=answer_in_context_summary,
        n_queries=n_queries,
        n_episodes=n_episodes,
        n_localized=n_localized,
        n_unlocalized=n_unlocalized,
        n_verbatim=n_verbatim,
        n_fragment=n_fragment,
        n_single_token=n_single_token,
        n_context_hit=n_context_hit,
        n_hydration_failure=n_hydration_failure,
        n_retrieval_miss=n_retrieval_miss,
        regime=regime,
    )


class TestClassifyQuery:
    def test_unlocalized_is_metric_ceiling(self) -> None:
        assert (
            _classify_query(
                status=STATUS_UNLOCALIZED,
                answer_ep_ids={"a"},
                retrieved_ep_ids={"a"},
                context_hit=True,
            )
            == "unlocalized"
        )

    def test_single_token_is_metric_ceiling(self) -> None:
        assert (
            _classify_query(
                status=STATUS_SINGLE_TOKEN,
                answer_ep_ids={"a"},
                retrieved_ep_ids={"a"},
                context_hit=True,
            )
            == "single_token"
        )

    def test_context_hit_when_fragment_present(self) -> None:
        assert (
            _classify_query(
                status=STATUS_VERBATIM,
                answer_ep_ids={"a"},
                retrieved_ep_ids={"a"},
                context_hit=True,
            )
            == "context_hit"
        )

    def test_hydration_failure_when_retrieved_but_body_missing(self) -> None:
        """The answer-bearing episode IS in the top-10 but its body is absent
        from the assembled context (the ``top_bodies`` injection drops it) ->
        the fragment is lost -> hydration failure."""
        assert (
            _classify_query(
                status=STATUS_VERBATIM,
                answer_ep_ids={"a"},
                retrieved_ep_ids={"a"},
                context_hit=False,
            )
            == "hydration_failure"
        )

    def test_retrieval_miss_when_episode_outside_top_k(self) -> None:
        assert (
            _classify_query(
                status=STATUS_VERBATIM,
                answer_ep_ids={"a"},
                retrieved_ep_ids={"b"},
                context_hit=False,
            )
            == "retrieval_miss"
        )


class TestDecideContextAssembly:
    def test_invalid_regime_on_fallback_g2(self) -> None:
        decision = decide_context_assembly(_result(regime="fallback_g2"))
        assert decision["decision"] == "invalid_regime"
        assert decision["flip"] is False

    def test_hydration_bottleneck_when_hydration_rate_high(self) -> None:
        """hydration_failure_rate = 2/9 = 0.222 >= 0.10 -> the assembler IS
        defective (retrieved episodes without a body in the context)."""
        decision = decide_context_assembly(
            _result(n_hydration_failure=2, n_retrieval_miss=2, n_context_hit=4)
        )
        assert decision["decision"] == "hydration_bottleneck"
        assert decision["flip"] is True

    def test_retrieval_ceiling_when_retrieval_miss_rate_high(self) -> None:
        """retrieval_miss_rate = 4/9 = 0.444 >= 0.40 -> the gap is the episode
        recall ceiling (the answer-bearing episode is outside the top-10)."""
        decision = decide_context_assembly(
            _result(n_hydration_failure=0, n_retrieval_miss=4, n_context_hit=4)
        )
        assert decision["decision"] == "retrieval_ceiling"
        assert decision["flip"] is False

    def test_metric_ceiling_when_metric_ceiling_rate_high(self) -> None:
        """metric_ceiling_rate = (1+1)/10 = 0.20 >= 0.15 -> single_token +
        unlocalized dominate the gap (never hits with min_ngram=2)."""
        decision = decide_context_assembly(
            _result(n_hydration_failure=0, n_retrieval_miss=0, n_context_hit=8)
        )
        assert decision["decision"] == "metric_ceiling"
        assert decision["flip"] is False

    def test_context_assembly_ok_when_nothing_dominates(self) -> None:
        decision = decide_context_assembly(
            _result(
                n_queries=10,
                n_localized=10,
                n_unlocalized=0,
                n_single_token=0,
                n_hydration_failure=0,
                n_retrieval_miss=0,
                n_context_hit=10,
            )
        )
        assert decision["decision"] == "context_assembly_ok"
        assert decision["flip"] is False

    def test_threshold_boundary_hydration_bottleneck(self) -> None:
        decision = decide_context_assembly(
            _result(
                n_queries=10,
                n_localized=10,
                n_unlocalized=0,
                n_single_token=0,
                n_hydration_failure=1,
                n_retrieval_miss=0,
                n_context_hit=9,
            )
        )
        assert decision["decision"] == "hydration_bottleneck"

    def test_threshold_boundary_retrieval_ceiling(self) -> None:
        decision = decide_context_assembly(
            _result(
                n_queries=10,
                n_localized=10,
                n_unlocalized=0,
                n_single_token=0,
                n_hydration_failure=0,
                n_retrieval_miss=4,
                n_context_hit=6,
            )
        )
        assert decision["decision"] == "retrieval_ceiling"

    def test_threshold_boundary_metric_ceiling(self) -> None:
        decision = decide_context_assembly(
            _result(
                n_queries=20,
                n_localized=19,
                n_unlocalized=1,
                n_single_token=2,
                n_hydration_failure=0,
                n_retrieval_miss=0,
                n_context_hit=17,
            )
        )
        assert decision["decision"] == "metric_ceiling"


class TestRenderContextAssemblyReport:
    def test_render_contains_metrics_and_decision(self) -> None:
        result = run_context_assembly_experiment(corpus="synthetic")
        decision = decide_context_assembly(result)
        report = render_context_assembly_report(result, decision)
        assert "episode recall@10" in report
        assert "answer-in-context rate" in report
        assert "hydration failures" in report
        assert "retrieval misses" in report
        assert f"decision: {decision['decision']}" in report


class TestRunContextAssemblySynthetic:
    def test_mechanics_three_cases(self) -> None:
        """Case A (context hit) + case B (retrieval miss) + case C (single-token
        metric ceiling): the disjoint buckets sum to n_queries and the rates
        reproduce the expected mechanics."""
        result = run_context_assembly_experiment(corpus="synthetic")
        assert result.regime == "hybrid"
        assert result.n_queries == 9
        assert result.n_localized == 9
        assert result.n_unlocalized == 0
        assert result.n_verbatim == 7
        assert result.n_fragment == 0
        assert result.n_single_token == 2
        assert result.n_context_hit == 3
        assert result.n_hydration_failure == 0
        assert result.n_retrieval_miss == 4
        assert result.episode_recall_at_k == pytest.approx(5 / 9)
        assert result.answer_in_context_rate == pytest.approx(3 / 9)
        # The disjoint-bucket invariant.
        assert (
            result.n_context_hit
            + result.n_hydration_failure
            + result.n_retrieval_miss
            + result.n_single_token
            + result.n_unlocalized
            == result.n_queries
        )

    def test_unknown_corpus_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown corpus"):
            run_context_assembly_experiment(corpus="nope")


class TestRunExperimentWiring:
    def test_context_assembly_via_run_experiment(self) -> None:
        """``run_experiment(experiment='context_assembly')`` delegates to the
        module and renders the report."""
        from seahorse.benchmark.experiments.runner import (
            render_experiment_report,
            run_experiment,
        )

        report = run_experiment(experiment="context_assembly", corpus="synthetic")
        assert report.experiment == "context_assembly"
        assert report.decision["decision"] in (
            "hydration_bottleneck",
            "retrieval_ceiling",
            "metric_ceiling",
            "context_assembly_ok",
        )
        rendered = render_experiment_report(report)
        assert "Context-assembly experiment" in rendered

    def test_context_assembly_rejects_claude_mem_corpus(self) -> None:
        from seahorse.benchmark.experiments.runner import run_experiment

        with pytest.raises(ValueError, match="'synthetic' or 'lmeb-s'"):
            run_experiment(experiment="context_assembly", corpus="claude-mem")
