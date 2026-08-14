"""Efficiency metrics — REAL tokens + latency p95 per level.

- ``TokenEfficiencyMetric`` — ``savings_vs_full_only_baseline_pct`` with REAL
  measured tokens (via the reader tokenizer), never ``len*50``. The baseline is
  the full haystack context tokenized.
- ``LatencyP95Metric`` — p95 INDEX from the QA responses' ``latency_ms``; p95
  TIMELINE/FULL from the ``LevelProbeRunner`` (isolated probes, no reader LLM).
"""

from __future__ import annotations

from collections.abc import Sequence

from seahorse.benchmark.config import BenchmarkConfig
from seahorse.benchmark.contracts import (
    BenchmarkInstance,
    MetricReport,
    MetricResult,
    SUTResponse,
)
from seahorse.benchmark.metrics import _p95


class TokenEfficiencyMetric:
    """Token savings vs the full-context baseline (REAL tokens)."""

    def __init__(self, tokenizer) -> None:
        self._tokenizer = tokenizer

    def name(self) -> str:
        return "token_efficiency"

    def requires_golden(self) -> bool:
        return False

    def requires_retrieval(self) -> bool:
        return True

    def compute(
        self,
        instances: Sequence[BenchmarkInstance],
        responses: Sequence[SUTResponse],
        config: BenchmarkConfig,
    ) -> MetricResult:
        baseline = self._full_context_tokens(instances)
        measured = sum(r.tokens_consumed_measured for r in responses)
        savings = (baseline - measured) / baseline if baseline > 0 else 0.0
        return MetricResult(
            metric_name=self.name(),
            report=MetricReport(
                metric_name=self.name(),
                global_value=savings,
                n_samples=len(responses),
                details={
                    "tokens_measured": True,
                    "baseline": "full_context_tokenized",
                    "baseline_tokens": baseline,
                    "measured_tokens": measured,
                },
            ),
        )

    def _full_context_tokens(self, instances: Sequence[BenchmarkInstance]) -> int:
        text = "\n".join(
            turn["body"]
            for inst in instances
            for session in inst.haystack
            for turn in session.get("turns", [])
        )
        return self._tokenizer.count(text)


class LatencyP95Metric:
    """p95 latency per disclosure level.

    INDEX comes from the QA responses (``latency_ms["index"]``); TIMELINE/FULL
    come from the ``LevelProbeRunner``'s isolated probes (passed in the
    constructor — the runner computes them once per run).
    """

    def __init__(self, probe_results: dict | None = None) -> None:
        self._probe_results = probe_results or {}

    def name(self) -> str:
        return "latency_p95_ms"

    def requires_golden(self) -> bool:
        return False

    def requires_retrieval(self) -> bool:
        return True

    def compute(
        self,
        instances: Sequence[BenchmarkInstance],
        responses: Sequence[SUTResponse],
        config: BenchmarkConfig,
    ) -> MetricResult:
        index_lat = [r.latency_ms.get("index", 0.0) for r in responses]
        by_slice = {
            "index": _p95(index_lat),
            "timeline": self._probe_results.get("p95_timeline_ms", 0.0),
            "full": self._probe_results.get("p95_full_ms", 0.0),
        }
        return MetricResult(
            metric_name=self.name(),
            report=MetricReport(
                metric_name=self.name(),
                global_value=by_slice["index"],
                by_slice=by_slice,
                n_samples=len(responses),
            ),
        )


class LatencyP95RerankMetric:
    """p95 of the INDEX call latency when rerank is enabled.

    ``p95_index_rerank_ms`` is the stage-3 rerank budget (NFR: <= 500ms).
    The SUT records ``latency_ms["index_rerank"]`` ONLY when ``rerank_enabled``
    (the rerank-path INDEX latency); the metric reports 0.0 when absent
    (baseline variants — the base path keeps its 250ms promise).
    """

    def name(self) -> str:
        return "latency_p95_rerank_ms"

    def requires_golden(self) -> bool:
        return False

    def requires_retrieval(self) -> bool:
        return True

    def compute(
        self,
        instances: Sequence[BenchmarkInstance],
        responses: Sequence[SUTResponse],
        config: BenchmarkConfig,
    ) -> MetricResult:
        rerank_lat = [r.latency_ms.get("index_rerank", 0.0) for r in responses]
        return MetricResult(
            metric_name=self.name(),
            report=MetricReport(
                metric_name=self.name(),
                global_value=_p95(rerank_lat),
                n_samples=len(responses),
            ),
        )


__all__ = ["TokenEfficiencyMetric", "LatencyP95Metric", "LatencyP95RerankMetric"]
