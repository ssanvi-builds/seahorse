"""The experiment registry — the single map from experiment id to adapter (C4).

Registration is import-time and fail-loud: a duplicate name, or a name outside
the single-homed ``EXPERIMENTS`` tuple (variants.py), raises ``ValueError``.
``runner.py`` is the composition point that imports every adapter module —
``runner.run_experiment``/``render_experiment_report`` look adapters up here.

Adding an experiment means: a module with the ``run_``/``decide_``/``render_``
trio + one registration here (or one adapter class) + one ``EXPERIMENTS`` entry.
Two small edits, not two 14-branch dispatch switches.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from seahorse.benchmark.experiments.protocol import (
    Experiment,
    ExperimentContext,
    ExperimentReport,
)
from seahorse.benchmark.experiments.variants import EXPERIMENTS

EXPERIMENT_REGISTRY: dict[str, Experiment] = {}


def register(experiment: Experiment) -> None:
    """Register an adapter, fail-loud on duplicates and unknown names."""
    if experiment.name in EXPERIMENT_REGISTRY:
        raise ValueError(f"duplicate experiment registration: {experiment.name!r}")
    if experiment.name not in EXPERIMENTS:
        raise ValueError(f"experiment {experiment.name!r} is not in EXPERIMENTS")
    EXPERIMENT_REGISTRY[experiment.name] = experiment


def get_experiment(name: str) -> Experiment:
    """Look up an adapter by experiment id (the old dispatch-guard message)."""
    try:
        return EXPERIMENT_REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"unknown experiment: {name!r} (expected {EXPERIMENTS!r})"
        ) from None


@dataclass(frozen=True)
class StandaloneExperiment:
    """A standalone experiment adapter — metadata plus lazy module resolution.

    One dataclass replaces the per-experiment dispatch branches: the module's
    run/decide/render functions keep their signatures (the wiring tests call
    them directly); the adapter only knows where they live and what the
    ``ExperimentContext`` contributes as call kwargs. The module import is
    lazy (the benchmark must never pay for the 13 experiment modules it is
    not running).
    """

    name: str
    corpora: tuple[str, ...]
    module: str
    run_attr: str
    decide_attr: str
    render_attr: str
    kwargs_from_ctx: Callable[[ExperimentContext], dict[str, Any]]
    matrix: bool = False

    def run(self, ctx: ExperimentContext) -> Any:
        module = importlib.import_module(self.module)
        return getattr(module, self.run_attr)(**self.kwargs_from_ctx(ctx))

    def decide(self, result: Any) -> dict:
        module = importlib.import_module(self.module)
        return getattr(module, self.decide_attr)(result)

    def render(self, report: ExperimentReport) -> str:
        module = importlib.import_module(self.module)
        return getattr(module, self.render_attr)(report.batch_result, report.decision)


def _corpus_top_k(ctx: ExperimentContext) -> dict[str, Any]:
    return {"corpus": ctx.corpus, "top_k": ctx.top_k}


def _corpus_top_k_subsample(ctx: ExperimentContext) -> dict[str, Any]:
    return {**_corpus_top_k(ctx), "subsample": ctx.subsample}


def _corpus_top_k_subsample_reader(ctx: ExperimentContext) -> dict[str, Any]:
    return {**_corpus_top_k_subsample(ctx), "reader": ctx.reader_llm}


def _end_to_end_kwargs(ctx: ExperimentContext) -> dict[str, Any]:
    from seahorse.benchmark.harness.context import ContextMode

    return {
        **_corpus_top_k_subsample(ctx),
        # The ContextMode cast is the documented pass-through (validated by
        # the assembler downstream) — a bogus mode behaves exactly as before.
        "context_mode": cast(ContextMode, ctx.context_mode),
        "reader": ctx.reader_llm,
    }


def _reader_quality_kwargs(ctx: ExperimentContext) -> dict[str, Any]:
    if ctx.corpus == "synthetic":
        # CI: deterministic doubles (the module defaults) — no Ollama.
        reader_weak = ctx.reader_llm
        reader_strong = ctx.reader_llm
    else:
        # Authoritative: the real reader pair — weak from ``--reader-model``,
        # strong from ``--strong-reader-model``. A caller-injected
        # ``reader_llm`` overrides both (e.g. ``StubReaderLLM`` for
        # retrieval-only).
        from seahorse.benchmark.harness.reader_llm import ReaderLLMClient

        reader_weak = ctx.reader_llm or ReaderLLMClient(ctx.reader_model)
        reader_strong = ctx.reader_llm or ReaderLLMClient(ctx.strong_reader_model)
    return {
        **_corpus_top_k_subsample(ctx),
        "reader_weak": reader_weak,
        "reader_strong": reader_strong,
    }


_LMEB = ("synthetic", "lmeb-s")

_STANDALONE: tuple[StandaloneExperiment, ...] = (
    StandaloneExperiment(
        name="batch",
        # (d) per-turn batching: no EvaluationRunner, no BenchmarkDataset —
        # the real claude-mem episodes (or the synthetic mechanical check)
        # via turn-cluster recall@k.
        corpora=("synthetic", "claude-mem"),
        module="seahorse.benchmark.experiments.batch",
        run_attr="run_batch_experiment",
        decide_attr="decide_batch",
        render_attr="render_batch_report",
        kwargs_from_ctx=_corpus_top_k,
    ),
    StandaloneExperiment(
        # (f) entity-centric recall@k over synthetic entity facts (or LMEB-S).
        name="entity_centric",
        corpora=_LMEB,
        module="seahorse.benchmark.experiments.entity_centric",
        run_attr="run_entity_centric_experiment",
        decide_attr="decide_entity_centric",
        render_attr="render_entity_centric_report",
        kwargs_from_ctx=_corpus_top_k,
    ),
    StandaloneExperiment(
        # (e) 1-hop vs 2-hop recall@k delta over synthetic entity chains.
        name="multi_hop",
        corpora=_LMEB,
        module="seahorse.benchmark.experiments.multi_hop",
        run_attr="run_multi_hop_experiment",
        decide_attr="decide_multi_hop",
        render_attr="render_multi_hop_report",
        kwargs_from_ctx=_corpus_top_k,
    ),
    StandaloneExperiment(
        # (g) FAMA-style / MPA A/B over the S sweep vs knowledge updates.
        name="decay",
        corpora=_LMEB,
        module="seahorse.benchmark.experiments.decay",
        run_attr="run_decay_experiment",
        decide_attr="decide_decay",
        render_attr="render_decay_report",
        kwargs_from_ctx=_corpus_top_k,
    ),
    StandaloneExperiment(
        # (h) procedural recall@k over the synthetic skills corpus.
        name="skills",
        corpora=_LMEB,
        module="seahorse.benchmark.experiments.skills",
        run_attr="run_skills_experiment",
        decide_attr="decide_skills",
        render_attr="render_skills_report",
        kwargs_from_ctx=_corpus_top_k,
    ),
    StandaloneExperiment(
        # (A5) recall@10 per RRF_K value over golden-answer episodes.
        name="rrf_k",
        corpora=_LMEB,
        module="seahorse.benchmark.experiments.rrf_k",
        run_attr="run_rrf_k_experiment",
        decide_attr="decide_rrf_k",
        render_attr="render_rrf_k_report",
        kwargs_from_ctx=_corpus_top_k_subsample,
    ),
    StandaloneExperiment(
        # (A6) recall@10 across baseline / rerank summary / rerank body.
        name="rerank_body",
        corpora=_LMEB,
        module="seahorse.benchmark.experiments.rerank_body",
        run_attr="run_rerank_body_experiment",
        decide_attr="decide_rerank_body",
        render_attr="render_rerank_body_report",
        kwargs_from_ctx=_corpus_top_k_subsample,
    ),
    StandaloneExperiment(
        # (A4) recall@10 ceiling vs the reader's end-to-end accuracy.
        name="end_to_end",
        corpora=_LMEB,
        module="seahorse.benchmark.experiments.end_to_end",
        run_attr="run_end_to_end_experiment",
        decide_attr="decide_end_to_end",
        render_attr="render_end_to_end_report",
        kwargs_from_ctx=_end_to_end_kwargs,
    ),
    StandaloneExperiment(
        # (reader-context A/B) end-to-end accuracy across summary | body |
        # body_bounded assembler modes.
        name="reader_context",
        corpora=_LMEB,
        module="seahorse.benchmark.experiments.reader_context",
        run_attr="run_reader_context_experiment",
        decide_attr="decide_reader_context",
        render_attr="render_reader_context_report",
        kwargs_from_ctx=_corpus_top_k_subsample_reader,
    ),
    StandaloneExperiment(
        # (reader-quality A/B) end-to-end accuracy weak vs strong reader.
        name="reader_quality",
        corpora=_LMEB,
        module="seahorse.benchmark.experiments.reader_quality",
        run_attr="run_reader_quality_experiment",
        decide_attr="decide_reader_quality",
        render_attr="render_reader_quality_report",
        kwargs_from_ctx=_reader_quality_kwargs,
    ),
    StandaloneExperiment(
        # (episode granularity) session- vs episode-level recall comparison.
        name="episode_granularity",
        corpora=_LMEB,
        module="seahorse.benchmark.experiments.episode_granularity",
        run_attr="run_episode_granularity_experiment",
        decide_attr="decide_episode_granularity",
        render_attr="render_episode_granularity_report",
        kwargs_from_ctx=_corpus_top_k_subsample,
    ),
    StandaloneExperiment(
        # (context-assembly) the answer-in-context gap decomposition.
        name="context_assembly",
        corpora=_LMEB,
        module="seahorse.benchmark.experiments.context_assembly",
        run_attr="run_context_assembly_experiment",
        decide_attr="decide_context_assembly",
        render_attr="render_context_assembly_report",
        kwargs_from_ctx=_corpus_top_k_subsample,
    ),
    StandaloneExperiment(
        # (two-stage) within-session hybrid re-rank vs the global top-10.
        name="two_stage_retrieval",
        corpora=_LMEB,
        module="seahorse.benchmark.experiments.two_stage_retrieval",
        run_attr="run_two_stage_experiment",
        decide_attr="decide_two_stage",
        render_attr="render_two_stage_report",
        kwargs_from_ctx=_corpus_top_k_subsample,
    ),
)

for _experiment in _STANDALONE:
    register(_experiment)