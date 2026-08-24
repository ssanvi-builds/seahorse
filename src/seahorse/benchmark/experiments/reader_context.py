"""Reader-context A/B experiment — does hydrating the FULL body close the reader gap?

Falsifies the hypothesis "the summary-only context representation is why the
reader cannot extract answers" (the A4 ``reader_bottleneck``: end-to-end
accuracy 0.070 vs recall@10 0.790 on the LMEB-S subsample). The reader-context
A/B measures end-to-end accuracy across the three assembler modes — ``summary``
(baseline), ``body`` (full hydration) and ``body_bounded`` (per-episode cap) —
with a reader (``ExtractiveReader`` for synthetic CI, the real
``ReaderLLMClient`` for the authoritative LMEB-S run).

Metrics per mode:
- **recall@10**: whether the golden session is in the top-10 (the ceiling,
  shared across modes — the context representation cannot change retrieval).
- **end-to-end accuracy**: whether the reader's answer matches the golden answer
  (normalized substring match; abstention questions count correct when the
  reader abstains on an empty context).

Decision (``decide_reader_context``): flip iff a body mode recovers >=
``READER_CONTEXT_DELTA_PP`` (10pp) more end-to-end accuracy than summary — the
evidence to hydrate bodies in the product's answer path. Honest regime
detection: all-zero scores => ``fallback_g2`` => invalid decision (fail-loud
honesty).

The synthetic corpus verifies the harness MECHANICS (no model); the
authoritative decision comes from an LMEB-S run (``--corpus lmeb-s``), which
ingests the real haystack with the real embedder and measures SESSION-level
recall over the reproducible 100 subsample. The corpus is built ONCE and the
three modes measure over the same facade (a per-mode rebuild would re-ingest
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
READER_CONTEXT_TOP_K = 10

# Decision threshold: a body mode must recover >= 10pp more end-to-end accuracy
# than summary to justify hydrating bodies in the product's answer path (the
# ceiling gap is recall@10 - e2e_summary, ~72pp on the real corpus).
READER_CONTEXT_DELTA_PP = 0.10

# The honest detected regime that invalidates a hybrid-regime experiment.
_FALLBACK_G2 = "fallback_g2"

# The three modes the A/B measures (all in the harness context contract).
CONTEXT_MODES: tuple[ContextMode, ...] = ("summary", "body", "body_bounded")


@dataclass(frozen=True)
class ReaderContextExperimentResult:
    """The end-to-end accuracy across the three context modes.

    ``recall_at_k`` is the summary-mode ceiling — the context representation
    cannot change retrieval, so all modes share it (recorded once; the honest
    ceiling for the delta). ``regime`` is the summary-mode regime (all modes
    share the same retrieval path).
    """

    recall_at_k: float
    e2e_summary: float
    e2e_body: float
    e2e_body_bounded: float
    n_queries: int
    n_episodes: int
    regime: str  # hybrid | fallback_g2


def _measure_modes(
    facade: Any,
    episodes: list[Episode],
    questions: list[EndToEndQuestion],
    ep_id_to_session: dict[str, str],
    top_k: int,
    *,
    reader: Any | None,
) -> tuple[float, float, float, float, int, int, str]:
    """Run the end-to-end measurement once per mode over the SAME corpus.

    Returns ``(recall, e2e_summary, e2e_body, e2e_body_bounded, n_queries,
    n_episodes, regime)``. The reader defaults to the deterministic
    ``ExtractiveReader`` (synthetic CI); the authoritative run injects the real
    ``ReaderLLMClient``.
    """
    if reader is None:
        reader = ExtractiveReader()
    mode_results: dict[ContextMode, tuple[float, float, int, int, str]] = {}
    for mode in CONTEXT_MODES:
        mode_results[mode] = measure_end_to_end(
            facade,
            episodes,
            questions,
            ep_id_to_session,
            top_k,
            context_mode=mode,
            reader=reader,
        )
    summary = mode_results["summary"]
    body = mode_results["body"]
    bounded = mode_results["body_bounded"]
    return (
        summary[0],
        summary[1],
        body[1],
        bounded[1],
        summary[2],
        summary[3],
        summary[4],
    )


def run_reader_context_experiment(
    *,
    corpus: str = "synthetic",
    db_path: Path | str | None = None,
    top_k: int = READER_CONTEXT_TOP_K,
    subsample: bool = True,
    reader: Any | None = None,
) -> ReaderContextExperimentResult:
    """Run the reader-context A/B and return the result.

    ``corpus`` is ``"synthetic"`` (mechanical CI verification) or ``"lmeb-s"``
    (the real corpus, authoritative — the reproducible 100 subsample by default;
    ``subsample=False`` opts into the full-corpus overnight run). ``db_path``
    defaults to a fresh temp DB (reproducible). The corpus is built ONCE and the
    three modes measure over the same facade.
    """
    if corpus not in ("synthetic", "lmeb-s"):
        raise ValueError(
            f"unknown corpus: {corpus!r} (expected 'synthetic' or 'lmeb-s')"
        )
    tmp = Path(tempfile.mkdtemp(prefix="seahorse-readercontext-"))
    db = Path(db_path) if db_path is not None else tmp / "bench.db"
    if corpus == "synthetic":
        facade, storage, episodes, questions, ep_id_to_session = build_synthetic_corpus(db)
    else:
        facade, storage, episodes, questions, ep_id_to_session = build_real_corpus(
            db, subsample=subsample
        )
    try:
        recall, e2e_s, e2e_b, e2e_bb, n_queries, n_episodes, regime = _measure_modes(
            facade,
            episodes,
            questions,
            ep_id_to_session,
            top_k,
            reader=reader,
        )
    finally:
        storage.close()
    return ReaderContextExperimentResult(
        recall_at_k=recall,
        e2e_summary=e2e_s,
        e2e_body=e2e_b,
        e2e_body_bounded=e2e_bb,
        n_queries=n_queries,
        n_episodes=n_episodes,
        regime=regime,
    )


def decide_reader_context(result: ReaderContextExperimentResult) -> dict:
    """Apply the decision: hydrate bodies in the answer path or keep summary.

    Returns a decision dict (``decision``, ``flip``, ``reason``,
    ``recall_at_k``, ``e2e_summary``, ``e2e_body``, ``e2e_body_bounded``).
    Invalid (no decision) when the run degraded to ``fallback_g2`` (fail-loud
    honesty). The flip uses the BEST body mode (full or bounded) — the product
    would pick the winning budget.
    """
    if result.regime == _FALLBACK_G2:
        return {
            "decision": "invalid_regime",
            "flip": False,
            "reason": (
                "the run degraded to the listing regime (hybrid retrieval not wired); "
                "the reader-context comparison is not meaningful — re-run with the "
                "embeddings extra"
            ),
            "recall_at_k": result.recall_at_k,
            "e2e_summary": result.e2e_summary,
            "e2e_body": result.e2e_body,
            "e2e_body_bounded": result.e2e_body_bounded,
        }
    best_body = max(result.e2e_body, result.e2e_body_bounded)
    best_mode = "body" if result.e2e_body >= result.e2e_body_bounded else "body_bounded"
    delta = best_body - result.e2e_summary
    if delta >= READER_CONTEXT_DELTA_PP:
        return {
            "decision": "hydrate_body",
            "flip": True,
            "reason": (
                f"hydrating the FULL body recovers {delta * 100:.1f}pp more "
                f"end-to-end accuracy than summary ({result.e2e_summary:.3f} -> "
                f"{best_body:.3f}, best mode {best_mode}) on recall@{READER_CONTEXT_TOP_K} "
                f"{result.recall_at_k:.3f} — the summary-only context was the reader "
                f"bottleneck; hydrate bodies in the product's answer path"
            ),
            "recall_at_k": result.recall_at_k,
            "e2e_summary": result.e2e_summary,
            "e2e_body": result.e2e_body,
            "e2e_body_bounded": result.e2e_body_bounded,
        }
    return {
        "decision": "keep_summary",
        "flip": False,
        "reason": (
            f"hydrating the FULL body does not recover >= "
            f"{READER_CONTEXT_DELTA_PP:.0%} more end-to-end accuracy than summary "
            f"(best body {best_body:.3f} vs summary {result.e2e_summary:.3f}, gap "
            f"{delta * 100:.1f}pp) on recall@{READER_CONTEXT_TOP_K} "
            f"{result.recall_at_k:.3f} — the summary representation is not the "
            f"reader bottleneck; keep summary"
        ),
        "recall_at_k": result.recall_at_k,
        "e2e_summary": result.e2e_summary,
        "e2e_body": result.e2e_body,
        "e2e_body_bounded": result.e2e_body_bounded,
    }


def render_reader_context_report(
    result: ReaderContextExperimentResult, decision: dict
) -> str:
    """Human-readable report for the CLI (metrics + decision)."""
    lines = [
        "# Reader-context A/B experiment: does the FULL body close the reader gap?",
        "",
        f"regime: {result.regime}",
        f"episodes: {result.n_episodes}",
        f"queries: {result.n_queries}",
        f"recall@{READER_CONTEXT_TOP_K} (the ceiling): {result.recall_at_k:.3f}",
        f"end-to-end accuracy (summary): {result.e2e_summary:.3f}",
        f"end-to-end accuracy (body): {result.e2e_body:.3f}",
        f"end-to-end accuracy (body_bounded): {result.e2e_body_bounded:.3f}",
        "",
        "## Decision",
        f"decision: {decision.get('decision')}",
        f"flip: {decision.get('flip')}",
        f"reason: {decision.get('reason', '')}",
    ]
    return "\n".join(lines)


__all__ = [
    "CONTEXT_MODES",
    "READER_CONTEXT_DELTA_PP",
    "READER_CONTEXT_TOP_K",
    "ReaderContextExperimentResult",
    "decide_reader_context",
    "render_reader_context_report",
    "run_reader_context_experiment",
]
