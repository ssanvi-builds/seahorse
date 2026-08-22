"""Experiment runner — the variant matrices over the benchmark harness.

An experiment runs the harness once per ``ExperimentVariant`` (the manifest
``score_source`` is the variant), with the decision thresholds applied to the
resulting retrieval metrics. ``run_experiment`` is the entry point; the report
carries the honest per-variant regime detection (``fallback_g2`` → invalid
decision, fail-loud honesty) plus the feature verdicts.

Corpora:
- ``synthetic`` (default, CI): the deterministic canonical corpus + a
  content-hash embedder — verifies the harness MECHANICS without any model or
  download. NOT the science.
- ``lmeb-s``: the real LongMemEval haystack + the auto-resolved fastembed
  backend — the authoritative decision (requires the ``benchmark`` +
  ``embeddings`` extras).

The clock is wired per variant: ``AdvancingClock(base=earliest_session_date,
delta=1 day)`` so ``created_at`` spans the haystack — the recency boost reads
that spread. Deterministic and temporally ordered.
"""

from __future__ import annotations

import copy
import logging
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from seahorse.benchmark.config import BenchmarkConfig
from seahorse.benchmark.contracts import BenchmarkDataset, DatasetLoader
from seahorse.benchmark.corpus_builder import (
    AdvancingClock,
    CorpusBuilder,
    earliest_session_date,
)
from seahorse.benchmark.experiments.decide import (
    ExperimentResult,
    decide_decay_rrf,
    decide_embed,
    decide_recency,
    decide_rerank,
)
from seahorse.benchmark.experiments.synthetic import (
    HashEmbedder,
    HashReranker,
    make_synthetic_dataset,
)
from seahorse.benchmark.experiments.variants import (
    CORPORA,
    EXPERIMENTS,
    ExperimentVariant,
    variants_for,
)
from seahorse.benchmark.harness.context import ContextMode
from seahorse.benchmark.harness.reader_llm import ReaderLLMClient
from seahorse.benchmark.harness.tokenizer import Tokenizer
from seahorse.benchmark.knowledge_update_simulator import KnowledgeUpdateSimulator
from seahorse.benchmark.metrics.efficiency import (
    LatencyP95Metric,
    LatencyP95RerankMetric,
    TokenEfficiencyMetric,
)
from seahorse.benchmark.metrics.memory import (
    FAMAGapMetric,
    KnowledgeUpdateAccuracyMetric,
)
from seahorse.benchmark.metrics.registry import MetricRegistry
from seahorse.benchmark.metrics.retrieval import MRR, NDCGAtK, PrecisionAtK, RecallAtK
from seahorse.benchmark.reporters.json_reporter import JsonReporter
from seahorse.benchmark.reporters.markdown_reporter import MarkdownReporter
from seahorse.benchmark.runner import EvaluationRunner
from seahorse.benchmark.sut.seahorse_sut import SeahorseSUT
from seahorse.facade import build_facade
from seahorse.retrieval.decay import DecayConfig
from seahorse.retrieval.recency import RecencyConfig

# Fallback clock delta (1 day) when the dataset has no usable date spread.
_CLOCK_DELTA_DAYS = 86400.0


def _clock_delta_seconds(dataset: BenchmarkDataset) -> float:
    """Delta per write so ``created_at`` spans the haystack's REAL date range.

    The ``AdvancingClock`` is called once per write. A fixed 1-day delta would
    make ``created_at`` span ``N_writes`` days — 547 years for LMEB's 199K
    turns — so the recency boost's ``age = now - created_at`` would be
    meaningless (every episode ancient → factor ≈ 1). Deriving the delta from
    the deduped session-date spread keeps the boost's age in the corpus's
    actual time range (the real time difference between sessions).
    """
    seen: set[str] = set()
    dates: list[datetime] = []
    n_turns = 0
    for inst in dataset.instances:
        for session in inst.haystack:
            sid = session["session_id"]
            if sid in seen:
                continue
            seen.add(sid)
            if session.get("date") is not None:
                dates.append(session["date"])
            n_turns += len(session.get("turns", []))
    if not dates or n_turns <= 0:
        return _CLOCK_DELTA_DAYS
    span = (max(dates) - min(dates)).total_seconds()
    if span <= 0:
        return _CLOCK_DELTA_DAYS
    return span / n_turns


@dataclass(frozen=True)
class ExperimentReport:
    """The full experiment artifact: per-variant results + the verdict.

    ``batch_result`` is set only for the per-turn batching experiment, which is
    a standalone measurement (no per-variant ``EvaluationRunner`` results) —
    the turn-cluster recall@k + the decision.
    """

    experiment: str
    corpus: str
    temporal_mode: bool
    results: tuple[ExperimentResult, ...]
    decision: dict
    batch_result: Any | None = None


@dataclass(frozen=True)
class CorpusTemplate:
    """A corpus ingested ONCE, shared across variants that embed the same text.

    Warm-DB: the recency variants (and the embed ``body`` variant)
    embed IDENTICAL passage text — the recency boost is a query-time post-RRF
    step reading ``created_at``/``now``, so one ingest serves all of them. Each
    variant copies the template DB (fast) and re-attaches the bridge
    (``skip_ingest``). ``clock_base`` is the post-ingest clock position: variant
    clocks seed from it so the recency boost reads the same ``now`` vs
    ``created_at`` spread as a fresh-DB run (bit-identical metrics).
    """

    db_path: Path
    bridge: dict
    clock_base: datetime
    clock_delta: float


def make_metric_registry(tokenizer: Tokenizer) -> MetricRegistry:
    """The standard benchmark metric set (the LLM-free honest floor)."""
    reg = MetricRegistry()
    reg.register(RecallAtK())
    reg.register(NDCGAtK())
    reg.register(MRR())
    reg.register(PrecisionAtK())
    reg.register(FAMAGapMetric())
    reg.register(KnowledgeUpdateAccuracyMetric())
    reg.register(TokenEfficiencyMetric(tokenizer))
    reg.register(LatencyP95Metric())
    # Rerank: the rerank-path INDEX p95 (0.0 for baseline variants — the
    # SUT records latency_ms["index_rerank"] only when rerank_enabled).
    reg.register(LatencyP95RerankMetric())
    return reg


class _StaticLoader:
    """``DatasetLoader`` over a pre-built dataset (no HF re-download per variant)."""

    def __init__(self, dataset: BenchmarkDataset) -> None:
        self._dataset = dataset

    @staticmethod
    def name() -> str:
        return "static"

    @staticmethod
    def available_configs() -> tuple[str, ...]:
        return ("s",)

    def load(self, config: BenchmarkConfig) -> BenchmarkDataset:
        return self._dataset


def _load_dataset(corpus: str, *, subsample: bool = False) -> BenchmarkDataset:
    """Load the corpus once; the runner deep-copies per variant.

    ``subsample`` applies the reproducible balanced 100 (the 2026-08-07
    documented compromise for LMEB-S — the full-corpus ingest hangs on FTS5 and
    would run overnight) and recomputes ``split_hash`` over the SUBSAMPLED
    instances so the fingerprint identifies the subsample (honesty).
    """
    if corpus == "synthetic":
        return make_synthetic_dataset()
    if corpus == "lmeb-s":
        from seahorse.benchmark.adapters.longmemeval import LMEBLoader  # lazy: datasets extra

        dataset = LMEBLoader.load(BenchmarkConfig(dataset_config="s"))
        if subsample:
            from seahorse.benchmark.experiments.subsample import subsample_dataset  # lazy

            return subsample_dataset(dataset)
        return dataset
    raise ValueError(f"unknown corpus: {corpus!r} (expected {CORPORA!r})")


_logger = logging.getLogger(__name__)


def _resolve_pit_queries(experiment: str, pit_queries: bool) -> bool:
    """Force active-now queries for the now-regime ranking experiments.

    The recency/decay seams are gated by ``pit is None`` (ADR-03): a PIT query
    reproduces state-as-of-t with pure RRF and never applies the bias. Running
    ``decay_rrf``/``recency`` with ``pit_queries=True`` would therefore measure
    a forced null (the 2026-08-21 correction) — the SUT PITs every query and
    the seam silently never fires. These experiments must query active-now for
    the bias to be testable.
    """
    if experiment in ("decay_rrf", "recency") and pit_queries:
        _logger.warning(
            "experiment=%s forces pit_queries=False: the recency/decay seams "
            "are gated by `pit is None` (ADR-03); a PIT query would measure a "
            "forced null",
            experiment,
        )
        return False
    return pit_queries


def _recency_config(variant: ExperimentVariant) -> RecencyConfig | None:
    if variant.recency_config is None:
        return None
    return RecencyConfig(**variant.recency_config)


def _decay_config(variant: ExperimentVariant) -> DecayConfig | None:
    if variant.decay_config is None:
        return None
    return DecayConfig(**variant.decay_config)


def _reranker_for(corpus: str):
    """The cross-encoder for a corpus: deterministic stub (synthetic) or the real
    fastembed backend (lmeb-s). Query-time pure — never requires a reindex."""
    if corpus == "synthetic":
        return HashReranker()
    from seahorse.embeddings.rerank_backend import build_fastembed_reranker  # lazy

    return build_fastembed_reranker()


def _rerank_model_name(corpus: str) -> str:
    """The pinned cross-encoder identity for the fingerprint."""
    if corpus == "synthetic":
        return "hash"
    from seahorse.embeddings.rerank_backend import MODEL_NAME  # lazy

    return MODEL_NAME


def _facade_factory(
    corpus: str, clock: Callable[[], Any], variant: ExperimentVariant
) -> Callable[[Path], tuple[Any, Any]]:
    """Composition-root wiring per variant + corpus."""

    def _build(db_path: Path):
        kwargs: dict = {
            "clock": clock,
            "recency": _recency_config(variant),
            "decay": _decay_config(variant),
            "embed_mode": variant.embed_mode,
        }
        if corpus == "synthetic":
            # Deterministic hybrid regime: the content-hash embedder keeps the
            # kNN path REAL without a model download.
            kwargs["retrieval_available"] = True
            kwargs["passage_embedder"] = HashEmbedder()
        if variant.rerank_enabled:
            # Rerank: the composition-root swap is the ONLY wiring — the
            # SUT knows nothing about the cross-encoder internals (delegation
            # purity).
            kwargs["reranker"] = _reranker_for(corpus)
        return build_facade(db_path, **kwargs)

    return _build


def _template_variant(embed_mode: str) -> ExperimentVariant:
    """The baseline variant the corpus template is ingested with (no recency)."""
    return ExperimentVariant(
        name="template",
        score_source="mvp1_rrf",
        embed_mode=embed_mode,
        description="warm-DB corpus template (shared ingest)",
    )


def _build_template(
    *,
    base_config: BenchmarkConfig,
    dataset: BenchmarkDataset,
    corpus: str,
    reader: Any,
    tokenizer: Tokenizer,
    embed_mode: str,
    temporal: bool,
) -> CorpusTemplate:
    """Ingest the corpus ONCE into a template DB + capture the bridge.

    The expensive step is the embedding; variants that embed the same text share
    this template. The template DB is copied per variant (fast filesystem copy)
    and the bridge is re-attached to each variant SUT (``skip_ingest``). The
    ``new_ep_ids_after_improve`` metadata is set on the shared dataset instances
    so the deep-copied variant datasets carry it into the metrics.
    """
    template_dir = Path(tempfile.mkdtemp(prefix="seahorse-template-"))
    clock = AdvancingClock(
        base=earliest_session_date(dataset), delta_seconds=_clock_delta_seconds(dataset)
    )
    build = _facade_factory(corpus, clock, _template_variant(embed_mode))
    facade, storage = build(template_dir / "bench.db")
    sut = SeahorseSUT(
        facade,
        lambda: facade,
        reader_llm=reader,
        tokenizer=tokenizer,
        fact_id_to_session={},
        temporal_mode=temporal,
        top_k=base_config.top_k,
        score_source="mvp1_rrf",
        recency_config=None,
        embed_mode=embed_mode,
    )
    CorpusBuilder(sut).ingest(dataset)
    kus = KnowledgeUpdateSimulator(sut)
    updates = kus.derive_updates(dataset)
    new_ep_ids = kus.apply(sut, updates)
    for inst_id, ep_ids in new_ep_ids.items():
        inst = next(i for i in dataset.instances if i.instance_id == inst_id)
        inst.metadata["new_ep_ids_after_improve"] = ep_ids
    bridge = {
        "ep_id_to_session": dict(sut._ep_id_to_session),
        "fact_id_to_session": dict(sut.fact_id_to_session),
        "fact_key_to_ep_id": dict(sut.fact_key_to_ep_id),
    }
    clock_base = clock.position()
    storage.close()
    return CorpusTemplate(
        db_path=template_dir / "bench.db",
        bridge=bridge,
        clock_base=clock_base,
        clock_delta=_clock_delta_seconds(dataset),
    )


def render_experiment_report(report: ExperimentReport) -> str:
    """Human-readable sweep table + decision block (for the CLI)."""
    if report.experiment == "batch":
        from seahorse.benchmark.experiments.batch import (
            BatchExperimentResult,
            render_batch_report,
        )

        return render_batch_report(
            cast(BatchExperimentResult, report.batch_result), report.decision
        )
    if report.experiment == "entity_centric":
        from seahorse.benchmark.experiments.entity_centric import (
            EntityCentricResult,
            render_entity_centric_report,
        )

        return render_entity_centric_report(
            cast(EntityCentricResult, report.batch_result), report.decision
        )
    if report.experiment == "multi_hop":
        from seahorse.benchmark.experiments.multi_hop import (
            MultiHopExperimentResult,
            render_multi_hop_report,
        )

        return render_multi_hop_report(
            cast(MultiHopExperimentResult, report.batch_result), report.decision
        )
    if report.experiment == "decay":
        from seahorse.benchmark.experiments.decay import (
            DecayExperimentResult,
            render_decay_report,
        )

        return render_decay_report(
            cast(DecayExperimentResult, report.batch_result), report.decision
        )
    if report.experiment == "skills":
        from seahorse.benchmark.experiments.skills import (
            SkillsExperimentResult,
            render_skills_report,
        )

        return render_skills_report(
            cast(SkillsExperimentResult, report.batch_result), report.decision
        )
    if report.experiment == "rrf_k":
        from seahorse.benchmark.experiments.rrf_k import (
            RrfKExperimentResult,
            render_rrf_k_report,
        )

        return render_rrf_k_report(
            cast(RrfKExperimentResult, report.batch_result), report.decision
        )
    if report.experiment == "rerank_body":
        from seahorse.benchmark.experiments.rerank_body import (
            RerankBodyExperimentResult,
            render_rerank_body_report,
        )

        return render_rerank_body_report(
            cast(RerankBodyExperimentResult, report.batch_result), report.decision
        )
    if report.experiment == "end_to_end":
        from seahorse.benchmark.experiments.end_to_end import (
            EndToEndExperimentResult,
            render_end_to_end_report,
        )

        return render_end_to_end_report(
            cast(EndToEndExperimentResult, report.batch_result), report.decision
        )
    lines = [
        f"# Benchmark experiment: {report.experiment}  (corpus={report.corpus}, "
        f"temporal={report.temporal_mode})",
        "",
        f"{'variant':<28} {'detected':<20} {'recall@10':>9} {'ndcg@10':>8}",
    ]
    for r in report.results:
        recall = r.metric("recall@10").global_value if "recall@10" in r.metrics else float("nan")
        ndcg = r.metric("ndcg@10").global_value if "ndcg@10" in r.metrics else float("nan")
        lines.append(
            f"{r.variant.name:<28} {r.detected_score_source:<20} "
            f"{recall:>9.3f} {ndcg:>8.3f}"
        )
    if report.experiment == "recency":
        lines.append("")
        lines.append("recall@10 by slice (temporal-reasoning / knowledge-update):")
        for r in report.results:
            by_slice = r.metric("recall@10").by_slice
            tr = by_slice.get("temporal-reasoning", float("nan"))
            ku = by_slice.get("knowledge-update", float("nan"))
            lines.append(f"  {r.variant.name:<28} tr={tr:.3f} ku={ku:.3f}")
    if report.experiment == "rerank":
        lines.append("")
        lines.append("p95_index_rerank_ms (rerank-path INDEX latency):")
        for r in report.results:
            p95 = r.metric("latency_p95_rerank_ms").global_value
            lines.append(f"  {r.variant.name:<28} p95={p95:.0f}ms")
    lines.append("")
    lines.append("## Decision")
    lines.append(f"decision: {report.decision.get('decision')}")
    lines.append(f"flip: {report.decision.get('flip')}")
    lines.append(f"reason: {report.decision.get('reason', '')}")
    return "\n".join(lines)


def run_experiment(
    *,
    experiment: str,
    corpus: str = "synthetic",
    output_dir: str = "benchmark-output",
    reader_model: str = "ollama/qwen3:1.7b",
    judge_model: str = "ollama/qwen2.5:7b",
    top_k: int = 10,
    temporal: bool = True,
    reader_llm=None,
    warm_db: bool = True,
    template_cache: dict | None = None,
    pit_queries: bool = True,
    subsample: bool = True,
    context_mode: str = "summary",
) -> ExperimentReport:
    """Run a benchmark experiment and return the report with the decision verdict.

    ``reader_llm`` defaults to a real ``ReaderLLMClient`` (LMEB runs); the
    synthetic/CI verification injects a deterministic double.

    Warm-DB: variants that embed the same text share ONE corpus ingest
    (the recency sweep is 10 variants over identical embeddings — a fresh-DB
    run would re-embed ~49M tokens per variant). ``warm_db=True`` (default)
    builds a shared ``CorpusTemplate`` per ``(corpus, embed_mode, temporal,
    dataset_hash)``; ``template_cache`` reuses an external cache across
    ``run_experiment`` calls (the embed experiment's ``body`` variant reuses
    the recency experiment's body template). ``warm_db=False`` runs each
    variant on a fresh DB (the previous fresh-DB behavior, used to verify equivalence).

    Subsample: the authoritative LMEB-S runs default to the reproducible
    balanced 100 (the 2026-08-07 documented compromise — the full-corpus ingest
    hangs on FTS5). ``subsample=False`` opts into the full-corpus overnight run.
    """
    if experiment not in EXPERIMENTS:
        raise ValueError(f"unknown experiment: {experiment!r} (expected {EXPERIMENTS!r})")
    if corpus not in CORPORA:
        raise ValueError(f"unknown corpus: {corpus!r} (expected {CORPORA!r})")

    # ADR-03: the recency/decay seams are gated by `pit is None`. A temporal PIT
    # query would silently disable the seam and measure a forced null (the
    # 2026-08-21 correction). Force active-now queries for these experiments so
    # the bias is actually testable.
    pit_queries = _resolve_pit_queries(experiment, pit_queries)

    if experiment == "batch":
        # (d) per-turn batching is a standalone measurement: no
        # EvaluationRunner, no BenchmarkDataset — the corpus is the real
        # claude-mem episodes (or the synthetic mechanical verification) and the
        # metric is the turn-cluster recall@k. Delegates to the batch module.
        from seahorse.benchmark.experiments.batch import (
            decide_batch,
            run_batch_experiment,
        )

        if corpus not in ("synthetic", "claude-mem"):
            raise ValueError(
                f"batch experiment corpus must be 'synthetic' or 'claude-mem', "
                f"got {corpus!r}"
            )
        batch_result = run_batch_experiment(corpus=corpus, top_k=top_k)
        return ExperimentReport(
            experiment="batch",
            corpus=corpus,
            temporal_mode=temporal,
            results=(),
            decision=decide_batch(batch_result),
            batch_result=batch_result,
        )

    if experiment == "entity_centric":
        # (f) entity-centric recall is a standalone measurement: no
        # EvaluationRunner, no BenchmarkDataset — the corpus is the synthetic
        # entity-centric facts (or the authoritative LMEB-S run, not yet wired)
        # and the metric is the entity recall@k. Delegates to the entity_centric
        # module. The standalone result reuses the ``batch_result`` slot.
        from seahorse.benchmark.experiments.entity_centric import (
            decide_entity_centric,
            run_entity_centric_experiment,
        )

        if corpus not in ("synthetic", "lmeb-s"):
            raise ValueError(
                f"entity_centric experiment corpus must be 'synthetic' or 'lmeb-s', "
                f"got {corpus!r}"
            )
        entity_result = run_entity_centric_experiment(corpus=corpus, top_k=top_k)
        return ExperimentReport(
            experiment="entity_centric",
            corpus=corpus,
            temporal_mode=temporal,
            results=(),
            decision=decide_entity_centric(entity_result),
            batch_result=entity_result,
        )

    if experiment == "multi_hop":
        # (e) multi-hop recall is a standalone measurement: no EvaluationRunner,
        # no BenchmarkDataset — the corpus is the synthetic entity chains (or the
        # authoritative LMEB-S multi-session run, not yet built) and the metric
        # is the 1-hop vs 2-hop recall@k delta. Delegates to the multi_hop
        # module. The standalone result reuses the ``batch_result`` slot.
        from seahorse.benchmark.experiments.multi_hop import (
            decide_multi_hop,
            run_multi_hop_experiment,
        )

        if corpus not in ("synthetic", "lmeb-s"):
            raise ValueError(
                f"multi_hop experiment corpus must be 'synthetic' or 'lmeb-s', "
                f"got {corpus!r}"
            )
        multi_hop_result = run_multi_hop_experiment(corpus=corpus, top_k=top_k)
        return ExperimentReport(
            experiment="multi_hop",
            corpus=corpus,
            temporal_mode=temporal,
            results=(),
            decision=decide_multi_hop(multi_hop_result),
            batch_result=multi_hop_result,
        )

    if experiment == "decay":
        # (g) decay FAMA-style is a standalone measurement: no EvaluationRunner,
        # no BenchmarkDataset — the corpus is the synthetic knowledge-update
        # facts (or the authoritative LMEB-S knowledge-update run, not yet built)
        # and the metric is the FAMA-style / MPA A/B over the S sweep. Delegates
        # to the decay module. The standalone result reuses the ``batch_result``
        # slot.
        from seahorse.benchmark.experiments.decay import (
            decide_decay,
            run_decay_experiment,
        )

        if corpus not in ("synthetic", "lmeb-s"):
            raise ValueError(
                f"decay experiment corpus must be 'synthetic' or 'lmeb-s', "
                f"got {corpus!r}"
            )
        decay_result = run_decay_experiment(corpus=corpus, top_k=top_k)
        return ExperimentReport(
            experiment="decay",
            corpus=corpus,
            temporal_mode=temporal,
            results=(),
            decision=decide_decay(decay_result),
            batch_result=decay_result,
        )

    if experiment == "skills":
        # (h) skills retrieval is a standalone measurement: no EvaluationRunner,
        # no BenchmarkDataset — the corpus is the synthetic procedural episodes
        # (or the authoritative LMEB-S procedural run, not yet built) and the
        # metric is the procedural recall@k. Delegates to the skills module. The
        # standalone result reuses the ``batch_result`` slot.
        from seahorse.benchmark.experiments.skills import (
            decide_skills,
            run_skills_experiment,
        )

        if corpus not in ("synthetic", "lmeb-s"):
            raise ValueError(
                f"skills experiment corpus must be 'synthetic' or 'lmeb-s', "
                f"got {corpus!r}"
            )
        skills_result = run_skills_experiment(corpus=corpus, top_k=top_k)
        return ExperimentReport(
            experiment="skills",
            corpus=corpus,
            temporal_mode=temporal,
            results=(),
            decision=decide_skills(skills_result),
            batch_result=skills_result,
        )

    if experiment == "rrf_k":
        # (A5) RRF_K sweep is a standalone measurement: no EvaluationRunner, no
        # BenchmarkDataset — the corpus is the synthetic golden-answer episodes
        # (or the authoritative LMEB-S run, not yet built) and the metric is the
        # recall@10 per RRF_K value. Delegates to the rrf_k module. The
        # standalone result reuses the ``batch_result`` slot.
        from seahorse.benchmark.experiments.rrf_k import (
            decide_rrf_k,
            run_rrf_k_experiment,
        )

        if corpus not in ("synthetic", "lmeb-s"):
            raise ValueError(
                f"rrf_k experiment corpus must be 'synthetic' or 'lmeb-s', "
                f"got {corpus!r}"
            )
        rrf_k_result = run_rrf_k_experiment(
            corpus=corpus, top_k=top_k, subsample=subsample
        )
        return ExperimentReport(
            experiment="rrf_k",
            corpus=corpus,
            temporal_mode=temporal,
            results=(),
            decision=decide_rrf_k(rrf_k_result),
            batch_result=rrf_k_result,
        )

    if experiment == "rerank_body":
        # (A6) rerank-with-body re-test is a standalone measurement: no
        # EvaluationRunner, no BenchmarkDataset — the corpus is the synthetic
        # mid-turn-answer episodes (or the authoritative LMEB-S run, not yet
        # built) and the metric is the recall@10 across baseline / rerank
        # summary / rerank body. Delegates to the rerank_body module. The
        # standalone result reuses the ``batch_result`` slot.
        from seahorse.benchmark.experiments.rerank_body import (
            decide_rerank_body,
            run_rerank_body_experiment,
        )

        if corpus not in ("synthetic", "lmeb-s"):
            raise ValueError(
                f"rerank_body experiment corpus must be 'synthetic' or 'lmeb-s', "
                f"got {corpus!r}"
            )
        rerank_body_result = run_rerank_body_experiment(
            corpus=corpus, top_k=top_k, subsample=subsample
        )
        return ExperimentReport(
            experiment="rerank_body",
            corpus=corpus,
            temporal_mode=temporal,
            results=(),
            decision=decide_rerank_body(rerank_body_result),
            batch_result=rerank_body_result,
        )

    if experiment == "end_to_end":
        # (A4) end-to-end measurement is a standalone measurement: no
        # EvaluationRunner, no BenchmarkDataset — the corpus is the synthetic
        # retrievable/unretrievable episodes (or the authoritative LMEB-S run,
        # not yet built) and the metric is recall@10 (the ceiling) vs the
        # reader's end-to-end accuracy. Delegates to the end_to_end module. The
        # standalone result reuses the ``batch_result`` slot.
        from seahorse.benchmark.experiments.end_to_end import (
            decide_end_to_end,
            run_end_to_end_experiment,
        )

        if corpus not in ("synthetic", "lmeb-s"):
            raise ValueError(
                f"end_to_end experiment corpus must be 'synthetic' or 'lmeb-s', "
                f"got {corpus!r}"
            )
        end_to_end_result = run_end_to_end_experiment(
            corpus=corpus,
            top_k=top_k,
            subsample=subsample,
            context_mode=cast(ContextMode, context_mode),
            reader=reader_llm,
        )
        return ExperimentReport(
            experiment="end_to_end",
            corpus=corpus,
            temporal_mode=temporal,
            results=(),
            decision=decide_end_to_end(end_to_end_result),
            batch_result=end_to_end_result,
        )

    base_config = BenchmarkConfig(
        adapter="lmeb" if corpus == "lmeb-s" else "synthetic",
        dataset_config="s",
        reader_model=reader_model,
        judge_model=judge_model,
        temporal_mode=temporal,
        output_dir=output_dir,
        top_k=top_k,
    )
    base_config.validate()
    dataset = _load_dataset(corpus, subsample=subsample)
    tokenizer = Tokenizer()
    reader = reader_llm or ReaderLLMClient(reader_model)
    registry = make_metric_registry(tokenizer)
    variants = variants_for(experiment)
    cache = template_cache if template_cache is not None else ({} if warm_db else None)

    results: list[ExperimentResult] = []
    with tempfile.TemporaryDirectory(prefix="seahorse-exp-") as tmp:
        for variant in variants:
            results.append(
                _run_variant(
                    base_config=base_config,
                    variant=variant,
                    dataset=dataset,
                    corpus=corpus,
                    tmp=Path(tmp),
                    output_dir=Path(output_dir),
                    reader=reader,
                    tokenizer=tokenizer,
                    registry=registry,
                    template_cache=cache,
                    pit_queries=pit_queries,
                )
            )

    baseline, sweep = results[0], results[1:]
    if experiment == "recency":
        decision = decide_recency(baseline, sweep)
    elif experiment == "rerank":
        decision = decide_rerank(baseline, sweep[0])
    elif experiment == "decay_rrf":
        decision = decide_decay_rrf(baseline, sweep[0])
    else:
        decision = decide_embed(baseline, sweep[0])
    return ExperimentReport(
        experiment=experiment,
        corpus=corpus,
        temporal_mode=temporal,
        results=tuple(results),
        decision=decision,
    )


def _run_variant(
    *,
    base_config: BenchmarkConfig,
    variant: ExperimentVariant,
    dataset: BenchmarkDataset,
    corpus: str,
    tmp: Path,
    output_dir: Path,
    reader: Any,
    tokenizer: Tokenizer,
    registry: MetricRegistry,
    template_cache: dict | None = None,
    pit_queries: bool = True,
) -> ExperimentResult:
    """Run one variant through the ``EvaluationRunner`` and collect the metrics.

    Warm-DB: when ``template_cache`` is provided, variants that embed
    the same text share one ingested corpus — the template DB is copied into the
    variant's dir (fast) and the SUT re-attaches the template bridge, so the
    runner skips re-ingestion (``skip_ingest=True``). The variant clock seeds
    from the template's post-ingest position so the recency boost reads the same
    ``now`` vs ``created_at`` spread as a fresh-DB run.
    """
    from dataclasses import replace

    variant_config = replace(base_config, **variant.as_config_kwargs())
    if variant.rerank_enabled:
        # Rerank: pin the cross-encoder identity in the fingerprint — the
        # run_id differs from the baseline.
        variant_config = replace(variant_config, rerank_model=_rerank_model_name(corpus))
    variant_config.validate()

    template = None
    if template_cache is not None:
        key = (
            corpus,
            variant.embed_mode,
            variant_config.temporal_mode,
            dataset.split_hash,
        )
        template = template_cache.get(key)
        if template is None:
            template = _build_template(
                base_config=base_config,
                dataset=dataset,
                corpus=corpus,
                reader=reader,
                tokenizer=tokenizer,
                embed_mode=variant.embed_mode,
                temporal=variant_config.temporal_mode,
            )
            template_cache[key] = template

    clock = AdvancingClock(
        base=earliest_session_date(dataset), delta_seconds=_clock_delta_seconds(dataset)
    )
    if template is not None:
        clock = AdvancingClock(base=template.clock_base, delta_seconds=template.clock_delta)
    build = _facade_factory(corpus, clock, variant)
    db_dir = tmp / variant.name
    db_dir.mkdir(parents=True, exist_ok=True)
    if template is not None:
        # The variant queries the copied corpus DB; ``bench2.db`` stays a fresh
        # empty DB (build_facade creates it) — the SUT's ``reset()`` semantics
        # (start over) are preserved and no ~1GB copy is wasted per variant.
        shutil.copy2(template.db_path, db_dir / "bench.db")
    out_dir = output_dir / variant.name
    out_dir.mkdir(parents=True, exist_ok=True)

    storages: list[Any] = []
    sut_holder: dict[str, SeahorseSUT | None] = {"sut": None}

    def _facade(db_name: str):
        facade, storage = build(db_dir / db_name)
        storages.append(storage)
        return facade

    def sut_factory() -> SeahorseSUT:
        sut = SeahorseSUT(
            _facade("bench.db"),
            lambda: _facade("bench2.db"),
            reader_llm=reader,
            tokenizer=tokenizer,
            fact_id_to_session=(
                dict(template.bridge["fact_id_to_session"]) if template else {}
            ),
            temporal_mode=variant_config.temporal_mode,
            pit_queries=pit_queries,
            top_k=variant_config.top_k,
            score_source=variant_config.score_source,
            recency_config=variant_config.recency_config,
            decay_config=variant_config.decay_config,
            rerank_enabled=variant_config.rerank_enabled,
            embed_mode=variant_config.embed_mode,
            ep_id_to_session=(
                dict(template.bridge["ep_id_to_session"]) if template else None
            ),
            fact_key_to_ep_id=(
                dict(template.bridge["fact_key_to_ep_id"]) if template else None
            ),
        )
        sut_holder["sut"] = sut
        return sut

    runner = EvaluationRunner(
        variant_config,
        # The static loader holds the pre-built dataset (no HF re-download per
        # variant); it structurally satisfies the DatasetLoader Protocol at
        # runtime (instance ``load`` is found by hasattr), mypy needs the cast.
        loader=cast(DatasetLoader, _StaticLoader(copy.deepcopy(dataset))),
        sut_factory=sut_factory,
        metric_registry=registry,
        # Per-variant artifacts (manifest/report/samples) → reproducible outputs.
        reporters=[JsonReporter(str(out_dir)), MarkdownReporter(str(out_dir))],
        tokenizer=tokenizer,
    )
    try:
        manifest = runner.run(skip_ingest=template is not None)
    finally:
        for storage in storages:
            storage.close()

    sut = sut_holder["sut"]
    detected = (
        sut._detected_score_source  # noqa: SLF001 — honest regime, set on first query
        if sut is not None and sut._detected_score_source is not None
        else variant_config.score_source
    )
    return ExperimentResult(
        variant=variant,
        metrics=manifest.metrics,
        detected_score_source=detected,
        run_errors=manifest.run_errors,
        run_id=manifest.fingerprint.run_id,
    )


__all__ = [
    "ExperimentReport",
    "CorpusTemplate",
    "run_experiment",
    "render_experiment_report",
    "make_metric_registry",
    "_build_template",
    "_run_variant",
]
