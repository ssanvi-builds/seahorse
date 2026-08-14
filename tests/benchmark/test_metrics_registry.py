"""Tests for the ``MetricRegistry`` (pluggable registration)."""

from __future__ import annotations

import pytest

from seahorse.benchmark.config import BenchmarkConfig
from seahorse.benchmark.contracts import MetricReport, MetricResult
from seahorse.benchmark.metrics.registry import MetricRegistry
from seahorse.benchmark.metrics.retrieval import MRR, RecallAtK


class _FakeMetric:
    def __init__(self, name: str, value: float) -> None:
        self._name = name
        self._value = value

    def name(self) -> str:
        return self._name

    def requires_golden(self) -> bool:
        return False

    def requires_retrieval(self) -> bool:
        return False

    def compute(self, instances, responses, config) -> MetricResult:
        return MetricResult(
            metric_name=self._name,
            report=MetricReport(metric_name=self._name, global_value=self._value, n_samples=1),
        )


def test_register_and_get():
    reg = MetricRegistry()
    reg.register(_FakeMetric("m1", 0.5))
    assert reg.get("m1").name() == "m1"


def test_get_unknown_raises():
    reg = MetricRegistry()
    with pytest.raises(KeyError, match="m1"):
        reg.get("m1")


def test_compute_all_returns_ordered_results():
    reg = MetricRegistry()
    reg.register(_FakeMetric("a", 0.1))
    reg.register(_FakeMetric("b", 0.2))
    results = reg.compute_all([], [], BenchmarkConfig())
    assert [r.metric_name for r in results] == ["a", "b"]
    assert results[0].report.global_value == 0.1


def test_registry_holds_instances_with_dependencies():
    """Metrics are instances (not classes) so they can carry dependencies."""
    reg = MetricRegistry()
    reg.register(RecallAtK())
    reg.register(MRR())
    assert set(reg.names()) == {"recall@10", "mrr"}
