"""Tests for the F7 experiment (d) — batch-por-turno (f7 §5d).

The synthetic corpus verifies the harness MECHANICS (ADR-10): coherent turns
(observations sharing a topic) must yield high cluster recall@k, diverse turns
low cluster recall@k. The authoritative decision comes from the real claude-mem
corpus (``--corpus claude-mem``).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.benchmark.experiments.batch import (
    BATCH_RECALL_THRESHOLD,
    BATCH_TOP_K,
    BatchExperimentResult,
    build_synthetic_corpus,
    compute_turn_clusters,
    decide_batch,
    run_batch_experiment,
)
from seahorse.contracts.episode import Episode

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _ep(i: int, session: str, prompt: int, title: str, narrative: str) -> Episode:
    return Episode(
        id=f"ep-{session}-{prompt}-{i}",
        created_at=NOW,
        schema_version="1.1",
        provenance={
            "source_type": "importer",
            "importer_vendor": "claude-mem",
            "extraction_mode": "skip",
            "session_id": "claude-mem-import-test",
            "x-claude-mem-session-id": session,
            "x-claude-mem-prompt-number": prompt,
        },
        body=f"# {title}\n\n{narrative}",
        title=title,
        valid_at=NOW,
        cognitive_type="semantic",
        source_type="importer",
    )


class TestComputeTurnClusters:
    def test_groups_by_session_and_prompt(self) -> None:
        eps = [
            _ep(0, "s1", 1, "A", "a"),
            _ep(1, "s1", 1, "B", "b"),
            _ep(2, "s1", 2, "C", "c"),
            _ep(3, "s1", 2, "C2", "c2"),
            _ep(4, "s2", 1, "D", "d"),
        ]
        clusters = compute_turn_clusters(eps)
        assert len(clusters) == 2  # (s1,1) and (s1,2); (s2,1) has 1 obs
        by_key = {(c.session_id, c.prompt_number): c for c in clusters}
        assert set(by_key) == {("s1", 1), ("s1", 2)}
        assert len(by_key[("s1", 1)].ep_ids) == 2

    def test_skips_singleton_turns(self) -> None:
        eps = [_ep(0, "s1", 1, "A", "a"), _ep(1, "s1", 2, "B", "b")]
        assert compute_turn_clusters(eps) == []

    def test_skips_episodes_without_turn_fields(self) -> None:
        ep = _ep(0, "s1", 1, "A", "a")
        ep = ep.model_copy(
            update={
                "provenance": {
                    **ep.provenance,
                    "x-claude-mem-session-id": None,
                    "x-claude-mem-prompt-number": None,
                }
            }
        )
        assert compute_turn_clusters([ep]) == []

    def test_subjects_are_the_h1(self) -> None:
        eps = [_ep(0, "s1", 1, "France capital", "Paris."), _ep(1, "s1", 1, "B", "b")]
        clusters = compute_turn_clusters(eps)
        assert clusters[0].subjects[0] == "France capital"


class TestRunBatchExperimentSynthetic:
    def test_hybrid_regime_no_fallback(self) -> None:
        result = run_batch_experiment(corpus="synthetic")
        assert result.regime == "hybrid"
        assert result.n_turns == 3  # (syn-s1,1) + (syn-s1,2) + (syn-s2,1)
        assert result.n_observations == 24  # 9 turn obs + 15 background distractors

    def test_coherent_turns_high_cluster_recall(self) -> None:
        """The synthetic coherent turns must be recovered as clusters (mechanics)."""
        result = run_batch_experiment(corpus="synthetic")
        # Coherent turns dominate the cluster queries (6 of 9); the diverse turn
        # (3 obs) drags the mean down but the coherent signal must be strong.
        assert result.cluster_recall_at_k > 0.5
        assert result.individual_recall_at_k > result.cluster_recall_at_k

    def test_turn_sizes_reported(self) -> None:
        result = run_batch_experiment(corpus="synthetic")
        assert sorted(result.turn_sizes) == [3, 3, 3]

    def test_unknown_corpus_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown corpus"):
            run_batch_experiment(corpus="nope")


class TestDecideBatch:
    def test_batch_por_turno_when_cluster_recall_high(self) -> None:
        result = BatchExperimentResult(
            cluster_recall_at_k=0.8,
            individual_recall_at_k=0.9,
            n_turns=2,
            n_observations=6,
            n_cluster_queries=6,
            turn_sizes=(3, 3),
            recoverable_turns=1.0,
            regime="hybrid",
        )
        decision = decide_batch(result)
        assert decision["decision"] == "batch_por_turno"
        assert decision["flip"] is True

    def test_por_sesion_when_cluster_recall_low(self) -> None:
        result = BatchExperimentResult(
            cluster_recall_at_k=0.2,
            individual_recall_at_k=0.9,
            n_turns=2,
            n_observations=6,
            n_cluster_queries=6,
            turn_sizes=(3, 3),
            recoverable_turns=0.0,
            regime="hybrid",
        )
        decision = decide_batch(result)
        assert decision["decision"] == "por_sesion"
        assert decision["flip"] is False

    def test_invalid_regime_on_fallback_g2(self) -> None:
        result = BatchExperimentResult(
            cluster_recall_at_k=0.0,
            individual_recall_at_k=0.0,
            n_turns=2,
            n_observations=6,
            n_cluster_queries=6,
            turn_sizes=(3, 3),
            recoverable_turns=0.0,
            regime="fallback_g2",
        )
        decision = decide_batch(result)
        assert decision["decision"] == "invalid_regime"
        assert decision["flip"] is False

    def test_threshold_boundary(self) -> None:
        """At exactly the threshold the turn is a recoverable unit (>=)."""
        result = BatchExperimentResult(
            cluster_recall_at_k=BATCH_RECALL_THRESHOLD,
            individual_recall_at_k=0.9,
            n_turns=1,
            n_observations=3,
            n_cluster_queries=3,
            turn_sizes=(3,),
            recoverable_turns=1.0,
            regime="hybrid",
        )
        assert decide_batch(result)["decision"] == "batch_por_turno"


class TestBuildSyntheticCorpus:
    def test_ingests_and_retrieves(self, tmp_path) -> None:
        facade, storage, episodes = build_synthetic_corpus(tmp_path / "bench.db")
        try:
            rows = facade.recall("France capital", k=BATCH_TOP_K)
            assert rows
            assert any(r.score > 0.0 for r in rows)  # hybrid, not G2
        finally:
            storage.close()


class TestRunExperimentWiring:
    def test_batch_via_run_experiment(self) -> None:
        """``run_experiment(experiment='batch')`` delegates to the batch module."""
        from seahorse.benchmark.experiments.runner import (
            render_experiment_report,
            run_experiment,
        )

        report = run_experiment(experiment="batch", corpus="synthetic")
        assert report.experiment == "batch"
        assert report.batch_result is not None
        assert report.batch_result.regime == "hybrid"
        assert report.decision["decision"] in ("batch_por_turno", "por_sesion")
        rendered = render_experiment_report(report)
        assert "batch-por-turno" in rendered
        assert "decision:" in rendered

    def test_batch_rejects_lmeb_corpus(self) -> None:
        from seahorse.benchmark.experiments.runner import run_experiment

        with pytest.raises(ValueError, match="batch experiment corpus"):
            run_experiment(experiment="batch", corpus="lmeb-s")
