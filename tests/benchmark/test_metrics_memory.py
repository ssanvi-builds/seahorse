"""Tests for the bi-temporal memory metrics."""

from __future__ import annotations

from seahorse.benchmark.config import BenchmarkConfig
from seahorse.benchmark.contracts import BenchmarkInstance, SUTResponse
from seahorse.benchmark.metrics.memory import FAMAGapMetric, KnowledgeUpdateAccuracyMetric


def _inst(instance_id, capabilities, *, metadata=None):
    return BenchmarkInstance(
        instance_id=instance_id,
        question="Q?",
        golden_answer="A",
        golden_session_ids=(),
        golden_evidence=(),
        question_type="knowledge-update",
        capabilities=tuple(capabilities),
        cognitive_category="semantic",
        question_date=None,
        haystack=(),
        metadata=metadata or {},
    )


def _resp(ep_ids, invalidated=()):
    return SUTResponse(
        answer="A",
        retrieved_ep_ids=tuple(ep_ids),
        retrieved_fact_ids=(),
        sut_metadata={"invalidated_ep_ids": list(invalidated)},
    )


def test_fama_gap_zero_by_design():
    """Sanity check: recall filters invalidated by construction → gap=0."""
    inst = _inst("q1", ["knowledge-update"])
    resp = _resp(["e1"])
    result = FAMAGapMetric().compute([inst], [resp], BenchmarkConfig())
    assert result.report.global_value == 0.0
    assert result.report.details["caveat"] == "sanity_check_no_baseline_mvp1"


def test_fama_gap_reports_invalidated_if_present():
    inst = _inst("q1", ["knowledge-update"])
    resp = _resp(["e1"], invalidated=["e1"])
    result = FAMAGapMetric().compute([inst], [resp], BenchmarkConfig())
    assert result.report.global_value == 1.0


def test_knowledge_update_accuracy_new_version_in_top_k():
    inst = _inst("q1", ["knowledge-update"], metadata={"new_ep_ids_after_improve": ["e2"]})
    resp = _resp(["e2", "e1"])
    result = KnowledgeUpdateAccuracyMetric().compute([inst], [resp], BenchmarkConfig())
    assert result.report.global_value == 1.0
    assert result.report.n_samples == 1


def test_knowledge_update_accuracy_old_version_only():
    inst = _inst("q1", ["knowledge-update"], metadata={"new_ep_ids_after_improve": ["e2"]})
    resp = _resp(["e1"])
    result = KnowledgeUpdateAccuracyMetric().compute([inst], [resp], BenchmarkConfig())
    assert result.report.global_value == 0.0


def test_knowledge_update_accuracy_skips_non_ku():
    inst = _inst("q1", ["information-extraction"])
    resp = _resp(["e1"])
    result = KnowledgeUpdateAccuracyMetric().compute([inst], [resp], BenchmarkConfig())
    assert result.report.n_samples == 0
    assert result.report.global_value == 0.0
