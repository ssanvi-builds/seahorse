"""The benchmark harness — a reproducible LongMemEval benchmark harness.

It is the second validator of Seahorse's long-term-memory claims: it measures,
reproducibly, that the engine (bi-temporal storage, hybrid retrieval,
progressive disclosure) produces comparable metrics over public long-term-
memory benchmarks. It is infrastructure, not a published comparison: the
current release delivers the harness, the LongMemEval-S adapter, and the
``SeahorseSUT``; external baselines (Mem0/Zep/Letta) are a medium-term goal.

Delegation purity: the harness knows ONLY ``MemoryFacade`` and its return types
(``WriteResult``, ``list[IndexRow]``, ``TimelineWindow``, ``list[FullDetail]``,
``Episode``). It never imports engine, persistence, or hybrid-retrieval
internals.

Reproducibility: the ``PinningFingerprint`` is bit-comparable between identical
runs (no timestamps); execution metadata lives in a separate section.
``AdvancingClock`` is deterministic AND temporally ordered.
"""

from __future__ import annotations

from seahorse.benchmark.config import BenchmarkConfig
from seahorse.benchmark.contracts import (
    BenchmarkDataset,
    BenchmarkInstance,
    MetricReport,
    MetricResult,
    SUTResponse,
)

__all__ = [
    "BenchmarkConfig",
    "BenchmarkInstance",
    "BenchmarkDataset",
    "SUTResponse",
    "MetricReport",
    "MetricResult",
]
