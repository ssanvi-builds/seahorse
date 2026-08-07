"""Tests for the reporters (f5-16 §5.5/§6.4)."""

from __future__ import annotations

import json

from seahorse.benchmark.config import BenchmarkConfig
from seahorse.benchmark.contracts import MetricReport, MetricResult, SUTResponse
from seahorse.benchmark.reporters.ci_gate import CIGate
from seahorse.benchmark.reporters.json_reporter import JsonReporter
from seahorse.benchmark.reporters.manifest import (
    ExecutionMetadata,
    PinningFingerprint,
    RunManifest,
    write_manifest,
)
from seahorse.benchmark.reporters.markdown_reporter import MarkdownReporter


def _fingerprint() -> PinningFingerprint:
    return PinningFingerprint(
        config_hash="c" * 64,
        dataset_hash="d" * 64,
        loader_code_sha256="l" * 64,
        embedding_identity="me5-small:384:abc:int8",
        embedding_batch_config="batch_size=1_forced",
        knn_completeness=1.0,
        reader_model_used="ollama/qwen3:1.7b@sha256:abc",
        judge_model_used="ollama/qwen2.5:7b@sha256:def",
        seahorse_version="git1",
        skeleton_version="git2",
        reader_system_prompt_sha256="p" * 64,
        judge_rubric_hashes={"multi-session": "r" * 64},
        ingest_template_sha256="i" * 64,
        sut_name="seahorse",
        sut_version="0.1.0",
        temporal_mode=False,
        score_source="mvp1_rrf",
        reproducibility_class="local_near_deterministic",
        expected_match_rate=0.956,
        judge_validation_status="unvalidated_with_small_model",
    )


def _manifest() -> RunManifest:
    return RunManifest(
        fingerprint=_fingerprint(),
        execution=ExecutionMetadata(started_at="2026-08-07T00:00:00Z"),
        metrics={
            "recall@10": MetricReport(
                metric_name="recall@10", global_value=0.5, n_samples=5
            )
        },
    )


def test_fingerprint_run_id_is_deterministic():
    a = _fingerprint().run_id
    b = _fingerprint().run_id
    assert a == b
    assert len(a) == 16


def test_manifest_round_trip(tmp_path):
    manifest = _manifest()
    path = tmp_path / "manifest.json"
    write_manifest(manifest, path)
    loaded = json.loads(path.read_text("utf-8"))
    assert loaded["fingerprint"]["run_id"] == manifest.fingerprint.run_id
    assert loaded["metrics"]["recall@10"]["global_value"] == 0.5


def test_json_reporter_writes_artifacts(tmp_path, synthetic_dataset):
    reporter = JsonReporter(tmp_path)
    manifest = _manifest()
    responses = [SUTResponse(answer="A", retrieved_ep_ids=(), retrieved_fact_ids=())]
    results = [
        MetricResult(
            metric_name="recall@10",
            report=MetricReport(metric_name="recall@10", global_value=0.5, n_samples=5),
        )
    ]
    reporter.render(synthetic_dataset, responses, results, manifest, BenchmarkConfig())
    assert (tmp_path / "manifest.json").exists()
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "samples.jsonl").exists()
    samples = (tmp_path / "samples.jsonl").read_text("utf-8").strip().splitlines()
    assert len(samples) == len(synthetic_dataset.instances)


def test_markdown_reporter_writes_report(tmp_path, synthetic_dataset):
    reporter = MarkdownReporter(tmp_path)
    manifest = _manifest()
    results = [
        MetricResult(
            metric_name="recall@10",
            report=MetricReport(metric_name="recall@10", global_value=0.5, n_samples=5),
        )
    ]
    reporter.render(synthetic_dataset, [], results, manifest, BenchmarkConfig())
    report = (tmp_path / "report.md").read_text("utf-8")
    assert "Seahorse Benchmark Report" in report
    assert "recall@10" in report


def test_ci_gate_pass():
    gate = CIGate(thresholds={"recall@10": 0.4})
    results = [
        MetricResult(
            metric_name="recall@10",
            report=MetricReport(metric_name="recall@10", global_value=0.5, n_samples=5),
        )
    ]
    assert gate.evaluate(results) == 0


def test_ci_gate_fail():
    gate = CIGate(thresholds={"recall@10": 0.6})
    results = [
        MetricResult(
            metric_name="recall@10",
            report=MetricReport(metric_name="recall@10", global_value=0.5, n_samples=5),
        )
    ]
    assert gate.evaluate(results) == 10


def test_ci_gate_tamper_detection(tmp_path):
    manifest = _manifest()
    path = tmp_path / "manifest.json"
    write_manifest(manifest, path)
    gate = CIGate()
    assert gate.verify_tamper(manifest, path) == 0
    # Tamper: modify the file
    path.write_text(path.read_text("utf-8").replace("0.5", "0.9"), encoding="utf-8")
    assert gate.verify_tamper(manifest, path) == 3
