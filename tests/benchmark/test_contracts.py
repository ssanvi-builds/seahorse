"""Tests for the #16 benchmark contracts (f5-16 §2.2)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from seahorse.benchmark.contracts import (
    BenchmarkInstance,
    DatasetLoader,
    MemorySystemSUT,
    Metric,
    MetricReport,
    MetricResult,
    Reporter,
    SUTResponse,
)


def test_benchmark_instance_is_frozen():
    inst = BenchmarkInstance(
        instance_id="q1",
        question="Q?",
        golden_answer="A",
        golden_session_ids=("s1",),
        golden_evidence=(),
        question_type="single-session-user",
        capabilities=(),
        cognitive_category="episodic",
        question_date=None,
        haystack=(),
    )
    with pytest.raises(FrozenInstanceError):
        inst.question = "changed"  # type: ignore[misc]


def test_benchmark_instance_metadata_is_mutable():
    """metadata is a mutable dict inside the frozen dataclass (runner attaches
    new_ep_ids_after_improve for knowledge_update_accuracy, f5-16 §4.5)."""
    inst = BenchmarkInstance(
        instance_id="q1",
        question="Q?",
        golden_answer="A",
        golden_session_ids=(),
        golden_evidence=(),
        question_type="knowledge-update",
        capabilities=("knowledge-update",),
        cognitive_category="semantic",
        question_date=None,
        haystack=(),
    )
    inst.metadata["new_ep_ids_after_improve"] = ["e1"]
    assert inst.metadata["new_ep_ids_after_improve"] == ["e1"]


def test_sut_response_carries_retrieval_bridge():
    """SUTResponse exposes ep_ids + fact_ids + session_ids (f5-16 §3.7)."""
    resp = SUTResponse(
        answer="A",
        retrieved_ep_ids=("e1",),
        retrieved_fact_ids=("f1",),
        retrieved_session_ids=("s1",),
    )
    assert resp.retrieved_session_ids == ("s1",)
    assert resp.depth_reached == "index"


def test_metric_report_round_trip_shape():
    report = MetricReport(
        metric_name="recall@10",
        global_value=0.5,
        by_slice={"multi-session": 0.4},
        n_samples=10,
    )
    assert report.metric_name == "recall@10"
    assert report.by_slice["multi-session"] == 0.4


def test_metric_result_wraps_report():
    report = MetricReport(metric_name="mrr", global_value=0.3, n_samples=5)
    result = MetricResult(metric_name="mrr", report=report)
    assert result.report.global_value == 0.3


def test_dataset_has_deterministic_identity(synthetic_dataset):
    assert synthetic_dataset.split_hash == "abc123"
    assert synthetic_dataset.loader_code_sha256 == "def456"
    assert len(synthetic_dataset.instances) == 5


def test_protocols_are_runtime_checkable():
    """The four stable interfaces are @runtime_checkable Protocols."""
    assert hasattr(DatasetLoader, "__protocol_attrs__")
    assert hasattr(MemorySystemSUT, "__protocol_attrs__")
    assert hasattr(Metric, "__protocol_attrs__")
    assert hasattr(Reporter, "__protocol_attrs__")
