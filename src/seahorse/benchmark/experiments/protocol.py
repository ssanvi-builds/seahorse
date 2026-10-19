"""The experiment seam — one interface for all 17 benchmark experiments (C4).

Inventory verdict (C4 deepening, 2026-10-19): every experiment — the 4
variant-matrix ones (recency/rerank/embed/decay_rrf) and the 13 standalone
measures — implements the same ``Experiment`` trio:

- ``run(ExperimentContext)`` executes the measurement. Matrix adapters return
  ``tuple[ExperimentResult, ...]`` shaped ``(baseline, *sweep)``; standalone
  adapters return their module-local frozen result.
- ``decide(result)`` is the pure fail-loud verdict (``fallback_g2`` →
  ``invalid_regime``).
- ``render(ExperimentReport)`` is the CLI-facing output — the table, inserts,
  and decision block the human reads.

``ExperimentReport`` is the common envelope with two result channels, both
load-bearing (tests assert on each): ``results`` (the per-variant matrix
channel) and ``batch_result`` (the standalone channel).

Registration is import-time via ``experiments/registry.py``; ``runner.py`` is
the composition point that imports every adapter module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from seahorse.benchmark.experiments.decide import ExperimentResult


@dataclass(frozen=True)
class ExperimentContext:
    """Immutable per-invocation inputs the orchestrator hands to ``run``.

    The orchestrator (``runner.run_experiment``) is the only production
    constructor; adapters treat it as read-only. ``pit_queries`` arrives
    post-resolution (``_resolve_pit_queries`` — the ADR-03 recency/decay gate
    already applied). ``template_cache`` is the caller's mutable dict passed
    by reference (warm-DB sharing across invocations) — frozen dataclass
    semantics do not extend to the referenced object.
    """

    corpus: str
    top_k: int
    subsample: bool
    temporal: bool
    pit_queries: bool
    output_dir: str
    reader_llm: Any | None
    reader_model: str
    judge_model: str
    strong_reader_model: str
    context_mode: str
    warm_db: bool
    template_cache: dict | None


@runtime_checkable
class Experiment(Protocol):
    """The single experiment seam: ``run``/``decide``/``render``.

    ``matrix`` discriminates the two result channels: matrix adapters fill
    ``ExperimentReport.results`` (a ``(baseline, *sweep)`` tuple), standalone
    adapters fill ``ExperimentReport.batch_result``. The orchestrator
    cross-checks the flag against the outcome's type — drift raises
    ``ValueError`` instead of silently mis-slotting.

    Note: ``runtime_checkable`` validates only the METHODS (run/decide/render);
    the data members (name/corpora/matrix) are invisible to ``isinstance`` —
    structural conformance is pinned by test instead.
    """

    name: str
    corpora: tuple[str, ...]
    matrix: bool

    def run(self, ctx: ExperimentContext) -> Any: ...

    def decide(self, result: Any) -> dict: ...

    def render(self, report: ExperimentReport) -> str: ...


@dataclass(frozen=True)
class ExperimentReport:
    """The full experiment artifact: results + the verdict.

    ``results`` is the per-variant ``ExperimentResult`` channel, filled by the
    matrix experiments (``(baseline, *sweep)``). ``batch_result`` is the
    standalone channel: the module's bespoke frozen ``XxxResult`` (the name is
    historical — the batch experiment pioneered the slot, the other standalone
    measures followed).
    """

    experiment: str
    corpus: str
    temporal_mode: bool
    results: tuple[ExperimentResult, ...]
    decision: dict
    batch_result: Any | None = None