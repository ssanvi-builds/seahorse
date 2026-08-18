"""Tests for the decay experiment — FAMA-style (decide Sprint D).

The synthetic corpus verifies the harness MECHANICS: the decay bias (a query-time
downweight by age) must improve FAMA-style (the obsolete old version stops
surfacing) without damaging MPA (the valid new version stays in top-k). The
authoritative decision comes from an LMEB-S knowledge-update run
(``--corpus lmeb-s``, not yet built).
"""

from __future__ import annotations

import pytest

from seahorse.benchmark.experiments.decay import (
    DECAY_FAMA_GAIN_THRESHOLD,
    DECAY_MPA_DAMAGE_THRESHOLD,
    DECAY_S_SWEEP,
    DECAY_TOP_K,
    DecayExperimentResult,
    DecaySweepPoint,
    build_synthetic_corpus,
    decide_decay,
    render_decay_report,
    run_decay_experiment,
)


def _result(
    *,
    fama_off: float = 0.0,
    mpa_off: float = 1.0,
    fama_on: float = 1.0,
    mpa_on: float = 1.0,
    best_s: float = 0.8,
    regime: str = "hybrid",
) -> DecayExperimentResult:
    return DecayExperimentResult(
        fama_off=fama_off,
        mpa_off=mpa_off,
        fama_on=fama_on,
        mpa_on=mpa_on,
        best_s=best_s,
        sweep=tuple(DecaySweepPoint(s=s, fama=0.0, mpa=1.0) for s in DECAY_S_SWEEP),
        n_facts=7,
        n_queries=7,
        regime=regime,
    )


class TestRunDecayExperimentSynthetic:
    def test_hybrid_regime_no_fallback(self) -> None:
        result = run_decay_experiment(corpus="synthetic")
        assert result.regime == "hybrid"
        assert result.n_facts == 7
        assert result.n_queries == 7
        assert len(result.sweep) == len(DECAY_S_SWEEP)

    def test_decay_improves_fama_without_damaging_mpa(self) -> None:
        """The synthetic corpus is designed so decay (best S) improves FAMA-style
        (the obsolete old version stops surfacing) while MPA stays high (the
        valid new version remains in top-k)."""
        result = run_decay_experiment(corpus="synthetic")
        # Without decay the obsolete old version surfaces (FAMA low).
        assert result.fama_off < 0.5
        # With decay the old version stops surfacing (FAMA high).
        assert result.fama_on > result.fama_off
        assert result.fama_on - result.fama_off >= DECAY_FAMA_GAIN_THRESHOLD
        # MPA is not damaged beyond the cap.
        assert result.mpa_off - result.mpa_on <= DECAY_MPA_DAMAGE_THRESHOLD
        assert result.mpa_on > 0.5

    def test_sweep_starts_at_decay_off(self) -> None:
        """S=0 is the decay-OFF baseline: the result's OFF metrics match it."""
        result = run_decay_experiment(corpus="synthetic")
        assert result.sweep[0].s == 0.0
        assert result.fama_off == result.sweep[0].fama
        assert result.mpa_off == result.sweep[0].mpa

    def test_unknown_corpus_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown corpus"):
            run_decay_experiment(corpus="nope")

    def test_lmeb_corpus_not_built_fail_loud(self) -> None:
        with pytest.raises(NotImplementedError, match="not built yet"):
            run_decay_experiment(corpus="lmeb-s")


class TestDecideDecay:
    def test_sprint_d_when_fama_gain_and_mpa_ok(self) -> None:
        result = _result(fama_off=0.0, mpa_off=1.0, fama_on=1.0, mpa_on=1.0, best_s=0.8)
        decision = decide_decay(result)
        assert decision["decision"] == "sprint_d"
        assert decision["flip"] is True

    def test_decay_off_when_fama_gain_below_threshold(self) -> None:
        result = _result(fama_off=0.9, mpa_off=1.0, fama_on=0.92, mpa_on=1.0, best_s=0.2)
        decision = decide_decay(result)
        assert decision["decision"] == "decay_off"
        assert decision["flip"] is False

    def test_decay_off_when_mpa_damaged(self) -> None:
        result = _result(fama_off=0.0, mpa_off=1.0, fama_on=1.0, mpa_on=0.5, best_s=1.0)
        decision = decide_decay(result)
        assert decision["decision"] == "decay_off"
        assert decision["flip"] is False

    def test_invalid_regime_on_fallback_g2(self) -> None:
        result = _result(fama_off=0.0, mpa_off=0.0, fama_on=0.0, mpa_on=0.0, regime="fallback_g2")
        decision = decide_decay(result)
        assert decision["decision"] == "invalid_regime"
        assert decision["flip"] is False

    def test_threshold_boundary(self) -> None:
        """At the thresholds the decision is sprint_d (>= gain, <= damage).

        A tiny epsilon keeps the MPA damage just inside the cap — 0.05 is not
        exact in binary, so ``1.0 - 0.05`` rounds to a hair above the threshold.
        """
        result = _result(
            fama_off=0.0,
            mpa_off=1.0,
            fama_on=DECAY_FAMA_GAIN_THRESHOLD,
            mpa_on=1.0 - DECAY_MPA_DAMAGE_THRESHOLD + 1e-9,
            best_s=0.4,
        )
        assert decide_decay(result)["decision"] == "sprint_d"


class TestBuildSyntheticCorpus:
    def test_ingests_and_retrieves(self, tmp_path) -> None:
        facade, storage, episodes, facts = build_synthetic_corpus(tmp_path / "bench.db")
        try:
            # 7 facts x 2 versions + 32 distractors = 46 episodes.
            assert len(episodes) == 46
            assert len(facts) == 7
            # The stored fact ids are the engine-derived ep_ids.
            stored_ids = {ep.id for ep in episodes}
            assert all(old_id in stored_ids and new_id in stored_ids for _, old_id, new_id in facts)
            rows = facade.recall("What is the capital of France?", k=DECAY_TOP_K)
            assert rows
            assert any(r.score > 0.0 for r in rows)  # hybrid, not the listing regime
        finally:
            storage.close()


class TestRenderDecayReport:
    def test_render_contains_metrics_and_decision(self) -> None:
        result = _result(fama_off=0.0, mpa_off=1.0, fama_on=1.0, mpa_on=1.0, best_s=0.8)
        decision = decide_decay(result)
        rendered = render_decay_report(result, decision)
        assert "Decay experiment: FAMA-style" in rendered
        assert "FAMA-style: 0.000 -> 1.000" in rendered
        assert "MPA:        1.000 -> 1.000" in rendered
        assert "decision: sprint_d" in rendered


class TestRunExperimentWiring:
    def test_decay_via_run_experiment(self) -> None:
        """``run_experiment(experiment='decay')`` delegates to the decay module."""
        from seahorse.benchmark.experiments.runner import (
            render_experiment_report,
            run_experiment,
        )

        report = run_experiment(experiment="decay", corpus="synthetic")
        assert report.experiment == "decay"
        assert report.batch_result is not None
        assert report.batch_result.regime == "hybrid"
        assert report.decision["decision"] in ("sprint_d", "decay_off")
        rendered = render_experiment_report(report)
        assert "Decay experiment: FAMA-style" in rendered
        assert "decision:" in rendered

    def test_decay_rejects_claude_mem_corpus(self) -> None:
        from seahorse.benchmark.experiments.runner import run_experiment

        with pytest.raises(ValueError, match="decay experiment corpus"):
            run_experiment(experiment="decay", corpus="claude-mem")
