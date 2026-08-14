"""Benchmark contracts — the four stable interfaces of the harness.

Loader / Runner / Metrics / Reporter are each a ``@runtime_checkable``
``Protocol``. The dataclasses are the canonical payload shapes the harness
passes between them.

Delegation purity: these contracts reference NO internal Seahorse component.
``SUTResponse`` carries the retrieval bridge (``retrieved_fact_ids`` +
``retrieved_session_ids``) so Recall@k/nDCG@k are computable over the
``fact_id → session_id`` map populated by the ``CorpusBuilder``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from seahorse.benchmark.config import BenchmarkConfig


@dataclass(frozen=True)
class BenchmarkInstance:
    """Canonical representation of a single benchmark question.

    ``haystack`` is the ordered sessions relevant to this question
    (``[{session_id, date, turns: [...]}]``). ``knowledge_updates`` carries the
    explicit ``(fact_key, old_ep_id, old_body, new_body, session_id, date)``
    pairs for knowledge-update questions; when empty, the
    ``KnowledgeUpdateSimulator`` derives them from the haystack.
    ``metadata`` is a mutable dict the runner uses to attach runtime state
    (e.g. ``new_ep_ids_after_improve`` for ``knowledge_update_accuracy``).
    """

    instance_id: str
    question: str
    golden_answer: str | None
    golden_session_ids: tuple[str, ...]  # for retrieval metrics (answer_session_ids)
    golden_evidence: tuple[str, ...]  # evidence statements with optional timestamps
    question_type: str  # e.g. "single-session-user", "multi-session", "knowledge-update"
    capabilities: tuple[str, ...]  # LongMemEval capabilities
    cognitive_category: str  # episodic | semantic | dialogue | procedural | n/a
    question_date: datetime | None  # temporal anchor for the question
    haystack: tuple[dict, ...]  # ordered sessions: [{session_id, date, turns: [...]}]
    # [(fact_key, old_ep_id, old_body, new_body, session_id, date)]
    knowledge_updates: tuple[dict, ...] = ()
    abstention: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkDataset:
    """A loaded benchmark split with deterministic identity hashes."""

    name: str
    version: str
    config: str
    split_hash: str  # SHA-256 of serialized instances (deterministic)
    loader_code_sha256: str  # SHA-256 of dataset loader script (trust_remote_code audit)
    instances: tuple[BenchmarkInstance, ...]
    metadata: dict


@runtime_checkable
class DatasetLoader(Protocol):
    """Adapter interface: translate an external benchmark to canonical form."""

    @staticmethod
    def load(config: BenchmarkConfig) -> BenchmarkDataset: ...

    @staticmethod
    def name() -> str: ...

    @staticmethod
    def available_configs() -> tuple[str, ...]: ...


@dataclass(frozen=True)
class SUTResponse:
    """A single query's answer + retrieval trace + efficiency measurements.

    ``retrieved_fact_ids`` and ``retrieved_session_ids`` are the retrieval
    bridge: the SUT resolves each ``IndexRow.fact_id`` to its session via the
    ``fact_id → session_id`` map. ``tokens_consumed_measured`` is REAL (via the
    reader tokenizer), never ``len*50``.
    """

    answer: str
    retrieved_ep_ids: tuple[str, ...]  # INDEX level
    retrieved_fact_ids: tuple[str, ...]  # for ep_id→session_id bridge
    retrieved_session_ids: tuple[str, ...] = ()  # resolved via the bridge
    retrieved_timeline_ep_ids: tuple[str, ...] = ()
    retrieved_full_ep_ids: tuple[str, ...] = ()
    depth_reached: str = "index"
    tokens_consumed_measured: int = 0  # REAL tokens via tokenizer
    tokens_consumed_estimated: int = 0  # heuristic (reported separately)
    latency_ms: dict[str, float] = field(default_factory=dict)
    reader_latency_ms: float = 0.0
    total_query_latency_ms: float = 0.0
    sut_metadata: dict = field(default_factory=dict)


@runtime_checkable
class MemorySystemSUT(Protocol):
    """Shared SUT interface for Seahorse AND external baselines (a medium-term goal).

    ``ingest`` / ``apply_knowledge_updates`` return the ep_ids they created so
    the ``KnowledgeUpdateSimulator`` can track ``new_ep_ids`` for
    ``knowledge_update_accuracy``.
    """

    def ingest(self, sessions: Sequence[dict]) -> list[str]: ...

    def apply_knowledge_updates(self, updates: Sequence[dict]) -> list[str]: ...

    def query(self, question: str, *, question_date: datetime | None = None) -> SUTResponse: ...

    def probe_level(self, question: str, level: str) -> dict: ...  # isolated level probe

    def reset(self) -> None: ...

    def identity(self) -> dict: ...


@dataclass(frozen=True)
class MetricReport:
    """Nested metric structure — global + by_slice + stats."""

    metric_name: str
    global_value: float
    # by question_type / cognitive_category
    by_slice: dict[str, float] = field(default_factory=dict)
    std: float | None = None
    prediction_interval_95: tuple[float, float] | None = None
    n_samples: int = 0
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MetricResult:
    """A computed metric: name + its nested report."""

    metric_name: str
    report: MetricReport


@runtime_checkable
class Metric(Protocol):
    """A computable metric. Concrete metrics are instances (they may carry
    dependencies such as the tokenizer or probe results)."""

    def name(self) -> str: ...

    def requires_golden(self) -> bool: ...

    def requires_retrieval(self) -> bool: ...

    def compute(
        self,
        instances: Sequence[BenchmarkInstance],
        responses: Sequence[SUTResponse],
        config: BenchmarkConfig,
    ) -> MetricResult: ...


@runtime_checkable
class Reporter(Protocol):
    """Renders a run to a concrete artifact (manifest.json / report.md / CI)."""

    def render(
        self,
        dataset: BenchmarkDataset,
        responses: Sequence[SUTResponse],
        metric_results: Sequence[MetricResult],
        manifest: Any,  # RunManifest (reporters/manifest.py)
        config: BenchmarkConfig,
    ) -> str: ...


__all__ = [
    "BenchmarkInstance",
    "BenchmarkDataset",
    "DatasetLoader",
    "SUTResponse",
    "MemorySystemSUT",
    "MetricReport",
    "MetricResult",
    "Metric",
    "Reporter",
]
