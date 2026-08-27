"""Tests for the reader-quality A/B experiment (``reader_quality.py``).

The experiment measures end-to-end accuracy with a WEAK reader (baseline) vs a
STRONG reader over ONE corpus — the evidence for whether the reader MODEL is
the bottleneck (the A4 ``reader_bottleneck``). The synthetic runs verify the
MECHANICS (no model); the authoritative decision comes from an LMEB-S run.
"""

from __future__ import annotations

from seahorse.benchmark.experiments.end_to_end import ExtractiveReader
from seahorse.benchmark.experiments.reader_quality import (
    READER_QUALITY_DELTA_PP,
    ReaderQualityExperimentResult,
    decide_reader_quality,
    render_reader_quality_report,
    run_reader_quality_experiment,
)


class _AbstainingReader:
    """Deterministic weak reader double: always abstains (empty answer)."""

    def generate(self, question: str, context: str, question_date=None) -> str:
        return ""


class TestRunReaderQualitySynthetic:
    def test_hybrid_regime_no_fallback(self) -> None:
        result = run_reader_quality_experiment(
            corpus="synthetic",
            reader_weak=_AbstainingReader(),
            reader_strong=ExtractiveReader(),
        )
        assert result.regime == "hybrid"
        assert result.n_queries == 10
        assert result.n_episodes >= 15

    def test_recall_is_the_shared_ceiling(self) -> None:
        """The reader cannot change retrieval: recall@10 is the ceiling and both
        readers measure under it."""
        result = run_reader_quality_experiment(
            corpus="synthetic",
            reader_weak=_AbstainingReader(),
            reader_strong=ExtractiveReader(),
        )
        assert result.recall_at_k == 0.5
        assert 0.0 <= result.e2e_weak <= result.recall_at_k
        assert 0.0 <= result.e2e_strong <= result.recall_at_k

    def test_strong_reader_recovers_e2e(self) -> None:
        """The mechanics: the extractive reader recovers the 5 retrievable
        answers (e2e 0.5) while the abstaining weak reader recovers nothing
        (e2e 0.0) — a stronger reader closes the gap."""
        result = run_reader_quality_experiment(
            corpus="synthetic",
            reader_weak=_AbstainingReader(),
            reader_strong=ExtractiveReader(),
        )
        assert result.e2e_weak == 0.0
        assert result.e2e_strong == 0.5

    def test_unknown_corpus_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="unknown corpus"):
            run_reader_quality_experiment(corpus="nope")


class TestDecideReaderQuality:
    @staticmethod
    def _result(
        e2e_weak: float, e2e_strong: float, regime: str = "hybrid"
    ) -> ReaderQualityExperimentResult:
        return ReaderQualityExperimentResult(
            recall_at_k=0.5,
            e2e_weak=e2e_weak,
            e2e_strong=e2e_strong,
            n_queries=10,
            n_episodes=15,
            regime=regime,
        )

    def test_reader_quality_bottleneck_when_strong_recovers(self) -> None:
        decision = decide_reader_quality(self._result(0.1, 0.5))
        assert decision["decision"] == "reader_quality_bottleneck"
        assert decision["flip"] is True

    def test_context_assembly_when_strong_does_not_help(self) -> None:
        decision = decide_reader_quality(self._result(0.3, 0.32))
        assert decision["decision"] == "context_assembly_bottleneck"
        assert decision["flip"] is False

    def test_invalid_regime_on_fallback_g2(self) -> None:
        decision = decide_reader_quality(self._result(0.0, 0.0, regime="fallback_g2"))
        assert decision["decision"] == "invalid_regime"
        assert decision["flip"] is False

    def test_threshold_boundary(self) -> None:
        """At exactly the threshold the flip happens (>= DELTA_PP)."""
        decision = decide_reader_quality(
            self._result(0.1, 0.1 + READER_QUALITY_DELTA_PP)
        )
        assert decision["decision"] == "reader_quality_bottleneck"


class TestRenderReaderQualityReport:
    def test_render_contains_metrics_and_decision(self) -> None:
        result = run_reader_quality_experiment(
            corpus="synthetic",
            reader_weak=_AbstainingReader(),
            reader_strong=ExtractiveReader(),
        )
        decision = decide_reader_quality(result)
        report = render_reader_quality_report(result, decision)
        assert "end-to-end accuracy (weak reader)" in report
        assert "end-to-end accuracy (strong reader)" in report
        assert f"decision: {decision['decision']}" in report


class TestRunExperimentWiring:
    def test_reader_quality_via_run_experiment(self) -> None:
        """``run_experiment(experiment='reader_quality')`` delegates to the module
        and renders the report (synthetic CI: deterministic doubles, no Ollama)."""
        from seahorse.benchmark.experiments.runner import (
            render_experiment_report,
            run_experiment,
        )

        report = run_experiment(experiment="reader_quality", corpus="synthetic")
        assert report.experiment == "reader_quality"
        assert report.decision["decision"] in (
            "reader_quality_bottleneck",
            "context_assembly_bottleneck",
        )
        rendered = render_experiment_report(report)
        assert "Reader-quality A/B experiment" in rendered

    def test_reader_quality_rejects_claude_mem_corpus(self) -> None:
        import pytest

        from seahorse.benchmark.experiments.runner import run_experiment

        with pytest.raises(ValueError, match="'synthetic' or 'lmeb-s'"):
            run_experiment(experiment="reader_quality", corpus="claude-mem")
