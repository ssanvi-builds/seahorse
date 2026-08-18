"""Tests for the end-to-end measurement experiment (A4) — the reader's value.

The synthetic corpus verifies the harness MECHANICS: 5 retrievable questions
(the answer episode shares the query's distinctive token -> recovered -> the
reader extracts the answer) and 5 unretrievable questions (the answer episode
shares only common words -> outranked below the top-10 -> the reader cannot
answer). The mechanics are: end-to-end accuracy tracks recall@10 (the ceiling),
so retrieval quality is the bottleneck. The authoritative decision comes from an
LMEB-S run (``--corpus lmeb-s``, not yet built).
"""

from __future__ import annotations

import pytest

from seahorse.benchmark.experiments.end_to_end import (
    END_TO_END_TOP_K,
    EndToEndExperimentResult,
    ExtractiveReader,
    build_synthetic_corpus,
    decide_end_to_end,
    render_end_to_end_report,
    run_end_to_end_experiment,
)


class TestRunEndToEndExperimentSynthetic:
    def test_hybrid_regime_no_fallback(self) -> None:
        result = run_end_to_end_experiment(corpus="synthetic")
        assert result.regime == "hybrid"
        assert result.n_queries == 10
        assert result.n_episodes >= 15  # 5 retrievable + 5 unretrievable + distractors

    def test_e2e_tracks_recall_ceiling(self) -> None:
        """The A4 ceiling: the reader only sees the top-10, so end-to-end
        accuracy is bounded by recall@10. The retrievable questions recover +
        answer; the unretrievable questions do neither."""
        result = run_end_to_end_experiment(corpus="synthetic")
        # Exactly the 5 retrievable questions are recovered and answered.
        assert result.recall_at_k == 0.5
        assert result.end_to_end_accuracy == 0.5
        # The reader extracts what retrieval recovers (no reader-side loss).
        assert result.ceiling_gap == 0.0

    def test_unknown_corpus_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown corpus"):
            run_end_to_end_experiment(corpus="nope")


class TestExtractiveReader:
    def test_extracts_most_overlapping_line(self) -> None:
        reader = ExtractiveReader()
        context = (
            "1. [Alpha] The capital of Alpha is A.\n"
            "2. [France] The capital of France is Paris."
        )
        answer = reader.generate("What is the capital of France?", context)
        assert "Paris" in answer

    def test_abstains_on_empty_context(self) -> None:
        reader = ExtractiveReader()
        assert reader.generate("Is there any information?", "") == ""


class TestDecideEndToEnd:
    def test_retrieval_bottleneck_when_gap_zero(self) -> None:
        result = EndToEndExperimentResult(
            recall_at_k=0.5,
            end_to_end_accuracy=0.5,
            ceiling_gap=0.0,
            n_queries=10,
            n_episodes=16,
            regime="hybrid",
        )
        decision = decide_end_to_end(result)
        assert decision["decision"] == "retrieval_bottleneck"
        assert decision["flip"] is False

    def test_reader_bottleneck_when_gap_large(self) -> None:
        result = EndToEndExperimentResult(
            recall_at_k=1.0,
            end_to_end_accuracy=0.5,
            ceiling_gap=0.5,
            n_queries=10,
            n_episodes=16,
            regime="hybrid",
        )
        decision = decide_end_to_end(result)
        assert decision["decision"] == "reader_bottleneck"
        assert decision["flip"] is False

    def test_invalid_regime_on_fallback_g2(self) -> None:
        result = EndToEndExperimentResult(
            recall_at_k=0.0,
            end_to_end_accuracy=0.0,
            ceiling_gap=0.0,
            n_queries=10,
            n_episodes=16,
            regime="fallback_g2",
        )
        decision = decide_end_to_end(result)
        assert decision["decision"] == "invalid_regime"
        assert decision["flip"] is False


class TestBuildSyntheticCorpus:
    def test_ingests_and_retrieves(self, tmp_path) -> None:
        facade, storage, episodes, questions, bridge = build_synthetic_corpus(
            tmp_path / "bench.db"
        )
        try:
            assert len(questions) == 10
            # The bridge maps stored ep_ids to their sessions (the recall@10 check).
            assert bridge
            rows = facade.recall("What is the capital of France?", k=END_TO_END_TOP_K)
            assert rows
            assert any(r.score > 0.0 for r in rows)  # hybrid, not the listing regime
        finally:
            storage.close()


class TestRenderEndToEndReport:
    def test_render_contains_metrics_and_decision(self) -> None:
        result = EndToEndExperimentResult(
            recall_at_k=0.5,
            end_to_end_accuracy=0.5,
            ceiling_gap=0.0,
            n_queries=10,
            n_episodes=16,
            regime="hybrid",
        )
        decision = decide_end_to_end(result)
        rendered = render_end_to_end_report(result, decision)
        assert "End-to-end measurement experiment" in rendered
        assert "recall@10 (the ceiling): 0.500" in rendered
        assert "end-to-end accuracy (reader vs golden): 0.500" in rendered
        assert "decision: retrieval_bottleneck" in rendered


class TestRunExperimentWiring:
    def test_end_to_end_via_run_experiment(self) -> None:
        """``run_experiment(experiment='end_to_end')`` delegates to the module."""
        from seahorse.benchmark.experiments.runner import (
            render_experiment_report,
            run_experiment,
        )

        report = run_experiment(experiment="end_to_end", corpus="synthetic")
        assert report.experiment == "end_to_end"
        assert report.batch_result is not None
        assert report.batch_result.regime == "hybrid"
        assert report.decision["decision"] in ("retrieval_bottleneck", "reader_bottleneck")
        rendered = render_experiment_report(report)
        assert "End-to-end measurement experiment" in rendered
        assert "decision:" in rendered

    def test_end_to_end_rejects_claude_mem_corpus(self) -> None:
        from seahorse.benchmark.experiments.runner import run_experiment

        with pytest.raises(ValueError, match="end_to_end experiment corpus"):
            run_experiment(experiment="end_to_end", corpus="claude-mem")
