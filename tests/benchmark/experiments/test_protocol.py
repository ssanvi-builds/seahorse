"""The experiment protocol contract (C4) — one seam for the 17 experiments.

``runtime_checkable`` validates only the protocol METHODS, never its data
members (the same limitation ``facade.py`` documents for ``supports_pit``), so
this suite pins the data members explicitly via type hints and structural
conformance — the C3 ``test_structurally_satisfies_protocol`` pattern.
"""

from __future__ import annotations

import dataclasses
import typing

import pytest

from seahorse.benchmark.experiments.protocol import (
    Experiment,
    ExperimentContext,
    ExperimentReport,
)

# ---------------------------------------------------------------------------
# ExperimentContext
# ---------------------------------------------------------------------------


def test_experiment_context_fields_are_pinned():
    names = [f.name for f in dataclasses.fields(ExperimentContext)]
    assert names == [
        "corpus",
        "top_k",
        "subsample",
        "temporal",
        "pit_queries",
        "output_dir",
        "reader_llm",
        "reader_model",
        "judge_model",
        "strong_reader_model",
        "context_mode",
        "warm_db",
        "template_cache",
    ]


def test_experiment_context_is_frozen():
    ctx = ExperimentContext(
        corpus="synthetic",
        top_k=10,
        subsample=True,
        temporal=True,
        pit_queries=True,
        output_dir="benchmark-output",
        reader_llm=None,
        reader_model="m",
        judge_model="j",
        strong_reader_model="s",
        context_mode="summary",
        warm_db=True,
        template_cache=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.corpus = "lmeb-s"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Experiment protocol — members pinned explicitly (isinstance can't)
# ---------------------------------------------------------------------------


def test_experiment_protocol_data_members():
    hints = typing.get_type_hints(Experiment)
    assert hints["name"] is str
    assert hints["matrix"] is bool
    assert hints["corpora"] == tuple[str, ...]


def test_experiment_protocol_methods():
    for method in ("run", "decide", "render"):
        assert callable(getattr(Experiment, method))
    assert typing.get_type_hints(Experiment)["corpora"] == tuple[str, ...]


class _FullAdapter:
    name = "fake"
    corpora = ("synthetic",)
    matrix = False

    def run(self, ctx: ExperimentContext) -> object: ...

    def decide(self, result: object) -> dict: ...

    def render(self, report: ExperimentReport) -> str: ...


class _MissingRender:
    name = "fake"
    corpora = ("synthetic",)
    matrix = False

    def run(self, ctx: ExperimentContext) -> object: ...

    def decide(self, result: object) -> dict: ...


def test_runtime_checkable_checks_methods_only():
    assert isinstance(_FullAdapter(), Experiment)
    assert not isinstance(_MissingRender(), Experiment)


# ---------------------------------------------------------------------------
# ExperimentReport — the common envelope with two channels
# ---------------------------------------------------------------------------


def test_experiment_report_fields_are_pinned():
    names = [f.name for f in dataclasses.fields(ExperimentReport)]
    assert names == [
        "experiment",
        "corpus",
        "temporal_mode",
        "results",
        "decision",
        "batch_result",
    ]


def test_experiment_report_batch_result_defaults_to_none():
    report = ExperimentReport(
        experiment="rrf_k",
        corpus="synthetic",
        temporal_mode=True,
        results=(),
        decision={"decision": "keep_on"},
    )
    assert report.batch_result is None


def test_experiment_report_is_frozen():
    report = ExperimentReport(
        experiment="recency",
        corpus="synthetic",
        temporal_mode=True,
        results=(),
        decision={},
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.decision = {}  # type: ignore[misc]


def test_runner_reexports_experiment_report():
    from seahorse.benchmark.experiments import runner

    assert runner.ExperimentReport is ExperimentReport