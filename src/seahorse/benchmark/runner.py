"""``EvaluationRunner`` + ``LevelProbeRunner`` — the deterministic orchestrator.

The runner loads the dataset, ingests the corpus (skip-mode), applies knowledge
updates, probes the disclosure levels in isolation, runs the QA queries,
computes the metrics, and renders the reports (f5-16 §9.2). It never crashes on
a single question (f5-16 §8.3): ``EmptyQueryError`` skips the instance and
marks it as an error.

``LevelProbeRunner`` measures p95 latency per disclosure level WITHOUT the
reader LLM (f5-16 §3.6 F2) — the mandatory TIMELINE/FULL metrics are collected
here even in MVP-1 flat mode.
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from seahorse.benchmark.config import BenchmarkConfig
from seahorse.benchmark.contracts import (
    BenchmarkDataset,
    DatasetLoader,
    MemorySystemSUT,
    MetricResult,
    Reporter,
    SUTResponse,
)
from seahorse.benchmark.corpus_builder import CorpusBuilder
from seahorse.benchmark.knowledge_update_simulator import KnowledgeUpdateSimulator
from seahorse.benchmark.metrics import _p95
from seahorse.benchmark.metrics.registry import MetricRegistry
from seahorse.benchmark.reporters.manifest import (
    ExecutionMetadata,
    PinningFingerprint,
    RunManifest,
)
from seahorse.benchmark.sut.seahorse_sut import SeahorseSUT
from seahorse.facade import EmptyQueryError

_PROMPTS_DIR = Path(__file__).parent / "harness" / "prompts"


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:  # noqa: BLE001 — not in a git repo (e.g. installed wheel)
        return "dev"


def _file_sha256(path: Path) -> str:
    if path.exists():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    return ""


class LevelProbeRunner:
    """Measures p95 latency per disclosure level in isolation (no reader LLM)."""

    def __init__(self, sut: MemorySystemSUT, *, sample_size: int = 10) -> None:
        self._sut = sut
        self._sample_size = sample_size

    def probe_levels(self, dataset: BenchmarkDataset) -> dict:
        questions = [inst.question for inst in dataset.instances[: self._sample_size]]
        index_lat: list[float] = []
        timeline_lat: list[float] = []
        full_lat: list[float] = []
        for q in questions:
            index_lat.append(self._sut.probe_level(q, "index")["latency_ms"])
            timeline_lat.append(self._sut.probe_level(q, "timeline")["latency_ms"])
            full_lat.append(self._sut.probe_level(q, "full")["latency_ms"])
        return {
            "p95_index_ms": _p95(index_lat),
            "p95_timeline_ms": _p95(timeline_lat),
            "p95_full_ms": _p95(full_lat),
        }


class EvaluationRunner:
    """Deterministic orchestrator: load → ingest → update → probe → query → report."""

    def __init__(
        self,
        config: BenchmarkConfig,
        *,
        loader: DatasetLoader,
        sut_factory: Callable[[], SeahorseSUT],
        metric_registry: MetricRegistry,
        reporters: Sequence[Reporter],
        tokenizer: Any,
    ) -> None:
        self._config = config
        self._loader = loader
        self._sut_factory = sut_factory
        self._metric_registry = metric_registry
        self._reporters = reporters
        self._tokenizer = tokenizer

    def run(self) -> RunManifest:
        self._config.validate()
        dataset = self._loader.load(self._config)
        sut = self._sut_factory()

        # Ingest the corpus (skip-mode, deterministic) + knowledge updates.
        CorpusBuilder(sut).ingest(dataset)
        kus = KnowledgeUpdateSimulator(sut)
        updates = kus.derive_updates(dataset)
        new_ep_ids = kus.apply(sut, updates)
        for inst_id, ep_ids in new_ep_ids.items():
            inst = next(i for i in dataset.instances if i.instance_id == inst_id)
            inst.metadata["new_ep_ids_after_improve"] = ep_ids

        # Isolated level probes (p95 TIMELINE/FULL without the reader LLM).
        probe = LevelProbeRunner(sut, sample_size=self._config.sample_size)
        probe_results = probe.probe_levels(dataset)

        # QA queries — never crash on a single question (f5-16 §8.3): an
        # EmptyQueryError skips the instance (excluded from the metrics) and is
        # recorded in the manifest.
        valid_instances = []
        responses: list[SUTResponse] = []
        run_errors: list[str] = []
        for inst in dataset.instances:
            try:
                responses.append(sut.query(inst.question, question_date=inst.question_date))
                valid_instances.append(inst)
            except EmptyQueryError:
                run_errors.append(inst.instance_id)

        results = self._metric_registry.compute_all(
            valid_instances, responses, self._config
        )
        manifest = self._build_manifest(dataset, sut, results, probe_results, run_errors)
        for reporter in self._reporters:
            reporter.render(dataset, responses, results, manifest, self._config)
        return manifest

    def _build_manifest(
        self,
        dataset: BenchmarkDataset,
        sut: MemorySystemSUT,
        results: Sequence[MetricResult],
        probe_results: dict,
        run_errors: list[str],
    ) -> RunManifest:
        started = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        fingerprint = PinningFingerprint(
            config_hash=self._config.config_hash(),
            dataset_hash=dataset.split_hash,
            loader_code_sha256=dataset.loader_code_sha256,
            embedding_identity="me5-small:384:<sha12>:int8",
            embedding_batch_config="batch_size=1_forced",  # OQ-16-12
            knn_completeness=1.0,  # OQ-16-12: sync indexer, drain is a no-op
            reader_model_used=self._config.reader_model,
            judge_model_used=self._config.judge_model,
            seahorse_version=_git_sha(),
            skeleton_version=_git_sha(),
            reader_system_prompt_sha256=_file_sha256(_PROMPTS_DIR / "reader_system_prompt.txt"),
            judge_rubric_hashes={},
            ingest_template_sha256=_file_sha256(_PROMPTS_DIR / "ingest_template.txt"),
            sut_name="seahorse",
            sut_version="0.1.0",
            temporal_mode=self._config.temporal_mode,
            score_source=self._config.score_source,
            reproducibility_class=self._config.reproducibility_class,
            expected_match_rate=self._config.expected_match_rate,
            judge_validation_status=self._config.judge_validation_status,
        )
        execution = ExecutionMetadata(
            started_at=started,
            environment={"device": "cpu", "sqlite_wal": True},
        )
        return RunManifest(
            fingerprint=fingerprint,
            execution=execution,
            metrics={r.metric_name: r.report for r in results},
            run_errors=run_errors,
        )


__all__ = ["EvaluationRunner", "LevelProbeRunner"]
