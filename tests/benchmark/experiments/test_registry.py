"""The experiment registry (C4) — the 13 standalone experiment adapters.

One ``StandaloneExperiment`` dataclass per standalone experiment replaces the
per-experiment dispatch branches in ``run_experiment``: the adapter knows WHERE
the module's run/decide/render functions live and WHAT the ``ExperimentContext``
contributes as call kwargs. The registry enforces the corpus set per adapter
with the same error shape the old branches used, registered fail-loud against
the single-homed ``EXPERIMENTS`` tuple.
"""

from __future__ import annotations

import dataclasses
import importlib
from types import SimpleNamespace
from typing import Any

import pytest

from seahorse.benchmark.experiments import runner
from seahorse.benchmark.experiments.protocol import Experiment
from seahorse.benchmark.experiments.registry import (
    EXPERIMENT_REGISTRY,
    StandaloneExperiment,
    get_experiment,
    register,
)
from seahorse.benchmark.experiments.variants import EXPERIMENTS

_STUB_RESULT: dict = {"regime": "stub"}
_STUB_DECISION: dict = {"decision": "stub"}


def _stub_fns(calls: dict[str, Any], result: Any) -> tuple[Any, Any, Any]:
    def run_fn(**kwargs: Any) -> Any:
        calls["run"] = kwargs
        return result

    def decide_fn(res: Any) -> dict:
        calls["decide"] = res
        return _STUB_DECISION

    def render_fn(res: Any, decision: dict) -> str:
        calls["render"] = (res, decision)
        return "rendered"

    return run_fn, decide_fn, render_fn


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    run_attr: str,
    decide_attr: str,
    render_attr: str,
    result: Any,
) -> dict[str, Any]:
    """Patch the module's run/decide/render trio with recording stubs."""
    mod = importlib.import_module(module_name)
    calls: dict[str, Any] = {}
    run_fn, decide_fn, render_fn = _stub_fns(calls, result)
    monkeypatch.setattr(mod, run_attr, run_fn)
    monkeypatch.setattr(mod, decide_attr, decide_fn)
    monkeypatch.setattr(mod, render_attr, render_fn)
    return calls


def _wire_reader_quality(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch reader_quality's run+decide with recording stubs."""
    from seahorse.benchmark.experiments import reader_quality

    captured: dict[str, Any] = {}

    def run_fn(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _STUB_RESULT

    def decide_fn(res: Any) -> dict:
        captured["decided"] = res
        return _STUB_DECISION

    monkeypatch.setattr(reader_quality, "run_reader_quality_experiment", run_fn)
    monkeypatch.setattr(reader_quality, "decide_reader_quality", decide_fn)
    return captured


# ---------------------------------------------------------------------------
# Registry integrity
# ---------------------------------------------------------------------------


def test_standalone_registry_is_complete():
    matrix = {"recency", "rerank", "embed", "decay_rrf"}
    assert set(EXPERIMENT_REGISTRY) == set(EXPERIMENTS) - matrix
    assert len(EXPERIMENT_REGISTRY) == 13


def test_adapters_satisfy_experiment_protocol():
    for adapter in EXPERIMENT_REGISTRY.values():
        assert isinstance(adapter, Experiment)


def test_adapter_modules_resolve_with_declared_attrs():
    for adapter in EXPERIMENT_REGISTRY.values():
        mod = importlib.import_module(adapter.module)
        for attr in (adapter.run_attr, adapter.decide_attr, adapter.render_attr):
            assert callable(getattr(mod, attr)), (adapter.name, attr)


def test_get_experiment_returns_registered_adapter():
    assert get_experiment("batch").name == "batch"


def test_get_experiment_unknown_raises_with_experiments_hint():
    with pytest.raises(ValueError, match=r"unknown experiment: 'bogus'"):
        get_experiment("bogus")


def _registration(name: str = "bogus") -> StandaloneExperiment:
    return StandaloneExperiment(
        name=name,
        corpora=("synthetic",),
        module="seahorse.benchmark.experiments.batch",
        run_attr="run_batch_experiment",
        decide_attr="decide_batch",
        render_attr="render_batch_report",
        kwargs_from_ctx=lambda ctx: {"corpus": ctx.corpus, "top_k": ctx.top_k},
    )


def test_register_rejects_duplicate_name():
    with pytest.raises(ValueError, match="duplicate experiment registration"):
        register(_registration("batch"))


def test_register_rejects_name_outside_experiments():
    with pytest.raises(ValueError, match="is not in EXPERIMENTS"):
        register(_registration("bogus"))


def test_standalone_experiment_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        _registration("batch").name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Corpus enforcement (fires before any ingest — the adapter corpora check)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, bad_corpus",
    [("batch", "lmeb-s")]
    + [(name, "claude-mem") for name in EXPERIMENT_REGISTRY if name != "batch"],
)
def test_standalone_adapters_reject_out_of_corpora(name: str, bad_corpus: str):
    with pytest.raises(ValueError, match=f"{name} experiment corpus must be"):
        runner.run_experiment(experiment=name, corpus=bad_corpus)


# ---------------------------------------------------------------------------
# Delegation: run/decide/render via the adapter, envelope via the orchestrator
# ---------------------------------------------------------------------------


def test_delegation_plain_kwargs(monkeypatch: pytest.MonkeyPatch):
    calls = _wire(
        monkeypatch,
        "seahorse.benchmark.experiments.entity_centric",
        "run_entity_centric_experiment",
        "decide_entity_centric",
        "render_entity_centric_report",
        _STUB_RESULT,
    )
    report = runner.run_experiment(experiment="entity_centric", corpus="synthetic")
    assert calls["run"] == {"corpus": "synthetic", "top_k": 10}
    assert calls["decide"] is _STUB_RESULT
    assert report.experiment == "entity_centric"
    assert report.corpus == "synthetic"
    assert report.results == ()
    assert report.batch_result is _STUB_RESULT
    assert report.decision is _STUB_DECISION
    assert runner.render_experiment_report(report) == "rendered"
    assert calls["render"] == (_STUB_RESULT, _STUB_DECISION)


def test_delegation_subsample_kwargs(monkeypatch: pytest.MonkeyPatch):
    calls = _wire(
        monkeypatch,
        "seahorse.benchmark.experiments.two_stage_retrieval",
        "run_two_stage_experiment",
        "decide_two_stage",
        "render_two_stage_report",
        _STUB_RESULT,
    )
    report = runner.run_experiment(
        experiment="two_stage_retrieval", corpus="synthetic"
    )
    assert calls["run"] == {"corpus": "synthetic", "top_k": 10, "subsample": True}
    assert report.batch_result is _STUB_RESULT


def test_delegation_end_to_end_kwargs(monkeypatch: pytest.MonkeyPatch):
    calls = _wire(
        monkeypatch,
        "seahorse.benchmark.experiments.end_to_end",
        "run_end_to_end_experiment",
        "decide_end_to_end",
        "render_end_to_end_report",
        _STUB_RESULT,
    )
    reader = object()
    runner.run_experiment(
        experiment="end_to_end", corpus="synthetic", reader_llm=reader
    )
    assert calls["run"] == {
        "corpus": "synthetic",
        "top_k": 10,
        "subsample": True,
        "context_mode": "summary",
        "reader": reader,
    }


def test_delegation_reader_context_kwargs(monkeypatch: pytest.MonkeyPatch):
    calls = _wire(
        monkeypatch,
        "seahorse.benchmark.experiments.reader_context",
        "run_reader_context_experiment",
        "decide_reader_context",
        "render_reader_context_report",
        _STUB_RESULT,
    )
    reader = object()
    runner.run_experiment(
        experiment="reader_context", corpus="synthetic", reader_llm=reader
    )
    assert calls["run"]["reader"] is reader


def test_delegation_reader_quality_synthetic_inherits_injection(
    monkeypatch: pytest.MonkeyPatch,
):
    reader = object()
    captured = _wire_reader_quality(monkeypatch)
    runner.run_experiment(
        experiment="reader_quality", corpus="synthetic", reader_llm=reader
    )
    # CI branch: both readers inherit the injected value verbatim.
    assert captured["reader_weak"] is reader
    assert captured["reader_strong"] is reader
    assert captured["decided"] is _STUB_RESULT


def test_delegation_reader_quality_synthetic_default_is_none(
    monkeypatch: pytest.MonkeyPatch,
):
    captured = _wire_reader_quality(monkeypatch)
    runner.run_experiment(experiment="reader_quality", corpus="synthetic")
    assert captured["reader_weak"] is None
    assert captured["reader_strong"] is None


def test_delegation_reader_quality_lmeb_builds_reader_pair(
    monkeypatch: pytest.MonkeyPatch,
):
    from seahorse.benchmark.harness.reader_llm import ReaderLLMClient

    captured = _wire_reader_quality(monkeypatch)
    runner.run_experiment(
        experiment="reader_quality",
        corpus="lmeb-s",
        reader_model="ollama/weak",
        strong_reader_model="ollama/strong",
    )
    assert isinstance(captured["reader_weak"], ReaderLLMClient)
    assert isinstance(captured["reader_strong"], ReaderLLMClient)
    assert captured["reader_weak"] is not captured["reader_strong"]


def test_delegation_batch_allows_claude_mem(monkeypatch: pytest.MonkeyPatch):
    calls = _wire(
        monkeypatch,
        "seahorse.benchmark.experiments.batch",
        "run_batch_experiment",
        "decide_batch",
        "render_batch_report",
        _STUB_RESULT,
    )
    report = runner.run_experiment(experiment="batch", corpus="claude-mem")
    assert calls["run"] == {"corpus": "claude-mem", "top_k": 10}
    assert report.corpus == "claude-mem"


# ---------------------------------------------------------------------------
# Envelope assembly + the matrix/outcome cross-check
# ---------------------------------------------------------------------------


def _fake_adapter(matrix: bool) -> Any:
    def decide(result: Any) -> dict:
        return _STUB_DECISION

    return SimpleNamespace(name="fake", corpora=("synthetic",), matrix=matrix, decide=decide)


def test_assemble_report_standalone_channel():
    report = runner._assemble_report(
        _fake_adapter(matrix=False), _STUB_RESULT, "synthetic", True
    )
    assert report.results == ()
    assert report.batch_result is _STUB_RESULT
    assert report.decision is _STUB_DECISION
    assert report.temporal_mode is True


def test_assemble_report_cross_checks_outcome_channel():
    with pytest.raises(ValueError, match="outcome/channel mismatch"):
        runner._assemble_report(
            _fake_adapter(matrix=False), (1, 2), "synthetic", True
        )