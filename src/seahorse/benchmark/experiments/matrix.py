"""The variant-matrix experiment adapters — recency/rerank/embed/decay_rrf (C4).

The matrix experiments run the benchmark harness once per
``ExperimentVariant`` (``variants_for`` decides the sweep) and decide over the
baseline-vs-sweep retrieval metrics. ``run`` is the harness bridge call, the
delegating to the runner's ``_run_variant`` (the EvaluationRunner +
template-cache machinery stays single-homed there); ``decide`` routes to the
module decision (recency compares the whole sweep, the others compare the
baseline against the first sweep variant); ``render`` emits the shared variant
table, the experiment-specific inserts, and the decision block.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from seahorse.benchmark.experiments import decide as decide_mod
from seahorse.benchmark.experiments import variants as variants_mod
from seahorse.benchmark.experiments.decide import ExperimentResult
from seahorse.benchmark.experiments.protocol import ExperimentContext, ExperimentReport
from seahorse.benchmark.experiments.registry import register
from seahorse.benchmark.experiments.variants import CORPORA


class MatrixExperiment:
    """Base of the 4 variant-matrix adapters: harness loop + sweep rendering."""

    name: str
    matrix = True
    corpora = CORPORA

    def run(self, ctx: ExperimentContext) -> tuple[ExperimentResult, ...]:
        from seahorse.benchmark.config import BenchmarkConfig
        from seahorse.benchmark.experiments import runner as runner_mod
        from seahorse.benchmark.harness.reader_llm import ReaderLLMClient
        from seahorse.benchmark.harness.tokenizer import Tokenizer

        base_config = BenchmarkConfig(
            adapter="lmeb" if ctx.corpus == "lmeb-s" else "synthetic",
            dataset_config="s",
            reader_model=ctx.reader_model,
            judge_model=ctx.judge_model,
            temporal_mode=ctx.temporal,
            output_dir=ctx.output_dir,
            top_k=ctx.top_k,
        )
        base_config.validate()
        dataset = runner_mod._load_dataset(ctx.corpus, subsample=ctx.subsample)
        tokenizer = Tokenizer()
        reader = ctx.reader_llm or ReaderLLMClient(ctx.reader_model)
        registry = runner_mod.make_metric_registry(tokenizer)
        variants = variants_mod.variants_for(self.name)
        cache = (
            ctx.template_cache
            if ctx.template_cache is not None
            else ({} if ctx.warm_db else None)
        )

        results: list[ExperimentResult] = []
        with tempfile.TemporaryDirectory(prefix="seahorse-exp-") as tmp:
            for variant in variants:
                results.append(
                    runner_mod._run_variant(
                        base_config=base_config,
                        variant=variant,
                        dataset=dataset,
                        corpus=ctx.corpus,
                        tmp=Path(tmp),
                        output_dir=Path(ctx.output_dir),
                        reader=reader,
                        tokenizer=tokenizer,
                        registry=registry,
                        template_cache=cache,
                        pit_queries=ctx.pit_queries,
                    )
                )
        return tuple(results)

    def decide(self, result: tuple[ExperimentResult, ...]) -> dict:
        raise NotImplementedError

    def render(self, report: ExperimentReport) -> str:
        """The shared sweep table + the experiment inserts + decision block."""
        lines = self._table_lines(report)
        lines.extend(self._extra_lines(report))
        lines.extend(
            [
                "",
                "## Decision",
                f"decision: {report.decision.get('decision')}",
                f"flip: {report.decision.get('flip')}",
                f"reason: {report.decision.get('reason', '')}",
            ]
        )
        return "\n".join(lines)

    def _table_lines(self, report: ExperimentReport) -> list[str]:
        lines = [
            f"# Benchmark experiment: {report.experiment}  (corpus={report.corpus}, "
            f"temporal={report.temporal_mode})",
            "",
            f"{'variant':<28} {'detected':<20} {'recall@10':>9} {'ndcg@10':>8}",
        ]
        for r in report.results:
            recall = (
                r.metric("recall@10").global_value
                if "recall@10" in r.metrics
                else float("nan")
            )
            ndcg = (
                r.metric("ndcg@10").global_value
                if "ndcg@10" in r.metrics
                else float("nan")
            )
            lines.append(
                f"{r.variant.name:<28} {r.detected_score_source:<20} "
                f"{recall:>9.3f} {ndcg:>8.3f}"
            )
        return lines

    def _extra_lines(self, report: ExperimentReport) -> list[str]:
        return []


class RecencyMatrixExperiment(MatrixExperiment):
    name = "recency"

    def decide(self, result: tuple[ExperimentResult, ...]) -> dict:
        return decide_mod.decide_recency(result[0], result[1:])

    def _extra_lines(self, report: ExperimentReport) -> list[str]:
        lines = [
            "",
            "recall@10 by slice (temporal-reasoning / knowledge-update):",
        ]
        for r in report.results:
            by_slice = r.metric("recall@10").by_slice
            tr = by_slice.get("temporal-reasoning", float("nan"))
            ku = by_slice.get("knowledge-update", float("nan"))
            lines.append(f"  {r.variant.name:<28} tr={tr:.3f} ku={ku:.3f}")
        return lines


class RerankMatrixExperiment(MatrixExperiment):
    name = "rerank"

    def decide(self, result: tuple[ExperimentResult, ...]) -> dict:
        return decide_mod.decide_rerank(result[0], result[1])

    def _extra_lines(self, report: ExperimentReport) -> list[str]:
        lines = [
            "",
            "p95_index_rerank_ms (rerank-path INDEX latency):",
        ]
        for r in report.results:
            p95 = r.metric("latency_p95_rerank_ms").global_value
            lines.append(f"  {r.variant.name:<28} p95={p95:.0f}ms")
        return lines


class EmbedMatrixExperiment(MatrixExperiment):
    name = "embed"

    def decide(self, result: tuple[ExperimentResult, ...]) -> dict:
        return decide_mod.decide_embed(result[0], result[1])


class DecayRrfMatrixExperiment(MatrixExperiment):
    name = "decay_rrf"

    def decide(self, result: tuple[ExperimentResult, ...]) -> dict:
        return decide_mod.decide_decay_rrf(result[0], result[1])


for _experiment in (
    RecencyMatrixExperiment(),
    RerankMatrixExperiment(),
    EmbedMatrixExperiment(),
    DecayRrfMatrixExperiment(),
):
    register(_experiment)