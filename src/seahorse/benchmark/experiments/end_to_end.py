"""End-to-end measurement experiment (A4) — the reader's real value.

Falsifies the hypothesis "retrieval quality is a hard ceiling on the product's
end-to-end value". The reader LLM only sees the top-10 retrieved rows
(summaries/subjects), so recall@10 is an UPPER BOUND on end-to-end accuracy: if
the golden session is absent from the top-10, the reader cannot answer. This
experiment measures the end-to-end number (reader answer vs golden answer) that
is 0.0 by design in retrieval-only mode.

Metrics:
- **recall@10**: whether the golden session is in the top-10 (the ceiling).
- **end-to-end accuracy**: whether the reader's answer matches the golden answer
  (normalized substring match; abstention questions count correct when the
  reader abstains on an empty context).

The reader is a deterministic EXTRACTIVE double (``ExtractiveReader``): it
returns the context line with the most query-token overlap — a stand-in for the
real reader LLM (``ReaderLLMClient``) that verifies the MECHANICS without an
Ollama call. The authoritative end-to-end number comes from an LMEB-S run with
the real reader (``--corpus lmeb-s``), which ingests the real haystack and
measures SESSION-level recall (any retrieved episode from the golden session —
LMEB answers live in sessions, not a single turn) over the reproducible 100
subsample. The PIT path (``state_at``) degrades to active-now when the facade
does not support it (``PitRecallNotSupportedMVP0``), mirroring the SUT's honest
fallback.

Decision (``decide_end_to_end``): informational — reports the end-to-end value
and the ceiling gap (``recall@10 - end_to_end_accuracy``). A large gap means the
reader is the bottleneck (retrieval recovers the session but the reader cannot
extract the answer); a small gap means retrieval is the bottleneck (the session
is absent). No flip — the decision establishes the baseline the product must
move.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from seahorse.benchmark._tmpdirs import mkdtemp_scoped
from seahorse.benchmark.experiments.lmeb_corpus import (
    build_real_facade,
    ingest_haystack,
    load_lmeb_subsample,
)
from seahorse.benchmark.harness.context import (
    ContextMode,
    assemble_context,
    batch_body_for,
)
from seahorse.contracts.episode import Episode
from seahorse.facade import build_facade
from seahorse.facade.errors import PitRecallNotSupportedMVP0
from seahorse.facade.types import Provenance, RememberPayload

# The k for the recall@k measurement (harness default).
END_TO_END_TOP_K = 10

# Abstention golden answers: the reader must abstain (empty context -> empty
# answer) for these to count correct.
_ABSTENTION_ANSWERS = {"no", "none", "n/a", "not available"}

# The honest detected regime that invalidates a hybrid-regime experiment.
_FALLBACK_G2 = "fallback_g2"


@dataclass(frozen=True)
class EndToEndQuestion:
    """A full-pipeline probe: the query + the golden answer + the PIT date."""

    query: str
    golden_answer: str
    golden_session_ids: tuple[str, ...]
    question_date: datetime | None = None


@dataclass(frozen=True)
class EndToEndExperimentResult:
    """The end-to-end measurement: recall@10 (the ceiling) + reader accuracy."""

    recall_at_k: float
    end_to_end_accuracy: float
    ceiling_gap: float  # recall@10 - end_to_end_accuracy (>= 0 by construction)
    n_queries: int
    n_episodes: int
    regime: str  # hybrid | fallback_g2


class ExtractiveReader:
    """Deterministic extractive reader double (no Ollama).

    Returns the context line with the most query-token overlap (a stand-in for
    the real reader LLM's extractive behavior). An empty context (abstention) or
    no overlap returns ``""`` — the reader abstains. Verifies the harness
    MECHANICS — NOT the science (fail-loud honesty).
    """

    def generate(self, question: str, context: str, question_date=None) -> str:
        q_tokens = set(_normalize_tokens(question))
        best_line = ""
        best_score = 0
        for line in context.splitlines():
            line_tokens = _normalize_tokens(line)
            score = sum(1 for t in line_tokens if t in q_tokens)
            if score > best_score:
                best_score = score
                best_line = line
        return best_line


def _normalize_tokens(text: str) -> list[str]:
    """Lower-case + strip non-alphanumeric tokens (reader tokenizer)."""
    return [t for t in re.sub(r"[^a-z0-9 ]", "", text.lower()).split() if t]


def _normalize_answer(text: str) -> str:
    """Lower-case + strip punctuation for the golden-vs-reader match."""
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _make_synthetic_episodes() -> tuple[list[Episode], list[EndToEndQuestion]]:
    """Deterministic synthetic corpus: retrievable + unretrievable questions.

    The A4 ceiling: the reader only sees the top-10, so recall@10 is an upper
    bound on end-to-end accuracy. The corpus makes this falsifiable:

    - **Retrievable** (5): the answer episode shares the query's DISTINCTIVE
      token (the country name) so it ranks 1-2 — the reader extracts the answer.
    - **Unretrievable** (5): the answer episode shares only the common words
      (capital/of/is/the) so it is OUTRANKED by the distractors (below the
      top-10) — the reader cannot answer because the golden session is absent.

    The distractors (10) share the common words and fill the top-10, pushing the
    unretrievable answers out. The exact numbers are NOT the science (fail-loud
    honesty) — the mechanics are: retrievable questions recover + answer,
    unretrievable questions do not, so the ceiling gap is measurable.
    """
    now = datetime(2026, 1, 1, tzinfo=UTC)
    episodes: list[Episode] = []
    questions: list[EndToEndQuestion] = []

    def _ep(i: int, title: str, body: str, session_id: str) -> Episode:
        # The summary is the body's first sentence (the ``deterministic_extract``
        # behavior) — the reader sees it, NOT the full body (the A4 concern).
        return Episode(
            id=f"syn-e2e-{i}",
            created_at=now,
            schema_version="1.1",
            provenance={
                "source_type": "importer",
                "importer_vendor": "claude-mem",
                "extraction_mode": "skip",
                "session_id": session_id,
            },
            body=body,
            title=title,
            summary=_first_sentence(body),
            valid_at=now,
            cognitive_type="semantic",
            source_type="importer",
        )

    # Retrievable: the answer episode shares the country name (distinctive).
    retrievable = (
        ("France", "Paris"),
        ("Spain", "Madrid"),
        ("Italy", "Rome"),
        ("Japan", "Tokyo"),
        ("Brazil", "Brasilia"),
    )
    for i, (country, capital) in enumerate(retrievable):
        episodes.append(
            _ep(
                i,
                country,
                f"The capital of {country} is {capital}.",
                f"s-retrievable-{i}",
            )
        )
        questions.append(
            EndToEndQuestion(
                query=f"What is the capital of {country}?",
                golden_answer=capital,
                golden_session_ids=(f"s-retrievable-{i}",),
            )
        )

    # Unretrievable: the answer episode shares only the common words, so it is
    # outranked by the distractors (below the top-10).
    unretrievable = (
        ("Mars", "Olympus"),
        ("Venus", "Aphrodite"),
        ("Mercury", "Hermes"),
        ("Jupiter", "Zeus"),
        ("Saturn", "Cronus"),
    )
    for i, (planet, capital) in enumerate(unretrievable):
        # The title is a NEUTRAL word (NOT the planet name): the facade prepends
        # the title to the stored body, so a "Mars" title would leak the query
        # token into the body and recover the episode. The golden session is
        # about the planet, but the episode is stored under a neutral title.
        episodes.append(
            _ep(
                5 + i,
                "Colony",
                f"{capital} rules the {planet}ian colony.",
                f"s-unretrievable-{i}",
            )
        )
        questions.append(
            EndToEndQuestion(
                query=f"What is the capital of {planet}?",
                golden_answer=capital,
                golden_session_ids=(f"s-unretrievable-{i}",),
            )
        )

    # Distractors: share the common words (capital/of/is/the) and fill the
    # top-10, pushing the unretrievable answers out.
    for i, (country, capital) in enumerate(
        (
            ("Alpha", "A"),
            ("Beta", "B"),
            ("Gamma", "C"),
            ("Delta", "D"),
            ("Epsilon", "E"),
            ("Zeta", "F"),
            ("Eta", "G"),
            ("Theta", "H"),
            ("Iota", "I"),
            ("Kappa", "J"),
        )
    ):
        episodes.append(
            _ep(
                10 + i,
                country,
                f"The capital of {country} is {capital}.",
                f"s-distractor-{i}",
            )
        )

    return episodes, questions


def _first_sentence(text: str) -> str:
    """The first sentence of a body (the ``deterministic_extract`` summary)."""
    stripped = text.strip()
    if not stripped:
        return ""
    return stripped.split(".", 1)[0] + "." if "." in stripped else stripped


def _ingest_episodes(
    facade: Any, episodes: list[Episode]
) -> tuple[list[Episode], dict[str, str], dict[str, str]]:
    """Ingest episodes via the facade's ``remember`` (the single write path, skip mode).

    Returns ``(stored, id_map, ep_id_to_session)`` where ``id_map`` maps the
    ORIGINAL episode id to the STORED ``ep_id`` (the engine derives a
    deterministic UUIDv5 for importer source, which may differ from
    ``Episode.id``) and ``ep_id_to_session`` maps the STORED ep_id to its
    session (the recall@10 ceiling check). Episodes rejected by a collision
    (``WriteResult.ep_id`` is None) are NOT stored and excluded.
    """
    stored: list[Episode] = []
    id_map: dict[str, str] = {}
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
            extraction_mode="skip",
        )
        if result.ep_id is None:
            continue  # COLLISION — not stored, not in the corpus
        stored.append(ep.model_copy(update={"id": result.ep_id}))
        id_map[ep.id] = result.ep_id
        session_id = ep.provenance.get("session_id", "")
        ep_id_to_session[result.ep_id] = session_id
    return stored, id_map, ep_id_to_session


def build_synthetic_corpus(
    db_path: Path,
) -> tuple[Any, Any, list[Episode], list[EndToEndQuestion], dict[str, str]]:
    """Build the synthetic corpus (mechanical CI verification, no model).

    Returns ``(facade, storage, stored_episodes, questions, ep_id_to_session)``.
    """
    episodes, questions = _make_synthetic_episodes()
    # The synthetic corpus needs a deterministic embedder for the hybrid path.
    from seahorse.benchmark.experiments.synthetic import HashEmbedder

    facade, storage = build_facade(
        db_path, retrieval_available=True, passage_embedder=HashEmbedder()
    )
    stored, _, ep_id_to_session = _ingest_episodes(facade, episodes)
    return facade, storage, stored, questions, ep_id_to_session


def build_real_corpus(
    db_path: Path, *, subsample: bool = True
) -> tuple[Any, Any, list[Episode], list[EndToEndQuestion], dict[str, str]]:
    """Build the real LMEB-S corpus (the authoritative decision).

    Ingests the real haystack (the reproducible 100 subsample by default) with
    the real fastembed backend and returns ``(facade, storage, [], questions,
    ep_id_to_session)``. ``episodes`` is empty — the session-level questions
    carry no per-episode answer — and ``ep_id_to_session`` is the TRUE stored
    episode inventory the session-level recall resolves through. Every LMEB
    instance carries a ``question_date`` and ``golden_answer``.
    """
    dataset = load_lmeb_subsample(subsample=subsample)
    facade, storage = build_real_facade(db_path)
    _, ep_id_to_session = ingest_haystack(facade, dataset)
    questions = [
        EndToEndQuestion(
            query=inst.question,
            golden_answer=inst.golden_answer or "",
            golden_session_ids=inst.golden_session_ids,
            question_date=inst.question_date,
        )
        for inst in dataset.instances
    ]
    return facade, storage, [], questions, ep_id_to_session


def _format_context(
    rows, *, mode: ContextMode = "summary", body_for=None
) -> str:
    """The reader's context via the assembler seam (summary | body | body_bounded).

    ``body_for`` maps ep_id → body (the hydrated bodies for the non-summary
    modes); None falls back to the summary line.
    """
    return assemble_context(rows, mode=mode, body_for=body_for)


def measure_end_to_end(
    facade: Any,
    episodes: list[Episode],
    questions: list[EndToEndQuestion],
    ep_id_to_session: dict[str, str],
    top_k: int,
    *,
    context_mode: ContextMode = "summary",
    reader: Any | None = None,
) -> tuple[float, float, int, int, str]:
    """Run the full pipeline (retrieve -> reader -> answer) per question.

    Returns ``(recall_at_k, end_to_end_accuracy, n_queries, n_episodes,
    regime)``. The regime degrades to ``fallback_g2`` when any query returns
    rows with all-zero scores (the hybrid path was not wired).

    ``context_mode`` selects the reader's context representation (the
    reader-context A/B axis); the body modes hydrate the top-k via
    ``batch_body_for`` (active-now — FULL PIT is a later release). ``reader``
    defaults to the deterministic ``ExtractiveReader``; the authoritative run
    injects the real ``ReaderLLMClient``.
    """
    if reader is None:
        reader = ExtractiveReader()
    regime = "hybrid"
    recall_hits: list[float] = []
    e2e_hits: list[float] = []
    for q in questions:
        # ``session_boost=False``: the benchmark measures the hybrid ranking's
        # recall@10 (the pure RRF + configured stages). The session boost is a
        # separate product stage measured by the two-stage experiment (upper
        # bound) + the engine verification (automatic version); including it
        # here would change the authoritative baseline (0.790/0.533) and make
        # the suite inconsistent.
        # Temporal-reasoning questions evaluate with ``pit=state_at(question_date)``
        # (the SUT's ``_recall`` behavior) so the state as ranked is the old
        # version, pre-update. Honest degrade (mirroring ``SeahorseSUT._recall``):
        # a regime without a PIT axis raises ``PitRecallNotSupportedMVP0`` from
        # the facade → fall back to active-now, never crash the run.
        if q.question_date is not None:
            from seahorse.disclosure.types import PITPoint

            try:
                rows = facade.recall(
                    q.query,
                    k=top_k,
                    pit=PITPoint(kind="state_at", t=q.question_date),
                    session_boost=False,
                )
            except PitRecallNotSupportedMVP0:
                rows = facade.recall(q.query, k=top_k, session_boost=False)
        else:
            rows = facade.recall(q.query, k=top_k, session_boost=False)
        if rows and all(r.score == 0.0 for r in rows):
            regime = _FALLBACK_G2
        # recall@10 (the ceiling): any retrieved episode from the golden session.
        retrieved_sessions = {ep_id_to_session.get(r.ep_id, "") for r in rows}
        recall_hits.append(
            1.0 if retrieved_sessions & set(q.golden_session_ids) else 0.0
        )
        # end-to-end: the reader's answer vs the golden answer. The context is
        # the top-k rows rendered by the assembler seam — summary by default,
        # hydrated bodies in the body modes (the A4 concern).
        body_for = None
        if context_mode != "summary":
            bodies = batch_body_for(facade, [r.ep_id for r in rows])
            body_for = bodies.get
        context = _format_context(rows, mode=context_mode, body_for=body_for)
        answer = reader.generate(q.query, context, q.question_date)
        answer_norm = _normalize_answer(answer)
        golden_norm = _normalize_answer(q.golden_answer)
        if golden_norm in _ABSTENTION_ANSWERS:
            e2e_hits.append(1.0 if answer_norm == "" else 0.0)
        else:
            e2e_hits.append(1.0 if golden_norm in answer_norm else 0.0)
    n = len(questions)
    return (
        sum(recall_hits) / n if n else 0.0,
        sum(e2e_hits) / n if n else 0.0,
        n,
        len(episodes),
        regime,
    )


def run_end_to_end_experiment(
    *,
    corpus: str = "synthetic",
    db_path: Path | str | None = None,
    top_k: int = END_TO_END_TOP_K,
    subsample: bool = True,
    context_mode: ContextMode = "summary",
    reader: Any | None = None,
) -> EndToEndExperimentResult:
    """Run the end-to-end measurement and return the result.

    ``corpus`` is ``"synthetic"`` (mechanical CI verification) or ``"lmeb-s"``
    (the real corpus, authoritative — the reproducible 100 subsample by default;
    ``subsample=False`` opts into the full-corpus overnight run). ``db_path``
    defaults to a fresh temp DB (reproducible). ``context_mode`` is the
    reader-context A/B axis (summary | body | body_bounded); ``reader`` defaults
    to the deterministic ``ExtractiveReader`` (the authoritative run injects the
    real ``ReaderLLMClient``).
    """
    if corpus not in ("synthetic", "lmeb-s"):
        raise ValueError(
            f"unknown corpus: {corpus!r} (expected 'synthetic' or 'lmeb-s')"
        )
    tmp = Path(mkdtemp_scoped("seahorse-e2e-"))
    db = Path(db_path) if db_path is not None else tmp / "bench.db"
    if corpus == "synthetic":
        facade, storage, episodes, questions, ep_id_to_session = build_synthetic_corpus(db)
    else:
        facade, storage, episodes, questions, ep_id_to_session = build_real_corpus(
            db, subsample=subsample
        )
    try:
        recall_at_k, e2e, n_queries, n_episodes, regime = measure_end_to_end(
            facade,
            episodes,
            questions,
            ep_id_to_session,
            top_k,
            context_mode=context_mode,
            reader=reader,
        )
    finally:
        storage.close()
    return EndToEndExperimentResult(
        recall_at_k=recall_at_k,
        end_to_end_accuracy=e2e,
        ceiling_gap=recall_at_k - e2e,
        n_queries=n_queries,
        n_episodes=n_episodes,
        regime=regime,
    )


def decide_end_to_end(result: EndToEndExperimentResult) -> dict:
    """Apply the decision: report the end-to-end baseline + the bottleneck.

    Returns a decision dict (``decision``, ``flip``, ``reason``,
    ``recall_at_k``, ``end_to_end_accuracy``, ``ceiling_gap``). Invalid (no
    decision) when the run degraded to ``fallback_g2`` (fail-loud honesty).
    """
    if result.regime == _FALLBACK_G2:
        return {
            "decision": "invalid_regime",
            "flip": False,
            "reason": (
                "the run degraded to the listing regime (hybrid retrieval not wired); "
                "the end-to-end measurement is not meaningful — re-run with the "
                "embeddings extra"
            ),
            "recall_at_k": result.recall_at_k,
            "end_to_end_accuracy": result.end_to_end_accuracy,
            "ceiling_gap": result.ceiling_gap,
        }
    if result.ceiling_gap >= 0.05:
        return {
            "decision": "reader_bottleneck",
            "flip": False,
            "reason": (
                f"end-to-end accuracy {result.end_to_end_accuracy:.3f} is "
                f"{result.ceiling_gap * 100:.1f}pp below recall@{END_TO_END_TOP_K} "
                f"{result.recall_at_k:.3f} — retrieval recovers the session but the "
                f"reader cannot extract the answer; the reader is the bottleneck"
            ),
            "recall_at_k": result.recall_at_k,
            "end_to_end_accuracy": result.end_to_end_accuracy,
            "ceiling_gap": result.ceiling_gap,
        }
    return {
        "decision": "retrieval_bottleneck",
        "flip": False,
        "reason": (
            f"end-to-end accuracy {result.end_to_end_accuracy:.3f} tracks "
            f"recall@{END_TO_END_TOP_K} {result.recall_at_k:.3f} (gap "
            f"{result.ceiling_gap * 100:.1f}pp) — the reader extracts what retrieval "
            f"recovers; retrieval quality is the bottleneck (the A4 blocker)"
        ),
        "recall_at_k": result.recall_at_k,
        "end_to_end_accuracy": result.end_to_end_accuracy,
        "ceiling_gap": result.ceiling_gap,
    }


def render_end_to_end_report(result: EndToEndExperimentResult, decision: dict) -> str:
    """Human-readable report for the CLI (metrics + decision)."""
    lines = [
        "# End-to-end measurement experiment: the reader's real value",
        "",
        f"regime: {result.regime}",
        f"episodes: {result.n_episodes}",
        f"queries: {result.n_queries}",
        f"recall@{END_TO_END_TOP_K} (the ceiling): {result.recall_at_k:.3f}",
        f"end-to-end accuracy (reader vs golden): {result.end_to_end_accuracy:.3f}",
        f"ceiling gap (recall@10 - e2e): {result.ceiling_gap:.3f}",
        "",
        "## Decision",
        f"decision: {decision.get('decision')}",
        f"flip: {decision.get('flip')}",
        f"reason: {decision.get('reason', '')}",
    ]
    return "\n".join(lines)


__all__ = [
    "END_TO_END_TOP_K",
    "EndToEndExperimentResult",
    "EndToEndQuestion",
    "ExtractiveReader",
    "build_real_corpus",
    "build_synthetic_corpus",
    "decide_end_to_end",
    "measure_end_to_end",
    "render_end_to_end_report",
    "run_end_to_end_experiment",
]
