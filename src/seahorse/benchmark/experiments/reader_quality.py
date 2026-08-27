"""Reader-quality A/B experiment — does a stronger reader model close the gap?

Falsifies the hypothesis "the reader MODEL is the bottleneck" (the A4
``reader_bottleneck``: end-to-end accuracy 0.070 vs recall@10 0.790 on the
LMEB-S subsample). The reader-context A/B (2026-08-22) already ruled out the
context REPRESENTATION (``keep_summary``); the episode-granularity experiment
(2026-08-25) ruled out retrieval GRANULARITY (``reader_bottleneck``). The
remaining candidate is the reader MODEL itself: the baseline ``qwen3:0.6b`` is
a weak extractor. This experiment measures end-to-end accuracy with a WEAK
reader (the A4 baseline) vs a STRONG reader (the cloud model) over the SAME
corpus — if the strong reader recovers >= ``READER_QUALITY_DELTA_PP`` (10pp),
the reader model is the bottleneck; if not, the loss is in context assembly
(the answer fragment is not in the top-k context).

Metrics per reader:
- **recall@10**: whether the golden session is in the top-10 (the ceiling,
  shared across readers — the reader cannot change retrieval).
- **end-to-end accuracy**: whether the reader's answer matches the golden answer
  (normalized substring match; abstention questions count correct when the
  reader abstains on an empty context).

Decision (``decide_reader_quality``): flip iff the strong reader recovers >=
``READER_QUALITY_DELTA_PP`` (10pp) more end-to-end accuracy than the weak
baseline — the evidence that the reader MODEL is the bottleneck (the benchmark
should recommend a stronger reader). Honest regime detection: all-zero scores
=> ``fallback_g2`` => invalid decision (fail-loud honesty).

The synthetic corpus verifies the harness MECHANICS (no model); the
authoritative decision comes from an LMEB-S run (``--corpus lmeb-s``), which
ingests the real haystack with the real embedder and measures SESSION-level
recall over the reproducible 100 subsample. The corpus is built ONCE and the
two readers measure over the same facade (a per-reader rebuild would re-ingest
~20 min each).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from seahorse.benchmark.experiments.end_to_end import (
    EndToEndQuestion,
    ExtractiveReader,
    build_real_corpus,
    build_synthetic_corpus,
    measure_end_to_end,
)
from seahorse.benchmark.harness.context import ContextMode
from seahorse.contracts.episode import Episode

# The k for the recall@k measurement (harness default).
READER_QUALITY_TOP_K = 10

# Decision threshold: the strong reader must recover >= 10pp more end-to-end
# accuracy than the weak baseline to attribute the loss to the reader MODEL
# (the ceiling gap is recall@10 - e2e_weak, ~72pp on the real corpus).
READER_QUALITY_DELTA_PP = 0.10

# The honest detected regime that invalidates a hybrid-regime experiment.
_FALLBACK_G2 = "fallback_g2"

# The context representation is fixed at summary — the reader-context A/B
# (2026-08-22) already decided ``keep_summary``; this experiment varies the
# reader, not the assembler.
_CONTEXT_MODE: ContextMode = "summary"


@dataclass(frozen=True)
class ReaderQualityExperimentResult:
    """The end-to-end accuracy with the weak vs strong reader.

    ``recall_at_k`` is the shared ceiling — the reader cannot change retrieval,
    so both readers measure over the same top-10 (recorded once; the honest
    ceiling for the delta). ``regime`` is the weak-reader regime (both readers
    share the same retrieval path).
    """

    recall_at_k: float
    e2e_weak: float
    e2e_strong: float
    n_queries: int
    n_episodes: int
    regime: str  # hybrid | fallback_g2


def _measure_readers(
    facade: Any,
    episodes: list[Episode],
    questions: list[EndToEndQuestion],
    ep_id_to_session: dict[str, str],
    top_k: int,
    *,
    reader_weak: Any | None,
    reader_strong: Any | None,
) -> tuple[float, float, float, int, int, str]:
    """Run the end-to-end measurement once per reader over the SAME corpus.

    Returns ``(recall, e2e_weak, e2e_strong, n_queries, n_episodes, regime)``.
    Both readers default to the deterministic ``ExtractiveReader`` (synthetic
    CI); the authoritative run injects the real ``ReaderLLMClient`` pair.
    """
    if reader_weak is None:
        reader_weak = ExtractiveReader()
    if reader_strong is None:
        reader_strong = ExtractiveReader()
    weak = measure_end_to_end(
        facade,
        episodes,
        questions,
        ep_id_to_session,
        top_k,
        context_mode=_CONTEXT_MODE,
        reader=reader_weak,
    )
    strong = measure_end_to_end(
        facade,
        episodes,
        questions,
        ep_id_to_session,
        top_k,
        context_mode=_CONTEXT_MODE,
        reader=reader_strong,
    )
    return (
        weak[0],
        weak[1],
        strong[1],
        weak[2],
        weak[3],
        weak[4],
    )


def run_reader_quality_experiment(
    *,
    corpus: str = "synthetic",
    db_path: Path | str | None = None,
    top_k: int = READER_QUALITY_TOP_K,
    subsample: bool = True,
    reader_weak: Any | None = None,
    reader_strong: Any | None = None,
) -> ReaderQualityExperimentResult:
    """Run the reader-quality A/B and return the result.

    ``corpus`` is ``"synthetic"`` (mechanical CI verification) or ``"lmeb-s"``
    (the real corpus, authoritative — the reproducible 100 subsample by default;
    ``subsample=False`` opts into the full-corpus overnight run). ``db_path``
    defaults to a fresh temp DB (reproducible). The corpus is built ONCE and the
    two readers measure over the same facade.
    """
    if corpus not in ("synthetic", "lmeb-s"):
        raise ValueError(
            f"unknown corpus: {corpus!r} (expected 'synthetic' or 'lmeb-s')"
        )
    tmp = Path(tempfile.mkdtemp(prefix="seahorse-readerquality-"))
    db = Path(db_path) if db_path is not None else tmp / "bench.db"
    if corpus == "synthetic":
        facade, storage, episodes, questions, ep_id_to_session = build_synthetic_corpus(db)
    else:
        facade, storage, episodes, questions, ep_id_to_session = build_real_corpus(
            db, subsample=subsample
        )
    try:
        recall, e2e_w, e2e_s, n_queries, n_episodes, regime = _measure_readers(
            facade,
            episodes,
            questions,
            ep_id_to_session,
            top_k,
            reader_weak=reader_weak,
            reader_strong=reader_strong,
        )
    finally:
        storage.close()
    return ReaderQualityExperimentResult(
        recall_at_k=recall,
        e2e_weak=e2e_w,
        e2e_strong=e2e_s,
        n_queries=n_queries,
        n_episodes=n_episodes,
        regime=regime,
    )


def decide_reader_quality(result: ReaderQualityExperimentResult) -> dict:
    """Apply the decision: is the reader MODEL the bottleneck?

    Returns a decision dict (``decision``, ``flip``, ``reason``,
    ``recall_at_k``, ``e2e_weak``, ``e2e_strong``). Invalid (no decision) when
    the run degraded to ``fallback_g2`` (fail-loud honesty). The flip is the
    benchmark's reader recommendation: a strong reader recovering >= 10pp means
    the reader model is the bottleneck (recommend a stronger reader); otherwise
    the loss is in context assembly (the answer fragment is not in the top-k
    context).
    """
    if result.regime == _FALLBACK_G2:
        return {
            "decision": "invalid_regime",
            "flip": False,
            "reason": (
                "the run degraded to the listing regime (hybrid retrieval not wired); "
                "the reader-quality comparison is not meaningful — re-run with the "
                "embeddings extra"
            ),
            "recall_at_k": result.recall_at_k,
            "e2e_weak": result.e2e_weak,
            "e2e_strong": result.e2e_strong,
        }
    delta = result.e2e_strong - result.e2e_weak
    if delta >= READER_QUALITY_DELTA_PP:
        return {
            "decision": "reader_quality_bottleneck",
            "flip": True,
            "reason": (
                f"the strong reader recovers {delta * 100:.1f}pp more end-to-end "
                f"accuracy than the weak baseline ({result.e2e_weak:.3f} -> "
                f"{result.e2e_strong:.3f}) on recall@{READER_QUALITY_TOP_K} "
                f"{result.recall_at_k:.3f} — the reader MODEL is the bottleneck; "
                f"the benchmark should recommend a stronger reader"
            ),
            "recall_at_k": result.recall_at_k,
            "e2e_weak": result.e2e_weak,
            "e2e_strong": result.e2e_strong,
        }
    return {
        "decision": "context_assembly_bottleneck",
        "flip": False,
        "reason": (
            f"the strong reader does not recover >= {READER_QUALITY_DELTA_PP:.0%} "
            f"more end-to-end accuracy than the weak baseline ({result.e2e_weak:.3f} "
            f"-> {result.e2e_strong:.3f}, gap {delta * 100:.1f}pp) on "
            f"recall@{READER_QUALITY_TOP_K} {result.recall_at_k:.3f} — the reader "
            f"model is NOT the bottleneck; the loss is in context assembly (the "
            f"answer fragment is not in the top-k context)"
        ),
        "recall_at_k": result.recall_at_k,
        "e2e_weak": result.e2e_weak,
        "e2e_strong": result.e2e_strong,
    }


def render_reader_quality_report(
    result: ReaderQualityExperimentResult, decision: dict
) -> str:
    """Human-readable report for the CLI (metrics + decision)."""
    lines = [
        "# Reader-quality A/B experiment: does a stronger reader close the gap?",
        "",
        f"regime: {result.regime}",
        f"episodes: {result.n_episodes}",
        f"queries: {result.n_queries}",
        f"recall@{READER_QUALITY_TOP_K} (the ceiling): {result.recall_at_k:.3f}",
        f"end-to-end accuracy (weak reader): {result.e2e_weak:.3f}",
        f"end-to-end accuracy (strong reader): {result.e2e_strong:.3f}",
        f"delta (strong - weak): {result.e2e_strong - result.e2e_weak:.3f}",
        "",
        "## Decision",
        f"decision: {decision.get('decision')}",
        f"flip: {decision.get('flip')}",
        f"reason: {decision.get('reason', '')}",
    ]
    return "\n".join(lines)


__all__ = [
    "READER_QUALITY_DELTA_PP",
    "READER_QUALITY_TOP_K",
    "ReaderQualityExperimentResult",
    "decide_reader_quality",
    "render_reader_quality_report",
    "run_reader_quality_experiment",
]
