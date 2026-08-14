"""``MetricRegistry`` — pluggable metric registration.

Adding a metric = registering an instance; the runner and reporters never
change. The registry holds instances (not classes) so metrics can carry
dependencies (tokenizer, probe results).
"""

from __future__ import annotations

from collections.abc import Sequence

from seahorse.benchmark.config import BenchmarkConfig
from seahorse.benchmark.contracts import (
    BenchmarkInstance,
    Metric,
    MetricResult,
    SUTResponse,
)


class MetricRegistry:
    """Ordered registry of ``Metric`` instances, keyed by ``metric.name()``."""

    def __init__(self) -> None:
        self._metrics: dict[str, Metric] = {}

    def register(self, metric: Metric) -> None:
        self._metrics[metric.name()] = metric

    def get(self, name: str) -> Metric:
        if name not in self._metrics:
            raise KeyError(f"Unknown metric: {name}. Available: {list(self._metrics)}")
        return self._metrics[name]

    def all(self) -> list[Metric]:
        return list(self._metrics.values())

    def names(self) -> list[str]:
        return list(self._metrics)

    def compute_all(
        self,
        instances: Sequence[BenchmarkInstance],
        responses: Sequence[SUTResponse],
        config: BenchmarkConfig,
    ) -> list[MetricResult]:
        return [m.compute(instances, responses, config) for m in self._metrics.values()]


__all__ = ["MetricRegistry"]
