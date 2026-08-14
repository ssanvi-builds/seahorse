"""Tests for ``BenchmarkConfig`` (judge self-preference gate)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from seahorse.benchmark.config import BenchmarkConfig


def test_default_config_validates():
    cfg = BenchmarkConfig()
    cfg.validate()  # reader != judge by default


def test_validate_rejects_reader_equals_judge():
    """The load-bearing gate: generator != judge."""
    cfg = BenchmarkConfig(reader_model="ollama/qwen3:1.7b", judge_model="ollama/qwen3:1.7b")
    with pytest.raises(ValueError, match="different families"):
        cfg.validate()


def test_validate_rejects_non_positive_top_k():
    with pytest.raises(ValueError, match="top_k"):
        BenchmarkConfig(top_k=0).validate()


def test_validate_rejects_unknown_score_source():
    with pytest.raises(ValueError, match="score_source"):
        BenchmarkConfig(score_source="bogus").validate()  # type: ignore[arg-type]


def test_validate_rejects_unknown_embed_mode():
    with pytest.raises(ValueError, match="embed_mode"):
        BenchmarkConfig(embed_mode="summary-only").validate()  # type: ignore[arg-type]


def test_config_is_frozen():
    cfg = BenchmarkConfig()
    with pytest.raises(FrozenInstanceError):
        cfg.top_k = 5  # type: ignore[misc]


def test_config_hash_is_deterministic():
    a = BenchmarkConfig().config_hash()
    b = BenchmarkConfig().config_hash()
    assert a == b
    assert len(a) == 64  # SHA-256 hex


def test_config_hash_changes_with_parameters():
    base = BenchmarkConfig().config_hash()
    changed = BenchmarkConfig(top_k=5).config_hash()
    assert base != changed


def test_experiment_variants_are_configurable():
    """The harness must support the experiment variants."""
    for score_source in ("mvp1_rrf", "mvp1_rrf_recency", "rrf_rerank"):
        cfg = BenchmarkConfig(score_source=score_source)  # type: ignore[arg-type]
        cfg.validate()
    cfg = BenchmarkConfig(
        recency_config={"gamma": 0.5, "half_life_days": 30},
        rerank_enabled=True,
        rerank_model="hooman650/bge-reranker-v2-m3-onnx-o4",
    )
    cfg.validate()
    assert cfg.recency_config == {"gamma": 0.5, "half_life_days": 30}
    assert cfg.rerank_model == "hooman650/bge-reranker-v2-m3-onnx-o4"


def test_rerank_enabled_requires_pinned_model():
    """rerank_enabled without a pinned rerank_model is rejected — the
    cross-encoder identity goes in the fingerprint."""
    with pytest.raises(ValueError, match="rerank_model"):
        BenchmarkConfig(rerank_enabled=True).validate()


def test_rerank_model_changes_config_hash():
    """The reranker identity is part of the fingerprint — the rerank run_id
    differs from the baseline."""
    base = BenchmarkConfig().config_hash()
    rerank = BenchmarkConfig(
        rerank_enabled=True, rerank_model="hooman650/bge-reranker-v2-m3-onnx-o4"
    ).config_hash()
    assert base != rerank
