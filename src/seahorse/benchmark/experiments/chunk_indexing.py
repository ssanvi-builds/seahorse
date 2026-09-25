"""Chunk-indexing experiment — does per-window indexing lift episode recall?

The P3.2 seam's decision gate. Whole-episode embeddings dilute an answer
fragment across the body's full vocabulary mass (the P3.1 baseline measured
episode-level recall@10 = 0.533 on the v1.5.0 tree); a window concentrated on
the query topic carries it at full weight. This experiment measures BOTH
sides of the pair over the same corpus and applies the keep/revert gates.

Metrics (reusing the ``episode_granularity`` measurement core for
comparability, active-now, retrieval-only):

- **session_level_recall@10** — any retrieved episode from the golden session
  (the control: chunking must not LOSE what flag-off retrieves).
- **episode_level_recall@10** — whether a localized answer-bearing episode is
  in the top-10 (denominator = LOCALIZED questions only).
- **answer_in_context_rate** — does a distinctive answer fragment reach the
  top-k body context?
- **latency_p95_ms** — per-question ``perf_counter`` around the recall call
  ONLY (body hydration and context assembly are excluded: the 250ms promise
  is the base-path INDEX budget), aggregated with the harness's deterministic
  p95.
- **n_chunks / avg_chunks_per_episode** — the vector-surface census: flag-off
  leaves the chunk table EMPTY (0, honest); chunked counts the real windows.

Decision (``decide_chunk_indexing``), explicit gates from the P3 plan:

- keep iff episode_level_recall@10 >= 0.583 (the 0.533 baseline + 0.05) AND
  p95 <= 250ms → adopt the chunked surface (MINOR release).
- else revert (naming the failing gate(s)) — the migration is never published,
  so the revert is clean.
- ``fallback_g2`` regime → ``invalid_regime`` (fail-loud honesty).
- ``chunk_mode="off"`` → ``baseline_measurement`` — the flag-off side of the
  pair carries no keep/revert verdict.

The synthetic corpus verifies the harness MECHANICS (no model): Cases A
(recoverable) and B (session-only) from the granularity corpus, plus Case C —
a long multi-window golden body whose dense early window carries the query
tokens and whose answer lives in a later window. The authoritative decision
comes from the LMEB-S pair run (``--chunk-mode off`` then ``chunked`` over the
same committed tree).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from seahorse.benchmark._tmpdirs import mkdtemp_scoped
from seahorse.benchmark.experiments._shared import (
    BENCH_EPOCH as _EPOCH,
)
from seahorse.benchmark.experiments._shared import (
    FALLBACK_G2 as _FALLBACK_G2,
)
from seahorse.benchmark.experiments._shared import (
    first_sentence as _first_sentence,
)
from seahorse.benchmark.experiments._shared import (
    golden_session_ep_ids as _golden_session_ep_ids,
)
from seahorse.benchmark.experiments._shared import (
    ingest_episodes_session_map as _ingest_episodes,
)
from seahorse.benchmark.experiments._shared import (
    is_fallback_regime,
)
from seahorse.benchmark.experiments._shared import (
    mean_or_zero as _rate,
)
from seahorse.benchmark.experiments._shared import (
    recall_rows as _recall_rows,
)
from seahorse.benchmark.experiments._shared import (
    stub_episode as _stub_episode,
)
from seahorse.benchmark.experiments.end_to_end import (
    EndToEndQuestion,
    build_real_corpus,
)
from seahorse.benchmark.experiments.episode_granularity import (
    ANSWER_FRAGMENT_MIN_NGRAM,
)
from seahorse.benchmark.experiments.episode_locator import (
    STATUS_UNLOCALIZED,
    answer_fragment_present,
    locate_answer_episodes,
)
from seahorse.benchmark.harness.context import assemble_context, batch_body_for
from seahorse.benchmark.metrics import _p95
from seahorse.contracts.episode import Episode
from seahorse.embeddings.types import CHUNK_MODES

# The k for the recall@k measurement (harness default, same as P3.1).
CHUNK_INDEXING_TOP_K = 10

# The P3.1 baseline pin: episode-level recall@10 on the v1.5.0 tree (2026-09-24,
# 45,580 episodes / 100 queries / 92 localized). Changing this is a decision
# about the comparison target, never a refactor.
CHUNK_BASELINE_EPISODE_RECALL = 0.533

# The material-improvement threshold (from the P3 plan): keep requires at least
# baseline + 0.05 — a smaller lift is not worth a doubled vector surface.
CHUNK_IMPROVEMENT_THRESHOLD = 0.05

# The keep gate (baseline + threshold), named explicitly so the comparison is
# float-stable (0.533 + 0.05 is not bit-exact 0.583) and the decision reason
# reads as a pinned threshold, not a derived sum.
CHUNK_KEEP_GATE_EPISODE_RECALL = 0.583

# The p95 latency budget (the base-path INDEX promise, 250ms).
CHUNK_P95_BUDGET_MS = 250


@dataclass(frozen=True)
class ChunkIndexingExperimentResult:
    """One side of the chunk-indexing pair over one corpus.

    ``episode_level_recall_at_k`` and ``answer_in_context_rate`` use the
    LOCALIZED questions as denominator (``n_localized``); the ``n_unlocalized``
    answers are reported separately so a localization miss is never counted as
    a retrieval miss. ``latency_p95_ms`` covers the recall call only (the
    INDEX budget). ``n_chunks`` is the mode-honest census: 0 for flag-off.
    """

    chunk_mode: str
    session_level_recall_at_k: float
    episode_level_recall_at_k: float
    answer_in_context_rate: float
    latency_p95_ms: float
    n_queries: int
    n_episodes: int  # stored episodes (the retrieval universe)
    n_localized: int
    n_unlocalized: int
    n_chunks: int  # indexed chunks (0 when chunk_mode="off")
    avg_chunks_per_episode: float
    regime: str  # hybrid | fallback_g2


def _build_synthetic_corpus(
    db_path: Path,
    chunk_mode: str,
    *,
    with_long_case: bool = True,
) -> tuple[Any, Any, list[EndToEndQuestion], dict[str, str]]:
    """Deterministic corpus exercising the chunk-indexing mechanics.

    Three cases (5 questions each), over one hybrid facade (HashEmbedder):

    - **Case A** (recoverable) — the granularity corpus's single-episode golden
      session: the answer episode shares the query tokens → session AND
      episode recall hit under BOTH modes (the short body is a single chunk —
      the flag-off embedding surface unchanged).
    - **Case B** (session-only) — the granularity corpus's decoy+answer pair:
      session recall hit, episode recall miss under BOTH modes (the control
      that chunking does not change what token-disjoint retrieval misses).
    - **Case C** (long-window) — the multi-chunk fold case: a golden session
      with one SHORT decoy (keeps the session retrievable under both modes)
      and one LONG multi-window body — the dense early window carries the
      query topic at full weight, the answer lives in a later window. Under
      ``chunked`` the kNN over-fetches window vectors and the repository folds
      them into the PARENT once (the multi-chunk→one-hit invariant); the
      answer fragment reaches the context through the whole parent body.

    With the bag-of-tokens ``HashEmbedder``, retrieval PARITY between the two
    modes is the expected mechanical result over this corpus — a repeated
    topic dominates the whole-body vector too, so the flag-off side retrieves
    the long episode as well. The dilution the P3 hypothesis predicts (an
    answer fragment lost across a long body's vocabulary mass) is a
    TRANSFORMER phenomenon the hash double cannot model; the authoritative
    gap measurement is the LMEB-S pair run with the real backend. Verifies the
    MECHANICS (fail-loud honesty): the exact numbers are NOT the science.

    ``with_long_case=False`` drops Case C: the short-body corpus exercises the
    single-chunk invariant (identical embeddings off vs chunked).
    Returns ``(facade, storage, questions, ep_id_to_session)``.
    """
    now = _EPOCH
    episodes: list[Episode] = []
    questions: list[EndToEndQuestion] = []
    counter = 0

    def _ep(session_id: str, body: str) -> Episode:
        nonlocal counter
        ep = Episode(
            id=f"syn-ch-{counter}",
            created_at=now,
            schema_version="1.1",
            provenance={"source_type": "agent", "session_id": session_id},
            body=body,
            summary=_first_sentence(body),
            # valid_at=None: an agent source cannot carry an arbitrary valid_at
            # (the engine's E_VALID_AT_HUMAN_ONLY guard).
        )
        counter += 1
        return ep

    # Case A (5): 1-episode golden session, answer ep shares the query token.
    a_countries = ("Avalon", "Borealis", "Cobalt", "Dunmore", "Eldoria")
    a_answers = (
        "Amber Ridge",
        "Blue Vale",
        "Crimson Gate",
        "Dusk Hollow",
        "Ember Spire",
    )
    for i, (country, answer) in enumerate(zip(a_countries, a_answers, strict=True)):
        sid = f"s-ch-a-{i}"
        episodes.append(_ep(sid, f"The capital of {country} is {answer}."))
        for d in range(5):
            episodes.append(
                _ep(
                    f"s-ch-adist-{i}",
                    f"The capital of {country} is Held{country}{d}.",
                )
            )
        questions.append(
            EndToEndQuestion(
                query=f"capital of {country}",
                golden_answer=answer,
                golden_session_ids=(sid,),
            )
        )

    # Case B (5): 2-episode golden session — decoy retrieved, answer ep not.
    b_countries = ("Feros", "Galacia", "Helvet", "Ishtar", "Jasmin")
    b_answers = (
        "Kestrel Peak",
        "Lumen Forge",
        "Meridian Gate",
        "Nyx Spire",
        "Oleander Run",
    )
    for i, (country, answer) in enumerate(zip(b_countries, b_answers, strict=True)):
        sid = f"s-ch-b-{i}"
        episodes.append(
            _ep(sid, f"The capital of {country} is disputed by the {country} court.")
        )
        # The answer-bearing episode shares NO query token (out of the top-10).
        episodes.append(
            _ep(sid, f"The {answer} stands tall on the northern ridge.")
        )
        for d in range(12):
            episodes.append(
                _ep(
                    f"s-ch-bdist-{i}",
                    f"The capital of {country} is Held{country}{d}.",
                )
            )
        questions.append(
            EndToEndQuestion(
                query=f"capital of {country}",
                golden_answer=answer,
                golden_session_ids=(sid,),
            )
        )

    # Case C (5): long multi-window golden session — the P3 mechanism case.
    if with_long_case:
        c_countries = ("Marrow", "Norvale", "Ostmark", "Pellucid", "Quorum")
        c_answers = (
            "Quarry Deep",
            "Rill Basin",
            "Summit Fold",
            "Tarn Hollow",
            "Umber Reach",
        )
        for i, (country, answer) in enumerate(
            zip(c_countries, c_answers, strict=True)
        ):
            sid = f"s-ch-c-{i}"
            # Short decoy: keeps the golden session retrievable under BOTH
            # modes (session recall is the control, not the signal).
            episodes.append(
                _ep(sid, f"The capital of {country} is Held{country}c.")
            )
            # Long golden body: the dense early window carries the query
            # topic at full weight (a top-1 window vector either way); the
            # filler pushes the answer into a LATER window (~3200 chars →
            # multiple windows at 1200/1080). Under chunked the window hits
            # fold into this parent ONCE (the multi-chunk→one-hit invariant).
            dense = f"capital of {country} " * 70
            filler = " ".join(
                f"Procedural note {j} archives the council minute record {j} "
                f"without further commentary or amendment."
                for j in range(18)
            )
            body = (
                f"{dense.rstrip()} {filler} The final ruling names {answer} "
                f"as the capital of {country}."
            )
            episodes.append(_ep(sid, body))
            for d in range(8):
                episodes.append(
                    _ep(
                        f"s-ch-cdist-{i}",
                        f"The capital of {country} is Held{country}{d}.",
                    )
                )
            questions.append(
                EndToEndQuestion(
                    query=f"capital of {country}",
                    golden_answer=answer,
                    golden_session_ids=(sid,),
                )
            )

    from seahorse.benchmark.experiments.synthetic import HashEmbedder  # lazy
    from seahorse.facade import build_facade  # lazy

    facade, storage = build_facade(
        db_path,
        retrieval_available=True,
        passage_embedder=HashEmbedder(),
        chunk_mode=chunk_mode,
    )
    ep_id_to_session = _ingest_episodes(facade, episodes)
    return facade, storage, questions, ep_id_to_session


def _chunk_census(storage: Any, n_episodes: int) -> tuple[int, float]:
    """The vector-surface census: chunk count + average chunks per episode.

    Honest per-mode value: the flag-off run leaves the chunk table EMPTY
    (count 0, average 0.0); the chunked run counts the real windows. The
    chunked repository is the single reader — the classic surface (one vector
    per episode) has no chunk rows to count.
    """
    from seahorse.persistence.vector_index_chunked import (  # lazy: vec0
        SqliteChunkedVectorIndexRepository,
    )

    repo = SqliteChunkedVectorIndexRepository(storage.connection_manager)
    n_chunks = repo.chunk_count()
    avg = n_chunks / n_episodes if n_episodes else 0.0
    return n_chunks, avg


def _measure_chunk_indexing(
    facade: Any,
    storage: Any,
    questions: Sequence[EndToEndQuestion],
    ep_id_to_session: dict[str, str],
    top_k: int,
    chunk_mode: str,
) -> ChunkIndexingExperimentResult:
    """Run the chunk-indexing measurement (recall rates + latency + census)."""
    session_to_ep_ids: dict[str, list[str]] = {}
    for ep_id, sid in ep_id_to_session.items():
        session_to_ep_ids.setdefault(sid, []).append(ep_id)

    session_hits: list[float] = []
    episode_hits: list[float] = []
    context_hits: list[float] = []
    latencies: list[float] = []
    n_localized = 0
    n_unlocalized = 0
    regime = "hybrid"

    for q in questions:
        # Latency: the recall call ONLY (the 250ms promise is the base-path
        # INDEX budget — body hydration and context assembly are excluded).
        t0 = time.perf_counter()
        rows = _recall_rows(facade, q, top_k)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        if is_fallback_regime(rows):
            regime = _FALLBACK_G2
        retrieved_ep_ids = [r.ep_id for r in rows]
        retrieved_sessions = {ep_id_to_session.get(rid, "") for rid in retrieved_ep_ids}
        session_hits.append(
            1.0 if retrieved_sessions & set(q.golden_session_ids) else 0.0
        )

        # Localize the answer-bearing episode(s) of the golden session.
        golden_ep_ids = _golden_session_ep_ids(q.golden_session_ids, session_to_ep_ids)
        bodies = batch_body_for(facade, golden_ep_ids)
        stubs = [_stub_episode(eid, bodies[eid]) for eid in golden_ep_ids if eid in bodies]
        loc = locate_answer_episodes(q.golden_answer, stubs)
        if loc.status == STATUS_UNLOCALIZED:
            n_unlocalized += 1
        else:
            n_localized += 1
            episode_hits.append(
                1.0 if set(retrieved_ep_ids) & set(loc.answer_ep_ids) else 0.0
            )

        # Diagnostic: does a distinctive answer fragment reach the top-k body
        # context? (the bridge between episode recall and e2e)
        top_bodies = batch_body_for(facade, retrieved_ep_ids)
        context = assemble_context(rows, mode="body", body_for=top_bodies.get)
        context_hits.append(
            1.0
            if answer_fragment_present(
                q.golden_answer, context, min_ngram=ANSWER_FRAGMENT_MIN_NGRAM
            )
            else 0.0
        )

    n_chunks, avg_chunks = _chunk_census(storage, len(ep_id_to_session))
    return ChunkIndexingExperimentResult(
        chunk_mode=chunk_mode,
        session_level_recall_at_k=_rate(session_hits),
        episode_level_recall_at_k=_rate(episode_hits),
        answer_in_context_rate=_rate(context_hits),
        latency_p95_ms=_p95(latencies),
        n_queries=len(questions),
        n_episodes=len(ep_id_to_session),
        n_localized=n_localized,
        n_unlocalized=n_unlocalized,
        n_chunks=n_chunks,
        avg_chunks_per_episode=avg_chunks,
        regime=regime,
    )


def run_chunk_indexing_experiment(
    *,
    corpus: str = "synthetic",
    db_path: Path | str | None = None,
    top_k: int = CHUNK_INDEXING_TOP_K,
    subsample: bool = True,
    chunk_mode: str = "chunked",
) -> ChunkIndexingExperimentResult:
    """Run one side of the chunk-indexing pair and return the result.

    ``corpus`` is ``"synthetic"`` (mechanical CI verification) or ``"lmeb-s"``
    (the real corpus, authoritative — the reproducible 100 subsample by
    default; ``subsample=False`` opts into the full-corpus overnight run).
    ``chunk_mode`` is ``"chunked"`` (the candidate, default) or ``"off"`` (the
    baseline side of the pair — P3.3 runs both over the same committed tree).
    ``db_path`` defaults to a fresh temp DB.
    """
    if corpus not in ("synthetic", "lmeb-s"):
        raise ValueError(
            f"unknown corpus: {corpus!r} (expected 'synthetic' or 'lmeb-s')"
        )
    if chunk_mode not in CHUNK_MODES:
        raise ValueError(
            f"chunk_mode must be one of {CHUNK_MODES!r}, got {chunk_mode!r}"
        )
    tmp = Path(mkdtemp_scoped("seahorse-chunkindexing-"))
    db = Path(db_path) if db_path is not None else tmp / "bench.db"
    if corpus == "synthetic":
        facade, storage, questions, ep_id_to_session = _build_synthetic_corpus(
            db, chunk_mode
        )
    else:
        facade, storage, _episodes, questions, ep_id_to_session = build_real_corpus(
            db, subsample=subsample, chunk_mode=chunk_mode
        )
    try:
        return _measure_chunk_indexing(
            facade, storage, questions, ep_id_to_session, top_k, chunk_mode
        )
    finally:
        storage.close()


def decide_chunk_indexing(result: ChunkIndexingExperimentResult) -> dict:
    """Apply the P3 gates: keep the chunked surface or revert it.

    keep iff episode_level_recall@10 >= baseline+0.05 AND p95 <= 250ms; else
    revert naming the failing gate(s). ``fallback_g2`` → ``invalid_regime``
    (fail-loud honesty). The flag-off side (``chunk_mode="off"``) is the
    baseline measurement — no keep/revert verdict, it exists to be compared.
    """
    echo = {
        "chunk_mode": result.chunk_mode,
        "episode_level_recall_at_k": result.episode_level_recall_at_k,
        "latency_p95_ms": result.latency_p95_ms,
    }
    if result.regime == _FALLBACK_G2:
        return {
            "decision": "invalid_regime",
            "flip": False,
            "reason": (
                "the run degraded to the listing regime (fallback_g2 — hybrid "
                "retrieval not wired) — the chunk-indexing measurement is not "
                "meaningful; re-run with the embeddings extra"
            ),
            **echo,
        }
    if result.chunk_mode != "chunked":
        return {
            "decision": "baseline_measurement",
            "flip": False,
            "reason": (
                "the flag-off run is the baseline side of the pair — no "
                "keep/revert verdict; compare against the chunked run over "
                "the same committed tree"
            ),
            **echo,
        }
    keep_recall = result.episode_level_recall_at_k >= CHUNK_KEEP_GATE_EPISODE_RECALL
    keep_latency = result.latency_p95_ms <= CHUNK_P95_BUDGET_MS
    if keep_recall and keep_latency:
        return {
            "decision": "keep_chunk_indexing",
            "flip": True,
            "reason": (
                f"episode-level recall@{CHUNK_INDEXING_TOP_K} "
                f"{result.episode_level_recall_at_k:.3f} >= "
                f"{CHUNK_KEEP_GATE_EPISODE_RECALL:.3f} (baseline "
                f"{CHUNK_BASELINE_EPISODE_RECALL:.3f} + "
                f"{CHUNK_IMPROVEMENT_THRESHOLD}) AND p95 "
                f"{result.latency_p95_ms:.1f}ms <= {CHUNK_P95_BUDGET_MS}ms — "
                f"adopt the chunked vector surface (MINOR release)"
            ),
            **echo,
        }
    failing: list[str] = []
    if not keep_recall:
        failing.append(
            f"episode-level recall@{CHUNK_INDEXING_TOP_K} "
            f"{result.episode_level_recall_at_k:.3f} < "
            f"{CHUNK_KEEP_GATE_EPISODE_RECALL:.3f}"
        )
    if not keep_latency:
        failing.append(
            f"p95 {result.latency_p95_ms:.1f}ms > {CHUNK_P95_BUDGET_MS}ms"
        )
    both = "both gates failed" if len(failing) == 2 else "gate failed"
    return {
        "decision": "revert_chunk_indexing",
        "flip": False,
        "reason": (
            f"revert — {both}: {' AND '.join(failing)}; revert the "
            f"chunk-indexing commits (migration 013 was never published — no "
            f"user DB carries it) and document the negative result"
        ),
        **echo,
    }


def render_chunk_indexing_report(
    result: ChunkIndexingExperimentResult, decision: dict
) -> str:
    """Human-readable report for the CLI (metrics + census + decision)."""
    lines = [
        "# Chunk-indexing experiment: does per-window indexing lift episode recall?",
        "",
        f"regime: {result.regime}",
        f"chunk mode: {result.chunk_mode}",
        f"episodes (stored): {result.n_episodes}",
        f"chunks (indexed): {result.n_chunks}",
        f"avg chunks per episode: {result.avg_chunks_per_episode:.2f}",
        f"queries: {result.n_queries}",
        f"localized: {result.n_localized} / unlocalized: {result.n_unlocalized}",
        f"session-level recall@{CHUNK_INDEXING_TOP_K}: "
        f"{result.session_level_recall_at_k:.3f}",
        f"episode-level recall@{CHUNK_INDEXING_TOP_K}: "
        f"{result.episode_level_recall_at_k:.3f}",
        f"answer-in-context rate: {result.answer_in_context_rate:.3f}",
        f"latency p95 (recall only): {result.latency_p95_ms:.1f}ms",
        "",
        "## Decision",
        f"decision: {decision.get('decision')}",
        f"flip: {decision.get('flip')}",
        f"reason: {decision.get('reason', '')}",
    ]
    return "\n".join(lines)


__all__ = [
    "CHUNK_BASELINE_EPISODE_RECALL",
    "CHUNK_IMPROVEMENT_THRESHOLD",
    "CHUNK_INDEXING_TOP_K",
    "CHUNK_KEEP_GATE_EPISODE_RECALL",
    "CHUNK_P95_BUDGET_MS",
    "ChunkIndexingExperimentResult",
    "decide_chunk_indexing",
    "render_chunk_indexing_report",
    "run_chunk_indexing_experiment",
]