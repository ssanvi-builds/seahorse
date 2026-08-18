"""Multi-hop recall experiment (e) — decide Rung 3 (physical graph).

Falsifies the hypothesis "BFS-as-INDEX (stage-2 chain) adds >= X% recall@k over
vector+BM25 only for multi-hop questions". The corrected construct
(incorporation §6.7, F4-rung3 finding): BFS-as-INDEX over subject/fact_id
clustering does NOT measure traversal by typed edges. This experiment probes
edge traversal directly: a synthetic corpus of entity chains where episode A
mentions entity B, episode B mentions entity C, etc.

Metrics:
- **recall@k (1-hop)**: for each chain, a query that mentions the answer
  entity directly (e.g. "What does Aurora use?") — the answer episode is
  directly retrievable by token overlap.
- **recall@k (2-hop)**: for each chain, a query that mentions the SOURCE
  entity and requires traversing one chain edge (e.g. "What does the project
  that Alice leads use?") — the answer episode does NOT mention the source
  entity, so the answer is only reachable by traversing the edge.

Decision (``decide_multi_hop``): if 2-hop recall@k is >= ``MULTI_HOP_DELTA_PP``
(5pp, the spec's proposed threshold) LOWER than 1-hop recall@k, the current
retrieval cannot recover multi-hop answers → Rung 3 (physical graph with edge
traversal) is justified. If the delta is < 5pp, the current retrieval covers
multi-hop → no graph needed. Honest regime detection: all-zero scores =>
``fallback_g2`` => invalid decision (fail-loud honesty).

The synthetic corpus verifies the harness MECHANICS in CI (``HashEmbedder``,
no model download) — NOT the science. The authoritative decision comes from an
LMEB-S multi-session run (``--corpus lmeb-s``), which is not yet built
(``build_real_corpus`` raises ``NotImplementedError``, fail-loud).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from seahorse.benchmark.experiments.synthetic import HashEmbedder
from seahorse.contracts.episode import Episode
from seahorse.facade import build_facade
from seahorse.facade.types import Provenance, RememberPayload

# The k for the recall@k measurement (harness default).
MULTI_HOP_TOP_K = 10

# Decision threshold (design choice, the spec's proposal): the current
# retrieval must recover multi-hop answers within 5pp of the direct (1-hop)
# recall; a larger gap means the answer is only reachable by traversing the
# chain → Rung 3 (physical graph with edge traversal) is justified.
MULTI_HOP_DELTA_PP = 0.05

# The honest detected regime that invalidates a hybrid-regime experiment.
_FALLBACK_G2 = "fallback_g2"


@dataclass(frozen=True)
class MultiHopQuestion:
    """A multi-hop probe: the query + the golden answer episode + the hop count.

    ``hops`` is 1 (the query mentions the answer entity directly) or 2 (the
    query mentions the SOURCE entity and the answer is only reachable by
    traversing one chain edge).
    """

    query: str
    answer_ep_id: str
    hops: int


@dataclass(frozen=True)
class MultiHopExperimentResult:
    """The multi-hop recall measurement."""

    recall_at_k_1hop: float
    recall_at_k_2hop: float
    delta_pp: float  # recall_1hop - recall_2hop, in percentage points
    n_1hop_queries: int
    n_2hop_queries: int
    n_episodes: int
    regime: str  # hybrid | fallback_g2


def _make_synthetic_episodes() -> tuple[list[Episode], list[MultiHopQuestion]]:
    """Deterministic synthetic corpus: entity chains + background distractors.

    Each chain is ``person -> project -> technology``: the person's episode
    mentions the project, the project's episode mentions the technology. A
    1-hop question about the project is directly retrievable (the project's
    episode mentions the project). A 2-hop question about the person requires
    traversing the edge person->project: the answer lives in the project's
    episode, which does NOT mention the person.

    The background distractors (other projects) share the 2-hop queries'
    content tokens ("the", "project") so the top-k is selective: the 2-hop
    answer episode is NOT recovered by token overlap alone. The exact numbers
    are NOT the science (fail-loud honesty) — the mechanics are: 1-hop recall
    must be high, 2-hop recall must be low, so the delta is measurable.
    """
    now = datetime(2026, 1, 1, tzinfo=UTC)
    episodes: list[Episode] = []
    questions: list[MultiHopQuestion] = []

    def _ep(i: int, title: str, narrative: str) -> Episode:
        return Episode(
            id=f"syn-mh-{i}",
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

    # Entity chains: episode about person P mentions project X; episode about
    # project X mentions technology T. The 2-hop answer (in X's episode) does
    # NOT mention P — traversal is required.
    chains = (
        ("Alice", "Aurora", "Rust"),
        ("Bob", "Beacon", "Kafka"),
        ("Carol", "Comet", "TensorFlow"),
        ("Dave", "Draco", "Kotlin"),
        ("Eve", "Echo", "Erlang"),
    )
    for i, (person, project, tech) in enumerate(chains):
        episodes.append(_ep(i * 3, person, f"{person} leads {project}."))
        episodes.append(_ep(i * 3 + 1, project, f"{project} uses the {tech} language."))
        episodes.append(_ep(i * 3 + 2, tech, f"{tech} is a systems programming language."))
        # 1-hop: the query mentions the project directly -> the project's episode.
        questions.append(
            MultiHopQuestion(
                query=f"What does {project} use?",
                answer_ep_id=f"syn-mh-{i * 3 + 1}",
                hops=1,
            )
        )
        # 2-hop: the query mentions the person -> the answer is in the project's
        # episode, which does NOT mention the person (traversal required).
        questions.append(
            MultiHopQuestion(
                query=f"What does the project that {person} leads use?",
                answer_ep_id=f"syn-mh-{i * 3 + 1}",
                hops=2,
            )
        )

    # Background distractors: episodes that share the 2-hop queries' content
    # tokens ("the", "project") but are NOT answers — they make the top-k
    # selective so the 2-hop answer episode is not recovered by token overlap.
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
        episodes.append(_ep(15 + i, proj, f"The {proj} project uses {tech}."))

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
) -> tuple[Any, Any, list[Episode], list[MultiHopQuestion]]:
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
        MultiHopQuestion(
            query=q.query, answer_ep_id=id_map[q.answer_ep_id], hops=q.hops
        )
        for q in questions
        if q.answer_ep_id in id_map
    ]
    return facade, storage, stored, remapped


def build_real_corpus(db_path: Path) -> tuple[Any, Any, list[Episode], list[MultiHopQuestion]]:
    """Build the real LMEB-S multi-session corpus (the authoritative decision).

    NOT yet built: deriving multi-hop entity-centric questions from the LMEB-S
    haystack is a separate task. Fail-loud (``NotImplementedError``) rather than
    silently running the synthetic mechanics as if they were the science.
    """
    raise NotImplementedError(
        "the LMEB-S multi-hop corpus is not built yet; run with corpus='synthetic' "
        "to verify the harness mechanics"
    )


def _measure(
    facade: Any,
    episodes: list[Episode],
    questions: list[MultiHopQuestion],
    top_k: int,
) -> tuple[float, float, int, int, str]:
    """Run the recall@k measurement for 1-hop vs 2-hop questions.

    Returns ``(recall_1hop, recall_2hop, n_1hop, n_2hop, regime)``. The regime
    degrades to ``fallback_g2`` when any query returns rows with all-zero
    scores (the hybrid path was not wired).
    """
    ep_ids = {ep.id for ep in episodes}
    recalls_1hop: list[float] = []
    recalls_2hop: list[float] = []
    regime = "hybrid"
    for q in questions:
        if q.answer_ep_id not in ep_ids:
            continue  # the answer episode was not stored (collision)
        rows = facade.recall(q.query, k=top_k)
        retrieved = [r.ep_id for r in rows]
        if rows and all(r.score == 0.0 for r in rows):
            regime = _FALLBACK_G2
        hit = 1.0 if q.answer_ep_id in retrieved else 0.0
        if q.hops == 1:
            recalls_1hop.append(hit)
        else:
            recalls_2hop.append(hit)
    recall_1hop = sum(recalls_1hop) / len(recalls_1hop) if recalls_1hop else 0.0
    recall_2hop = sum(recalls_2hop) / len(recalls_2hop) if recalls_2hop else 0.0
    return recall_1hop, recall_2hop, len(recalls_1hop), len(recalls_2hop), regime


def run_multi_hop_experiment(
    *,
    corpus: str = "synthetic",
    db_path: Path | str | None = None,
    top_k: int = MULTI_HOP_TOP_K,
) -> MultiHopExperimentResult:
    """Run the multi-hop recall measurement and return the result.

    ``corpus`` is ``"synthetic"`` (mechanical CI verification) or ``"lmeb-s"``
    (the real corpus, authoritative — not yet built). ``db_path`` defaults to a
    fresh temp DB (reproducible).
    """
    if corpus not in ("synthetic", "lmeb-s"):
        raise ValueError(
            f"unknown corpus: {corpus!r} (expected 'synthetic' or 'lmeb-s')"
        )
    tmp = Path(tempfile.mkdtemp(prefix="seahorse-multihop-"))
    db = Path(db_path) if db_path is not None else tmp / "bench.db"
    if corpus == "synthetic":
        facade, storage, episodes, questions = build_synthetic_corpus(db)
    else:
        facade, storage, episodes, questions = build_real_corpus(db)
    try:
        recall_1hop, recall_2hop, n_1hop, n_2hop, regime = _measure(
            facade, episodes, questions, top_k
        )
    finally:
        storage.close()
    return MultiHopExperimentResult(
        recall_at_k_1hop=recall_1hop,
        recall_at_k_2hop=recall_2hop,
        delta_pp=(recall_1hop - recall_2hop) * 100.0,
        n_1hop_queries=n_1hop,
        n_2hop_queries=n_2hop,
        n_episodes=len(episodes),
        regime=regime,
    )


def decide_multi_hop(result: MultiHopExperimentResult) -> dict:
    """Apply the decision: materialize the physical graph (Rung 3) or not.

    Returns a decision dict (``decision``, ``flip``, ``reason``,
    ``recall_at_k_1hop``, ``recall_at_k_2hop``, ``delta_pp``). Invalid (no
    decision) when the run degraded to ``fallback_g2`` (fail-loud honesty).
    """
    if result.regime == _FALLBACK_G2:
        return {
            "decision": "invalid_regime",
            "flip": False,
            "reason": (
                "the run degraded to the listing regime (hybrid retrieval not wired); "
                "the multi-hop comparison is not meaningful — re-run with the embeddings extra"
            ),
            "recall_at_k_1hop": result.recall_at_k_1hop,
            "recall_at_k_2hop": result.recall_at_k_2hop,
            "delta_pp": result.delta_pp,
        }
    if result.delta_pp >= MULTI_HOP_DELTA_PP * 100.0:
        return {
            "decision": "rung3",
            "flip": True,
            "reason": (
                f"2-hop recall@{MULTI_HOP_TOP_K} {result.recall_at_k_2hop:.3f} is "
                f"{result.delta_pp:.1f}pp below 1-hop recall@{MULTI_HOP_TOP_K} "
                f"{result.recall_at_k_1hop:.3f} (>= {MULTI_HOP_DELTA_PP:.0%}) — the "
                f"current retrieval cannot recover multi-hop answers; materialize the "
                f"physical graph (Rung 3) with edge traversal"
            ),
            "recall_at_k_1hop": result.recall_at_k_1hop,
            "recall_at_k_2hop": result.recall_at_k_2hop,
            "delta_pp": result.delta_pp,
        }
    return {
        "decision": "no_graph",
        "flip": False,
        "reason": (
            f"2-hop recall@{MULTI_HOP_TOP_K} {result.recall_at_k_2hop:.3f} is within "
            f"{MULTI_HOP_DELTA_PP:.0%} of 1-hop recall@{MULTI_HOP_TOP_K} "
            f"{result.recall_at_k_1hop:.3f} — the current retrieval covers multi-hop; "
            f"no physical graph needed"
        ),
        "recall_at_k_1hop": result.recall_at_k_1hop,
        "recall_at_k_2hop": result.recall_at_k_2hop,
        "delta_pp": result.delta_pp,
    }


def render_multi_hop_report(result: MultiHopExperimentResult, decision: dict) -> str:
    """Human-readable report for the CLI (metrics + decision)."""
    lines = [
        "# Multi-hop recall experiment: Rung 3 (physical graph)",
        "",
        f"regime: {result.regime}",
        f"episodes: {result.n_episodes}",
        f"1-hop queries: {result.n_1hop_queries}",
        f"2-hop queries: {result.n_2hop_queries}",
        f"recall@{MULTI_HOP_TOP_K} (1-hop, direct): {result.recall_at_k_1hop:.3f}",
        f"recall@{MULTI_HOP_TOP_K} (2-hop, traversal): {result.recall_at_k_2hop:.3f}",
        f"delta (1-hop - 2-hop): {result.delta_pp:.1f}pp",
        "",
        "## Decision",
        f"decision: {decision.get('decision')}",
        f"flip: {decision.get('flip')}",
        f"reason: {decision.get('reason', '')}",
    ]
    return "\n".join(lines)


__all__ = [
    "MULTI_HOP_DELTA_PP",
    "MULTI_HOP_TOP_K",
    "MultiHopExperimentResult",
    "MultiHopQuestion",
    "build_real_corpus",
    "build_synthetic_corpus",
    "decide_multi_hop",
    "render_multi_hop_report",
    "run_multi_hop_experiment",
]
