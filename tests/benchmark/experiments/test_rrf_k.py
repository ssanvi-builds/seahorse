"""Tests for the RRF_K sweep experiment (A5) — the fusion constant.

The synthetic corpus verifies the harness MECHANICS: the golden answer is
rank-1 in BOTH sources (vector: concentrated token overlap; BM25: the rare
project name has high IDF), so recall@10 is 1.0 for every RRF_K value — the
sweep is deterministic and the decision is stable. The authoritative decision
comes from an LMEB-S run (``--corpus lmeb-s``), which measures SESSION-level
recall over the reproducible 100 subsample.
"""

from __future__ import annotations

import pytest

from seahorse.benchmark.experiments.rrf_k import (
    RRF_K_IMPROVE_PP,
    RRF_K_SWEEP,
    RRF_K_TOP_K,
    RrfKExperimentResult,
    RrfKQuestion,
    _measure,
    build_synthetic_corpus,
    decide_rrf_k,
    render_rrf_k_report,
    run_rrf_k_experiment,
)
from seahorse.facade.types import Provenance, RememberPayload


class TestRunRrfKExperimentSynthetic:
    def test_hybrid_regime_no_fallback(self) -> None:
        result = run_rrf_k_experiment(corpus="synthetic")
        assert result.regime == "hybrid"
        assert result.n_queries == 5
        assert result.n_episodes == 15  # 5 answers + 10 distractors
        assert len(result.recall_at_k_by_rrf_k) == len(RRF_K_SWEEP)

    def test_answers_always_recovered(self) -> None:
        """The golden answer is rank-1 in both sources, so every RRF_K recovers
        it — the sweep is deterministic and the decision is stable."""
        result = run_rrf_k_experiment(corpus="synthetic")
        for rrf_k, recall_at_k in result.recall_at_k_by_rrf_k:
            assert rrf_k in RRF_K_SWEEP
            assert recall_at_k == 1.0

    def test_unknown_corpus_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown corpus"):
            run_rrf_k_experiment(corpus="nope")


class TestDecideRrfK:
    def test_keep_60_when_no_improvement(self) -> None:
        result = RrfKExperimentResult(
            recall_at_k_by_rrf_k=((10, 1.0), (20, 1.0), (40, 1.0), (60, 1.0)),
            best_rrf_k=10,
            best_recall_at_k=1.0,
            default_recall_at_k=1.0,
            n_queries=5,
            n_episodes=15,
            regime="hybrid",
        )
        decision = decide_rrf_k(result)
        assert decision["decision"] == "keep_60"
        assert decision["flip"] is False

    def test_flip_when_lower_rrf_k_improves(self) -> None:
        result = RrfKExperimentResult(
            recall_at_k_by_rrf_k=((10, 1.0), (20, 0.8), (40, 0.8), (60, 0.8)),
            best_rrf_k=10,
            best_recall_at_k=1.0,
            default_recall_at_k=0.8,
            n_queries=5,
            n_episodes=15,
            regime="hybrid",
        )
        decision = decide_rrf_k(result)
        assert decision["decision"] == "flip_rrf_k"
        assert decision["flip"] is True
        assert decision["best_rrf_k"] == 10

    def test_invalid_regime_on_fallback_g2(self) -> None:
        result = RrfKExperimentResult(
            recall_at_k_by_rrf_k=((10, 0.0), (20, 0.0), (40, 0.0), (60, 0.0)),
            best_rrf_k=10,
            best_recall_at_k=0.0,
            default_recall_at_k=0.0,
            n_queries=5,
            n_episodes=15,
            regime="fallback_g2",
        )
        decision = decide_rrf_k(result)
        assert decision["decision"] == "invalid_regime"
        assert decision["flip"] is False

    def test_threshold_boundary(self) -> None:
        """A diff at/above the threshold justifies the flip (>=)."""
        result = RrfKExperimentResult(
            recall_at_k_by_rrf_k=((10, 0.9), (20, 0.8), (40, 0.8), (60, 0.8)),
            best_rrf_k=10,
            best_recall_at_k=0.9,
            default_recall_at_k=0.8,
            n_queries=5,
            n_episodes=15,
            regime="hybrid",
        )
        assert result.best_recall_at_k - result.default_recall_at_k >= RRF_K_IMPROVE_PP
        assert decide_rrf_k(result)["decision"] == "flip_rrf_k"


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
            rows = facade.recall("What does the Aurora project use?", k=RRF_K_TOP_K)
            assert rows
            assert any(r.score > 0.0 for r in rows)  # hybrid, not the listing regime
        finally:
            storage.close()


class TestRenderRrfKReport:
    def test_render_contains_metrics_and_decision(self) -> None:
        result = RrfKExperimentResult(
            recall_at_k_by_rrf_k=((10, 1.0), (20, 1.0), (40, 1.0), (60, 1.0)),
            best_rrf_k=10,
            best_recall_at_k=1.0,
            default_recall_at_k=1.0,
            n_queries=5,
            n_episodes=15,
            regime="hybrid",
        )
        decision = decide_rrf_k(result)
        rendered = render_rrf_k_report(result, decision)
        assert "RRF_K sweep experiment" in rendered
        assert "RRF_K=10  recall@10=1.000" in rendered
        assert "RRF_K=60  recall@10=1.000" in rendered
        assert "decision: keep_60" in rendered


class TestRunExperimentWiring:
    def test_rrf_k_via_run_experiment(self) -> None:
        """``run_experiment(experiment='rrf_k')`` delegates to the module."""
        from seahorse.benchmark.experiments.runner import (
            render_experiment_report,
            run_experiment,
        )

        report = run_experiment(experiment="rrf_k", corpus="synthetic")
        assert report.experiment == "rrf_k"
        assert report.batch_result is not None
        assert report.batch_result.regime == "hybrid"
        assert report.decision["decision"] in ("keep_60", "flip_rrf_k")
        rendered = render_experiment_report(report)
        assert "RRF_K sweep experiment" in rendered
        assert "decision:" in rendered

    def test_rrf_k_rejects_claude_mem_corpus(self) -> None:
        from seahorse.benchmark.experiments.runner import run_experiment

        with pytest.raises(ValueError, match="rrf_k experiment corpus"):
            run_experiment(experiment="rrf_k", corpus="claude-mem")


def _build_session_corpus(
    db_path,
) -> tuple[object, object, list, dict[str, str], list[RrfKQuestion]]:
    """A synthetic corpus with DISTINCT sessions (the LMEB-S shape).

    Each golden session carries one answer episode with the query's distinctive
    tokens; the ``ep_id_to_session`` bridge maps stored ep_ids to sessions. The
    returned ``episodes`` list is empty (session-level questions carry no
    per-episode answer — the real ``build_real_corpus`` returns the same).
    """
    from seahorse.benchmark.experiments.synthetic import HashEmbedder
    from seahorse.facade import build_facade

    facade, storage = build_facade(
        db_path, retrieval_available=True, passage_embedder=HashEmbedder()
    )
    sessions = {
        "s-aurora": ("Aurora", "Aurora project uses Rust for the pipeline."),
        "s-beacon": ("Beacon", "Beacon project uses Kafka for the pipeline."),
        "s-comet": ("Comet", "Comet project uses TensorFlow for the pipeline."),
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
        RrfKQuestion(query=f"What does the {project} project use?", golden_session_ids=(s,))
        for s, (project, _) in sessions.items()
    ]
    return facade, storage, [], bridge, questions


class TestSessionLevelRecall:
    """LMEB-S recall is SESSION-level: any retrieved episode from the golden
    session counts (golden answers live in sessions, not a single identifiable
    turn). This exercises the same ``_measure`` branch the real run uses."""

    def test_golden_session_in_top10_hits(self, tmp_path) -> None:
        facade, storage, _, bridge, questions = _build_session_corpus(
            tmp_path / "s.db"
        )
        try:
            recall_by_rrf_k, n_queries, n_episodes, regime = _measure(
                facade,
                storage,
                [],
                questions,
                RRF_K_TOP_K,
                ep_id_to_session=bridge,
            )
            assert regime == "hybrid"
            assert n_queries == 3
            assert n_episodes == 3  # the bridge is the true stored-episode inventory
            for rrf_k, recall_at_k in recall_by_rrf_k:
                assert rrf_k in RRF_K_SWEEP
                assert recall_at_k == 1.0
        finally:
            storage.close()

    def test_ghost_session_misses(self, tmp_path) -> None:
        facade, storage, _, bridge, questions = _build_session_corpus(
            tmp_path / "s.db"
        )
        ghost = RrfKQuestion(
            query="What does the Ghost project use?", golden_session_ids=("s-ghost",)
        )
        try:
            recall_by_rrf_k, _, _, regime = _measure(
                facade,
                storage,
                [],
                questions + [ghost],
                RRF_K_TOP_K,
                ep_id_to_session=bridge,
            )
            assert regime == "hybrid"
            for _, recall_at_k in recall_by_rrf_k:
                assert recall_at_k == 0.75  # 3 hits + 1 miss
        finally:
            storage.close()


class TestRunRrfKExperimentRealWiring:
    def test_lmeb_subsample_flag_plumbed(self, monkeypatch) -> None:
        """``run_rrf_k_experiment(corpus='lmeb-s', subsample=...)`` forwards the
        flag to ``build_real_corpus`` (the wiring, without the real ingest)."""
        calls: dict = {}

        def fake_build_real(db, *, subsample):
            calls["subsample"] = subsample
            facade, storage, episodes, questions = build_synthetic_corpus(db)
            return facade, storage, episodes, questions, {}

        monkeypatch.setattr(
            "seahorse.benchmark.experiments.rrf_k.build_real_corpus", fake_build_real
        )
        monkeypatch.setattr(
            "seahorse.benchmark.experiments.rrf_k.real_query_embedder", lambda: None
        )
        result = run_rrf_k_experiment(corpus="lmeb-s", subsample=False)
        assert calls["subsample"] is False
        assert result.regime == "hybrid"

    def test_default_subsample_true(self, monkeypatch) -> None:
        calls: dict = {}

        def fake_build_real(db, *, subsample):
            calls["subsample"] = subsample
            facade, storage, episodes, questions = build_synthetic_corpus(db)
            return facade, storage, episodes, questions, {}

        monkeypatch.setattr(
            "seahorse.benchmark.experiments.rrf_k.build_real_corpus", fake_build_real
        )
        monkeypatch.setattr(
            "seahorse.benchmark.experiments.rrf_k.real_query_embedder", lambda: None
        )
        run_rrf_k_experiment(corpus="lmeb-s")  # default -> the documented subsample
        assert calls["subsample"] is True
