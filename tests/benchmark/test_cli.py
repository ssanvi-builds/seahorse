"""Tests for the benchmark CLI (f5-16 §2.1)."""

from __future__ import annotations

from seahorse.benchmark.adapters.registry import AdapterRegistry
from seahorse.benchmark.cli import list_adapters, list_benchmarks, run_benchmark
from tests.benchmark.conftest import FakeReaderLLM, make_synthetic_dataset


@AdapterRegistry.register("fake-cli")
class _FakeLoader:
    @staticmethod
    def load(config):
        return make_synthetic_dataset()

    @staticmethod
    def name() -> str:
        return "fake-cli"

    @staticmethod
    def available_configs() -> tuple[str, ...]:
        return ("s",)


def test_list_benchmarks_includes_lmeb():
    assert "lmeb" in list_benchmarks()


def test_list_adapters_includes_seahorse():
    assert "seahorse" in list_adapters()


def test_run_benchmark_on_synthetic(tmp_path):
    code = run_benchmark(
        adapter="fake-cli",
        output_dir=str(tmp_path),
        reader_model="fake-reader",
        judge_model="fake-judge",
        reader_llm=FakeReaderLLM(),
    )
    assert code == 0  # pass (no thresholds)
    assert (tmp_path / "manifest.json").exists()
    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "samples.jsonl").exists()


def test_run_benchmark_fails_below_threshold(tmp_path):
    code = run_benchmark(
        adapter="fake-cli",
        output_dir=str(tmp_path),
        reader_model="fake-reader",
        judge_model="fake-judge",
        reader_llm=FakeReaderLLM(),
        thresholds={"recall@10": 0.99},  # unreachable → fail
    )
    assert code == 10
