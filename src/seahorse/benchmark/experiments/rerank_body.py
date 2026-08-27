"""Rerank-with-body re-test experiment (A6) — re-open the keep_rrf decision.

Falsifies the hypothesis "the cross-encoder rerank scored a poor representation
(summary/subject, ~200 chars) and was therefore rejected". The stage-3 rerank
hydrates ``row.summary or row.subject`` (the first sentence of the turn via
``deterministic_extract``) and scores (query, summary) pairs — but LongMemEval
answers often sit MID-TURN, so the cross-encoder scores a truncated, often
padded representation. This experiment re-tests scoring the FULL BODY with a
larger over-fetch (``k_rerank`` 20 → 50).

Metrics:
- **recall@10 (baseline)**: RRF only (no rerank) — the answer is recovered by
  vector+BM25 (the body is indexed).
- **recall@10 (rerank summary)**: RRF + cross-encoder over summary/subject —
  the answer's summary is a generic filler with no query tokens, so the
  reranker demotes it below the distractors.
- **recall@10 (rerank body)**: RRF + cross-encoder over the FULL body — the
  answer's body carries the query tokens, so the reranker keeps it.

Decision (``decide_rerank_body``): if rerank(body) recovers the answer while
rerank(summary) does not (delta >= ``RERANK_BODY_DELTA_PP``, 5pp), the body
representation is worth re-opening on LMEB-S (the keep_rrf decision was made on
a biased subsample). Honest regime detection: all-zero scores => ``fallback_g2``
=> invalid decision (fail-loud honesty).

The synthetic corpus verifies the harness MECHANICS in Python (``HashEmbedder`` +
``HashReranker``, no model download) — NOT the science. The authoritative
decision comes from an LMEB-S run (``--corpus lmeb-s``), which ingests the real
haystack with the real embedder and measures SESSION-level recall (any
retrieved episode from the golden session — LMEB answers live in sessions, not
a single turn) over the reproducible 100 subsample.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from seahorse.benchmark.experiments.lmeb_corpus import (
    build_real_facade,
    ingest_haystack,
    load_lmeb_subsample,
    real_query_embedder,
)
from seahorse.benchmark.experiments.synthetic import HashEmbedder, HashReranker
from seahorse.contracts.episode import Episode
from seahorse.embeddings.query_adapter import AsyncToSyncQueryEmbedder
from seahorse.facade import build_facade
from seahorse.facade.types import Provenance, RememberPayload
from seahorse.retrieval.engine import recall

# The k for the recall@k measurement (harness default).
RERANK_BODY_TOP_K = 10

# The rerank over-fetch for the body re-test (the plan's proposal: 20 -> 50-100).
RERANK_BODY_OVERFETCH_K = 50

# Decision threshold: rerank(body) must recover >= 5pp more than rerank(summary)
# to justify re-opening the keep_rrf decision.
RERANK_BODY_DELTA_PP = 0.05

# The honest detected regime that invalidates a hybrid-regime experiment.
_FALLBACK_G2 = "fallback_g2"


@dataclass(frozen=True)
class RerankBodyQuestion:
    """A retrieval probe: the query + the golden answer episode/session.

    The synthetic corpus sets ``answer_ep_id`` (episode-level). The LMEB-S
    corpus sets ``golden_session_ids`` (session-level — LMEB answers live in
    sessions, not a single identifiable turn); the real ``_measure`` resolves
    retrieved ep_ids to sessions via the bridge.
    """

    query: str
    answer_ep_id: str = ""
    golden_session_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RerankBodyExperimentResult:
    """The recall@10 measurement across the three rerank configurations."""

    recall_at_k_baseline: float  # RRF only (no rerank)
    recall_at_k_rerank_summary: float  # RRF + rerank over summary/subject
    recall_at_k_rerank_body: float  # RRF + rerank over the full body
    n_queries: int
    n_episodes: int
    regime: str  # hybrid | fallback_g2


def _make_synthetic_episodes() -> tuple[list[Episode], list[RerankBodyQuestion]]:
    """Deterministic synthetic corpus: mid-turn answers + background distractors.

    Each answer episode's BODY carries the query's distinctive tokens (the rare
    project name + ``use``) so vector+BM25 recover it, but its SUMMARY is a
    generic filler with NO query tokens — the exact A6 pathology (the answer
    sits mid-turn, not in the ~200-char summary). The distractors' summaries DO
    carry the common query tokens, so the summary-reranker promotes them above
    the answer. The exact numbers are NOT the science (fail-loud honesty) — the
    mechanics are: baseline recovers the answer, summary-rerank demotes it,
    body-rerank keeps it.
    """
    now = datetime(2026, 1, 1, tzinfo=UTC)
    episodes: list[Episode] = []
    questions: list[RerankBodyQuestion] = []

    def _ep(i: int, title: str, narrative: str, summary: str) -> Episode:
        return Episode(
            id=f"syn-rb-{i}",
            created_at=now,
            schema_version="1.1",
            provenance={
                "source_type": "importer",
                "importer_vendor": "claude-mem",
                "extraction_mode": "skip",
                "session_id": "claude-mem-import-syn",
            },
            body=f"# {title}\n\n{narrative}",
            title=title,
            summary=summary,
            valid_at=now,
            cognitive_type="semantic",
            source_type="importer",
        )

    # Golden answers: the BODY carries the query tokens (the project name in the
    # title + project/use/the in the narrative); the SUMMARY is a generic filler
    # with NO query tokens (the answer is mid-turn, not in the summary). The
    # narrative does NOT repeat the project name — the HashReranker tokenizer
    # strips newlines without a space, so "Draco\\n\\nDraco" would merge into
    # "dracodraco" and lose the match.
    answers = (
        ("Aurora", "Rust"),
        ("Beacon", "Kafka"),
        ("Comet", "TensorFlow"),
        ("Draco", "Kotlin"),
        ("Echo", "Erlang"),
    )
    for i, (project, tech) in enumerate(answers):
        episodes.append(
            _ep(
                i,
                project,
                f"The project uses {tech} for the deployment pipeline and the "
                f"monitoring stack.",
                "A project update.",
            )
        )
        questions.append(
            RerankBodyQuestion(
                query=f"What does the {project} project use?",
                answer_ep_id=f"syn-rb-{i}",
            )
        )

    # Background distractors: the BODY and the SUMMARY both carry the common
    # query tokens (the/project/use) — the summary-reranker promotes them above
    # the answer (whose summary has no query tokens). Short narratives keep the
    # body-rerank signal clean (the answer's title token + extra "the" win).
    for i, (proj, tech) in enumerate(
        (
            ("Zephyr", "Go"),
            ("Orion", "Python"),
            ("Vega", "Java"),
            ("Nova", "C++"),
            ("Lyra", "Ruby"),
            ("Pulsar", "Scala"),
            ("Hydra", "Swift"),
            ("Phoenix", "Dart"),
            ("Atlas", "Perl"),
            ("Sirius", "Haskell"),
        )
    ):
        episodes.append(
            _ep(
                5 + i,
                proj,
                f"The project uses {tech}.",
                f"The project uses {tech}.",
            )
        )

    return episodes, questions


def _ingest_episodes(
    facade: Any, episodes: list[Episode]
) -> tuple[list[Episode], dict[str, str]]:
    """Ingest episodes via the facade's ``remember`` (the single write path, skip mode).

    Returns ``(stored, id_map)`` where ``id_map`` maps the ORIGINAL episode id
    to the STORED ``ep_id`` (the engine derives a deterministic UUIDv5 for
    importer source, which may differ from ``Episode.id``). Episodes rejected by
    a collision (``WriteResult.ep_id`` is None) are NOT stored and excluded.
    """
    stored: list[Episode] = []
    id_map: dict[str, str] = {}
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
    return stored, id_map


def _build_real_reranker() -> Any:
    """The real fastembed cross-encoder (bge-reranker-v2-m3, MIT) for the LMEB-S run.

    Lazy import (the ``embeddings`` extra); the ONNX model downloads on the
    first build (~1.1GB) — a one-time cost the rerank-body run accepts.
    """
    from seahorse.embeddings.rerank_backend import build_fastembed_reranker  # lazy

    return build_fastembed_reranker()


def build_synthetic_corpus(
    db_path: Path,
) -> tuple[Any, Any, list[Episode], list[RerankBodyQuestion]]:
    """Build the synthetic corpus (mechanical CI verification, no model).

    Returns ``(facade, storage, stored_episodes, questions)`` where the
    questions' ``answer_ep_id`` values are the STORED ep_ids.
    """
    episodes, questions = _make_synthetic_episodes()
    facade, storage = build_facade(
        db_path, retrieval_available=True, passage_embedder=HashEmbedder()
    )
    stored, id_map = _ingest_episodes(facade, episodes)
    remapped = [
        RerankBodyQuestion(query=q.query, answer_ep_id=id_map[q.answer_ep_id])
        for q in questions
        if q.answer_ep_id in id_map
    ]
    return facade, storage, stored, remapped


def build_real_corpus(
    db_path: Path, *, subsample: bool = True
) -> tuple[Any, Any, list[Episode], list[RerankBodyQuestion], dict[str, str]]:
    """Build the real LMEB-S corpus (the authoritative decision).

    Ingests the real haystack (the reproducible 100 subsample by default) with
    the real fastembed backend and returns ``(facade, storage, [], questions,
    ep_id_to_session)``. ``episodes`` is empty — the session-level questions
    carry no per-episode answer — and ``ep_id_to_session`` is the TRUE stored
    episode inventory (one entry per stored ep_id) the session-level recall
    resolves retrieved episodes through.
    """
    dataset = load_lmeb_subsample(subsample=subsample)
    facade, storage = build_real_facade(db_path)
    _, ep_id_to_session = ingest_haystack(facade, dataset)
    questions = [
        RerankBodyQuestion(
            query=inst.question, golden_session_ids=inst.golden_session_ids
        )
        for inst in dataset.instances
    ]
    return facade, storage, [], questions, ep_id_to_session


def _measure(
    facade: Any,
    storage: Any,
    episodes: list[Episode],
    questions: list[RerankBodyQuestion],
    top_k: int,
    *,
    ep_id_to_session: dict[str, str] | None = None,
    embedder: Any | None = None,
    reranker: Any = None,
) -> tuple[float, float, float, int, int, str]:
    """Run the recall@10 measurement across the three rerank configurations.

    Returns ``(recall_baseline, recall_summary, recall_body, n_queries,
    n_episodes, regime)``. The regime degrades to ``fallback_g2`` when any query
    returns rows with all-zero scores (the hybrid path was not wired).

    Synthetic questions resolve at EPISODE level (``answer_ep_id``); real
    LMEB-S questions resolve at SESSION level (``golden_session_ids`` +
    ``ep_id_to_session`` bridge) — any retrieved episode from the golden session
    counts. ``embedder``/``reranker`` default to the deterministic hash doubles
    (synthetic); the caller passes the REAL query embedder + cross-encoder for
    the LMEB-S run.
    """
    ep_ids = {ep.id for ep in episodes}
    n_episodes = len(ep_id_to_session) if ep_id_to_session else len(ep_ids)
    if embedder is None:
        embedder = cast(Any, AsyncToSyncQueryEmbedder(HashEmbedder()))
    reranker = reranker if reranker is not None else HashReranker()
    regime = "hybrid"

    def _recall_at_k(rerank_text: str | None) -> float:
        nonlocal regime
        hits: list[float] = []
        for q in questions:
            if q.answer_ep_id and q.answer_ep_id not in ep_ids:
                continue  # the synthetic answer episode was not stored (collision)
            rows = recall(
                q.query,
                pit=None,
                embedder=embedder,
                vector_repo=storage.vector,
                fts_repo=storage.fts,
                episode_repo=storage.episodes,
                index_repo=storage.episode_index,
                k=top_k,
                reranker=reranker if rerank_text is not None else None,
                k_rerank=RERANK_BODY_OVERFETCH_K,
                rerank_text=rerank_text or "summary",
                # ``session_boost=False``: the synthetic corpus is a single
                # session (``claude-mem-import-syn``), so the engine's session
                # boost would re-rank ALL episodes within it — an artifact. The
                # experiment isolates the rerank stage's effect on recall.
                session_boost=False,
            )
            if rows and all(r.score == 0.0 for r in rows):
                regime = _FALLBACK_G2
            if q.golden_session_ids and ep_id_to_session:
                # session-level (LMEB-S): any retrieved episode from the golden session.
                retrieved_sessions = {ep_id_to_session.get(r.ep_id, "") for r in rows}
                hits.append(
                    1.0 if retrieved_sessions & set(q.golden_session_ids) else 0.0
                )
            else:
                retrieved = [r.ep_id for r in rows]
                hits.append(1.0 if q.answer_ep_id in retrieved else 0.0)
        return sum(hits) / len(hits) if hits else 0.0

    recall_baseline = _recall_at_k(None)
    recall_summary = _recall_at_k("summary")
    recall_body = _recall_at_k("body")
    return recall_baseline, recall_summary, recall_body, len(questions), n_episodes, regime


def run_rerank_body_experiment(
    *,
    corpus: str = "synthetic",
    db_path: Path | str | None = None,
    top_k: int = RERANK_BODY_TOP_K,
    subsample: bool = True,
) -> RerankBodyExperimentResult:
    """Run the rerank-with-body re-test and return the result.

    ``corpus`` is ``"synthetic"`` (mechanical CI verification) or ``"lmeb-s"``
    (the real corpus, authoritative — the reproducible 100 subsample by default;
    ``subsample=False`` opts into the full-corpus overnight run). ``db_path``
    defaults to a fresh temp DB (reproducible).
    """
    if corpus not in ("synthetic", "lmeb-s"):
        raise ValueError(
            f"unknown corpus: {corpus!r} (expected 'synthetic' or 'lmeb-s')"
        )
    tmp = Path(tempfile.mkdtemp(prefix="seahorse-rerankbody-"))
    db = Path(db_path) if db_path is not None else tmp / "bench.db"
    if corpus == "synthetic":
        facade, storage, episodes, questions = build_synthetic_corpus(db)
        bridge = None
        embedder = None
        reranker = None
    else:
        facade, storage, episodes, questions, bridge = build_real_corpus(
            db, subsample=subsample
        )
        embedder = real_query_embedder()
        reranker = _build_real_reranker()
    try:
        recall_baseline, recall_summary, recall_body, n_queries, n_episodes, regime = (
            _measure(
                facade,
                storage,
                episodes,
                questions,
                top_k,
                ep_id_to_session=bridge,
                embedder=embedder,
                reranker=reranker,
            )
        )
    finally:
        storage.close()
    return RerankBodyExperimentResult(
        recall_at_k_baseline=recall_baseline,
        recall_at_k_rerank_summary=recall_summary,
        recall_at_k_rerank_body=recall_body,
        n_queries=n_queries,
        n_episodes=n_episodes,
        regime=regime,
    )


def decide_rerank_body(result: RerankBodyExperimentResult) -> dict:
    """Apply the decision: re-open the keep_rrf decision or keep it sealed.

    Returns a decision dict (``decision``, ``flip``, ``reason``,
    ``recall_at_k_baseline``, ``recall_at_k_rerank_summary``,
    ``recall_at_k_rerank_body``). Invalid (no decision) when the run degraded to
    ``fallback_g2`` (fail-loud honesty).
    """
    if result.regime == _FALLBACK_G2:
        return {
            "decision": "invalid_regime",
            "flip": False,
            "reason": (
                "the run degraded to the listing regime (hybrid retrieval not wired); "
                "the rerank comparison is not meaningful — re-run with the embeddings extra"
            ),
            "recall_at_k_baseline": result.recall_at_k_baseline,
            "recall_at_k_rerank_summary": result.recall_at_k_rerank_summary,
            "recall_at_k_rerank_body": result.recall_at_k_rerank_body,
        }
    delta = result.recall_at_k_rerank_body - result.recall_at_k_rerank_summary
    if delta >= RERANK_BODY_DELTA_PP:
        return {
            "decision": "reopen_rerank",
            "flip": True,
            "reason": (
                f"rerank over the FULL body recovers {delta * 100:.1f}pp more "
                f"recall@{RERANK_BODY_TOP_K} than rerank over summary/subject "
                f"({result.recall_at_k_rerank_summary:.3f} -> "
                f"{result.recall_at_k_rerank_body:.3f}) — the summary representation "
                f"was the likely culprit; re-open the keep_rrf decision with an "
                f"LMEB-S body-rerank run"
            ),
            "recall_at_k_baseline": result.recall_at_k_baseline,
            "recall_at_k_rerank_summary": result.recall_at_k_rerank_summary,
            "recall_at_k_rerank_body": result.recall_at_k_rerank_body,
        }
    return {
        "decision": "keep_rrf",
        "flip": False,
        "reason": (
            f"rerank over the FULL body does not recover >= {RERANK_BODY_DELTA_PP:.0%} "
            f"more recall@{RERANK_BODY_TOP_K} than rerank over summary/subject "
            f"(body {result.recall_at_k_rerank_body:.3f} vs summary "
            f"{result.recall_at_k_rerank_summary:.3f}) — the representation was not "
            f"the culprit; keep the keep_rrf decision"
        ),
        "recall_at_k_baseline": result.recall_at_k_baseline,
        "recall_at_k_rerank_summary": result.recall_at_k_rerank_summary,
        "recall_at_k_rerank_body": result.recall_at_k_rerank_body,
    }


def render_rerank_body_report(result: RerankBodyExperimentResult, decision: dict) -> str:
    """Human-readable report for the CLI (metrics + decision)."""
    lines = [
        "# Rerank-with-body re-test experiment: keep_rrf re-open",
        "",
        f"regime: {result.regime}",
        f"episodes: {result.n_episodes}",
        f"queries: {result.n_queries}",
        f"recall@{RERANK_BODY_TOP_K} (baseline, RRF only): {result.recall_at_k_baseline:.3f}",
        f"recall@{RERANK_BODY_TOP_K} (rerank summary/subject): "
        f"{result.recall_at_k_rerank_summary:.3f}",
        f"recall@{RERANK_BODY_TOP_K} (rerank full body): {result.recall_at_k_rerank_body:.3f}",
        "",
        "## Decision",
        f"decision: {decision.get('decision')}",
        f"flip: {decision.get('flip')}",
        f"reason: {decision.get('reason', '')}",
    ]
    return "\n".join(lines)


__all__ = [
    "RERANK_BODY_DELTA_PP",
    "RERANK_BODY_OVERFETCH_K",
    "RERANK_BODY_TOP_K",
    "RerankBodyExperimentResult",
    "RerankBodyQuestion",
    "build_real_corpus",
    "build_synthetic_corpus",
    "decide_rerank_body",
    "render_rerank_body_report",
    "run_rerank_body_experiment",
]
