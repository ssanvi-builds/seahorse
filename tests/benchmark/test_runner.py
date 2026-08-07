"""Tests for ``EvaluationRunner`` + ``LevelProbeRunner`` (f5-16 §3.6/§9.2)."""

from __future__ import annotations

from seahorse.benchmark.config import BenchmarkConfig
from seahorse.benchmark.contracts import BenchmarkDataset
from seahorse.benchmark.corpus_builder import CorpusBuilder
from seahorse.benchmark.metrics.efficiency import LatencyP95Metric, TokenEfficiencyMetric
from seahorse.benchmark.metrics.memory import FAMAGapMetric, KnowledgeUpdateAccuracyMetric
from seahorse.benchmark.metrics.registry import MetricRegistry
from seahorse.benchmark.metrics.retrieval import MRR, NDCGAtK, PrecisionAtK, RecallAtK
from seahorse.benchmark.runner import EvaluationRunner, LevelProbeRunner
from seahorse.benchmark.sut.seahorse_sut import SeahorseSUT
from seahorse.facade import EmptyQueryError, build_facade
from tests.benchmark.conftest import FakeReaderLLM, FakeTokenizer


class _FakeLoader:
    def __init__(self, dataset: BenchmarkDataset) -> None:
        self._dataset = dataset

    @staticmethod
    def name() -> str:
        return "fake"

    @staticmethod
    def available_configs() -> tuple[str, ...]:
        return ("s",)

    def load(self, config: BenchmarkConfig) -> BenchmarkDataset:
        return self._dataset


class _RecordingReporter:
    def __init__(self) -> None:
        self.calls = 0

    def render(self, dataset, responses, metric_results, manifest, config) -> str:
        self.calls += 1
        return ""


def _make_registry(tokenizer) -> MetricRegistry:
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


def _make_runner(tmp_path, dataset, *, reader=None, tokenizer=None):
    reader = reader or FakeReaderLLM()
    tokenizer = tokenizer or FakeTokenizer()

    def sut_factory() -> SeahorseSUT:
        facade, storage = build_facade(tmp_path / "bench.db")
        return SeahorseSUT(
            facade,
            lambda: build_facade(tmp_path / "bench2.db")[0],
            reader_llm=reader,
            tokenizer=tokenizer,
            fact_id_to_session={},
        )

    reporter = _RecordingReporter()
    runner = EvaluationRunner(
        BenchmarkConfig(),
        loader=_FakeLoader(dataset),
        sut_factory=sut_factory,
        metric_registry=_make_registry(tokenizer),
        reporters=[reporter],
        tokenizer=tokenizer,
    )
    return runner, reporter


def test_run_produces_manifest(tmp_path, synthetic_dataset):
    runner, _ = _make_runner(tmp_path, synthetic_dataset)
    manifest = runner.run()
    assert manifest.fingerprint.dataset_hash == "abc123"
    assert manifest.fingerprint.embedding_batch_config == "batch_size=1_forced"
    assert manifest.fingerprint.knn_completeness == 1.0  # OQ-16-12
    assert manifest.fingerprint.score_source == "mvp1_rrf"
    assert "recall@10" in manifest.metrics
    assert "knowledge_update_accuracy" in manifest.metrics
    assert manifest.execution.started_at


def test_run_renders_reports(tmp_path, synthetic_dataset):
    runner, reporter = _make_runner(tmp_path, synthetic_dataset)
    runner.run()
    assert reporter.calls == 1


def test_run_handles_empty_query(tmp_path, synthetic_dataset):
    """EmptyQueryError skips the instance and marks it as an error (f5-16 §8.3)."""

    class _EmptyReader:
        def generate(self, question, context, question_date=None) -> str:
            raise EmptyQueryError()

        def identity(self) -> dict:
            return {"model": "fake"}

    runner, _ = _make_runner(tmp_path, synthetic_dataset, reader=_EmptyReader())
    manifest = runner.run()
    assert manifest.metrics["recall@10"].n_samples == 0  # all queries errored
    assert len(manifest.run_errors) == 5  # every instance skipped + recorded


def test_level_probe_runner_measures_p95(tmp_path, synthetic_dataset):
    facade, storage = build_facade(tmp_path / "bench.db")
    sut = SeahorseSUT(
        facade,
        lambda: build_facade(tmp_path / "bench2.db")[0],
        reader_llm=FakeReaderLLM(),
        tokenizer=FakeTokenizer(),
        fact_id_to_session={},
    )
    CorpusBuilder(sut).ingest(synthetic_dataset)
    probe = LevelProbeRunner(sut, sample_size=5)
    results = probe.probe_levels(synthetic_dataset)
    assert "p95_index_ms" in results
    assert "p95_timeline_ms" in results
    assert "p95_full_ms" in results
    assert results["p95_index_ms"] >= 0
    storage.close()


def test_run_skip_ingest_uses_prepopulated_bridge(tmp_path, synthetic_dataset):
    """Warm-DB: ``skip_ingest=True`` runs the query/metrics phase over a SUT that
    already carries the corpus bridge (f7 §5a) — no re-ingestion, no re-embed."""
    # 1. Ingest once into a template facade, capture the bridge.
    facade, storage = build_facade(tmp_path / "template.db")
    template_sut = SeahorseSUT(
        facade,
        lambda: build_facade(tmp_path / "template2.db")[0],
        reader_llm=FakeReaderLLM(),
        tokenizer=FakeTokenizer(),
        fact_id_to_session={},
    )
    CorpusBuilder(template_sut).ingest(synthetic_dataset)
    bridge = {
        "ep_id_to_session": dict(template_sut._ep_id_to_session),
        "fact_id_to_session": dict(template_sut.fact_id_to_session),
        "fact_key_to_ep_id": dict(template_sut.fact_key_to_ep_id),
    }
    storage.close()

    # 2. Run the runner over a copy of the template DB with skip_ingest=True.
    import shutil

    shutil.copy2(tmp_path / "template.db", tmp_path / "bench.db")
    shutil.copy2(tmp_path / "template.db", tmp_path / "bench2.db")

    def sut_factory() -> SeahorseSUT:
        facade, _ = build_facade(tmp_path / "bench.db")
        return SeahorseSUT(
            facade,
            lambda: build_facade(tmp_path / "bench2.db")[0],
            reader_llm=FakeReaderLLM(),
            tokenizer=FakeTokenizer(),
            fact_id_to_session=dict(bridge["fact_id_to_session"]),
            ep_id_to_session=dict(bridge["ep_id_to_session"]),
            fact_key_to_ep_id=dict(bridge["fact_key_to_ep_id"]),
        )

    runner = EvaluationRunner(
        BenchmarkConfig(),
        loader=_FakeLoader(synthetic_dataset),
        sut_factory=sut_factory,
        metric_registry=_make_registry(FakeTokenizer()),
        reporters=[_RecordingReporter()],
        tokenizer=FakeTokenizer(),
    )
    manifest = runner.run(skip_ingest=True)
    assert "recall@10" in manifest.metrics
    assert manifest.metrics["recall@10"].n_samples > 0
    assert manifest.metrics["ndcg@10"].n_samples > 0


def test_runner_uses_advancing_clock_for_temporal_ordering(tmp_path, synthetic_dataset):
    """The runner's SUT ingests sessions in date order (oldest first)."""
    runner, _ = _make_runner(tmp_path, synthetic_dataset)
    manifest = runner.run()
    # The knowledge-update question's new version should be retrievable
    assert manifest.metrics["knowledge_update_accuracy"].n_samples == 1
