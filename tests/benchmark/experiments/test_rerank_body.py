"""Tests for the rerank-with-body re-test experiment (A6) — keep_rrf re-open.

The synthetic corpus verifies the harness MECHANICS: the golden answer's BODY
carries the query tokens (so vector+BM25 recover it) but its SUMMARY is a
generic filler with NO query tokens (the answer is mid-turn). The distractors'
summaries DO carry the common query tokens, so the summary-reranker promotes
them above the answer. The mechanics are: baseline recovers the answer,
summary-rerank demotes it, body-rerank keeps it. The authoritative decision
comes from an LMEB-S run (``--corpus lmeb-s``, not yet built).
"""

from __future__ import annotations

import pytest

from seahorse.benchmark.experiments.rerank_body import (
    RERANK_BODY_DELTA_PP,
    RERANK_BODY_TOP_K,
    RerankBodyExperimentResult,
    RerankBodyQuestion,
    _measure,
    build_synthetic_corpus,
    decide_rerank_body,
    render_rerank_body_report,
    run_rerank_body_experiment,
)
from seahorse.facade.types import Provenance, RememberPayload


class TestRunRerankBodyExperimentSynthetic:
    def test_hybrid_regime_no_fallback(self) -> None:
        result = run_rerank_body_experiment(corpus="synthetic")
        assert result.regime == "hybrid"
        assert result.n_queries == 5
        assert result.n_episodes == 15  # 5 answers + 10 distractors

    def test_summary_rerank_demotes_body_rerank_keeps(self) -> None:
        """The A6 pathology: the answer sits mid-turn, so the summary-reranker
        demotes it (summary has no query tokens) while the body-reranker keeps
        it (body carries the query tokens)."""
        result = run_rerank_body_experiment(corpus="synthetic")
        # Baseline (RRF only) recovers the answer — the body is indexed.
        assert result.recall_at_k_baseline == 1.0
        # Summary-rerank demotes the answer below the distractors.
        assert result.recall_at_k_rerank_summary < result.recall_at_k_baseline
        # Body-rerank keeps the answer.
        assert result.recall_at_k_rerank_body > result.recall_at_k_rerank_summary

    def test_unknown_corpus_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown corpus"):
            run_rerank_body_experiment(corpus="nope")


class TestDecideRerankBody:
    def test_reopen_when_body_recovers_more(self) -> None:
        result = RerankBodyExperimentResult(
            recall_at_k_baseline=1.0,
            recall_at_k_rerank_summary=0.0,
            recall_at_k_rerank_body=1.0,
            n_queries=5,
            n_episodes=15,
            regime="hybrid",
        )
        decision = decide_rerank_body(result)
        assert decision["decision"] == "reopen_rerank"
        assert decision["flip"] is True

    def test_keep_rrf_when_body_does_not_help(self) -> None:
        result = RerankBodyExperimentResult(
            recall_at_k_baseline=1.0,
            recall_at_k_rerank_summary=0.8,
            recall_at_k_rerank_body=0.8,
            n_queries=5,
            n_episodes=15,
            regime="hybrid",
        )
        decision = decide_rerank_body(result)
        assert decision["decision"] == "keep_rrf"
        assert decision["flip"] is False

    def test_invalid_regime_on_fallback_g2(self) -> None:
        result = RerankBodyExperimentResult(
            recall_at_k_baseline=0.0,
            recall_at_k_rerank_summary=0.0,
            recall_at_k_rerank_body=0.0,
            n_queries=5,
            n_episodes=15,
            regime="fallback_g2",
        )
        decision = decide_rerank_body(result)
        assert decision["decision"] == "invalid_regime"
        assert decision["flip"] is False

    def test_threshold_boundary(self) -> None:
        """A delta at/above the threshold justifies the re-open (>=)."""
        result = RerankBodyExperimentResult(
            recall_at_k_baseline=1.0,
            recall_at_k_rerank_summary=0.7,
            recall_at_k_rerank_body=0.75,
            n_queries=5,
            n_episodes=15,
            regime="hybrid",
        )
        assert result.recall_at_k_rerank_body - result.recall_at_k_rerank_summary >= (
            RERANK_BODY_DELTA_PP
        )
        assert decide_rerank_body(result)["decision"] == "reopen_rerank"


class TestBuildSyntheticCorpus:
    def test_ingests_and_retrieves(self, tmp_path) -> None:
        facade, storage, episodes, questions = build_synthetic_corpus(
            tmp_path / "bench.db"
        )
        try:
            assert len(episodes) == 15
            assert len(questions) == 5
            # The stored answer ids are the engine-derived ep_ids.
            stored_ids = {ep.id for ep in episodes}
            assert all(q.answer_ep_id in stored_ids for q in questions)
            rows = facade.recall("What does the Aurora project use?", k=RERANK_BODY_TOP_K)
            assert rows
            assert any(r.score > 0.0 for r in rows)  # hybrid, not the listing regime
        finally:
            storage.close()


class TestRenderRerankBodyReport:
    def test_render_contains_metrics_and_decision(self) -> None:
        result = RerankBodyExperimentResult(
            recall_at_k_baseline=1.0,
            recall_at_k_rerank_summary=0.0,
            recall_at_k_rerank_body=1.0,
            n_queries=5,
            n_episodes=15,
            regime="hybrid",
        )
        decision = decide_rerank_body(result)
        rendered = render_rerank_body_report(result, decision)
        assert "Rerank-with-body re-test experiment" in rendered
        assert "recall@10 (baseline, RRF only): 1.000" in rendered
        assert "recall@10 (rerank summary/subject): 0.000" in rendered
        assert "recall@10 (rerank full body): 1.000" in rendered
        assert "decision: reopen_rerank" in rendered


class TestRunExperimentWiring:
    def test_rerank_body_via_run_experiment(self) -> None:
        """``run_experiment(experiment='rerank_body')`` delegates to the module."""
        from seahorse.benchmark.experiments.runner import (
            render_experiment_report,
            run_experiment,
        )

        report = run_experiment(experiment="rerank_body", corpus="synthetic")
        assert report.experiment == "rerank_body"
        assert report.batch_result is not None
        assert report.batch_result.regime == "hybrid"
        assert report.decision["decision"] in ("keep_rrf", "reopen_rerank")
        rendered = render_experiment_report(report)
        assert "Rerank-with-body re-test experiment" in rendered
        assert "decision:" in rendered

    def test_rerank_body_rejects_claude_mem_corpus(self) -> None:
        from seahorse.benchmark.experiments.runner import run_experiment

        with pytest.raises(ValueError, match="rerank_body experiment corpus"):
            run_experiment(experiment="rerank_body", corpus="claude-mem")


class TestSessionLevelRecall:
    """LMEB-S recall is SESSION-level (any retrieved episode from the golden
    session counts) — the real ``_measure`` branch the authoritative run uses."""

    def test_golden_session_recovered_by_baseline_and_body(self, tmp_path) -> None:
        from seahorse.benchmark.experiments.synthetic import HashEmbedder
        from seahorse.facade import build_facade

        facade, storage = build_facade(
            tmp_path / "s.db",
            retrieval_available=True,
            passage_embedder=HashEmbedder(),
        )
        sessions = {
            "s-aurora": ("Aurora", "Aurora project uses Rust for the pipeline."),
            "s-beacon": ("Beacon", "Beacon project uses Kafka for the pipeline."),
        }
        bridge: dict[str, str] = {}
        for session_id, (project, body) in sessions.items():
            wr = facade.remember(
                RememberPayload(
                    body=body,
                    by=Provenance(
                        source_type="agent", agent_id="test", session_id=session_id
                    ),
                    title=project,
                ),
                extraction_mode="skip",
            )
            assert wr.ep_id is not None
            bridge[wr.ep_id] = session_id
        questions = [
            RerankBodyQuestion(
                query=f"What does the {project} project use?",
                golden_session_ids=(s,),
            )
            for s, (project, _) in sessions.items()
        ]
        try:
            recall_baseline, _, recall_body, n_queries, n_episodes, regime = _measure(
                facade,
                storage,
                [],
                questions,
                RERANK_BODY_TOP_K,
                ep_id_to_session=bridge,
            )
            assert regime == "hybrid"
            assert n_queries == 2
            assert n_episodes == 2  # the bridge is the true stored-episode inventory
            assert recall_baseline == 1.0
            assert recall_body == 1.0
        finally:
            storage.close()


class TestRunRerankBodyRealWiring:
    def test_lmeb_subsample_flag_plumbed(self, monkeypatch) -> None:
        """``run_rerank_body_experiment(corpus='lmeb-s', subsample=...)`` forwards
        the flag to ``build_real_corpus`` (the wiring, without the real ingest)."""
        calls: dict = {}

        def fake_build_real(db, *, subsample):
            calls["subsample"] = subsample
            facade, storage, episodes, questions = build_synthetic_corpus(db)
            return facade, storage, episodes, questions, {}

        monkeypatch.setattr(
            "seahorse.benchmark.experiments.rerank_body.build_real_corpus",
            fake_build_real,
        )
        monkeypatch.setattr(
            "seahorse.benchmark.experiments.rerank_body.real_query_embedder",
            lambda: None,
        )
        monkeypatch.setattr(
            "seahorse.benchmark.experiments.rerank_body._build_real_reranker",
            lambda: None,
        )
        result = run_rerank_body_experiment(corpus="lmeb-s", subsample=False)
        assert calls["subsample"] is False
        assert result.regime == "hybrid"
