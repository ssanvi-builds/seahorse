"""Tests for ``seahorse.benchmark.experiments.skills`` — the skills retrieval
experiment (h).

The experiment measures whether the deterministic procedural skills
(``record_procedure``) are retrievable: given a procedural question, does the
hybrid retrieval recover the procedural episode(s) about that procedure? The
synthetic corpus verifies the harness MECHANICS (not the science).
"""

from __future__ import annotations

from seahorse.benchmark.experiments.skills import (
    SKILLS_RECALL_THRESHOLD,
    SkillsExperimentResult,
    build_synthetic_corpus,
    compute_procedure_clusters,
    decide_skills,
    render_skills_report,
    run_skills_experiment,
)


def _result(
    *,
    recall_at_k: float = 0.5,
    recovered_fraction: float = 0.5,
    n_procedures: int = 4,
    n_procedural_episodes: int = 12,
    per_procedure_recall: tuple[float, ...] = (1.0, 1.0, 0.333, 0.333),
    covered: float = 0.5,
    regime: str = "hybrid",
) -> SkillsExperimentResult:
    return SkillsExperimentResult(
        recall_at_k=recall_at_k,
        recovered_fraction=recovered_fraction,
        n_procedures=n_procedures,
        n_procedural_episodes=n_procedural_episodes,
        n_queries=n_procedures,
        per_procedure_recall=per_procedure_recall,
        covered_procedures=covered,
        regime=regime,
    )


# --- synthetic corpus mechanics ----------------------------------------------


def test_synthetic_corpus_builds_and_clusters(tmp_path) -> None:
    facade, storage, episodes = build_synthetic_corpus(tmp_path / "bench.db")
    try:
        clusters = compute_procedure_clusters(episodes)
        # 4 procedures (deploy, backup, migrate, tune) x 3 episodes each.
        assert len(clusters) == 4
        assert all(len(c.ep_ids) == 3 for c in clusters)
        assert {c.procedure for c in clusters} == {
            "deploy",
            "backup",
            "migrate",
            "tune",
        }
    finally:
        storage.close()


def test_synthetic_corpus_has_background_distractors(tmp_path) -> None:
    facade, storage, episodes = build_synthetic_corpus(tmp_path / "bench.db")
    try:
        # 12 procedural episodes + 15 background distractors.
        assert len(episodes) == 27
        assert len(compute_procedure_clusters(episodes)) == 4
    finally:
        storage.close()


def test_synthetic_run_hybrid_regime(tmp_path) -> None:
    result = run_skills_experiment(corpus="synthetic", db_path=tmp_path / "bench.db")
    assert result.regime == "hybrid"
    assert result.n_procedures == 4
    assert result.n_queries == 4
    # Named procedures (deploy, backup) are recovered; step-referenced
    # (migrate, tune) are not — the mean sits between the two sides.
    assert 0.0 < result.recall_at_k < 1.0
    assert len(result.per_procedure_recall) == 4


def test_synthetic_run_mechanics_are_deterministic(tmp_path) -> None:
    a = run_skills_experiment(corpus="synthetic", db_path=tmp_path / "a.db")
    b = run_skills_experiment(corpus="synthetic", db_path=tmp_path / "b.db")
    assert a.recall_at_k == b.recall_at_k
    assert a.per_procedure_recall == b.per_procedure_recall


# --- decision function --------------------------------------------------------


def test_decide_skills_no_llm_distillation_when_covered() -> None:
    d = decide_skills(_result(recall_at_k=0.667, covered=0.5))
    assert d["decision"] == "no_llm_distillation"
    assert d["flip"] is False


def test_decide_skills_considers_llm_distillation_when_low() -> None:
    d = decide_skills(_result(recall_at_k=0.333, covered=0.0))
    assert d["decision"] == "consider_llm_distillation"
    assert d["flip"] is True


def test_decide_skills_threshold_boundary() -> None:
    # recall@k exactly at the threshold → covered (no LLM distillation).
    d = decide_skills(_result(recall_at_k=SKILLS_RECALL_THRESHOLD))
    assert d["decision"] == "no_llm_distillation"


def test_decide_skills_invalid_regime_on_fallback_g2() -> None:
    d = decide_skills(_result(regime="fallback_g2"))
    assert d["decision"] == "invalid_regime"
    assert d["flip"] is False


# --- render ------------------------------------------------------------------


def test_render_skills_report_includes_metrics_and_decision() -> None:
    result = _result()
    text = render_skills_report(result, decide_skills(result))
    assert "procedures: 4" in text
    assert "recall@10 (procedural): 0.500" in text
    assert "## Decision" in text
    assert "decision: no_llm_distillation" in text


# --- runner wiring ------------------------------------------------------------


def test_runner_dispatch_skills(tmp_path) -> None:
    from seahorse.benchmark.experiments.runner import run_experiment

    report = run_experiment(experiment="skills", corpus="synthetic")
    assert report.experiment == "skills"
    assert report.decision["decision"] in (
        "no_llm_distillation",
        "consider_llm_distillation",
    )
    assert report.batch_result is not None
