"""Context-assembly experiment — where does the answer-in-context gap go?

Falsifies the A4 chain's final suspect: the ~15pp gap between episode-level
recall@10 (0.533) and answer-in-context (0.350) on the LMEB-S subsample. The
answer-in-context metric ALREADY uses full hydrated bodies (``mode="body"``),
so the gap is NOT representation — it is the distance between *the answer-
bearing episode being retrieved* and *a distinctive answer fragment reaching
the assembled context*.

This milestone decomposes that gap into disjoint per-query buckets:

- ``context_hit``        — a >= 2-token answer fragment is in the top-k body
  context (the assembler works).
- ``hydration_failure``  — the answer-bearing episode IS in the top-10 but its
  body is absent from the context (the assembler is defective).
- ``retrieval_miss``     — the answer-bearing episode is outside the top-10
  (the episode-recall ceiling, already measured at 0.533).
- ``single_token``       — the answer is a single token (never a hit with
  ``min_ngram=2``; a metric ceiling).
- ``unlocalized``        — the answer is derived (no episode states it; a
  metric ceiling).

Decision (``decide_context_assembly``), explicit thresholds in precedence order:

- ``fallback_g2`` regime → ``invalid_regime`` (fail-loud honesty).
- hydration_failure_rate >= 0.10 → ``hydration_bottleneck`` (flip=True — the
  assembler IS defective; fix ``batch_body_for``).
- retrieval_miss_rate >= 0.40 → ``retrieval_ceiling`` (flip=False — the gap is
  the episode-recall ceiling; two-stage session→episode is a follow-up
  candidate, NOT a flip in this milestone).
- metric_ceiling_rate >= 0.15 → ``metric_ceiling`` (flip=False — single_token +
  unlocalized dominate; document and close).
- otherwise → ``context_assembly_ok`` (flip=False).

The synthetic corpus verifies the harness MECHANICS (no model); the
authoritative decision comes from an LMEB-S run (``--corpus lmeb-s``), which
ingests the real haystack and measures the gap decomposition over the
reproducible 100 subsample.
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from seahorse.benchmark.experiments.end_to_end import (
    EndToEndQuestion,
    build_real_corpus,
)
from seahorse.benchmark.experiments.episode_granularity import (
    ANSWER_FRAGMENT_MIN_NGRAM,
)
from seahorse.benchmark.experiments.episode_locator import (
    STATUS_SINGLE_TOKEN,
    STATUS_UNLOCALIZED,
    answer_fragment_present,
    locate_answer_episodes,
)
from seahorse.benchmark.harness.context import assemble_context, batch_body_for
from seahorse.contracts.episode import Episode
from seahorse.facade.errors import PitRecallNotSupportedMVP0
from seahorse.facade.types import Provenance, RememberPayload

# The k for the recall@k measurement (harness default).
CONTEXT_ASSEMBLY_TOP_K = 10

# Decision thresholds (from the milestone prompt, explicit).
HYDRATION_FAILURE_RATE_THRESHOLD = 0.10  # >=10% of localized with a missing body
RETRIEVAL_MISS_RATE_THRESHOLD = 0.40  # >=40% of localized outside the top-10
METRIC_CEILING_RATE_THRESHOLD = 0.15  # >=15% of queries that can never be hits

# The honest detected regime that invalidates a hybrid-regime experiment.
_FALLBACK_G2 = "fallback_g2"

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class ContextAssemblyExperimentResult:
    """The answer-in-context gap decomposition over one corpus.

    ``episode_recall_at_k`` uses the LOCALIZED questions as denominator
    (``n_localized``); ``answer_in_context_rate`` uses ALL questions (reproduces
    the 0.350 headline). ``answer_in_context_summary`` is a REPORTED diagnostic
    (the product's summary-only representation) — it does NOT participate in
    the classification or the decision (the representation axis is already
    decided by ``reader_context``). The disjoint-bucket invariant holds:
    ``n_context_hit + n_hydration_failure + n_retrieval_miss + n_single_token
    + n_unlocalized == n_queries``.
    """

    episode_recall_at_k: float
    answer_in_context_rate: float
    answer_in_context_summary: float
    n_queries: int
    n_episodes: int  # stored episodes (the retrieval universe)
    n_localized: int
    n_unlocalized: int
    n_verbatim: int
    n_fragment: int
    n_single_token: int
    n_context_hit: int
    n_hydration_failure: int
    n_retrieval_miss: int
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
    """Deterministic corpus exercising the context-assembly mechanics.

    Three cases (9 questions), over one hybrid facade (HashEmbedder):

    - **Case A** (context_hit, 3): a 1-episode golden session that shares the
      query's distinctive token AND contains a >= 2-token answer fragment →
      retrieved + hydrated → the fragment reaches the body context.
    - **Case B** (retrieval_miss, 4): a 2-episode golden session — a decoy that
      shares the query token (retrieved) + the answer-bearing episode that
      shares NONE (outside the top-10) → the fragment never reaches the context.
    - **Case C** (single_token, 2): an episode that shares the query token but
      the answer is a single token → a metric ceiling (never a 2-gram hit).

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
            id=f"syn-ca-{counter}",
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

    # Case A (3): 1-episode golden session, answer ep shares the query token
    # AND carries a >= 2-token fragment -> retrieved + hydrated -> context hit.
    a_countries = ("Avalon", "Borealis", "Cobalt")
    a_answers = ("Amber Ridge", "Blue Vale", "Crimson Gate")
    for i, (country, answer) in enumerate(zip(a_countries, a_answers, strict=True)):
        sid = f"s-ca-a-{i}"
        episodes.append(_ep(sid, f"The capital of {country} is {answer}."))
        for d in range(5):
            episodes.append(
                _ep(
                    f"s-ca-adist-{i}",
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

    # Case B (4): 2-episode golden session — decoy retrieved, answer ep not.
    b_countries = ("Dunmore", "Eldoria", "Feros", "Galacia")
    b_answers = (
        "Dusk Hollow",
        "Ember Spire",
        "Kestrel Peak",
        "Lumen Forge",
    )
    for i, (country, answer) in enumerate(zip(b_countries, b_answers, strict=True)):
        sid = f"s-ca-b-{i}"
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
                    f"s-ca-bdist-{i}",
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

    # Case C (2): 1-episode golden session, single-token answer (metric ceiling).
    c_countries = ("Helvet", "Ishtar")
    c_answers = ("Nyx", "Odin")
    for i, (country, answer) in enumerate(zip(c_countries, c_answers, strict=True)):
        sid = f"s-ca-c-{i}"
        episodes.append(_ep(sid, f"The capital of {country} is {answer}."))
        for d in range(5):
            episodes.append(
                _ep(
                    f"s-ca-cdist-{i}",
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


def _recall_rows(facade: Any, q: EndToEndQuestion, top_k: int):
    """Recall the top-k rows (active-now, the honest PIT fallback mirroring
    ``measure_end_to_end`` — a regime without a PIT axis raises
    ``PitRecallNotSupportedMVP0`` → active-now, never crash the run)."""
    if q.question_date is not None:
        from seahorse.disclosure.types import PITPoint  # lazy

        try:
            return facade.recall(
                q.query, k=top_k, pit=PITPoint(kind="state_at", t=q.question_date)
            )
        except PitRecallNotSupportedMVP0:
            return facade.recall(q.query, k=top_k)
    return facade.recall(q.query, k=top_k)


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


def _classify_query(
    *,
    status: str,
    answer_ep_ids: set[str],
    retrieved_ep_ids: Sequence[str],
    context_hit: bool,
) -> str:
    """Classify a query into one disjoint bucket.

    ``context_hit | hydration_failure | retrieval_miss | single_token |
    unlocalized``. The metric-ceiling buckets (``single_token``/``unlocalized``)
    are checked first — they can never be hits with ``min_ngram=2``. Then
    ``context_hit`` (the fragment may come from any retrieved episode), then
    ``hydration_failure`` (the answer-bearing episode IS retrieved but its body
    is absent from the context), then ``retrieval_miss``.
    """
    if status == STATUS_UNLOCALIZED:
        return "unlocalized"
    if status == STATUS_SINGLE_TOKEN:
        return "single_token"
    if context_hit:
        return "context_hit"
    if set(retrieved_ep_ids) & set(answer_ep_ids):
        return "hydration_failure"
    return "retrieval_miss"


def _measure_context_assembly(
    facade: Any,
    questions: Sequence[EndToEndQuestion],
    ep_id_to_session: dict[str, str],
    top_k: int,
) -> ContextAssemblyExperimentResult:
    """Run the gap-decomposition measurement over one corpus."""
    session_to_ep_ids: dict[str, list[str]] = {}
    for ep_id, sid in ep_id_to_session.items():
        session_to_ep_ids.setdefault(sid, []).append(ep_id)

    episode_hits: list[float] = []
    context_hits: list[float] = []
    context_hits_summary: list[float] = []
    n_localized = 0
    n_unlocalized = 0
    n_verbatim = 0
    n_fragment = 0
    n_single_token = 0
    n_context_hit = 0
    n_hydration_failure = 0
    n_retrieval_miss = 0
    regime = "hybrid"

    for q in questions:
        rows = _recall_rows(facade, q, top_k)
        if rows and all(r.score == 0.0 for r in rows):
            regime = _FALLBACK_G2
        retrieved_ep_ids = [r.ep_id for r in rows]

        # Localize the answer-bearing episode(s) of the golden session.
        golden_ep_ids = _golden_session_ep_ids(q.golden_session_ids, session_to_ep_ids)
        bodies = batch_body_for(facade, golden_ep_ids)
        stubs = [_stub_episode(eid, bodies[eid]) for eid in golden_ep_ids if eid in bodies]
        loc = locate_answer_episodes(q.golden_answer, stubs)
        status = loc.status
        if status == STATUS_UNLOCALIZED:
            # No episode states the answer -> no fragment can be in the context.
            n_unlocalized += 1
            context_hits.append(0.0)
            context_hits_summary.append(0.0)
            continue
        n_localized += 1
        if status == "verbatim":
            n_verbatim += 1
        elif status == "fragment":
            n_fragment += 1
        elif status == STATUS_SINGLE_TOKEN:
            n_single_token += 1
        answer_ep_ids = set(loc.answer_ep_ids)
        episode_hits.append(1.0 if set(retrieved_ep_ids) & answer_ep_ids else 0.0)

        # Assemble the top-k context in body mode (the diagnostic) + summary
        # mode (the reported product representation — does NOT decide).
        top_bodies = batch_body_for(facade, retrieved_ep_ids)
        context_body = assemble_context(rows, mode="body", body_for=top_bodies.get)
        context_summary = assemble_context(rows, mode="summary")
        hit_body = answer_fragment_present(
            q.golden_answer, context_body, min_ngram=ANSWER_FRAGMENT_MIN_NGRAM
        )
        hit_summary = answer_fragment_present(
            q.golden_answer, context_summary, min_ngram=ANSWER_FRAGMENT_MIN_NGRAM
        )
        context_hits.append(1.0 if hit_body else 0.0)
        context_hits_summary.append(1.0 if hit_summary else 0.0)

        # Classify the query into one disjoint bucket.
        bucket = _classify_query(
            status=status,
            answer_ep_ids=answer_ep_ids,
            retrieved_ep_ids=retrieved_ep_ids,
            context_hit=hit_body,
        )
        if bucket == "context_hit":
            n_context_hit += 1
        elif bucket == "hydration_failure":
            n_hydration_failure += 1
        elif bucket == "retrieval_miss":
            n_retrieval_miss += 1

    def _rate(values: Sequence[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return ContextAssemblyExperimentResult(
        episode_recall_at_k=_rate(episode_hits),
        answer_in_context_rate=_rate(context_hits),
        answer_in_context_summary=_rate(context_hits_summary),
        n_queries=len(questions),
        n_episodes=len(ep_id_to_session),
        n_localized=n_localized,
        n_unlocalized=n_unlocalized,
        n_verbatim=n_verbatim,
        n_fragment=n_fragment,
        n_single_token=n_single_token,
        n_context_hit=n_context_hit,
        n_hydration_failure=n_hydration_failure,
        n_retrieval_miss=n_retrieval_miss,
        regime=regime,
    )


def run_context_assembly_experiment(
    *,
    corpus: str = "synthetic",
    db_path: Path | str | None = None,
    top_k: int = CONTEXT_ASSEMBLY_TOP_K,
    subsample: bool = True,
) -> ContextAssemblyExperimentResult:
    """Run the context-assembly gap decomposition and return the result.

    ``corpus`` is ``"synthetic"`` (mechanical CI verification) or ``"lmeb-s"``
    (the real corpus, authoritative — the reproducible 100 subsample by default;
    ``subsample=False`` opts into the full-corpus overnight run). ``db_path``
    defaults to a fresh temp DB (reproducible).
    """
    if corpus not in ("synthetic", "lmeb-s"):
        raise ValueError(
            f"unknown corpus: {corpus!r} (expected 'synthetic' or 'lmeb-s')"
        )
    tmp = Path(tempfile.mkdtemp(prefix="seahorse-contextassembly-"))
    db = Path(db_path) if db_path is not None else tmp / "bench.db"
    if corpus == "synthetic":
        facade, storage, questions, ep_id_to_session = _build_synthetic_corpus(db)
    else:
        facade, storage, episodes, questions, ep_id_to_session = build_real_corpus(
            db, subsample=subsample
        )
    try:
        return _measure_context_assembly(
            facade, questions, ep_id_to_session, top_k
        )
    finally:
        storage.close()


def decide_context_assembly(result: ContextAssemblyExperimentResult) -> dict:
    """Apply the decision: is the context ASSEMBLER the bottleneck?

    Returns a decision dict (``decision``, ``flip``, ``reason``,
    ``episode_recall_at_k``, ``answer_in_context_rate``,
    ``hydration_failure_rate``, ``retrieval_miss_rate``,
    ``metric_ceiling_rate``, ``assembly_efficiency``). Invalid (no decision)
    when the run degraded to ``fallback_g2`` (fail-loud honesty). The flip is
    True ONLY for ``hydration_bottleneck`` — the assembler is defective and the
    fix lives in ``batch_body_for`` (harness). The ceiling decisions document
    and close (no invented fix).
    """
    if result.regime == _FALLBACK_G2:
        return {
            "decision": "invalid_regime",
            "flip": False,
            "reason": (
                "the run degraded to the listing regime (hybrid retrieval not wired); "
                "the context-assembly decomposition is not meaningful — re-run with "
                "the embeddings extra"
            ),
            "episode_recall_at_k": result.episode_recall_at_k,
            "answer_in_context_rate": result.answer_in_context_rate,
            "hydration_failure_rate": 0.0,
            "retrieval_miss_rate": 0.0,
            "metric_ceiling_rate": 0.0,
            "assembly_efficiency": 0.0,
        }
    hydration_failure_rate = (
        result.n_hydration_failure / result.n_localized if result.n_localized else 0.0
    )
    retrieval_miss_rate = (
        result.n_retrieval_miss / result.n_localized if result.n_localized else 0.0
    )
    metric_ceiling_rate = (
        (result.n_single_token + result.n_unlocalized) / result.n_queries
        if result.n_queries
        else 0.0
    )
    assembly_efficiency = (
        result.n_context_hit / (result.n_context_hit + result.n_hydration_failure)
        if (result.n_context_hit + result.n_hydration_failure)
        else 0.0
    )
    if hydration_failure_rate >= HYDRATION_FAILURE_RATE_THRESHOLD:
        return {
            "decision": "hydration_bottleneck",
            "flip": True,
            "reason": (
                f"hydration-failure rate {hydration_failure_rate:.3f} >= "
                f"{HYDRATION_FAILURE_RATE_THRESHOLD:.0%} — {result.n_hydration_failure} "
                f"localized answer-bearing episodes ARE in the top-10 but their "
                f"bodies are absent from the assembled context; the assembler IS "
                f"defective — fix ``batch_body_for`` (harness) and re-run"
            ),
            "episode_recall_at_k": result.episode_recall_at_k,
            "answer_in_context_rate": result.answer_in_context_rate,
            "hydration_failure_rate": hydration_failure_rate,
            "retrieval_miss_rate": retrieval_miss_rate,
            "metric_ceiling_rate": metric_ceiling_rate,
            "assembly_efficiency": assembly_efficiency,
        }
    if retrieval_miss_rate >= RETRIEVAL_MISS_RATE_THRESHOLD:
        return {
            "decision": "retrieval_ceiling",
            "flip": False,
            "reason": (
                f"retrieval-miss rate {retrieval_miss_rate:.3f} >= "
                f"{RETRIEVAL_MISS_RATE_THRESHOLD:.0%} — {result.n_retrieval_miss} "
                f"localized answer-bearing episodes are OUTSIDE the top-10; the "
                f"answer-in-context gap is the episode-recall ceiling "
                f"({result.episode_recall_at_k:.3f}), the assembler works "
                f"(assembly efficiency {assembly_efficiency:.3f}); two-stage "
                f"session→episode is a follow-up candidate, NOT a flip in this "
                f"milestone"
            ),
            "episode_recall_at_k": result.episode_recall_at_k,
            "answer_in_context_rate": result.answer_in_context_rate,
            "hydration_failure_rate": hydration_failure_rate,
            "retrieval_miss_rate": retrieval_miss_rate,
            "metric_ceiling_rate": metric_ceiling_rate,
            "assembly_efficiency": assembly_efficiency,
        }
    if metric_ceiling_rate >= METRIC_CEILING_RATE_THRESHOLD:
        return {
            "decision": "metric_ceiling",
            "flip": False,
            "reason": (
                f"metric-ceiling rate {metric_ceiling_rate:.3f} >= "
                f"{METRIC_CEILING_RATE_THRESHOLD:.0%} — {result.n_single_token} "
                f"single-token + {result.n_unlocalized} unlocalized answers can "
                f"never be answer-in-context hits (min_ngram=2); the gap is "
                f"inherent to the metric — document and close"
            ),
            "episode_recall_at_k": result.episode_recall_at_k,
            "answer_in_context_rate": result.answer_in_context_rate,
            "hydration_failure_rate": hydration_failure_rate,
            "retrieval_miss_rate": retrieval_miss_rate,
            "metric_ceiling_rate": metric_ceiling_rate,
            "assembly_efficiency": assembly_efficiency,
        }
    return {
        "decision": "context_assembly_ok",
        "flip": False,
        "reason": (
            f"no dominant cause: hydration-failure rate {hydration_failure_rate:.3f} "
            f"< {HYDRATION_FAILURE_RATE_THRESHOLD:.0%}, retrieval-miss rate "
            f"{retrieval_miss_rate:.3f} < {RETRIEVAL_MISS_RATE_THRESHOLD:.0%}, "
            f"metric-ceiling rate {metric_ceiling_rate:.3f} < "
            f"{METRIC_CEILING_RATE_THRESHOLD:.0%} — the assembler works and the "
            f"gap is small; document and close"
        ),
        "episode_recall_at_k": result.episode_recall_at_k,
        "answer_in_context_rate": result.answer_in_context_rate,
        "hydration_failure_rate": hydration_failure_rate,
        "retrieval_miss_rate": retrieval_miss_rate,
        "metric_ceiling_rate": metric_ceiling_rate,
        "assembly_efficiency": assembly_efficiency,
    }


def render_context_assembly_report(
    result: ContextAssemblyExperimentResult, decision: dict
) -> str:
    """Human-readable report for the CLI (metrics + gap decomposition + decision)."""
    lines = [
        "# Context-assembly experiment: where does the answer-in-context gap go?",
        "",
        f"regime: {result.regime}",
        f"episodes (stored): {result.n_episodes}",
        f"queries: {result.n_queries}",
        f"localized: {result.n_localized} / unlocalized: {result.n_unlocalized}",
        f"localization: verbatim {result.n_verbatim} / fragment {result.n_fragment} "
        f"/ single-token {result.n_single_token}",
        f"episode recall@{CONTEXT_ASSEMBLY_TOP_K}: {result.episode_recall_at_k:.3f}",
        f"answer-in-context rate (body): {result.answer_in_context_rate:.3f}",
        f"answer-in-context rate (summary, reported): {result.answer_in_context_summary:.3f}",
        "",
        "## Gap decomposition (disjoint buckets)",
        f"context hits: {result.n_context_hit}",
        f"hydration failures: {result.n_hydration_failure}",
        f"retrieval misses: {result.n_retrieval_miss}",
        f"single-token (metric ceiling): {result.n_single_token}",
        f"unlocalized (metric ceiling): {result.n_unlocalized}",
        "",
        "## Decision",
        f"decision: {decision.get('decision')}",
        f"flip: {decision.get('flip')}",
        f"reason: {decision.get('reason', '')}",
    ]
    return "\n".join(lines)


__all__ = [
    "CONTEXT_ASSEMBLY_TOP_K",
    "HYDRATION_FAILURE_RATE_THRESHOLD",
    "METRIC_CEILING_RATE_THRESHOLD",
    "RETRIEVAL_MISS_RATE_THRESHOLD",
    "ContextAssemblyExperimentResult",
    "decide_context_assembly",
    "render_context_assembly_report",
    "run_context_assembly_experiment",
]
