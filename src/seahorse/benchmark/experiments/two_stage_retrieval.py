"""Two-stage session→episode retrieval experiment — does re-ranking within the
golden session raise episode recall?

Falsifies the A4 chain's documented follow-up: episode-level recall@10 (0.533)
lags session-level recall@10 (0.790) — the answer-bearing episode is retrieved
53% of the time but its golden session 79%. ``episode_granularity`` measured a
vector-only within-session re-rank (top-1/3/5 = 0.413/0.685/0.826) and
explicitly deferred the hybrid re-score ("the engine has no session-restricted
recall; the hybrid re-score would fuse the same embedding with BM25"). This
milestone measures that hybrid re-score and decides whether the two-stage
session→episode retrieval is indicated.

Metrics (over the same reproducible 100 subsample, active-now, retrieval-only):

- **session_recall_at_k** — the baseline (reproduces 0.790): any retrieved
  episode from the golden session (denominator = ALL questions).
- **episode_recall_at_k** — the baseline (reproduces 0.533): a localized
  answer-bearing episode in the top-10 (denominator = LOCALIZED questions).
- **within_session_top{m}** — the rank of the answer-bearing episode WITHIN its
  own golden session (m ∈ {1,3,5}), scored by the HYBRID re-rank (vector +
  BM25 RRF over the episode bodies) — the two-stage diagnosis.
- **two_stage_episode_recall_{m}** — the joint rate: golden session retrieved
  AND the answer-bearing episode in the session's top-m (m ∈ {1,3,5}),
  denominator = LOCALIZED questions. The headline metric of the decision.

Decision (``decide_two_stage``), explicit thresholds:

- ``fallback_g2`` regime → ``invalid_regime`` (fail-loud honesty).
- two_stage_episode_recall_5 >= episode_recall_at_k + 0.05 →
  ``two_stage_indicated`` (flip=True — the session's top-5 surfaces the answer
  more often than the global top-10; implement session-restricted recall).
- otherwise → ``two_stage_not_indicated`` (flip=False — the two-stage does not
  beat the baseline; document and close).

The synthetic corpus verifies the harness MECHANICS (no model); the
authoritative decision comes from an LMEB-S run (``--corpus lmeb-s``).
"""

from __future__ import annotations

import re
import tempfile
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from seahorse.benchmark.experiments.end_to_end import (
    EndToEndQuestion,
    build_real_corpus,
)
from seahorse.benchmark.experiments.episode_locator import (
    STATUS_UNLOCALIZED,
    locate_answer_episodes,
)
from seahorse.benchmark.harness.context import batch_body_for
from seahorse.contracts.episode import Episode
from seahorse.facade.errors import PitRecallNotSupportedMVP0
from seahorse.facade.types import Provenance, RememberPayload
from seahorse.retrieval.constants import RRF_K

# The k for the recall@k measurement (harness default).
TWO_STAGE_TOP_K = 10

# The within-session rank cutoffs the experiment reports (m ∈ {1, 3, 5}).
SESSION_TOPS: tuple[int, ...] = (1, 3, 5)

# The two-stage must beat the episode-recall baseline by >= 5pp to be indicated
# (calibrated to the expected +12pp at @5 with the vector-only approximation).
TWO_STAGE_IMPROVEMENT_THRESHOLD = 0.05

# The minimum distinctive answer-fragment length for localization (>= 2 tokens —
# a single shared token is not distinctive).
ANSWER_FRAGMENT_MIN_NGRAM = 2

# The honest detected regime that invalidates a hybrid-regime experiment.
_FALLBACK_G2 = "fallback_g2"

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

Embedder = Callable[[Sequence[str], str], Sequence[Sequence[float]]]


@dataclass(frozen=True)
class TwoStageExperimentResult:
    """The session→episode two-stage picture over one corpus.

    ``session_recall_at_k`` uses ALL questions as denominator (reproduces the
    authoritative 0.790); ``episode_recall_at_k`` and the ``two_stage_*`` rates
    use the LOCALIZED questions (``n_localized``). The invariant
    ``two_stage_episode_recall_m = session_recall × within_session_top_m`` holds
    over the localized denominator (``n_session_hit / n_localized``), which is
    NOT the reported ``session_recall_at_k`` when unlocalized answers exist.
    """

    session_recall_at_k: float
    episode_recall_at_k: float
    within_session_top1: float
    within_session_top3: float
    within_session_top5: float
    two_stage_episode_recall_1: float
    two_stage_episode_recall_3: float
    two_stage_episode_recall_5: float
    n_queries: int
    n_localized: int
    n_session_hit: int
    n_session_miss: int
    n_within_hit_1: int
    n_within_hit_3: int
    n_within_hit_5: int
    regime: str  # hybrid | fallback_g2


def _first_sentence(text: str) -> str:
    """The first sentence of a body (the ``deterministic_extract`` summary)."""
    stripped = text.strip()
    if not stripped:
        return ""
    return stripped.split(".", 1)[0] + "." if "." in stripped else stripped


def _stub_episode(ep_id: str, body: str) -> Episode:
    """A lightweight Episode for the locator (only ``id`` + ``body`` are used)."""
    return Episode(
        id=ep_id,
        created_at=_EPOCH,
        schema_version="1.1",
        provenance={"source_type": "agent", "session_id": ""},
        body=body,
        valid_at=_EPOCH,
    )


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _normalize_tokens(text: str) -> list[str]:
    """Lower-case + strip non-alphanumeric tokens (the BM25 tokenizer)."""
    return [t for t in re.sub(r"[^a-z0-9 ]", "", text.lower()).split() if t]


def _ingest_episodes(facade: Any, episodes: Sequence[Episode]) -> dict[str, str]:
    """Ingest episodes via the facade write path (skip mode) → stored ep_id→session."""
    ep_id_to_session: dict[str, str] = {}
    for ep in episodes:
        result = facade.remember(
            RememberPayload(
                body=ep.body or "",
                by=cast(Provenance, dict(ep.provenance)),
                valid_at=ep.valid_at,
                cognitive_type=ep.cognitive_type,
                title=ep.title,
                summary=ep.summary,
            ),
            skip_extraction=True,
        )
        if result.ep_id is not None:
            ep_id_to_session[result.ep_id] = ep.provenance.get("session_id", "")
    return ep_id_to_session


def _build_synthetic_corpus(
    db_path: Path,
) -> tuple[Any, Any, list[EndToEndQuestion], dict[str, str]]:
    """Deterministic corpus exercising the two-stage mechanics.

    Three cases (9 questions), over one hybrid facade (HashEmbedder):

    - **Case A** (two-stage hit, 3): a 2-episode golden session — the answer
      episode shares the query's distinctive tokens (retrieved) + a decoy that
      shares fewer. The within-session hybrid re-rank puts the answer episode
      top-1 → two-stage hit at k=1,3,5.
    - **Case B** (within-session miss, 3): a 4-episode golden session — a decoy
      that shares the query token strongly (retrieved) + the answer episode that
      shares NONE (outside the top-10). The within-session re-rank puts the
      answer episode 4th → two-stage miss at k=1,3, hit at k=5.
    - **Case C** (session miss, 3): a decoy SESSION shares the query token; the
      golden session shares NONE → the golden session is not retrieved → miss.

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
            id=f"syn-ts-{counter}",
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

    # Case A (3): 2-episode golden session — answer ep shares the query tokens.
    a_countries = ("Avalon", "Borealis", "Cobalt")
    a_answers = ("Amber Ridge", "Blue Vale", "Crimson Gate")
    for i, (country, answer) in enumerate(zip(a_countries, a_answers, strict=True)):
        sid = f"s-ts-a-{i}"
        episodes.append(_ep(sid, f"The capital of {country} is {answer}."))
        episodes.append(_ep(sid, f"The {country} economy grew last quarter."))
        for d in range(5):
            episodes.append(
                _ep(
                    f"s-ts-adist-{i}",
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

    # Case B (3): 4-episode golden session — decoy retrieved, answer ep 4th.
    b_countries = ("Dunmore", "Eldoria", "Feros")
    b_answers = ("Dusk Hollow", "Ember Spire", "Kestrel Peak")
    for i, (country, answer) in enumerate(zip(b_countries, b_answers, strict=True)):
        sid = f"s-ts-b-{i}"
        episodes.append(
            _ep(sid, f"The capital of {country} is disputed by the {country} court.")
        )
        episodes.append(
            _ep(sid, f"The {country} parliament meets in {country} city.")
        )
        episodes.append(
            _ep(sid, f"The {country} river flows through the valley.")
        )
        # The answer-bearing episode shares NO query token (out of the top-10).
        episodes.append(
            _ep(sid, f"The {answer} stands tall on the northern ridge.")
        )
        for d in range(12):
            episodes.append(
                _ep(
                    f"s-ts-bdist-{i}",
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

    # Case C (3): a decoy SESSION shares the query token; the golden session not.
    c_countries = ("Galacia", "Helvet", "Ishtar")
    c_answers = ("Lumen Forge", "Meridian Gate", "Nyx Spire")
    for i, (country, answer) in enumerate(zip(c_countries, c_answers, strict=True)):
        sid = f"s-ts-c-{i}"
        # The golden session shares NO query token (never retrieved).
        episodes.append(
            _ep(sid, f"The {answer} stands tall on the northern ridge.")
        )
        # A decoy session shares the query token (retrieved instead).
        episodes.append(
            _ep(
                f"s-ts-cdecoy-{i}",
                f"The capital of {country} is Held{country}0.",
            )
        )
        for d in range(12):
            episodes.append(
                _ep(
                    f"s-ts-cdist-{i}",
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


def _default_embedder(corpus: str) -> Embedder:
    """The sync embedding seam for within-session ranking.

    Synthetic → the deterministic ``HashEmbedder`` (CI); lmeb-s → the real
    fastembed backend (the authoritative run). Returns a sync callable
    ``(texts, role) -> list[vectors]``.
    """
    model: Any
    if corpus == "synthetic":
        from seahorse.benchmark.experiments.synthetic import HashEmbedder  # lazy

        model = HashEmbedder()
    else:
        from seahorse.embeddings.fastembed_backend import build_fastembed_embedder  # lazy

        model = build_fastembed_embedder()
    from seahorse.embeddings.query_adapter import run_coroutine  # lazy

    def _embed(texts: Sequence[str], role: str) -> Sequence[Sequence[float]]:
        vecs = run_coroutine(model.embed(texts, role))
        return [list(row) for row in vecs]

    return _embed


def _recall_rows(facade: Any, q: EndToEndQuestion, top_k: int):
    """Recall the top-k rows (active-now, the honest PIT fallback mirroring
    ``measure_end_to_end`` — a regime without a PIT axis raises
    ``PitRecallNotSupportedMVP0`` → active-now, never crash the run).

    ``session_boost=False``: the experiment measures the two-stage UPPER BOUND
    (perfect golden-session identification from the pure-RRF baseline). The
    engine's session boost is the AUTOMATIC version (imperfect identification);
    measuring with it active would corrupt the baseline ``session_recall_at_k``
    (the boost re-ranks the top session, changing which sessions surface in the
    top-k). The engine's automatic version is verified separately (the
    authoritative re-run after the fix).
    """
    if q.question_date is not None:
        from seahorse.disclosure.types import PITPoint  # lazy

        try:
            return facade.recall(
                q.query,
                k=top_k,
                pit=PITPoint(kind="state_at", t=q.question_date),
                session_boost=False,
            )
        except PitRecallNotSupportedMVP0:
            return facade.recall(q.query, k=top_k, session_boost=False)
    return facade.recall(q.query, k=top_k, session_boost=False)


def _golden_session_ep_ids(
    golden_session_ids: Sequence[str], session_to_ep_ids: dict[str, list[str]]
) -> list[str]:
    """The stored episode ids of a question's golden sessions (deduped)."""
    seen: list[str] = []
    for sid in golden_session_ids:
        for ep_id in session_to_ep_ids.get(sid, []):
            if ep_id not in seen:
                seen.append(ep_id)
    return seen


def _hybrid_rank_within_session(
    query: str,
    episodes: Sequence[Episode],
    embedder: Embedder,
) -> list[str]:
    """Rank the golden session's episodes by hybrid score (vector + BM25 RRF).

    Approximation of the engine's session-scoped re-score: the engine has no
    session-restricted recall; this mirrors what it would do (body
    representation). Vector = query-vs-body cosine; BM25 = query-token overlap
    with the body; RRF-fused (1/(rrf_k + rank) per source), sorted desc with
    ep_id tie-break. Returns the ranked ep_ids (best first).
    """
    bodies = [ep.body or "" for ep in episodes]
    if not any(bodies):
        return []
    query_vec = embedder([query], "query")[0]
    body_vecs = embedder(bodies, "passage")
    q_tokens = set(_normalize_tokens(query))

    vector_ranked = sorted(
        (
            (ep.id, _cosine(query_vec, body_vecs[i]))
            for i, ep in enumerate(episodes)
            if ep.body
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )
    bm25_ranked = sorted(
        (
            (
                ep.id,
                float(
                    sum(1 for t in _normalize_tokens(ep.body or "") if t in q_tokens)
                ),
            )
            for ep in episodes
            if ep.body
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )
    scores: dict[str, float] = defaultdict(float)
    for ranked in (vector_ranked, bm25_ranked):
        for rank, (ep_id, _) in enumerate(ranked, start=1):
            scores[ep_id] += 1.0 / (RRF_K + rank)
    return sorted(scores, key=lambda ep_id: (-scores[ep_id], ep_id))


def _measure_two_stage(
    facade: Any,
    questions: Sequence[EndToEndQuestion],
    ep_id_to_session: dict[str, str],
    top_k: int,
    *,
    embedder: Embedder,
) -> TwoStageExperimentResult:
    """Run the two-stage measurement over one corpus."""
    session_to_ep_ids: dict[str, list[str]] = {}
    for ep_id, sid in ep_id_to_session.items():
        session_to_ep_ids.setdefault(sid, []).append(ep_id)

    session_hits: list[float] = []
    episode_hits: list[float] = []
    n_localized = 0
    n_session_hit = 0
    n_within_hit: dict[int, int] = dict.fromkeys(SESSION_TOPS, 0)
    regime = "hybrid"

    for q in questions:
        rows = _recall_rows(facade, q, top_k)
        if rows and all(r.score == 0.0 for r in rows):
            regime = _FALLBACK_G2
        retrieved_ep_ids = [r.ep_id for r in rows]
        retrieved_sessions = {ep_id_to_session.get(rid, "") for rid in retrieved_ep_ids}
        session_hit = bool(retrieved_sessions & set(q.golden_session_ids))
        session_hits.append(1.0 if session_hit else 0.0)

        # Localize the answer-bearing episode(s) of the golden session.
        golden_ep_ids = _golden_session_ep_ids(q.golden_session_ids, session_to_ep_ids)
        bodies = batch_body_for(facade, golden_ep_ids)
        stubs = [_stub_episode(eid, bodies[eid]) for eid in golden_ep_ids if eid in bodies]
        loc = locate_answer_episodes(q.golden_answer, stubs)
        if loc.status == STATUS_UNLOCALIZED:
            continue
        n_localized += 1
        episode_hits.append(
            1.0 if set(retrieved_ep_ids) & set(loc.answer_ep_ids) else 0.0
        )
        if session_hit:
            n_session_hit += 1
            ranked = _hybrid_rank_within_session(q.query, stubs, embedder)
            if ranked:
                answer_ids = set(loc.answer_ep_ids)
                within_rank = next(
                    (i + 1 for i, ep_id in enumerate(ranked) if ep_id in answer_ids),
                    None,
                )
                if within_rank is not None:
                    for m in SESSION_TOPS:
                        if within_rank <= m:
                            n_within_hit[m] += 1

    def _rate(values: Sequence[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return TwoStageExperimentResult(
        session_recall_at_k=_rate(session_hits),
        episode_recall_at_k=_rate(episode_hits),
        within_session_top1=(
            n_within_hit[1] / n_session_hit if n_session_hit else 0.0
        ),
        within_session_top3=(
            n_within_hit[3] / n_session_hit if n_session_hit else 0.0
        ),
        within_session_top5=(
            n_within_hit[5] / n_session_hit if n_session_hit else 0.0
        ),
        two_stage_episode_recall_1=(
            n_within_hit[1] / n_localized if n_localized else 0.0
        ),
        two_stage_episode_recall_3=(
            n_within_hit[3] / n_localized if n_localized else 0.0
        ),
        two_stage_episode_recall_5=(
            n_within_hit[5] / n_localized if n_localized else 0.0
        ),
        n_queries=len(questions),
        n_localized=n_localized,
        n_session_hit=n_session_hit,
        n_session_miss=n_localized - n_session_hit,
        n_within_hit_1=n_within_hit[1],
        n_within_hit_3=n_within_hit[3],
        n_within_hit_5=n_within_hit[5],
        regime=regime,
    )


def run_two_stage_experiment(
    *,
    corpus: str = "synthetic",
    db_path: Path | str | None = None,
    top_k: int = TWO_STAGE_TOP_K,
    subsample: bool = True,
    embedder: Embedder | None = None,
) -> TwoStageExperimentResult:
    """Run the two-stage measurement and return the result.

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
    tmp = Path(tempfile.mkdtemp(prefix="seahorse-twostage-"))
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
        return _measure_two_stage(
            facade, questions, ep_id_to_session, top_k, embedder=embedder
        )
    finally:
        storage.close()


def decide_two_stage(result: TwoStageExperimentResult) -> dict:
    """Apply the decision: is the two-stage session→episode retrieval indicated?

    Returns a decision dict (``decision``, ``flip``, ``reason``,
    ``session_recall_at_k``, ``episode_recall_at_k``,
    ``two_stage_episode_recall_3``, ``two_stage_episode_recall_5``). Invalid
    (no decision) when the run degraded to ``fallback_g2`` (fail-loud honesty).
    The flip is True ONLY for ``two_stage_indicated`` — the session's top-5
    (hybrid re-ranked) surfaces the answer-bearing episode more often than the
    global top-10; the fix is session-restricted recall in the engine.
    """
    if result.regime == _FALLBACK_G2:
        return {
            "decision": "invalid_regime",
            "flip": False,
            "reason": (
                "the run degraded to the listing regime (hybrid retrieval not wired); "
                "the two-stage measurement is not meaningful — re-run with the "
                "embeddings extra"
            ),
            "session_recall_at_k": result.session_recall_at_k,
            "episode_recall_at_k": result.episode_recall_at_k,
            "two_stage_episode_recall_3": result.two_stage_episode_recall_3,
            "two_stage_episode_recall_5": result.two_stage_episode_recall_5,
        }
    if (
        result.two_stage_episode_recall_5
        >= result.episode_recall_at_k + TWO_STAGE_IMPROVEMENT_THRESHOLD
    ):
        return {
            "decision": "two_stage_indicated",
            "flip": True,
            "reason": (
                f"two-stage episode recall@5 {result.two_stage_episode_recall_5:.3f} "
                f">= episode recall@{TWO_STAGE_TOP_K} {result.episode_recall_at_k:.3f} "
                f"+ {TWO_STAGE_IMPROVEMENT_THRESHOLD:.0%} — the golden session's "
                f"top-5 (hybrid re-ranked) surfaces the answer-bearing episode more "
                f"often than the global top-10; implement session-restricted "
                f"two-stage recall in the engine"
            ),
            "session_recall_at_k": result.session_recall_at_k,
            "episode_recall_at_k": result.episode_recall_at_k,
            "two_stage_episode_recall_3": result.two_stage_episode_recall_3,
            "two_stage_episode_recall_5": result.two_stage_episode_recall_5,
        }
    return {
        "decision": "two_stage_not_indicated",
        "flip": False,
        "reason": (
            f"two-stage episode recall@5 {result.two_stage_episode_recall_5:.3f} "
            f"< episode recall@{TWO_STAGE_TOP_K} {result.episode_recall_at_k:.3f} "
            f"+ {TWO_STAGE_IMPROVEMENT_THRESHOLD:.0%} — the session's top-5 does "
            f"not beat the global top-10; the two-stage is not indicated — "
            f"document and close"
        ),
        "session_recall_at_k": result.session_recall_at_k,
        "episode_recall_at_k": result.episode_recall_at_k,
        "two_stage_episode_recall_3": result.two_stage_episode_recall_3,
        "two_stage_episode_recall_5": result.two_stage_episode_recall_5,
    }


def render_two_stage_report(
    result: TwoStageExperimentResult, decision: dict
) -> str:
    """Human-readable report for the CLI (metrics + two-stage rates + decision)."""
    lines = [
        "# Two-stage session→episode experiment: does within-session re-ranking help?",
        "",
        f"regime: {result.regime}",
        f"queries: {result.n_queries}",
        f"localized: {result.n_localized}",
        f"session recall@{TWO_STAGE_TOP_K}: {result.session_recall_at_k:.3f}",
        f"episode recall@{TWO_STAGE_TOP_K} (baseline): {result.episode_recall_at_k:.3f}",
        f"within-session top-1 (hybrid): {result.within_session_top1:.3f}",
        f"within-session top-3 (hybrid): {result.within_session_top3:.3f}",
        f"within-session top-5 (hybrid): {result.within_session_top5:.3f}",
        f"two-stage episode recall@1: {result.two_stage_episode_recall_1:.3f}",
        f"two-stage episode recall@3: {result.two_stage_episode_recall_3:.3f}",
        f"two-stage episode recall@5: {result.two_stage_episode_recall_5:.3f}",
        f"session hits: {result.n_session_hit} / misses: {result.n_session_miss}",
        "",
        "## Decision",
        f"decision: {decision.get('decision')}",
        f"flip: {decision.get('flip')}",
        f"reason: {decision.get('reason', '')}",
    ]
    return "\n".join(lines)


__all__ = [
    "ANSWER_FRAGMENT_MIN_NGRAM",
    "SESSION_TOPS",
    "TWO_STAGE_IMPROVEMENT_THRESHOLD",
    "TWO_STAGE_TOP_K",
    "TwoStageExperimentResult",
    "decide_two_stage",
    "render_two_stage_report",
    "run_two_stage_experiment",
]
