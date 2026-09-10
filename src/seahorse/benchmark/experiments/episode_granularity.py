"""Episode-granularity experiment — is the answer-bearing episode retrievable?

Falsifies the A4 chain's remaining suspect: the ~70pp gap between session-level
recall@10 (0.790) and end-to-end accuracy (0.070–0.090). ``reader_context``
already ruled out the context representation (body recovers 2.0pp < 10pp flip
threshold); this milestone asks whether the **answer-bearing episode** — not
just its golden session — reaches the top-k. If it does not, no context
representation or reader can help.

Metrics (over the same reproducible 100 subsample, active-now, retrieval-only):

- **session_level_recall@10** — the baseline (reproduces 0.790): any retrieved
  episode from the golden session.
- **episode_level_recall@10** — whether a localized answer-bearing episode is in
  the top-10 (denominator = LOCALIZED questions only; the unlocalized derived
  answers are reported separately, never as retrieval misses).
- **within_session_top{m}** — the rank of the answer-bearing episode WITHIN its
  own golden session (m ∈ {1,3,5}), scored by embedding the query against the
  session's episode bodies — the two-stage-retrieval diagnosis. A vector-only
  approximation of the hybrid re-scoring (caveat documented).
- **answer_in_context_rate** — does a distinctive answer fragment (>= 2 tokens)
  reach the top-k body context? The bridge between episode recall and e2e
  (paraphrase answers mean exact matching underestimates).

Decision (``decide_episode_granularity``), explicit thresholds:

- episode_level_recall@10 >= 0.5 → ``reader_bottleneck`` (the episode IS
  retrieved; the reader is the blocker → reader-quality follow-up).
- < 0.5 AND within_session_top3 >= 0.5 → ``two_stage_retrieval`` (the episode
  is retrievable inside its session but not globally → the session→episode fix).
- < 0.5 AND within_session_top3 < 0.5 → ``episode_not_retrievable`` (not even
  inside its session → investigate).
- ``fallback_g2`` regime → ``invalid_regime`` (fail-loud honesty).

The synthetic corpus verifies the harness MECHANICS (no model); the
authoritative decision comes from an LMEB-S run (``--corpus lmeb-s``), which
ingests the real haystack and embeds the golden-session bodies with the real
fastembed backend.
"""

from __future__ import annotations

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
    Embedder,
    is_fallback_regime,
)
from seahorse.benchmark.experiments._shared import (
    cosine as _cosine,
)
from seahorse.benchmark.experiments._shared import (
    default_embedder as _default_embedder,
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
from seahorse.benchmark.experiments.episode_locator import (
    STATUS_UNLOCALIZED,
    answer_fragment_present,
    locate_answer_episodes,
)
from seahorse.benchmark.harness.context import assemble_context, batch_body_for
from seahorse.contracts.episode import Episode

# The k for the recall@k measurement (harness default).
EPISODE_GRANULARITY_TOP_K = 10

# Decision thresholds (from the milestone prompt, explicit).
EPISODE_LEVEL_RECALL_THRESHOLD = 0.5
WITHIN_SESSION_RANK_THRESHOLD = 0.5

# The within-session rank cutoffs the experiment reports (m ∈ {1, 3, 5}).
WITHIN_SESSION_TOPS: tuple[int, ...] = (1, 3, 5)

# The minimum distinctive answer-fragment length for answer_in_context (>= 2
# tokens — a single shared token is not distinctive). Single source: this
# module owns the constant; context_assembly and two_stage_retrieval import it.
ANSWER_FRAGMENT_MIN_NGRAM = 2


@dataclass(frozen=True)
class EpisodeGranularityExperimentResult:
    """The session- vs episode-level recall picture over one corpus.

    ``episode_level_recall_at_k`` and the ``within_session_top*`` rates use the
    LOCALIZED questions as denominator (``n_localized``); the ``n_unlocalized``
    answers (derived numbers, no episode states them) are reported separately
    so a localization miss is never counted as a retrieval miss.
    """

    session_level_recall_at_k: float
    episode_level_recall_at_k: float
    within_session_top1: float
    within_session_top3: float
    within_session_top5: float
    answer_in_context_rate: float
    n_queries: int
    n_episodes: int  # stored episodes (the retrieval universe)
    n_localized: int
    n_unlocalized: int
    regime: str  # hybrid | fallback_g2


def _build_synthetic_corpus(
    db_path: Path,
) -> tuple[Any, Any, list[EndToEndQuestion], dict[str, str]]:
    """Deterministic corpus exercising the episode-granularity mechanics.

    Two cases (5 questions each), over one hybrid facade (HashEmbedder):

    - **Case A** (recoverable): the golden session has a SINGLE episode that
      shares the query's distinctive token AND contains the answer → session AND
      episode recall hit, within-session rank 1.
    - **Case B** (session-only): the golden session has TWO episodes — a decoy
      that shares the query token (retrieved → session hit) + the answer-bearing
      episode that shares NONE (out of the top-10) → session recall hit,
      episode recall miss, within-session rank 2.

    Verifies the MECHANICS (fail-loud honesty): the exact numbers are NOT the
    science. Returns ``(facade, storage, questions, ep_id_to_session)``.
    """
    now = _EPOCH
    episodes: list[Episode] = []
    questions: list[EndToEndQuestion] = []
    counter = 0

    def _ep(session_id: str, body: str) -> Episode:
        nonlocal counter
        ep = Episode(
            id=f"syn-eg-{counter}",
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
        sid = f"s-eg-a-{i}"
        episodes.append(_ep(sid, f"The capital of {country} is {answer}."))
        for d in range(5):
            episodes.append(
                _ep(
                    f"s-eg-adist-{i}",
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
        sid = f"s-eg-b-{i}"
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
                    f"s-eg-bdist-{i}",
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
        db_path, retrieval_available=True, passage_embedder=HashEmbedder()
    )
    ep_id_to_session = _ingest_episodes(facade, episodes)
    return facade, storage, questions, ep_id_to_session


def _answer_rank_within_session(
    query: str,
    episodes: Sequence[Episode],
    answer_ep_ids: set[str],
    embedder: Embedder,
) -> int | None:
    """1-based rank of the best answer-bearing episode among the golden session's
    episodes, scored by query-vs-body embedding similarity.

    Returns None when no golden body is embeddable or no answer-bearing episode
    has a body. Vector-only approximation of the two-stage re-scoring (the
    engine has no session-restricted recall; the hybrid re-score would fuse the
    same embedding with BM25).
    """
    bodies = [ep.body or "" for ep in episodes]
    if not any(bodies):
        return None
    query_vec = embedder([query], "query")[0]
    body_vecs = embedder(bodies, "passage")
    scored = [
        (ep.id, _cosine(query_vec, body_vecs[i]))
        for i, ep in enumerate(episodes)
        if ep.body
    ]
    if not scored:
        return None
    ranked = sorted(scored, key=lambda pair: pair[1], reverse=True)
    best_index = next(
        (i for i, (ep_id, _) in enumerate(ranked) if ep_id in answer_ep_ids),
        None,
    )
    return None if best_index is None else best_index + 1


def _measure_episode_granularity(
    facade: Any,
    questions: Sequence[EndToEndQuestion],
    ep_id_to_session: dict[str, str],
    top_k: int,
    *,
    embedder: Embedder,
) -> EpisodeGranularityExperimentResult:
    """Run the three-granularity measurement over one corpus."""
    session_to_ep_ids: dict[str, list[str]] = {}
    for ep_id, sid in ep_id_to_session.items():
        session_to_ep_ids.setdefault(sid, []).append(ep_id)

    session_hits: list[float] = []
    episode_hits: list[float] = []
    top_hits: dict[int, list[float]] = {m: [] for m in WITHIN_SESSION_TOPS}
    context_hits: list[float] = []
    n_localized = 0
    n_unlocalized = 0
    regime = "hybrid"

    for q in questions:
        rows = _recall_rows(facade, q, top_k)
        if is_fallback_regime(rows):
            regime = _FALLBACK_G2
        retrieved_ep_ids = [r.ep_id for r in rows]
        retrieved_sessions = {ep_id_to_session.get(rid, "") for rid in retrieved_ep_ids}
        session_hits.append(1.0 if retrieved_sessions & set(q.golden_session_ids) else 0.0)

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
            rank = _answer_rank_within_session(
                q.query, stubs, set(loc.answer_ep_ids), embedder
            )
            if rank is not None:
                for m in WITHIN_SESSION_TOPS:
                    top_hits[m].append(1.0 if rank <= m else 0.0)

        # Diagnostic: does a distinctive answer fragment reach the top-k body context?
        top_bodies = batch_body_for(facade, retrieved_ep_ids)
        context = assemble_context(rows, mode="body", body_for=top_bodies.get)
        context_hits.append(
            1.0
            if answer_fragment_present(
                q.golden_answer, context, min_ngram=ANSWER_FRAGMENT_MIN_NGRAM
            )
            else 0.0
        )

    return EpisodeGranularityExperimentResult(
        session_level_recall_at_k=_rate(session_hits),
        episode_level_recall_at_k=_rate(episode_hits),
        within_session_top1=_rate(top_hits[1]),
        within_session_top3=_rate(top_hits[3]),
        within_session_top5=_rate(top_hits[5]),
        answer_in_context_rate=_rate(context_hits),
        n_queries=len(questions),
        n_episodes=len(ep_id_to_session),
        n_localized=n_localized,
        n_unlocalized=n_unlocalized,
        regime=regime,
    )


def run_episode_granularity_experiment(
    *,
    corpus: str = "synthetic",
    db_path: Path | str | None = None,
    top_k: int = EPISODE_GRANULARITY_TOP_K,
    subsample: bool = True,
    embedder: Embedder | None = None,
) -> EpisodeGranularityExperimentResult:
    """Run the episode-granularity measurement and return the result.

    ``corpus`` is ``"synthetic"`` (mechanical CI verification) or ``"lmeb-s"``
    (the real corpus, authoritative — the reproducible 100 subsample by default;
    ``subsample=False`` opts into the full-corpus overnight run). ``db_path``
    defaults to a fresh temp DB. The within-session embedder defaults to the
    deterministic ``HashEmbedder`` (synthetic) or the real fastembed backend
    (lmeb-s); ``embedder`` injects a sync ``(texts, role) -> vectors`` for tests.
    """
    if corpus not in ("synthetic", "lmeb-s"):
        raise ValueError(
            f"unknown corpus: {corpus!r} (expected 'synthetic' or 'lmeb-s')"
        )
    tmp = Path(mkdtemp_scoped("seahorse-egranularity-"))
    db = Path(db_path) if db_path is not None else tmp / "bench.db"
    if corpus == "synthetic":
        facade, storage, questions, ep_id_to_session = _build_synthetic_corpus(db)
    else:
        facade, storage, episodes, questions, ep_id_to_session = build_real_corpus(
            db, subsample=subsample
        )
    if embedder is None:
        embedder = _default_embedder(corpus)
    try:
        return _measure_episode_granularity(
            facade, questions, ep_id_to_session, top_k, embedder=embedder
        )
    finally:
        storage.close()


def decide_episode_granularity(result: EpisodeGranularityExperimentResult) -> dict:
    """Apply the decision: reader, two-stage retrieval, or episode-not-retrievable.

    Returns a decision dict (``decision``, ``flip``, ``reason``,
    ``session_level_recall_at_k``, ``episode_level_recall_at_k``,
    ``within_session_top3``, ``answer_in_context_rate``). Invalid (no decision)
    when the run degraded to ``fallback_g2`` (fail-loud honesty).
    """
    if result.regime == _FALLBACK_G2:
        return {
            "decision": "invalid_regime",
            "flip": False,
            "reason": (
                "the run degraded to the listing regime (hybrid retrieval not wired); "
                "the episode-granularity measurement is not meaningful — re-run "
                "with the embeddings extra"
            ),
            "session_level_recall_at_k": result.session_level_recall_at_k,
            "episode_level_recall_at_k": result.episode_level_recall_at_k,
            "within_session_top3": result.within_session_top3,
            "answer_in_context_rate": result.answer_in_context_rate,
        }
    if result.episode_level_recall_at_k >= EPISODE_LEVEL_RECALL_THRESHOLD:
        return {
            "decision": "reader_bottleneck",
            "flip": False,
            "reason": (
                f"episode-level recall@{EPISODE_GRANULARITY_TOP_K} "
                f"{result.episode_level_recall_at_k:.3f} >= "
                f"{EPISODE_LEVEL_RECALL_THRESHOLD:.0%} — the answer-bearing episode "
                f"IS retrieved; the reader is the bottleneck (follow-up: reader "
                f"quality, e.g. a stronger extractor)"
            ),
            "session_level_recall_at_k": result.session_level_recall_at_k,
            "episode_level_recall_at_k": result.episode_level_recall_at_k,
            "within_session_top3": result.within_session_top3,
            "answer_in_context_rate": result.answer_in_context_rate,
        }
    if result.within_session_top3 >= WITHIN_SESSION_RANK_THRESHOLD:
        return {
            "decision": "two_stage_retrieval",
            "flip": True,
            "reason": (
                f"episode-level recall@{EPISODE_GRANULARITY_TOP_K} "
                f"{result.episode_level_recall_at_k:.3f} < {EPISODE_LEVEL_RECALL_THRESHOLD:.0%} "
                f"BUT within-session top-3 {result.within_session_top3:.3f} >= "
                f"{WITHIN_SESSION_RANK_THRESHOLD:.0%} — the episode is retrievable "
                f"inside its session but not globally; apply the two-stage "
                f"retrieval fix (session -> episode)"
            ),
            "session_level_recall_at_k": result.session_level_recall_at_k,
            "episode_level_recall_at_k": result.episode_level_recall_at_k,
            "within_session_top3": result.within_session_top3,
            "answer_in_context_rate": result.answer_in_context_rate,
        }
    return {
        "decision": "episode_not_retrievable",
        "flip": False,
        "reason": (
            f"episode-level recall@{EPISODE_GRANULARITY_TOP_K} "
            f"{result.episode_level_recall_at_k:.3f} < {EPISODE_LEVEL_RECALL_THRESHOLD:.0%} "
            f"&& within-session top-3 {result.within_session_top3:.3f} < "
            f"{WITHIN_SESSION_RANK_THRESHOLD:.0%} — the answer-bearing episode is "
            f"not retrievable even within its session; investigate (is the answer "
            f"split across episodes? does the episode contain the answer?)"
        ),
        "session_level_recall_at_k": result.session_level_recall_at_k,
        "episode_level_recall_at_k": result.episode_level_recall_at_k,
        "within_session_top3": result.within_session_top3,
        "answer_in_context_rate": result.answer_in_context_rate,
    }


def render_episode_granularity_report(
    result: EpisodeGranularityExperimentResult, decision: dict
) -> str:
    """Human-readable report for the CLI (metrics + heuristics + decision)."""
    lines = [
        "# Episode-granularity experiment: is the answer-bearing episode retrievable?",
        "",
        f"regime: {result.regime}",
        f"episodes (stored): {result.n_episodes}",
        f"queries: {result.n_queries}",
        f"localized: {result.n_localized} / unlocalized: {result.n_unlocalized}",
        f"session-level recall@{EPISODE_GRANULARITY_TOP_K}: {result.session_level_recall_at_k:.3f}",
        f"episode-level recall@{EPISODE_GRANULARITY_TOP_K}: {result.episode_level_recall_at_k:.3f}",
        f"within-session top-1: {result.within_session_top1:.3f}",
        f"within-session top-3: {result.within_session_top3:.3f}",
        f"within-session top-5: {result.within_session_top5:.3f}",
        f"answer-in-context rate: {result.answer_in_context_rate:.3f}",
        "",
        "## Decision",
        f"decision: {decision.get('decision')}",
        f"flip: {decision.get('flip')}",
        f"reason: {decision.get('reason', '')}",
    ]
    return "\n".join(lines)


__all__ = [
    "ANSWER_FRAGMENT_MIN_NGRAM",
    "EPISODE_GRANULARITY_TOP_K",
    "EPISODE_LEVEL_RECALL_THRESHOLD",
    "WITHIN_SESSION_RANK_THRESHOLD",
    "WITHIN_SESSION_TOPS",
    "EpisodeGranularityExperimentResult",
    "decide_episode_granularity",
    "render_episode_granularity_report",
    "run_episode_granularity_experiment",
]
