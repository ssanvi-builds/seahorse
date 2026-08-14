"""Tests for the retrieval metrics."""

from __future__ import annotations

from seahorse.benchmark.config import BenchmarkConfig
from seahorse.benchmark.contracts import BenchmarkInstance, SUTResponse
from seahorse.benchmark.metrics.retrieval import MRR, NDCGAtK, PrecisionAtK, RecallAtK


def _inst(instance_id, golden, *, qtype="multi-session", cog="semantic", abstention=False):
    return BenchmarkInstance(
        instance_id=instance_id,
        question="Q?",
        golden_answer="A",
        golden_session_ids=tuple(golden),
        golden_evidence=(),
        question_type=qtype,
        capabilities=(),
        cognitive_category=cog,
        question_date=None,
        haystack=(),
        abstention=abstention,
    )


def _resp(sessions):
    return SUTResponse(
        answer="A",
        retrieved_ep_ids=tuple(f"e{i}" for i in range(len(sessions))),
        retrieved_fact_ids=tuple(f"f{i}" for i in range(len(sessions))),
        retrieved_session_ids=tuple(sessions),
    )


def test_recall_at_k_perfect():
    inst = _inst("q1", ["s1", "s2"])
    resp = _resp(["s1", "s2", "s3"])
    result = RecallAtK().compute([inst], [resp], BenchmarkConfig())
    assert result.report.global_value == 1.0
    assert result.report.n_samples == 1


def test_recall_at_k_partial():
    inst = _inst("q1", ["s1", "s2"])
    resp = _resp(["s1", "s3"])
    result = RecallAtK().compute([inst], [resp], BenchmarkConfig())
    assert result.report.global_value == 0.5


def test_recall_at_k_excludes_abstention():
    inst = _inst("q1", ["s1"], abstention=True)
    resp = _resp(["s1"])
    result = RecallAtK().compute([inst], [resp], BenchmarkConfig())
    assert result.report.n_samples == 0
    assert result.report.global_value == 0.0


def test_recall_at_k_slices_by_question_type():
    a = _inst("q1", ["s1"], qtype="multi-session")
    b = _inst("q2", ["s2"], qtype="knowledge-update")
    result = RecallAtK().compute([a, b], [_resp(["s1"]), _resp(["s9"])], BenchmarkConfig())
    assert result.report.by_slice["multi-session"] == 1.0
    assert result.report.by_slice["knowledge-update"] == 0.0
    assert result.report.details["by_cognitive_category"]["semantic"] == 0.5


def test_ndcg_at_k_binary_relevance():
    # golden = {s1, s2}; recovered = [s1, s3, s2]
    # DCG = 1/log2(2) + 0 + 1/log2(4) = 1 + 0.5 = 1.5
    # IDCG = 1/log2(2) + 1/log2(3) = 1 + 0.6309 = 1.6309
    inst = _inst("q1", ["s1", "s2"])
    resp = _resp(["s1", "s3", "s2"])
    result = NDCGAtK().compute([inst], [resp], BenchmarkConfig())
    expected = 1.5 / (1 + 1 / 1.58496)
    assert abs(result.report.global_value - expected) < 1e-6


def test_ndcg_at_k_dedupes_recovered_sessions():
    """A golden session appearing multiple times in the top-k (multiple episodes
    from the same session) must be counted ONCE — otherwise DCG overcounts and
    nDCG exceeds 1.0 (surfaced by the LMEB-S run: ndcg@10 = 1.136)."""
    inst = _inst("q1", ["s1"])
    resp = _resp(["s1", "s1", "s1", "s1", "s1", "s1", "s1", "s1", "s1", "s1"])
    result = NDCGAtK().compute([inst], [resp], BenchmarkConfig())
    assert abs(result.report.global_value - 1.0) < 1e-9


def test_ndcg_at_k_perfect_is_one():
    inst = _inst("q1", ["s1", "s2"])
    resp = _resp(["s1", "s2"])
    result = NDCGAtK().compute([inst], [resp], BenchmarkConfig())
    assert abs(result.report.global_value - 1.0) < 1e-9


def test_mrr_first_relevant_rank():
    inst = _inst("q1", ["s2"])
    resp = _resp(["s1", "s2", "s3"])
    result = MRR().compute([inst], [resp], BenchmarkConfig())
    assert abs(result.report.global_value - 0.5) < 1e-9  # rank 2 → 1/2


def test_mrr_zero_when_none_recovered():
    inst = _inst("q1", ["s9"])
    resp = _resp(["s1", "s2"])
    result = MRR().compute([inst], [resp], BenchmarkConfig())
    assert result.report.global_value == 0.0


def test_precision_at_k_uses_effective_k():
    # k=10 but only 2 recovered → effective k=2; 1 relevant → precision 0.5
    inst = _inst("q1", ["s1"])
    resp = _resp(["s1", "s3"])
    result = PrecisionAtK().compute([inst], [resp], BenchmarkConfig())
    assert result.report.global_value == 0.5
    assert result.report.details["k_effectivo_mean"] == 2.0


def test_precision_at_k_zero_when_empty():
    inst = _inst("q1", ["s1"])
    resp = _resp([])
    result = PrecisionAtK().compute([inst], [resp], BenchmarkConfig())
    assert result.report.global_value == 0.0
