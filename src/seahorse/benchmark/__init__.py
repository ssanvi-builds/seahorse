"""#16 LMEB Benchmark Skeleton — the reproducible harness for F7 experiments.

The skeleton is the second validator of Seahorse's competitive moat (f5-16):
it measures, reproducibly, that the L5 engine (bi-temporal #2 + hybrid
retrieval #11 + progressive disclosure #8) produces comparable metrics over
public long-term-memory benchmarks. It is infrastructure, not a published
comparison: MVP-1 delivers the harness + the LongMemEval-S adapter + the
``SeahorseSUT``; external baselines (Mem0/Zep/Letta) are mediano.

Delegation purity (f5-16 §2.4): the skeleton knows ONLY #12 (``MemoryFacade``)
and its return types (``WriteResult``, ``list[IndexRow]``, ``TimelineWindow``,
``list[FullDetail]``, ``Episode``). It never imports #2/#6/#11 internals.

Reproducibility (f5-16 §5.5): the ``PinningFingerprint`` is bit-comparable
between identical runs (no timestamps); execution metadata lives in a separate
section. ``AdvancingClock`` is deterministic AND temporally ordered.

References:
- f5-16-lmeb-benchmark-skeleton.md (the component spec)
- f7-experimental-design.md (the 8 experiments the harness serves)
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
