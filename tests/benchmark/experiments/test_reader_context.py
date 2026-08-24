"""Tests for the reader-context A/B experiment (``reader_context.py``).

The experiment measures end-to-end accuracy across the three assembler modes
(summary | body | body_bounded) over ONE corpus — the evidence for hydrating
bodies in the product's answer path (the A4 ``reader_bottleneck``). The
synthetic runs verify the MECHANICS (no model); the authoritative decision
comes from an LMEB-S run.
"""

from __future__ import annotations

from seahorse.benchmark.experiments.reader_context import (
    READER_CONTEXT_DELTA_PP,
    ReaderContextExperimentResult,
    decide_reader_context,
    render_reader_context_report,
    run_reader_context_experiment,
)


class TestRunReaderContextSynthetic:
    def test_hybrid_regime_no_fallback(self) -> None:
        result = run_reader_context_experiment(corpus="synthetic")
        assert result.regime == "hybrid"
        assert result.n_queries == 10
        assert result.n_episodes >= 15

    def test_recall_is_the_shared_ceiling(self) -> None:
        """The context representation cannot change retrieval: recall@10 is the
        summary-mode ceiling and all modes share it (e2e accuracy is bounded by
        it)."""
        result = run_reader_context_experiment(corpus="synthetic")
        assert result.recall_at_k == 0.5
        # All modes are at least 0 and at most the ceiling.
        for e2e in (result.e2e_summary, result.e2e_body, result.e2e_body_bounded):
            assert 0.0 <= e2e <= result.recall_at_k

    def test_unknown_corpus_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="unknown corpus"):
            run_reader_context_experiment(corpus="nope")


class TestDecideReaderContext:
    @staticmethod
    def _result(
        e2e_summary: float, e2e_body: float, e2e_body_bounded: float, regime: str = "hybrid"
    ) -> ReaderContextExperimentResult:
        return ReaderContextExperimentResult(
            recall_at_k=0.5,
            e2e_summary=e2e_summary,
            e2e_body=e2e_body,
            e2e_body_bounded=e2e_body_bounded,
            n_queries=10,
            n_episodes=15,
            regime=regime,
        )

    def test_hydrate_body_when_body_recovers_more(self) -> None:
        decision = decide_reader_context(self._result(0.1, 0.5, 0.4))
        assert decision["decision"] == "hydrate_body"
        assert decision["flip"] is True

    def test_keep_summary_when_body_does_not_help(self) -> None:
        decision = decide_reader_context(self._result(0.4, 0.4, 0.42))
        assert decision["decision"] == "keep_summary"
        assert decision["flip"] is False

    def test_invalid_regime_on_fallback_g2(self) -> None:
        decision = decide_reader_context(self._result(0.0, 0.0, 0.0, regime="fallback_g2"))
        assert decision["decision"] == "invalid_regime"
        assert decision["flip"] is False

    def test_threshold_boundary(self) -> None:
        """At exactly the threshold the flip happens (>= DELTA_PP)."""
        decision = decide_reader_context(
            self._result(0.1, 0.1 + READER_CONTEXT_DELTA_PP, 0.0)
        )
        assert decision["decision"] == "hydrate_body"

    def test_bounded_can_be_the_best_mode(self) -> None:
        """The flip uses the BEST body mode — body_bounded may win when full
        bodies waste the window."""
        decision = decide_reader_context(self._result(0.1, 0.15, 0.45))
        assert decision["decision"] == "hydrate_body"
        assert "body_bounded" in decision["reason"]


class TestRenderReaderContextReport:
    def test_render_contains_metrics_and_decision(self) -> None:
        result = run_reader_context_experiment(corpus="synthetic")
        decision = decide_reader_context(result)
        report = render_reader_context_report(result, decision)
        assert "end-to-end accuracy (summary)" in report
        assert "end-to-end accuracy (body)" in report
        assert "end-to-end accuracy (body_bounded)" in report
        assert f"decision: {decision['decision']}" in report


class TestRunExperimentWiring:
    def test_reader_context_via_run_experiment(self) -> None:
        """``run_experiment(experiment='reader_context')`` delegates to the module
        and renders the report."""
        from seahorse.benchmark.experiments.runner import (
            render_experiment_report,
            run_experiment,
        )

        report = run_experiment(experiment="reader_context", corpus="synthetic")
        assert report.experiment == "reader_context"
        assert report.decision["decision"] in ("hydrate_body", "keep_summary")
        rendered = render_experiment_report(report)
        assert "Reader-context A/B experiment" in rendered

    def test_reader_context_rejects_claude_mem_corpus(self) -> None:
        import pytest

        from seahorse.benchmark.experiments.runner import run_experiment

        with pytest.raises(ValueError, match="'synthetic' or 'lmeb-s'"):
            run_experiment(experiment="reader_context", corpus="claude-mem")
