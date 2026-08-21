"""RRF_K sweep experiment (A5) — does a lower fusion constant help?

Falsifies the hypothesis "RRF_K=60 flattens the top-rank advantage so much that
multi-source presence dominates over strong single-source relevance". The
production constant is ``RRF_K=60`` (``retrieval/constants.py``), explicitly
"fixed up front, never tuned on a batch". This experiment sweeps
``RRF_K ∈ {10, 20, 40, 60}`` over the real engine path (``recall`` +
``rrf_fuse``) and measures recall@10 per value.

Metrics:
- **recall@10 per RRF_K**: for each query, whether the golden answer episode is
  in the top-10 of the fused list. The synthetic corpus is designed so the
  answer is rank-1 in BOTH strategies (strong golden answer) and the distractors
  are mid-rank in both — the sweep verifies the fusion MECHANICS (deterministic,
  answer always recovered) rather than the science. The LMEB-S run measures
  SESSION-level recall (any retrieved episode from the golden session), because
  LMEB golden answers live in sessions, not in a single identifiable turn.

Decision (``decide_rrf_k``): if the best RRF_K improves recall@10 by >=
``RRF_K_IMPROVE_PP`` (5pp) over the production default 60, recommend the flip;
otherwise keep 60. Honest regime detection: all-zero scores => ``fallback_g2``
=> invalid decision (fail-loud honesty).

The synthetic corpus verifies the harness MECHANICS in CI (``HashEmbedder``,
no model download) — NOT the science. The authoritative decision comes from an
LMEB-S run (``--corpus lmeb-s``), which ingests the real haystack with the
real embedder and measures session-level recall over the reproducible 100
subsample.
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
from seahorse.benchmark.experiments.synthetic import HashEmbedder
from seahorse.contracts.episode import Episode
from seahorse.embeddings.query_adapter import AsyncToSyncQueryEmbedder
from seahorse.facade import build_facade
from seahorse.facade.types import Provenance, RememberPayload
from seahorse.retrieval.engine import recall

# The k for the recall@k measurement (harness default).
RRF_K_TOP_K = 10

# The sweep grid (the plan's proposal).
RRF_K_SWEEP = (10, 20, 40, 60)

# Decision threshold: a candidate RRF_K must improve recall@10 by >= 5pp over
# the production default 60 to justify the flip.
RRF_K_IMPROVE_PP = 0.05

# The honest detected regime that invalidates a hybrid-regime experiment.
_FALLBACK_G2 = "fallback_g2"


@dataclass(frozen=True)
class RrfKQuestion:
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
class RrfKExperimentResult:
    """The per-RRF_K recall@10 measurement."""

    recall_at_k_by_rrf_k: tuple[tuple[int, float], ...]
    best_rrf_k: int
    best_recall_at_k: float
    default_recall_at_k: float  # recall@10 at RRF_K=60 (the production default)
    n_queries: int
    n_episodes: int
    regime: str  # hybrid | fallback_g2


def _make_synthetic_episodes() -> tuple[list[Episode], list[RrfKQuestion]]:
    """Deterministic synthetic corpus: golden answers + background distractors.

    Each answer episode shares the query's DISTINCTIVE tokens (the rare project
    name + ``use``) so it is rank-1 in BOTH sources (vector: concentrated token
    overlap; BM25: the rare project name has high IDF). The background
    distractors share only the common tokens (``the``/``project``/``use``) so
    they sit mid-rank in both sources. The exact numbers are NOT the science
    (fail-loud honesty) — the mechanics are: the answer is always recovered, so
    the sweep is deterministic and the decision is stable.
    """
    now = datetime(2026, 1, 1, tzinfo=UTC)
    episodes: list[Episode] = []
    questions: list[RrfKQuestion] = []

    def _ep(i: int, title: str, narrative: str) -> Episode:
        return Episode(
            id=f"syn-rrf-{i}",
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
            valid_at=now,
            cognitive_type="semantic",
            source_type="importer",
        )

    # Golden answers: the answer episode mentions the project name + the tech,
    # so it shares the query's distinctive tokens (project + use).
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
                f"{project} project uses {tech} for the deployment pipeline and "
                f"the monitoring stack.",
            )
        )
        questions.append(
            RrfKQuestion(
                query=f"What does the {project} project use?",
                answer_ep_id=f"syn-rrf-{i}",
            )
        )

    # Background distractors: share only the common tokens (the/project/use) so
    # they sit mid-rank in both sources — they are NOT answers.
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
                f"The project uses {tech} for the build and the release.",
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


def build_synthetic_corpus(
    db_path: Path,
) -> tuple[Any, Any, list[Episode], list[RrfKQuestion]]:
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
        RrfKQuestion(query=q.query, answer_ep_id=id_map[q.answer_ep_id])
        for q in questions
        if q.answer_ep_id in id_map
    ]
    return facade, storage, stored, remapped


def build_real_corpus(
    db_path: Path, *, subsample: bool = True
) -> tuple[Any, Any, list[Episode], list[RrfKQuestion], dict[str, str]]:
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
        RrfKQuestion(query=inst.question, golden_session_ids=inst.golden_session_ids)
        for inst in dataset.instances
    ]
    return facade, storage, [], questions, ep_id_to_session


def _measure(
    facade: Any,
    storage: Any,
    episodes: list[Episode],
    questions: list[RrfKQuestion],
    top_k: int,
    *,
    ep_id_to_session: dict[str, str] | None = None,
    embedder: Any | None = None,
) -> tuple[tuple[tuple[int, float], ...], int, int, str]:
    """Run the recall@10 measurement per RRF_K value.

    Returns ``(recall_by_rrf_k, n_queries, n_episodes, regime)``. The regime
    degrades to ``fallback_g2`` when any query returns rows with all-zero scores
    (the hybrid path was not wired).

    Synthetic questions resolve at EPISODE level (``answer_ep_id``); real
    LMEB-S questions resolve at SESSION level (``golden_session_ids`` +
    ``ep_id_to_session`` bridge) — any retrieved episode from the golden session
    counts. ``embedder`` defaults to the deterministic hash (synthetic); the
    caller passes the REAL query embedder for the LMEB-S run.
    """
    ep_ids = {ep.id for ep in episodes}
    n_episodes = len(ep_id_to_session) if ep_id_to_session else len(ep_ids)
    if embedder is None:
        embedder = cast(Any, AsyncToSyncQueryEmbedder(HashEmbedder()))
    regime = "hybrid"
    recall_by_rrf_k: list[tuple[int, float]] = []
    for rrf_k in RRF_K_SWEEP:
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
                rrf_k=rrf_k,
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
        recall_by_rrf_k.append((rrf_k, sum(hits) / len(hits) if hits else 0.0))
    return tuple(recall_by_rrf_k), len(questions), n_episodes, regime


def run_rrf_k_experiment(
    *,
    corpus: str = "synthetic",
    db_path: Path | str | None = None,
    top_k: int = RRF_K_TOP_K,
    subsample: bool = True,
) -> RrfKExperimentResult:
    """Run the RRF_K sweep measurement and return the result.

    ``corpus`` is ``"synthetic"`` (mechanical CI verification) or ``"lmeb-s"``
    (the real corpus, authoritative — the reproducible 100 subsample by
    default; ``subsample=False`` opts into the full-corpus overnight run).
    ``db_path`` defaults to a fresh temp DB (reproducible).
    """
    if corpus not in ("synthetic", "lmeb-s"):
        raise ValueError(
            f"unknown corpus: {corpus!r} (expected 'synthetic' or 'lmeb-s')"
        )
    tmp = Path(tempfile.mkdtemp(prefix="seahorse-rrfk-"))
    db = Path(db_path) if db_path is not None else tmp / "bench.db"
    if corpus == "synthetic":
        facade, storage, episodes, questions = build_synthetic_corpus(db)
        bridge = None
        embedder = None
    else:
        facade, storage, episodes, questions, bridge = build_real_corpus(
            db, subsample=subsample
        )
        embedder = real_query_embedder()
    try:
        recall_by_rrf_k, n_queries, n_episodes, regime = _measure(
            facade,
            storage,
            episodes,
            questions,
            top_k,
            ep_id_to_session=bridge,
            embedder=embedder,
        )
    finally:
        storage.close()
    by_value = dict(recall_by_rrf_k)
    best_rrf_k = max(by_value, key=lambda k: (by_value[k], -k))
    return RrfKExperimentResult(
        recall_at_k_by_rrf_k=recall_by_rrf_k,
        best_rrf_k=best_rrf_k,
        best_recall_at_k=by_value[best_rrf_k],
        default_recall_at_k=by_value[60],
        n_queries=n_queries,
        n_episodes=n_episodes,
        regime=regime,
    )


def decide_rrf_k(result: RrfKExperimentResult) -> dict:
    """Apply the decision: flip the production RRF_K or keep 60.

    Returns a decision dict (``decision``, ``flip``, ``reason``,
    ``best_rrf_k``, ``best_recall_at_k``, ``default_recall_at_k``). Invalid (no
    decision) when the run degraded to ``fallback_g2`` (fail-loud honesty).
    """
    if result.regime == _FALLBACK_G2:
        return {
            "decision": "invalid_regime",
            "flip": False,
            "reason": (
                "the run degraded to the listing regime (hybrid retrieval not wired); "
                "the RRF_K comparison is not meaningful — re-run with the embeddings extra"
            ),
            "best_rrf_k": result.best_rrf_k,
            "best_recall_at_k": result.best_recall_at_k,
            "default_recall_at_k": result.default_recall_at_k,
        }
    if (
        result.best_rrf_k != 60
        and result.best_recall_at_k - result.default_recall_at_k >= RRF_K_IMPROVE_PP
    ):
        return {
            "decision": "flip_rrf_k",
            "flip": True,
            "reason": (
                f"RRF_K={result.best_rrf_k} improves recall@{RRF_K_TOP_K} by "
                f"{(result.best_recall_at_k - result.default_recall_at_k) * 100:.1f}pp "
                f"over the production default 60 ({result.default_recall_at_k:.3f} -> "
                f"{result.best_recall_at_k:.3f}) — the top-rank advantage is worth "
                f"restoring; flip the constant"
            ),
            "best_rrf_k": result.best_rrf_k,
            "best_recall_at_k": result.best_recall_at_k,
            "default_recall_at_k": result.default_recall_at_k,
        }
    return {
        "decision": "keep_60",
        "flip": False,
        "reason": (
            f"no RRF_K in {RRF_K_SWEEP} improves recall@{RRF_K_TOP_K} by >= "
            f"{RRF_K_IMPROVE_PP:.0%} over the production default 60 "
            f"(best {result.best_recall_at_k:.3f} at RRF_K={result.best_rrf_k}, "
            f"default {result.default_recall_at_k:.3f}) — keep the untuned constant; "
            f"the authoritative decision needs an LMEB-S run"
        ),
        "best_rrf_k": result.best_rrf_k,
        "best_recall_at_k": result.best_recall_at_k,
        "default_recall_at_k": result.default_recall_at_k,
    }


def render_rrf_k_report(result: RrfKExperimentResult, decision: dict) -> str:
    """Human-readable report for the CLI (metrics + decision)."""
    lines = [
        "# RRF_K sweep experiment: fusion constant",
        "",
        f"regime: {result.regime}",
        f"episodes: {result.n_episodes}",
        f"queries: {result.n_queries}",
        "",
        "recall@10 by RRF_K:",
    ]
    for rrf_k, recall_at_k in result.recall_at_k_by_rrf_k:
        marker = "  <- best" if rrf_k == result.best_rrf_k else ""
        lines.append(f"  RRF_K={rrf_k:<2}  recall@{RRF_K_TOP_K}={recall_at_k:.3f}{marker}")
    lines.extend(
        [
            "",
            "## Decision",
            f"decision: {decision.get('decision')}",
            f"flip: {decision.get('flip')}",
            f"reason: {decision.get('reason', '')}",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "RRF_K_IMPROVE_PP",
    "RRF_K_SWEEP",
    "RRF_K_TOP_K",
    "RrfKExperimentResult",
    "RrfKQuestion",
    "build_real_corpus",
    "build_synthetic_corpus",
    "decide_rrf_k",
    "render_rrf_k_report",
    "run_rrf_k_experiment",
]
