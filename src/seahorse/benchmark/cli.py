"""``seahorse benchmark`` — run/list/adapters entrypoints (f5-16 §2.1).

The CLI builds a fresh temp SQLite DB per run (reproducible), wires the
``SeahorseSUT`` over #12, runs the ``EvaluationRunner``, and returns the CI
exit code (0=Pass / 10=Fail / 3=Tampered).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from seahorse.benchmark.adapters.registry import AdapterRegistry
from seahorse.benchmark.config import BenchmarkConfig
from seahorse.benchmark.contracts import MetricResult
from seahorse.benchmark.harness.reader_llm import ReaderLLMClient
from seahorse.benchmark.harness.tokenizer import Tokenizer
from seahorse.benchmark.metrics.efficiency import LatencyP95Metric, TokenEfficiencyMetric
from seahorse.benchmark.metrics.memory import FAMAGapMetric, KnowledgeUpdateAccuracyMetric
from seahorse.benchmark.metrics.registry import MetricRegistry
from seahorse.benchmark.metrics.retrieval import MRR, NDCGAtK, PrecisionAtK, RecallAtK
from seahorse.benchmark.reporters.ci_gate import CIGate
from seahorse.benchmark.reporters.json_reporter import JsonReporter
from seahorse.benchmark.reporters.markdown_reporter import MarkdownReporter
from seahorse.benchmark.runner import EvaluationRunner
from seahorse.benchmark.sut.seahorse_sut import SeahorseSUT
from seahorse.facade import build_facade
from seahorse.retrieval.recency import RecencyConfig


def _make_registry(tokenizer: Tokenizer) -> MetricRegistry:
    reg = MetricRegistry()
    reg.register(RecallAtK())
    reg.register(NDCGAtK())
    reg.register(MRR())
    reg.register(PrecisionAtK())
    reg.register(FAMAGapMetric())
    reg.register(KnowledgeUpdateAccuracyMetric())
    reg.register(TokenEfficiencyMetric(tokenizer))
    reg.register(LatencyP95Metric())
    return reg


def _recency_config(gamma: float | None, half_life_days: float | None) -> dict | None:
    """Translate the ``--recency-*`` CLI flags to a ``RecencyConfig`` dict.

    Both flags are required together (fail-loud, no silent half-configuration):
    the F1 recency experiment sweeps γ × half_life as pairs (f7-experimental-design
    §5(a)). Returns None (pure RRF) when neither flag is set.
    """
    if gamma is None and half_life_days is None:
        return None
    if gamma is None or half_life_days is None:
        raise ValueError(
            "--recency-gamma and --recency-half-life must be set together"
        )
    return {"gamma": gamma, "half_life_days": half_life_days}


def run_benchmark(
    *,
    adapter: str = "lmeb",
    dataset_config: str = "s",
    reader_model: str = "ollama/qwen3:1.7b",
    judge_model: str = "ollama/qwen2.5:7b",
    temporal: bool = False,
    output_dir: str = "benchmark-output",
    top_k: int = 10,
    score_source: str = "mvp1_rrf",
    recency_gamma: float | None = None,
    recency_half_life: float | None = None,
    embed_mode: str = "body",
    thresholds: dict[str, float] | None = None,
    reader_llm=None,
) -> int:
    """Run the benchmark and return the CI exit code (0/10/3)."""
    recency_config = _recency_config(recency_gamma, recency_half_life)
    config = BenchmarkConfig(
        adapter=adapter,
        dataset_config=dataset_config,
        reader_model=reader_model,
        judge_model=judge_model,
        temporal_mode=temporal,
        output_dir=output_dir,
        top_k=top_k,
        score_source=score_source,  # type: ignore[arg-type]
        recency_config=recency_config,
        embed_mode=embed_mode,
    )
    config.validate()
    loader = AdapterRegistry.get(adapter)
    tokenizer = Tokenizer()
    reader = reader_llm or ReaderLLMClient(reader_model)
    tmp = tempfile.mkdtemp(prefix="seahorse-bench-")

    def sut_factory() -> SeahorseSUT:
        # F7 enabler (a)/(c): the composition-root swap is the ONLY wiring — the
        # SUT knows nothing about RecencyConfig/embed_mode internals (delegation
        # purity, f5-16 §2.4).
        recency = (
            RecencyConfig(**config.recency_config)
            if config.recency_config is not None
            else None
        )
        facade, storage = build_facade(
            Path(tmp) / "bench.db", recency=recency, embed_mode=config.embed_mode
        )
        return SeahorseSUT(
            facade,
            lambda: build_facade(
                Path(tmp) / "bench2.db", recency=recency, embed_mode=config.embed_mode
            )[0],
            reader_llm=reader,
            tokenizer=tokenizer,
            fact_id_to_session={},
            temporal_mode=temporal,
            top_k=top_k,
            score_source=score_source,
            recency_config=config.recency_config,
            embed_mode=config.embed_mode,
        )

    runner = EvaluationRunner(
        config,
        loader=loader,
        sut_factory=sut_factory,
        metric_registry=_make_registry(tokenizer),
        reporters=[JsonReporter(output_dir), MarkdownReporter(output_dir)],
        tokenizer=tokenizer,
    )
    manifest = runner.run()
    gate = CIGate(thresholds=thresholds or {})
    results = [
        MetricResult(metric_name=name, report=report)
        for name, report in manifest.metrics.items()
    ]
    tamper = gate.verify_tamper(manifest, Path(output_dir) / "manifest.json")
    if tamper != CIGate.EXIT_PASS:
        return tamper
    return gate.evaluate(results)


def list_benchmarks() -> list[str]:
    """Available dataset adapters (e.g. ['lmeb'])."""
    return AdapterRegistry.list()


def list_adapters() -> list[str]:
    """Available SUT adapters (MVP-1: seahorse only)."""
    return ["seahorse"]


__all__ = ["run_benchmark", "list_benchmarks", "list_adapters"]
