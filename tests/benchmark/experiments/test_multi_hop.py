"""Tests for the multi-hop recall experiment — Rung 3 (physical graph).

The synthetic corpus verifies the harness MECHANICS: 1-hop questions (query
mentions the answer entity directly) must yield high recall@k, 2-hop questions
(query requires traversing the chain edge) must yield low recall@k — the answer
episode does NOT mention the source entity. The authoritative decision comes
from an LMEB-S multi-session run (``--corpus lmeb-s``, not yet built).
"""

from __future__ import annotations

import pytest

from seahorse.benchmark.experiments.multi_hop import (
    MULTI_HOP_DELTA_PP,
    MULTI_HOP_TOP_K,
    MultiHopExperimentResult,
    build_synthetic_corpus,
    decide_multi_hop,
    render_multi_hop_report,
    run_multi_hop_experiment,
)


class TestRunMultiHopExperimentSynthetic:
    def test_hybrid_regime_no_fallback(self) -> None:
        result = run_multi_hop_experiment(corpus="synthetic")
        assert result.regime == "hybrid"
        assert result.n_1hop_queries == 5
        assert result.n_2hop_queries == 5
        assert result.n_episodes == 25  # 15 chain episodes + 10 distractors

    def test_1hop_high_2hop_low(self) -> None:
        """The corpus is designed so 2-hop is harder: the answer episode does
        NOT mention the source entity, so it is only reachable by traversal."""
        result = run_multi_hop_experiment(corpus="synthetic")
        # The control: a query that mentions the entity directly recovers it.
        assert result.recall_at_k_1hop > 0.5
        # The 2-hop answer is not recovered by token overlap alone.
        assert result.recall_at_k_2hop < result.recall_at_k_1hop
        assert result.delta_pp > 0.0

    def test_unknown_corpus_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown corpus"):
            run_multi_hop_experiment(corpus="nope")


class TestDecideMultiHop:
    def test_rung3_when_2hop_much_lower(self) -> None:
        result = MultiHopExperimentResult(
            recall_at_k_1hop=1.0,
            recall_at_k_2hop=0.0,
            delta_pp=100.0,
            n_1hop_queries=5,
            n_2hop_queries=5,
            n_episodes=25,
            regime="hybrid",
        )
        decision = decide_multi_hop(result)
        assert decision["decision"] == "rung3"
        assert decision["flip"] is True

    def test_no_graph_when_2hop_close(self) -> None:
        result = MultiHopExperimentResult(
            recall_at_k_1hop=0.9,
            recall_at_k_2hop=0.88,
            delta_pp=2.0,
            n_1hop_queries=5,
            n_2hop_queries=5,
            n_episodes=25,
            regime="hybrid",
        )
        decision = decide_multi_hop(result)
        assert decision["decision"] == "no_graph"
        assert decision["flip"] is False

    def test_invalid_regime_on_fallback_g2(self) -> None:
        result = MultiHopExperimentResult(
            recall_at_k_1hop=0.0,
            recall_at_k_2hop=0.0,
            delta_pp=0.0,
            n_1hop_queries=5,
            n_2hop_queries=5,
            n_episodes=25,
            regime="fallback_g2",
        )
        decision = decide_multi_hop(result)
        assert decision["decision"] == "invalid_regime"
        assert decision["flip"] is False

    def test_threshold_boundary(self) -> None:
        """At exactly the threshold the graph is justified (>=)."""
        result = MultiHopExperimentResult(
            recall_at_k_1hop=0.5,
            recall_at_k_2hop=0.5 - MULTI_HOP_DELTA_PP,
            delta_pp=MULTI_HOP_DELTA_PP * 100.0,
            n_1hop_queries=5,
            n_2hop_queries=5,
            n_episodes=25,
            regime="hybrid",
        )
        assert decide_multi_hop(result)["decision"] == "rung3"


class TestBuildSyntheticCorpus:
    def test_ingests_and_retrieves(self, tmp_path) -> None:
        facade, storage, episodes, questions = build_synthetic_corpus(
            tmp_path / "bench.db"
        )
        try:
            assert len(episodes) == 25
            assert len(questions) == 10
            # The stored answer ids are the engine-derived ep_ids.
            stored_ids = {ep.id for ep in episodes}
            assert all(q.answer_ep_id in stored_ids for q in questions)
            rows = facade.recall("What does Aurora use?", k=MULTI_HOP_TOP_K)
            assert rows
            assert any(r.score > 0.0 for r in rows)  # hybrid, not the listing regime
        finally:
            storage.close()


class TestRenderMultiHopReport:
    def test_render_contains_metrics_and_decision(self) -> None:
        result = MultiHopExperimentResult(
            recall_at_k_1hop=1.0,
            recall_at_k_2hop=0.0,
            delta_pp=100.0,
            n_1hop_queries=5,
            n_2hop_queries=5,
            n_episodes=25,
            regime="hybrid",
        )
        decision = decide_multi_hop(result)
        rendered = render_multi_hop_report(result, decision)
        assert "Multi-hop recall experiment" in rendered
        assert "recall@10 (1-hop, direct): 1.000" in rendered
        assert "recall@10 (2-hop, traversal): 0.000" in rendered
        assert "delta (1-hop - 2-hop): 100.0pp" in rendered
        assert "decision: rung3" in rendered


class TestRunExperimentWiring:
    def test_multi_hop_via_run_experiment(self) -> None:
        """``run_experiment(experiment='multi_hop')`` delegates to the module."""
        from seahorse.benchmark.experiments.runner import (
            render_experiment_report,
            run_experiment,
        )

        report = run_experiment(experiment="multi_hop", corpus="synthetic")
        assert report.experiment == "multi_hop"
        assert report.batch_result is not None
        assert report.batch_result.regime == "hybrid"
        assert report.decision["decision"] in ("rung3", "no_graph")
        rendered = render_experiment_report(report)
        assert "Multi-hop recall experiment" in rendered
        assert "decision:" in rendered

    def test_multi_hop_rejects_claude_mem_corpus(self) -> None:
        from seahorse.benchmark.experiments.runner import run_experiment

        with pytest.raises(ValueError, match="multi_hop experiment corpus"):
            run_experiment(experiment="multi_hop", corpus="claude-mem")
