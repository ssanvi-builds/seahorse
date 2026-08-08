"""Tests for the efficiency metrics (f5-16 §4.4 F4 — REAL tokens)."""

from __future__ import annotations

from seahorse.benchmark.config import BenchmarkConfig
from seahorse.benchmark.contracts import BenchmarkInstance, SUTResponse
from seahorse.benchmark.metrics.efficiency import (
    LatencyP95Metric,
    LatencyP95RerankMetric,
    TokenEfficiencyMetric,
)
from tests.benchmark.conftest import FakeTokenizer


def _inst_with_haystack(body: str) -> BenchmarkInstance:
    return BenchmarkInstance(
        instance_id="q1",
        question="Q?",
        golden_answer="A",
        golden_session_ids=(),
        golden_evidence=(),
        question_type="single-session-user",
        capabilities=(),
        cognitive_category="episodic",
        question_date=None,
        haystack=({"session_id": "s1", "date": None, "turns": [{"body": body}]},),
    )


def _resp(tokens: int) -> SUTResponse:
    return SUTResponse(
        answer="A",
        retrieved_ep_ids=(),
        retrieved_fact_ids=(),
        tokens_consumed_measured=tokens,
        latency_ms={"index": 10.0},
    )


def test_token_efficiency_savings():
    # baseline = tokenize the full haystack (1 token per 4 chars)
    inst = _inst_with_haystack("x" * 400)  # 100 tokens baseline
    resp = _resp(10)  # 10 tokens measured
    result = TokenEfficiencyMetric(FakeTokenizer()).compute([inst], [resp], BenchmarkConfig())
    assert abs(result.report.global_value - 0.9) < 1e-9  # (100-10)/100
    assert result.report.details["tokens_measured"] is True
    assert result.report.details["baseline_tokens"] == 100


def test_token_efficiency_zero_baseline():
    inst = _inst_with_haystack("")
    resp = _resp(0)
    result = TokenEfficiencyMetric(FakeTokenizer()).compute([inst], [resp], BenchmarkConfig())
    assert result.report.global_value == 0.0


def test_latency_p95_index_from_responses():
    inst = _inst_with_haystack("x")
    responses = [
        _resp(0),
        SUTResponse(
            answer="A", retrieved_ep_ids=(), retrieved_fact_ids=(), latency_ms={"index": 5.0}
        ),
        SUTResponse(
            answer="A", retrieved_ep_ids=(), retrieved_fact_ids=(), latency_ms={"index": 20.0}
        ),
        SUTResponse(
            answer="A", retrieved_ep_ids=(), retrieved_fact_ids=(), latency_ms={"index": 30.0}
        ),
    ]
    result = LatencyP95Metric().compute([inst], responses, BenchmarkConfig())
    assert result.report.by_slice["index"] == 30.0  # nearest-rank p95 of [5,10,20,30]


def test_latency_p95_merges_probe_results():
    inst = _inst_with_haystack("x")
    resp = _resp(0)
    probe = {"p95_timeline_ms": 42.7, "p95_full_ms": 18.3}
    result = LatencyP95Metric(probe).compute([inst], [resp], BenchmarkConfig())
    assert result.report.by_slice["timeline"] == 42.7
    assert result.report.by_slice["full"] == 18.3


def test_latency_p95_rerank_reads_index_rerank_key():
    """F2 (f7 §5b): the rerank-path INDEX p95 comes from latency_ms["index_rerank"]
    (set by the SUT only when rerank_enabled)."""
    inst = _inst_with_haystack("x")
    responses = [
        SUTResponse(
            answer="A", retrieved_ep_ids=(), retrieved_fact_ids=(),
            latency_ms={"index": 10.0, "index_rerank": 300.0},
        ),
        SUTResponse(
            answer="A", retrieved_ep_ids=(), retrieved_fact_ids=(),
            latency_ms={"index": 20.0, "index_rerank": 450.0},
        ),
    ]
    result = LatencyP95RerankMetric().compute([inst], responses, BenchmarkConfig())
    assert result.report.global_value == 450.0  # nearest-rank p95 of [300, 450]


def test_latency_p95_rerank_zero_when_absent():
    """Baseline variants (rerank OFF) have no index_rerank key → 0.0."""
    inst = _inst_with_haystack("x")
    resp = _resp(0)  # latency_ms = {"index": 10.0} only
    result = LatencyP95RerankMetric().compute([inst], [resp], BenchmarkConfig())
    assert result.report.global_value == 0.0
