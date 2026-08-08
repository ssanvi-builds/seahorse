"""Tests for the F7 experiment harness (variants / decide / runner).

The experiments decide F1 (recency) and F3 (embed) per f7-experimental-design
§5. The decision functions are pure and unit-tested with crafted results (all
threshold branches); the runner is verified end-to-end on the synthetic corpus
(mechanical CI verification — deterministic, no HF/Ollama).
"""

from __future__ import annotations

import pytest

from seahorse.benchmark.contracts import MetricReport
from seahorse.benchmark.experiments import (
    CORPORA,
    EXPERIMENTS,
    NDCG_DEGRADATION_PP,
    NDCG_IMPROVEMENT_PP,
    RECALL_IMPROVEMENT_PP,
    RECENCY_SLICES,
    RERANK_P95_MS,
    decide_embed,
    decide_recency,
    decide_rerank,
    embed_variants,
    recency_variants,
    rerank_variants,
    run_experiment,
    variants_for,
)
from seahorse.benchmark.experiments.decide import ExperimentResult
from seahorse.benchmark.experiments.variants import (
    RECENCY_SWEEP_GAMMAS,
    RECENCY_SWEEP_HALF_LIVES_DAYS,
)
from tests.benchmark.conftest import FakeReaderLLM

# ---------------------------------------------------------------- variants

class TestRecencyVariants:
    def test_baseline_plus_nine_combo_sweep(self):
        variants = recency_variants()
        assert len(variants) == 1 + 9
        base = variants[0]
        assert base.name == "mvp1_rrf"
        assert base.score_source == "mvp1_rrf"
        assert base.recency_config is None
        sweep = variants[1:]
        assert all(v.score_source == "mvp1_rrf_recency" for v in sweep)
        assert all(v.recency_config is not None for v in sweep)

    def test_sweep_covers_full_grid(self):
        combos = {
            (v.recency_config["gamma"], v.recency_config["half_life_days"])
            for v in recency_variants()[1:]
        }
        expected = {
            (g, float(hl)) for g in RECENCY_SWEEP_GAMMAS for hl in RECENCY_SWEEP_HALF_LIVES_DAYS
        }
        assert combos == expected

    def test_variant_names_unique(self):
        names = [v.name for v in recency_variants()]
        assert len(names) == len(set(names))


class TestEmbedVariants:
    def test_body_vs_body_plus_summary(self):
        variants = embed_variants()
        assert [v.embed_mode for v in variants] == ["body", "body+summary"]
        assert variants[0].name == "embed_body"
        assert variants[1].name == "embed_body_summary"


class TestRerankVariants:
    def test_baseline_vs_rerank(self):
        variants = rerank_variants()
        assert [v.name for v in variants] == ["mvp1_rrf", "rrf_rerank"]
        assert variants[0].rerank_enabled is False
        assert variants[0].score_source == "mvp1_rrf"
        assert variants[1].rerank_enabled is True
        assert variants[1].score_source == "rrf_rerank"

    def test_rerank_variant_config_kwargs(self):
        variant = rerank_variants()[1]
        kwargs = variant.as_config_kwargs()
        assert kwargs["rerank_enabled"] is True
        assert kwargs["score_source"] == "rrf_rerank"


class TestVariantsFor:
    def test_unknown_experiment_rejected(self):
        with pytest.raises(ValueError, match="experiment"):
            variants_for("bogus")

    def test_known_kinds(self):
        assert variants_for("recency") == recency_variants()
        assert variants_for("rerank") == rerank_variants()
        assert variants_for("embed") == embed_variants()


# ---------------------------------------------------------------- decide (a)

def _result(name: str, recall_global: float, ndcg: float, slices: dict, *,
            detected: str = "mvp1_rrf") -> ExperimentResult:
    return ExperimentResult(
        variant=next(v for v in recency_variants() + embed_variants() if v.name == name),
        metrics={
            "recall@10": MetricReport("recall@10", recall_global, by_slice=slices),
            "ndcg@10": MetricReport("ndcg@10", ndcg),
        },
        detected_score_source=detected,
        run_errors=[],
        run_id="r",
    )


def _recency_baseline() -> ExperimentResult:
    return _result(
        "mvp1_rrf",
        recall_global=0.5,
        ndcg=0.6,
        slices={"temporal-reasoning": 0.5, "knowledge-update": 0.5},
    )


class TestDecideRecency:
    def test_flip_when_slice_improves_and_ndcg_holds(self):
        baseline = _recency_baseline()
        variant = _result(
            "recency_g1_hl7",
            recall_global=0.55,
            ndcg=0.6,
            slices={"temporal-reasoning": 0.7, "knowledge-update": 0.5},
        )
        decision = decide_recency(baseline, [variant])
        assert decision["decision"] == "flip_f1"
        assert decision["flip"] is True
        assert decision["variant"] == "recency_g1_hl7"
        assert decision["recency_config"] == {"gamma": 1.0, "half_life_days": 7.0}

    def test_rejected_when_ndcg_degrades_beyond_1pp(self):
        baseline = _recency_baseline()
        degraded = _result(
            "recency_g1_hl7",
            recall_global=0.55,
            ndcg=baseline.metric("ndcg@10").global_value - NDCG_DEGRADATION_PP - 0.001,
            slices={"temporal-reasoning": 0.7, "knowledge-update": 0.5},
        )
        decision = decide_recency(baseline, [degraded])
        assert decision["decision"] == "keep_off"

    def test_keep_off_when_no_slice_improves(self):
        baseline = _recency_baseline()
        flat = _result(
            "recency_g0.25_hl30",
            recall_global=0.5,
            ndcg=0.6,
            slices={"temporal-reasoning": 0.5, "knowledge-update": 0.5},
        )
        decision = decide_recency(baseline, [flat])
        assert decision["decision"] == "keep_off"
        assert decision["flip"] is False

    def test_invalid_regime_when_baseline_fallback_g2(self):
        baseline = _recency_baseline()
        baseline = ExperimentResult(
            variant=baseline.variant,
            metrics=baseline.metrics,
            detected_score_source="fallback_g2",
            run_errors=[],
            run_id="r",
        )
        decision = decide_recency(baseline, [_recency_baseline()])
        assert decision["decision"] == "invalid_regime"
        assert decision["flip"] is False

    def test_picks_best_passing_variant(self):
        baseline = _recency_baseline()
        ok = _result(
            "recency_g0.5_hl30",
            recall_global=0.55,
            ndcg=0.6,
            slices={"temporal-reasoning": 0.6, "knowledge-update": 0.5},
        )
        better = _result(
            "recency_g1_hl7",
            recall_global=0.55,
            ndcg=0.6,
            slices={"temporal-reasoning": 0.8, "knowledge-update": 0.5},
        )
        decision = decide_recency(baseline, [ok, better])
        assert decision["variant"] == "recency_g1_hl7"


# ---------------------------------------------------------------- decide (b)

def _rerank_result(name: str, ndcg: float, p95: float, *,
                   detected: str = "mvp1_rrf") -> ExperimentResult:
    return ExperimentResult(
        variant=next(v for v in rerank_variants() if v.name == name),
        metrics={
            "recall@10": MetricReport("recall@10", 0.5),
            "ndcg@10": MetricReport("ndcg@10", ndcg),
            "latency_p95_rerank_ms": MetricReport("latency_p95_rerank_ms", p95),
        },
        detected_score_source=detected,
        run_errors=[],
        run_id="r",
    )


class TestDecideRerank:
    def test_implement_f2_when_ndcg_improves_and_p95_within_budget(self):
        baseline = _rerank_result("mvp1_rrf", ndcg=0.4, p95=0.0)
        variant = _rerank_result(
            "rrf_rerank", ndcg=0.4 + NDCG_IMPROVEMENT_PP + 0.01, p95=300.0
        )
        decision = decide_rerank(baseline, variant)
        assert decision["decision"] == "implement_f2"
        assert decision["flip"] is True
        assert decision["variant"] == "rrf_rerank"
        assert decision["p95_index_rerank_ms"] == pytest.approx(300.0)

    def test_keep_rrf_when_ndcg_below_threshold(self):
        baseline = _rerank_result("mvp1_rrf", ndcg=0.4, p95=0.0)
        variant = _rerank_result(
            "rrf_rerank", ndcg=0.4 + NDCG_IMPROVEMENT_PP / 2, p95=300.0
        )
        decision = decide_rerank(baseline, variant)
        assert decision["decision"] == "keep_rrf"
        assert decision["flip"] is False

    def test_keep_rrf_when_p95_exceeds_budget(self):
        baseline = _rerank_result("mvp1_rrf", ndcg=0.4, p95=0.0)
        variant = _rerank_result(
            "rrf_rerank", ndcg=0.5, p95=RERANK_P95_MS + 100.0
        )
        decision = decide_rerank(baseline, variant)
        assert decision["decision"] == "keep_rrf"
        assert decision["flip"] is False

    def test_invalid_regime_when_baseline_fallback_g2(self):
        baseline = _rerank_result("mvp1_rrf", ndcg=0.4, p95=0.0, detected="fallback_g2")
        variant = _rerank_result("rrf_rerank", ndcg=0.5, p95=300.0)
        decision = decide_rerank(baseline, variant)
        assert decision["decision"] == "invalid_regime"
        assert decision["flip"] is False


# ---------------------------------------------------------------- decide (c)

class TestDecideEmbed:
    def test_flip_f3_when_recall_improves_1pp(self):
        baseline = _result("embed_body", 0.6, 0.6, {})
        variant = _result("embed_body_summary", 0.7, 0.6, {})
        decision = decide_embed(baseline, variant)
        assert decision["decision"] == "flip_f3"
        assert decision["flip"] is True
        assert decision["recall_delta"] == pytest.approx(0.1)

    def test_keep_body_only_when_below_threshold(self):
        baseline = _result("embed_body", 0.6, 0.6, {})
        variant = _result(
            "embed_body_summary",
            0.6 + RECALL_IMPROVEMENT_PP / 2,
            0.6,
            {},
        )
        decision = decide_embed(baseline, variant)
        assert decision["decision"] == "keep_body_only"
        assert decision["flip"] is False

    def test_invalid_regime_when_baseline_fallback_g2(self):
        baseline = _result("embed_body", 0.6, 0.6, {}, detected="fallback_g2")
        variant = _result("embed_body_summary", 0.7, 0.6, {})
        decision = decide_embed(baseline, variant)
        assert decision["decision"] == "invalid_regime"


# ------------------------------------------------------------ runner (synthetic)

def _fake_kwargs():
    return {
        "reader_model": "fake-reader",
        "judge_model": "fake-judge",
        "reader_llm": FakeReaderLLM(),
    }


class TestRunExperimentSynthetic:
    def test_recency_report_shape(self, tmp_path):
        report = run_experiment(
            experiment="recency", corpus="synthetic", output_dir=str(tmp_path),
            **_fake_kwargs(),
        )
        assert len(report.results) == 10
        assert report.results[0].variant.name == "mvp1_rrf"
        # every variant ran in the hybrid regime (no fallback_g2)
        assert all(r.detected_score_source in ("mvp1_rrf", "mvp1_rrf_recency")
                   for r in report.results)
        for r in report.results:
            assert "recall@10" in r.metrics
            assert "ndcg@10" in r.metrics
            assert RECENCY_SLICES[0] in r.metric("recall@10").by_slice
        assert report.decision["decision"] in ("keep_off", "flip_f1")

    def test_embed_report_shape(self, tmp_path):
        report = run_experiment(
            experiment="embed", corpus="synthetic", output_dir=str(tmp_path),
            **_fake_kwargs(),
        )
        assert len(report.results) == 2
        assert [r.variant.embed_mode for r in report.results] == ["body", "body+summary"]
        assert report.decision["decision"] in ("keep_body_only", "flip_f3")

    def test_rerank_report_shape(self, tmp_path):
        report = run_experiment(
            experiment="rerank", corpus="synthetic", output_dir=str(tmp_path),
            **_fake_kwargs(),
        )
        assert len(report.results) == 2
        assert [r.variant.name for r in report.results] == ["mvp1_rrf", "rrf_rerank"]
        # Both variants ran in the hybrid regime (no fallback_g2).
        assert all(r.detected_score_source in ("mvp1_rrf", "rrf_rerank")
                   for r in report.results)
        for r in report.results:
            assert "ndcg@10" in r.metrics
            assert "latency_p95_rerank_ms" in r.metrics
        # The rerank variant pinned its model in the fingerprint.
        assert report.results[1].run_id != report.results[0].run_id
        assert report.decision["decision"] in ("keep_rrf", "implement_f2")

    def test_unknown_experiment_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="experiment"):
            run_experiment(
                experiment="bogus", corpus="synthetic", output_dir=str(tmp_path),
                **_fake_kwargs(),
            )

    def test_unknown_corpus_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="corpus"):
            run_experiment(
                experiment="recency", corpus="bogus", output_dir=str(tmp_path),
                **_fake_kwargs(),
            )

    def test_decision_is_deterministic(self, tmp_path):
        kwargs = _fake_kwargs()
        r1 = run_experiment(experiment="recency", corpus="synthetic",
                            output_dir=str(tmp_path), **kwargs)
        r2 = run_experiment(experiment="recency", corpus="synthetic",
                            output_dir=str(tmp_path), **kwargs)
        assert [r.variant.name for r in r1.results] == [r.variant.name for r in r2.results]
        assert [r.detected_score_source for r in r1.results] == [
            r.detected_score_source for r in r2.results
        ]
        assert r1.decision == r2.decision


def test_warm_db_matches_fresh_db(tmp_path):
    """Warm-DB (shared ingest across variants) must produce IDENTICAL retrieval
    metrics to fresh-DB runs — the recency boost reads created_at/now, both
    deterministic and identical across the shared template (f7 §5a)."""
    warm = run_experiment(
        experiment="recency", corpus="synthetic",
        output_dir=str(tmp_path / "warm"), **_fake_kwargs(), warm_db=True,
    )
    fresh = run_experiment(
        experiment="recency", corpus="synthetic",
        output_dir=str(tmp_path / "fresh"), **_fake_kwargs(), warm_db=False,
    )
    assert len(warm.results) == len(fresh.results) == 10
    for wr, fr in zip(warm.results, fresh.results, strict=True):
        assert wr.variant.name == fr.variant.name
        assert wr.metric("recall@10").global_value == fr.metric("recall@10").global_value
        assert wr.metric("ndcg@10").global_value == fr.metric("ndcg@10").global_value
        assert wr.metric("recall@10").by_slice == fr.metric("recall@10").by_slice
        assert wr.detected_score_source == fr.detected_score_source
    assert warm.decision == fresh.decision


def test_warm_db_embed_reuses_body_template(tmp_path):
    """The embed experiment's body variant reuses the body template; only
    body+summary needs its own ingest (f7 §5c — different embedding text)."""
    cache: dict = {}
    report = run_experiment(
        experiment="embed", corpus="synthetic",
        output_dir=str(tmp_path), **_fake_kwargs(), template_cache=cache,
    )
    assert len(report.results) == 2
    # Both variants ran in the hybrid regime with the shared template.
    assert all(r.detected_score_source == "mvp1_rrf" for r in report.results)
    # The cache holds exactly two templates: body (shared) + body+summary.
    assert len(cache) == 2
    assert any(k[1] == "body" for k in cache)
    assert any(k[1] == "body+summary" for k in cache)


def test_clock_delta_spans_real_date_range(synthetic_dataset):
    """The AdvancingClock delta is derived from the haystack's real date spread
    (span / deduped turns), NOT a fixed 1-day-per-write — a fixed delta would
    make created_at span N_writes days (547 years for LMEB's 199K turns) and
    the recency boost's age would be meaningless (f7 §5a)."""
    from seahorse.benchmark.experiments.runner import _clock_delta_seconds

    delta = _clock_delta_seconds(synthetic_dataset)
    # 3 deduped sessions spanning 2026-01-01..01-03 (2 days) → 16h per write.
    assert delta == pytest.approx(57600.0)
    assert delta < 86400.0  # strictly less than the old fixed 1-day delta


def test_experiments_and_corpora_constants():
    assert set(EXPERIMENTS) == {"recency", "rerank", "embed", "batch"}
    assert set(CORPORA) == {"synthetic", "lmeb-s", "claude-mem"}
