"""Tests for the benchmark CLI."""

from __future__ import annotations

import pytest

from seahorse.benchmark.adapters.registry import AdapterRegistry
from seahorse.benchmark.cli import (
    _decay_config,
    _recency_config,
    list_adapters,
    list_benchmarks,
    run_benchmark,
)
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


# ---------------------------------------------------------------- recency flags

def test_recency_config_none_when_no_flags():
    assert _recency_config(None, None) is None


def test_recency_config_from_flags():
    assert _recency_config(0.5, 30.0) == {"gamma": 0.5, "half_life_days": 30.0}


def test_recency_config_partial_flags_rejected():
    with pytest.raises(ValueError, match="together"):
        _recency_config(0.5, None)
    with pytest.raises(ValueError, match="together"):
        _recency_config(None, 30.0)


def test_run_benchmark_wires_recency_to_facade(tmp_path, monkeypatch):
    """The composition root receives a real ``RecencyConfig``."""
    import seahorse.benchmark.cli as bcli

    captured: dict = {}
    real = bcli.build_facade

    def spy(db_path, **kwargs):
        captured.update(kwargs)
        return real(db_path, **kwargs)

    monkeypatch.setattr(bcli, "build_facade", spy)
    code = run_benchmark(
        adapter="fake-cli",
        output_dir=str(tmp_path),
        reader_model="fake-reader",
        judge_model="fake-judge",
        reader_llm=FakeReaderLLM(),
        score_source="mvp1_rrf_recency",
        recency_gamma=0.5,
        recency_half_life=30.0,
    )
    assert code == 0
    recency = captured["recency"]
    assert recency is not None
    assert recency.gamma == pytest.approx(0.5)
    assert recency.half_life_days == pytest.approx(30.0)


def test_run_benchmark_default_recency_off(tmp_path, monkeypatch):
    """Without the flags, ``build_facade`` gets ``recency=None`` (pure RRF)."""
    import seahorse.benchmark.cli as bcli

    captured: dict = {}
    real = bcli.build_facade

    def spy(db_path, **kwargs):
        captured.update(kwargs)
        return real(db_path, **kwargs)

    monkeypatch.setattr(bcli, "build_facade", spy)
    run_benchmark(
        adapter="fake-cli",
        output_dir=str(tmp_path),
        reader_model="fake-reader",
        judge_model="fake-judge",
        reader_llm=FakeReaderLLM(),
    )
    assert captured["recency"] is None


# ---------------------------------------------------------------- decay flags

def test_decay_config_none_when_flag_unset():
    assert _decay_config(None) is None


def test_decay_config_from_flag():
    assert _decay_config(347.0) == {"default_half_life_days": 347.0}


def test_run_benchmark_wires_decay_to_facade(tmp_path, monkeypatch):
    """The composition root receives a real ``DecayConfig``."""
    import seahorse.benchmark.cli as bcli

    captured: dict = {}
    real = bcli.build_facade

    def spy(db_path, **kwargs):
        captured.update(kwargs)
        return real(db_path, **kwargs)

    monkeypatch.setattr(bcli, "build_facade", spy)
    code = run_benchmark(
        adapter="fake-cli",
        output_dir=str(tmp_path),
        reader_model="fake-reader",
        judge_model="fake-judge",
        reader_llm=FakeReaderLLM(),
        score_source="mvp1_decay",
        decay_half_life=200.0,
    )
    assert code == 0
    decay = captured["decay"]
    assert decay is not None
    assert decay.default_half_life_days == pytest.approx(200.0)


def test_run_benchmark_default_decay_off(tmp_path, monkeypatch):
    """Without the flag, ``build_facade`` gets ``decay=None`` (pure RRF)."""
    import seahorse.benchmark.cli as bcli

    captured: dict = {}
    real = bcli.build_facade

    def spy(db_path, **kwargs):
        captured.update(kwargs)
        return real(db_path, **kwargs)

    monkeypatch.setattr(bcli, "build_facade", spy)
    run_benchmark(
        adapter="fake-cli",
        output_dir=str(tmp_path),
        reader_model="fake-reader",
        judge_model="fake-judge",
        reader_llm=FakeReaderLLM(),
    )
    assert captured["decay"] is None


def test_run_benchmark_recency_variant_reported(tmp_path):
    """The manifest fingerprint carries the experiment variant.

    ``score_source`` is ``mvp1_rrf_recency`` and the ``config_hash`` bakes in
    the recency config (``BenchmarkConfig.recency_config`` is in the canonical
    JSON) — so the run_id differs from the pure-RRF baseline (reproducibility).
    """
    import json

    code = run_benchmark(
        adapter="fake-cli",
        output_dir=str(tmp_path),
        reader_model="fake-reader",
        judge_model="fake-judge",
        reader_llm=FakeReaderLLM(),
        score_source="mvp1_rrf_recency",
        recency_gamma=0.7,
        recency_half_life=21.0,
    )
    assert code == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["fingerprint"]["score_source"] == "mvp1_rrf_recency"
    assert manifest["fingerprint"]["sut_name"] == "seahorse"


# ---------------------------------------------------------------- embed mode

def test_run_benchmark_wires_embed_mode_to_facade(tmp_path, monkeypatch):
    """The composition root receives ``embed_mode``."""
    import seahorse.benchmark.cli as bcli

    captured: dict = {}
    real = bcli.build_facade

    def spy(db_path, **kwargs):
        captured.update(kwargs)
        return real(db_path, **kwargs)

    monkeypatch.setattr(bcli, "build_facade", spy)
    code = run_benchmark(
        adapter="fake-cli",
        output_dir=str(tmp_path),
        reader_model="fake-reader",
        judge_model="fake-judge",
        reader_llm=FakeReaderLLM(),
        embed_mode="body+summary",
    )
    assert code == 0
    assert captured["embed_mode"] == "body+summary"


def test_run_benchmark_default_embed_mode_body_summary(tmp_path, monkeypatch):
    """The benchmark CLI defaults to the current product default."""
    import seahorse.benchmark.cli as bcli

    captured: dict = {}
    real = bcli.build_facade

    def spy(db_path, **kwargs):
        captured.update(kwargs)
        return real(db_path, **kwargs)

    monkeypatch.setattr(bcli, "build_facade", spy)
    run_benchmark(
        adapter="fake-cli",
        output_dir=str(tmp_path),
        reader_model="fake-reader",
        judge_model="fake-judge",
        reader_llm=FakeReaderLLM(),
    )
    assert captured["embed_mode"] == "body+summary"


def test_run_benchmark_invalid_embed_mode_rejected(tmp_path):
    with pytest.raises(ValueError, match="embed_mode"):
        run_benchmark(
            adapter="fake-cli",
            output_dir=str(tmp_path),
            reader_model="fake-reader",
            judge_model="fake-judge",
            reader_llm=FakeReaderLLM(),
            embed_mode="bogus",
        )
