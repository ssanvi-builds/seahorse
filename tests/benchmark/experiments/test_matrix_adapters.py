"""The matrix adapters (C4) — the 4 variant experiments behind the seam.

recency/rerank/embed/decay_rrf move behind ``Experiment`` adapters: ``run``
drives the harness once per ``variants_for(name)`` variant, ``decide`` routes
to the module decision (recency compares the whole sweep, the others compare
baseline vs the first sweep variant), and ``render`` emits the shared variant
table with the experiment-specific inserts.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from seahorse.benchmark.experiments import runner
from seahorse.benchmark.experiments.matrix import (
    DecayRrfMatrixExperiment,
    EmbedMatrixExperiment,
    RecencyMatrixExperiment,
    RerankMatrixExperiment,
)
from seahorse.benchmark.experiments.protocol import Experiment, ExperimentContext, ExperimentReport

# ---------------------------------------------------------------------------
# Registry completeness — flipped: all 17 experiments behind the seam
# ---------------------------------------------------------------------------


def test_registry_covers_all_experiments():
    from seahorse.benchmark.experiments.registry import EXPERIMENT_REGISTRY
    from seahorse.benchmark.experiments.variants import EXPERIMENTS

    assert set(EXPERIMENT_REGISTRY) == set(EXPERIMENTS)
    assert len(EXPERIMENT_REGISTRY) == 17


@pytest.mark.parametrize(
    "adapter_factory",
    [
        RecencyMatrixExperiment,
        RerankMatrixExperiment,
        EmbedMatrixExperiment,
        DecayRrfMatrixExperiment,
    ],
)
def test_matrix_adapters_satisfy_protocol(adapter_factory):
    adapter = adapter_factory()
    assert adapter.matrix is True
    assert isinstance(adapter, Experiment)


# ---------------------------------------------------------------------------
# decide-routing: recency compares the sweep, the rest baseline vs sweep[0]
# ---------------------------------------------------------------------------


def _capture_decide(monkeypatch: pytest.MonkeyPatch, fn_name: str, key: str) -> list:
    from seahorse.benchmark.experiments import decide as decide_mod

    calls: list = []

    def stub(*args: Any) -> dict:
        calls.append((key, *args))
        return {"decision": "stub"}

    monkeypatch.setattr(decide_mod, fn_name, stub)
    return calls


def test_recency_decide_receives_baseline_and_sweep(
    monkeypatch: pytest.MonkeyPatch,
):
    calls = _capture_decide(monkeypatch, "decide_recency", "recency")
    r1, r2, r3 = object(), object(), object()
    decision = RecencyMatrixExperiment().decide((r1, r2, r3))
    assert calls == [("recency", r1, (r2, r3))]
    assert decision == {"decision": "stub"}


@pytest.mark.parametrize(
    "adapter_factory, fn_name",
    [
        (RerankMatrixExperiment, "decide_rerank"),
        (EmbedMatrixExperiment, "decide_embed"),
        (DecayRrfMatrixExperiment, "decide_decay_rrf"),
    ],
)
def test_other_decide_receives_baseline_and_first_sweep(
    monkeypatch: pytest.MonkeyPatch, adapter_factory, fn_name: str
):
    calls = _capture_decide(monkeypatch, fn_name, fn_name)
    r1, r2, r3 = object(), object(), object()
    decision = adapter_factory().decide((r1, r2, r3))
    assert calls == [(fn_name, r1, r2)]
    assert decision == {"decision": "stub"}


# ---------------------------------------------------------------------------
# run: drives the harness once per variant through the runner bridge
# ---------------------------------------------------------------------------


def _ctx(**overrides: Any) -> ExperimentContext:
    defaults: dict[str, Any] = {
        "corpus": "synthetic",
        "top_k": 10,
        "subsample": True,
        "temporal": True,
        "pit_queries": False,
        "output_dir": "benchmark-output",
        "reader_llm": None,
        "reader_model": "ollama/weak",
        "judge_model": "ollama/judge",
        "strong_reader_model": "ollama/strong",
        "context_mode": "summary",
        "warm_db": True,
        "template_cache": None,
    }
    defaults.update(overrides)
    return ExperimentContext(**defaults)


def _fake_dataset(corpus: str, *, subsample: bool) -> SimpleNamespace:
    return SimpleNamespace(instances=())


def test_matrix_run_drives_variant_loop(monkeypatch: pytest.MonkeyPatch):
    from seahorse.benchmark.experiments import runner as runner_mod
    from seahorse.benchmark.experiments import variants as variants_mod

    variants = [SimpleNamespace(name="v0"), SimpleNamespace(name="v1")]
    requested: list[str] = []

    def fake_variants_for(name: str):
        requested.append(name)
        return iter(variants)

    captured: list[dict[str, Any]] = []

    def fake_run_variant(**kwargs: Any) -> Any:
        captured.append(kwargs)
        return SimpleNamespace(variant=kwargs["variant"])

    monkeypatch.setattr(variants_mod, "variants_for", fake_variants_for)
    monkeypatch.setattr(runner_mod, "_load_dataset", _fake_dataset)
    monkeypatch.setattr(runner_mod, "_run_variant", fake_run_variant)

    out = RecencyMatrixExperiment().run(_ctx())
    assert requested == ["recency"]
    assert isinstance(out, tuple) and len(out) == 2
    assert captured[0]["variant"] is variants[0]
    assert captured[1]["variant"] is variants[1]
    assert captured[0]["corpus"] == "synthetic"
    assert captured[0]["pit_queries"] is False
    assert captured[0]["output_dir"] == Path("benchmark-output")


def test_matrix_run_warm_db_passes_outer_cache(monkeypatch: pytest.MonkeyPatch):
    from seahorse.benchmark.experiments import runner as runner_mod
    from seahorse.benchmark.experiments import variants as variants_mod

    cache: dict[str, Any] = {}
    captured: list[dict[str, Any]] = []

    monkeypatch.setattr(
        variants_mod, "variants_for", lambda name: iter([SimpleNamespace(name="v0")])
    )
    monkeypatch.setattr(runner_mod, "_load_dataset", _fake_dataset)
    monkeypatch.setattr(
        runner_mod,
        "_run_variant",
        lambda **kwargs: captured.append(kwargs) or SimpleNamespace(variant=kwargs["variant"]),
    )

    EmbedMatrixExperiment().run(_ctx(template_cache=cache))
    assert captured[0]["template_cache"] is cache


# ---------------------------------------------------------------------------
# render parity: the shared table, the inserts, the decision block
# ---------------------------------------------------------------------------


def _variant_result(
    name: str, *, recall: float, ndcg: float, by_slice: dict | None = None, p95: float | None = None
):
    def metric(key: str) -> SimpleNamespace:
        if key == "latency_p95_rerank_ms":
            return SimpleNamespace(global_value=p95, by_slice={})
        values = {"recall@10": recall, "ndcg@10": ndcg}
        return SimpleNamespace(global_value=values[key], by_slice=by_slice or {})

    return SimpleNamespace(
        variant=SimpleNamespace(name=name),
        detected_score_source="mvp1_rrf",
        metrics={"recall@10": object(), "ndcg@10": object()},
        metric=metric,
    )


def _row(name: str, recall: float, ndcg: float) -> str:
    return f"{name:<28} {'mvp1_rrf':<20} {recall:>9.3f} {ndcg:>8.3f}"


def test_render_recency_table_with_slice_insert():
    report = ExperimentReport(
        experiment="recency",
        corpus="synthetic",
        temporal_mode=True,
        results=(
            _variant_result(
                "baseline",
                recall=0.500,
                ndcg=0.400,
                by_slice={"temporal-reasoning": 0.300, "knowledge-update": 0.200},
            ),
            _variant_result(
                "sweep1",
                recall=0.600,
                ndcg=0.500,
                by_slice={"temporal-reasoning": 0.310, "knowledge-update": 0.210},
            ),
        ),
        decision={"decision": "keep_on", "flip": False, "reason": "flat"},
    )
    expected = "\n".join(
        [
            "# Benchmark experiment: recency  (corpus=synthetic, temporal=True)",
            "",
            f"{'variant':<28} {'detected':<20} {'recall@10':>9} {'ndcg@10':>8}",
            _row("baseline", 0.500, 0.400),
            _row("sweep1", 0.600, 0.500),
            "",
            "recall@10 by slice (temporal-reasoning / knowledge-update):",
            "  " + "baseline".ljust(28) + " tr=0.300 ku=0.200",
            "  " + "sweep1".ljust(28) + " tr=0.310 ku=0.210",
            "",
            "## Decision",
            "decision: keep_on",
            "flip: False",
            "reason: flat",
        ]
    )
    assert runner.render_experiment_report(report) == expected


def test_render_rerank_table_with_p95_insert():
    report = ExperimentReport(
        experiment="rerank",
        corpus="lmeb-s",
        temporal_mode=False,
        results=(
            _variant_result("baseline", recall=0.100, ndcg=0.200, p95=0.0),
            _variant_result("rerank", recall=0.150, ndcg=0.250, p95=420.0),
        ),
        decision={"decision": "enable", "flip": True, "reason": "improved"},
    )
    out = runner.render_experiment_report(report)
    expected_tail = "\n".join(
        [
            "",
            "p95_index_rerank_ms (rerank-path INDEX latency):",
            "  " + "baseline".ljust(28) + " p95=0ms",
            "  " + "rerank".ljust(28) + " p95=420ms",
            "",
            "## Decision",
            "decision: enable",
            "flip: True",
            "reason: improved",
        ]
    )
    assert out.endswith(expected_tail)
    assert out.startswith("# Benchmark experiment: rerank  (corpus=lmeb-s, temporal=False)")
    assert _row("baseline", 0.100, 0.200) in out


@pytest.mark.parametrize("experiment", ["embed", "decay_rrf"])
def test_render_embed_and_decay_have_no_insert(experiment: str):
    report = ExperimentReport(
        experiment=experiment,
        corpus="synthetic",
        temporal_mode=True,
        results=(_variant_result("baseline", recall=0.5, ndcg=0.6),),
        decision={"decision": "keep_off", "flip": False, "reason": "no gain"},
    )
    out = runner.render_experiment_report(report)
    assert out.endswith("reason: no gain")
    assert "by slice" not in out
    assert "p95" not in out


# ---------------------------------------------------------------------------
# Envelope cross-check: the results channel + the flag/outcome guard
# ---------------------------------------------------------------------------


def _fake_adapter(matrix: bool) -> Any:
    def decide(result: Any) -> dict:
        return {"decision": "stub"}

    return SimpleNamespace(name="fake", corpora=("synthetic",), matrix=matrix, decide=decide)


def test_assemble_report_matrix_channel():
    r1, r2 = object(), object()
    report = runner._assemble_report(_fake_adapter(matrix=True), (r1, r2), "synthetic", True)
    assert report.results == (r1, r2)
    assert report.batch_result is None


def test_assemble_report_matrix_flag_mismatch():
    with pytest.raises(ValueError, match="outcome/channel mismatch"):
        runner._assemble_report(_fake_adapter(matrix=True), object(), "synthetic", True)