"""F7 experiment runner — the variant matrices over the #16 harness (f7 §5).

An experiment IS the skeleton run once per ``ExperimentVariant`` (the manifest
``score_source`` is the variant, f7 §3), with the decision thresholds applied
to the resulting retrieval metrics. ``run_experiment`` is the entry point; the
report carries the honest per-variant regime detection (``fallback_g2`` →
invalid decision, ADR-10) plus the F1/F3 verdict.

Corpora:
- ``synthetic`` (default, CI): the deterministic canonical corpus + a
  content-hash embedder — verifies the harness MECHANICS without any model or
  download. NOT the science.
- ``lmeb-s``: the real LongMemEval haystack + the auto-resolved fastembed
  backend — the authoritative decision (requires the ``benchmark`` +
  ``embeddings`` extras).

The clock is wired per variant: ``AdvancingClock(base=earliest_session_date,
delta=1 day)`` so ``created_at`` spans the haystack — the recency boost reads
that spread (f5-16 §3.5). Deterministic and temporally ordered.
"""

from __future__ import annotations

import copy
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
    decide_embed,
    decide_recency,
)
from seahorse.benchmark.experiments.synthetic import HashEmbedder, make_synthetic_dataset
from seahorse.benchmark.experiments.variants import (
    CORPORA,
    EXPERIMENTS,
    ExperimentVariant,
    variants_for,
)
from seahorse.benchmark.harness.reader_llm import ReaderLLMClient
from seahorse.benchmark.harness.tokenizer import Tokenizer
from seahorse.benchmark.knowledge_update_simulator import KnowledgeUpdateSimulator
from seahorse.benchmark.metrics.efficiency import LatencyP95Metric, TokenEfficiencyMetric
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
    actual time range (f5-16 §3.5 U5: "la diferencia real entre sesiones").
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

    ``batch_result`` is set only for the batch-por-turno experiment (f7 §5d),
    which is a standalone measurement (no per-variant ``EvaluationRunner``
    results) — the turn-cluster recall@k + the decision.
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

    Warm-DB (f7 §5a): the recency variants (and the embed ``body`` variant)
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
    """The standard #16 metric set (the LLM-free honest floor, f5-16 §4.4)."""
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


def _load_dataset(corpus: str) -> BenchmarkDataset:
    """Load the corpus once; the runner deep-copies per variant."""
    if corpus == "synthetic":
        return make_synthetic_dataset()
    if corpus == "lmeb-s":
        from seahorse.benchmark.adapters.longmemeval import LMEBLoader  # lazy: datasets extra

        return LMEBLoader.load(BenchmarkConfig(dataset_config="s"))
    raise ValueError(f"unknown corpus: {corpus!r} (expected {CORPORA!r})")


def _recency_config(variant: ExperimentVariant) -> RecencyConfig | None:
    if variant.recency_config is None:
        return None
    return RecencyConfig(**variant.recency_config)


def _facade_factory(
    corpus: str, clock: Callable[[], Any], variant: ExperimentVariant
) -> Callable[[Path], tuple[Any, Any]]:
    """Composition-root wiring per variant + corpus (f7 enablers a/c, §3)."""

    def _build(db_path: Path):
        kwargs: dict = {
            "clock": clock,
            "recency": _recency_config(variant),
            "embed_mode": variant.embed_mode,
        }
        if corpus == "synthetic":
            # Deterministic hybrid regime: the content-hash embedder keeps the
            # kNN path REAL without a model download.
            kwargs["retrieval_available"] = True
            kwargs["passage_embedder"] = HashEmbedder()
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
    """Ingest the corpus ONCE into a template DB + capture the bridge (f7 §5a).

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
    lines = [
        f"# F7 experiment: {report.experiment}  (corpus={report.corpus}, "
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
) -> ExperimentReport:
    """Run an F7 experiment and return the report with the decision verdict.

    ``reader_llm`` defaults to a real ``ReaderLLMClient`` (LMEB runs); the
    synthetic/CI verification injects a deterministic double.

    Warm-DB (f7 §5a): variants that embed the same text share ONE corpus ingest
    (the recency sweep is 10 variants over identical embeddings — a fresh-DB
    run would re-embed ~49M tokens per variant). ``warm_db=True`` (default)
    builds a shared ``CorpusTemplate`` per ``(corpus, embed_mode, temporal,
    dataset_hash)``; ``template_cache`` reuses an external cache across
    ``run_experiment`` calls (the embed experiment's ``body`` variant reuses
    the recency experiment's body template). ``warm_db=False`` runs each
    variant on a fresh DB (the pre-warm-DB behavior, used to verify equivalence).
    """
    if experiment not in EXPERIMENTS:
        raise ValueError(f"unknown experiment: {experiment!r} (expected {EXPERIMENTS!r})")
    if corpus not in CORPORA:
        raise ValueError(f"unknown corpus: {corpus!r} (expected {CORPORA!r})")

    if experiment == "batch":
        # (d) batch-por-turno is a standalone measurement (f7 §5d): no
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
    dataset = _load_dataset(corpus)
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

    Warm-DB (f7 §5a): when ``template_cache`` is provided, variants that embed
    the same text share one ingested corpus — the template DB is copied into the
    variant's dir (fast) and the SUT re-attaches the template bridge, so the
    runner skips re-ingestion (``skip_ingest=True``). The variant clock seeds
    from the template's post-ingest position so the recency boost reads the same
    ``now`` vs ``created_at`` spread as a fresh-DB run.
    """
    from dataclasses import replace

    variant_config = replace(base_config, **variant.as_config_kwargs())
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
