"""Tests for the chunk-indexing experiment (``chunk_indexing.py``).

The experiment measures whether per-window (chunked) vector indexing lifts
episode-level recall@10 without breaking the p95 latency budget — the P3.2
seam's decision gate. The synthetic runs verify the MECHANICS (no model):
Case A (recoverable) + Case B (session-only) from the granularity corpus, plus
Case C (a long multi-window golden body whose query tokens live in the dense
early window and the answer in a later window — the P3 mechanism case); the
authoritative decision comes from the LMEB-S pair run (``--chunk-mode off``
then ``chunked`` over the same tree).
"""

from __future__ import annotations

import pytest

from seahorse.benchmark.experiments.chunk_indexing import (
    CHUNK_BASELINE_EPISODE_RECALL,
    CHUNK_IMPROVEMENT_THRESHOLD,
    CHUNK_INDEXING_TOP_K,
    CHUNK_KEEP_GATE_EPISODE_RECALL,
    CHUNK_P95_BUDGET_MS,
    ChunkIndexingExperimentResult,
    decide_chunk_indexing,
    render_chunk_indexing_report,
    run_chunk_indexing_experiment,
)


def _result(
    *,
    chunk_mode: str = "chunked",
    session: float = 1.0,
    episode: float = 0.6,
    context: float = 0.5,
    latency_p95_ms: float = 200.0,
    regime: str = "hybrid",
) -> ChunkIndexingExperimentResult:
    return ChunkIndexingExperimentResult(
        chunk_mode=chunk_mode,
        session_level_recall_at_k=session,
        episode_level_recall_at_k=episode,
        answer_in_context_rate=context,
        latency_p95_ms=latency_p95_ms,
        n_queries=100,
        n_episodes=45_580,
        n_localized=92,
        n_unlocalized=8,
        n_chunks=91_000,
        avg_chunks_per_episode=2.0,
        regime=regime,
    )


class TestDecisionGates:
    def test_constants_pin_the_p31_baseline_and_gates(self) -> None:
        """The gates are pinned constants — the P3.1 baseline (0.533, measured on
        the v1.5.0 tree), the +0.05 material-improvement threshold, the explicit
        0.583 keep gate, and the 250ms p95 budget. Changing any of them is a
        decision, not a refactor."""
        assert CHUNK_BASELINE_EPISODE_RECALL == 0.533
        assert CHUNK_IMPROVEMENT_THRESHOLD == 0.05
        assert CHUNK_KEEP_GATE_EPISODE_RECALL == 0.583
        assert CHUNK_P95_BUDGET_MS == 250
        assert CHUNK_INDEXING_TOP_K == 10
        # the keep gate IS baseline + threshold (approx: floats)
        assert (
            pytest.approx(CHUNK_KEEP_GATE_EPISODE_RECALL)
            == CHUNK_BASELINE_EPISODE_RECALL + CHUNK_IMPROVEMENT_THRESHOLD
        )

    def test_keep_when_recall_and_latency_pass(self) -> None:
        d = decide_chunk_indexing(_result(episode=0.583, latency_p95_ms=250.0))
        assert d["decision"] == "keep_chunk_indexing"
        assert d["flip"] is True
        assert d["chunk_mode"] == "chunked"
        assert d["episode_level_recall_at_k"] == 0.583
        assert d["latency_p95_ms"] == 250.0

    def test_revert_when_recall_short(self) -> None:
        d = decide_chunk_indexing(_result(episode=0.582, latency_p95_ms=100.0))
        assert d["decision"] == "revert_chunk_indexing"
        assert d["flip"] is False
        assert "episode-level recall" in d["reason"]

    def test_revert_when_latency_over_budget(self) -> None:
        d = decide_chunk_indexing(_result(episode=0.60, latency_p95_ms=251.0))
        assert d["decision"] == "revert_chunk_indexing"
        assert d["flip"] is False
        assert "p95" in d["reason"]

    def test_revert_when_both_gates_fail(self) -> None:
        d = decide_chunk_indexing(_result(episode=0.40, latency_p95_ms=900.0))
        assert d["decision"] == "revert_chunk_indexing"
        assert d["flip"] is False
        assert "both" in d["reason"]

    def test_invalid_regime_on_fallback(self) -> None:
        d = decide_chunk_indexing(_result(regime="fallback_g2"))
        assert d["decision"] == "invalid_regime"
        assert d["flip"] is False
        assert "fallback" in d["reason"]

    def test_baseline_measurement_when_flag_off(self) -> None:
        """The flag-off side of the P3.3 pair carries NO keep/revert verdict —
        it is the baseline measurement the chunked side is compared against."""
        d = decide_chunk_indexing(_result(chunk_mode="off", episode=0.533))
        assert d["decision"] == "baseline_measurement"
        assert d["flip"] is False
        assert "baseline" in d["reason"]


class TestRunSynthetic:
    def test_chunked_measures_multi_chunk_fold(self) -> None:
        """Case C under CHUNKED: the dense early window hits, the repository
        folds the window vectors into the PARENT once, and the answer fragment
        reaches the context through the whole parent body → episode + context
        hit. Case A (recoverable) hits; Case B (session-only) misses by design
        → episode/context recall 10/15, session recall 1.0."""
        result = run_chunk_indexing_experiment(corpus="synthetic", chunk_mode="chunked")
        assert result.regime == "hybrid"
        assert result.chunk_mode == "chunked"
        assert result.n_queries == 15
        assert result.n_localized == 15
        assert result.n_unlocalized == 0
        assert result.session_level_recall_at_k == 1.0
        assert result.episode_level_recall_at_k == pytest.approx(10 / 15)
        assert result.answer_in_context_rate == pytest.approx(10 / 15)
        assert result.latency_p95_ms > 0.0
        # census: the chunked surface is REAL — the multi-window bodies
        # produced more chunks than episodes.
        assert result.n_chunks > result.n_episodes > 0
        assert result.avg_chunks_per_episode > 1.0

    def test_off_is_census_empty_with_retrieval_parity(self) -> None:
        """The flag-off side over the synthetic corpus: an honest census (0
        chunks — the chunk table stays empty by construction) and retrieval
        PARITY with the chunked side. Parity is the expected mechanical
        result with the bag-of-tokens HashEmbedder (a repeated topic also
        dominates the whole-body vector); the dilution gap the P3 hypothesis
        predicts is a transformer phenomenon — the LMEB-S pair run's to
        measure, not this corpus's."""
        result = run_chunk_indexing_experiment(corpus="synthetic", chunk_mode="off")
        chunked = run_chunk_indexing_experiment(corpus="synthetic", chunk_mode="chunked")
        assert result.chunk_mode == "off"
        assert result.regime == "hybrid"
        assert result.n_chunks == 0
        assert result.avg_chunks_per_episode == 0.0
        assert result.session_level_recall_at_k == chunked.session_level_recall_at_k
        assert result.episode_level_recall_at_k == chunked.episode_level_recall_at_k
        assert result.answer_in_context_rate == chunked.answer_in_context_rate


class TestSingleChunkParity:
    def test_short_bodies_are_mode_invariant(self) -> None:
        """A body within one window is a single chunk — identical embedding to
        the flag-off surface. Over the short-body corpus (Cases A + B, no Case
        C) BOTH modes must agree on every retrieval metric; only the census
        differs (off: 0; chunked: one chunk per episode)."""
        import shutil
        from pathlib import Path

        from seahorse.benchmark._tmpdirs import mkdtemp_scoped
        from seahorse.benchmark.experiments.chunk_indexing import (
            _build_synthetic_corpus,
            _measure_chunk_indexing,
        )

        tmp = Path(mkdtemp_scoped("seahorse-chunkparity-"))
        try:
            off_f, off_s, questions, off_map = _build_synthetic_corpus(
                tmp / "off.db", "off", with_long_case=False
            )
            try:
                off = _measure_chunk_indexing(
                    off_f, off_s, questions, off_map, CHUNK_INDEXING_TOP_K, "off"
                )
            finally:
                off_s.close()
            ch_f, ch_s, questions2, ch_map = _build_synthetic_corpus(
                tmp / "chunked.db", "chunked", with_long_case=False
            )
            try:
                chunked = _measure_chunk_indexing(
                    ch_f, ch_s, questions2, ch_map, CHUNK_INDEXING_TOP_K, "chunked"
                )
            finally:
                ch_s.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        assert off.session_level_recall_at_k == chunked.session_level_recall_at_k
        assert off.episode_level_recall_at_k == chunked.episode_level_recall_at_k
        assert off.answer_in_context_rate == chunked.answer_in_context_rate
        assert off.n_queries == chunked.n_queries == 10
        # census: off table empty; chunked = one chunk per (short) episode
        assert off.n_chunks == 0
        assert chunked.n_chunks == chunked.n_episodes
        assert chunked.avg_chunks_per_episode == 1.0


class TestBoundaries:
    def test_unknown_corpus_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown corpus"):
            run_chunk_indexing_experiment(corpus="claude-mem", chunk_mode="chunked")

    def test_unknown_chunk_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="chunk_mode"):
            run_chunk_indexing_experiment(corpus="synthetic", chunk_mode="bogus")


class TestReport:
    def test_render_contains_metrics_and_decision(self) -> None:
        result = _result(episode=0.583, latency_p95_ms=250.0)
        d = decide_chunk_indexing(result)
        text = render_chunk_indexing_report(result, d)
        assert "Chunk-indexing" in text
        assert "chunk mode: chunked" in text
        assert "session-level recall@10" in text
        assert "episode-level recall@10" in text
        assert "answer-in-context rate" in text
        assert "latency p95" in text
        assert "chunks" in text
        assert "decision: keep_chunk_indexing" in text
        assert "flip: True" in text
        assert d["reason"] in text


class TestViaRunner:
    def test_chunk_indexing_via_run_experiment(self) -> None:
        """``run_experiment(experiment='chunk_indexing')`` delegates to the module
        (the standalone result rides the ``batch_result`` slot) and renders
        through the shared report path. Default ``chunk_mode='chunked'`` — the
        experiment's candidate side."""
        from seahorse.benchmark.experiments.runner import (
            render_experiment_report,
            run_experiment,
        )

        report = run_experiment(experiment="chunk_indexing", corpus="synthetic")
        assert report.experiment == "chunk_indexing"
        assert isinstance(report.batch_result, ChunkIndexingExperimentResult)
        assert report.batch_result.chunk_mode == "chunked"
        assert report.decision["decision"] == "keep_chunk_indexing"
        text = render_experiment_report(report)
        assert "Chunk-indexing" in text
        assert "decision: keep_chunk_indexing" in text

    def test_baseline_side_via_run_experiment(self) -> None:
        """``--chunk-mode off`` rides the same dispatch and reports the honest
        baseline-measurement verdict (no keep/revert on the baseline side)."""
        from seahorse.benchmark.experiments.runner import run_experiment

        report = run_experiment(
            experiment="chunk_indexing", corpus="synthetic", chunk_mode="off"
        )
        assert report.batch_result.chunk_mode == "off"
        assert report.decision["decision"] == "baseline_measurement"