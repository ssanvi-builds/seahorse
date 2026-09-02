"""Entity-centric recall experiment (f) — does the physical graph add value over clustering?

Measures whether the subject/fact_id clustering (the hybrid retrieval WITHOUT
edges) already covers entity-centric questions ("what do we know about X?"):
given a query about an entity, does the retrieval recover ALL episodes about
that entity, or only the ones that share surface tokens with the query?

Corpus: synthetic entity-centric facts (multiple episodes per entity). The
synthetic corpus verifies the harness MECHANICS in CI (``HashEmbedder``, no
model download) — NOT the science (fail-loud honesty). The authoritative
decision comes from an LMEB-S run (``--corpus lmeb-s``).

The synthetic corpus is designed to make the hypothesis FALSIFIABLE — it
contains BOTH sides of the claim:

- **Coherent entities** (Alice, Bob): the entity name appears in EVERY episode
  body, so the entity-centric query recovers the whole cluster — the clustering
  covers entity-centric without edges (high recall).
- **Scattered entities** (Carol, Dave): the episodes refer to the entity by role
  (only ONE body names the entity), so the entity query recovers only the naming
  episode — the clustering does NOT cover entity-centric (low recall). This is
  the case where the physical graph's edges would add value.

The engine enforces one active episode per subject (collision), so the coherent
entities use DISTINCT subjects (``Alice work`` / ``Alice home`` / ...) — the
entity name in the body is what the entity query recovers on, not the subject.

Metrics:
- **recall@k** (entity-centric): for each entity, the fraction of the entity's
  episodes recovered by the query "what do we know about <entity>". Averages
  across entities.
- **entity recall fraction**: the fraction of ALL entity episodes recovered by
  their entity query (the clustering's coverage of entity-centric).

Decision (``decide_entity_centric``): if recall@k >= ``ENTITY_RECALL_THRESHOLD``
the clustering covers entity-centric without edges → the physical graph adds no
value for this case (no Rung 3). If recall@k < threshold, the graph might add
value → consider Rung 3. Honest regime detection: all-zero scores =>
``fallback_g2`` => invalid decision (fail-loud honesty).

The threshold is a design choice (the spec does not pin one): the clustering
covers entity-centric when, on average, at least half of an entity's episodes
are recovered by an entity-centric query. The per-entity recall distribution is
reported so the reader can weigh the mix of covered vs uncovered entities.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from seahorse.benchmark._tmpdirs import mkdtemp_scoped
from seahorse.benchmark.experiments.synthetic import HashEmbedder
from seahorse.contracts.episode import Episode
from seahorse.facade import build_facade
from seahorse.facade.types import Provenance, RememberPayload

# The k for the entity-centric recall@k measurement (harness default).
ENTITY_TOP_K = 10

# Decision threshold (design choice, documented): the clustering covers
# entity-centric when, on average, >= half of an entity's episodes are
# recovered by an entity-centric query.
ENTITY_RECALL_THRESHOLD = 0.5

# The honest detected regime that invalidates a hybrid-regime experiment.
_FALLBACK_G2 = "fallback_g2"


@dataclass(frozen=True)
class EntityCluster:
    """An entity with its episodes (the golden set for the entity query)."""

    entity: str
    ep_ids: tuple[str, ...]
    subjects: tuple[str, ...]


@dataclass(frozen=True)
class EntityCentricResult:
    """The entity-centric recall measurement."""

    recall_at_k: float  # mean per-entity recall@k (the primary metric)
    entity_recall_fraction: float  # fraction of ALL entity episodes recovered
    n_entities: int
    n_entity_episodes: int
    n_queries: int
    per_entity_recall: tuple[float, ...]
    covered_entities: float  # fraction of entities with recall@k >= threshold
    regime: str  # hybrid | fallback_g2


def _subject_of(ep: Episode) -> str:
    """The episode's subject: the H1 of the body (the importer guarantees it)."""
    if ep.title:
        return ep.title
    body = ep.body or ""
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return body.strip() or ""


def compute_entity_clusters(episodes: list[Episode]) -> list[EntityCluster]:
    """Group episodes by entity (the ``x-entity`` provenance marker).

    Only episodes carrying the marker are entity episodes; the background
    distractors (no marker) are skipped. The cluster's ``ep_ids`` is the golden
    set for the entity-centric query.
    """
    groups: dict[str, list[Episode]] = {}
    for ep in episodes:
        entity = ep.provenance.get("x-entity")
        if entity is None:
            continue
        groups.setdefault(str(entity), []).append(ep)
    clusters: list[EntityCluster] = []
    for entity, eps in groups.items():
        clusters.append(
            EntityCluster(
                entity=entity,
                ep_ids=tuple(ep.id for ep in eps),
                subjects=tuple(_subject_of(ep) for ep in eps),
            )
        )
    return clusters


def _ingest_episodes(facade: Any, episodes: list[Episode]) -> list[Episode]:
    """Ingest episodes via the facade's ``remember`` (the single write path, skip mode).

    Returns the episodes with their STORED ``ep_id`` (the engine derives the id
    — a deterministic UUIDv5 for importer source, which may differ from
    ``Episode.id``). Episodes rejected by a collision (``WriteResult.ep_id`` is
    None) are NOT stored and are excluded from the corpus.
    """
    updated: list[Episode] = []
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
        updated.append(ep.model_copy(update={"id": result.ep_id}))
    return updated


def _make_synthetic_episodes() -> list[Episode]:
    """Deterministic synthetic corpus: coherent entities (high entity recall) +
    scattered entities (low entity recall) + background distractors — verifies
    the harness MECHANICS.

    With ``HashEmbedder``, episodes sharing the entity token are retrieved
    together by the entity query. The coherent entities name the entity in every
    body (recovered as a cluster); the scattered entities name it in only ONE
    body (the role-referenced episodes are invisible to the query — the case
    where the graph's edges would add value). The background episodes (no entity)
    make the top-k selective. The exact numbers are NOT the science (fail-loud
    honesty).
    """
    now = datetime(2026, 1, 1, tzinfo=UTC)
    episodes: list[Episode] = []

    def _ep(
        i: int, entity: str | None, title: str, narrative: str
    ) -> Episode:
        prov: dict[str, Any] = {
            "source_type": "importer",
            "importer_vendor": "claude-mem",
            "extraction_mode": "skip",
            "session_id": "claude-mem-import-syn",
            "x-entity": entity,
        }
        return Episode(
            id=f"syn-{i}",
            created_at=now,
            schema_version="1.1",
            provenance=prov,
            body=f"# {title}\n\n{narrative}",
            title=title,
            valid_at=now,
            cognitive_type="semantic",
            source_type="importer",
        )

    # Coherent entities: the entity name in EVERY body (distinct subjects so the
    # engine's one-active-per-subject collision does not drop episodes). The
    # entity query recovers the whole cluster — clustering covers entity-centric.
    for i, (entity, title, narrative) in enumerate(
        [
            ("Alice", "Alice work", "Alice works at Acme."),
            ("Alice", "Alice home", "Alice lives in Madrid."),
            ("Alice", "Alice hobbies", "Alice likes Python."),
            ("Bob", "Bob job", "Bob is a backend engineer at Globex."),
            ("Bob", "Bob home", "Bob lives in Berlin."),
            ("Bob", "Bob hobby", "Bob plays chess on weekends."),
        ]
    ):
        episodes.append(_ep(i, entity, title, narrative))
    # Scattered entities: the entity name in only ONE body (the rest refer to the
    # entity by role). The entity query recovers only the naming episode — the
    # clustering does NOT cover entity-centric (the graph might add value).
    for i, (entity, title, narrative) in enumerate(
        [
            ("Carol", "Design review", "Carol leads the design review."),
            ("Carol", "Mockups", "The design lead ships the new mockups."),
            ("Carol", "Critique", "The design lead runs the weekly critique."),
            ("Dave", "Onboarding", "Dave wrote the onboarding guide."),
            ("Dave", "Docs", "The docs owner maintains the API reference."),
            ("Dave", "Wiki", "The docs owner keeps the wiki current."),
        ]
    ):
        episodes.append(_ep(i + 6, entity, title, narrative))
    # Background distractors (no entity): make the top-k selective.
    for i, (title, narrative) in enumerate(
        [
            ("Quantum computing", "Qubits exploit superposition and entanglement."),
            ("Rust ownership", "Ownership moves values and borrows references."),
            ("SQLite WAL", "Write-ahead logging allows concurrent readers."),
            ("Docker networking", "Bridge networks isolate containers."),
            ("React hooks", "Hooks manage state in function components."),
            ("PostgreSQL indexes", "B-tree indexes speed up lookups."),
            ("HTTP caching", "Cache headers control response reuse."),
            ("GraphQL subscriptions", "Subscriptions push realtime updates."),
            ("Kubernetes pods", "Pods are the smallest deployable units."),
            ("Redis streams", "Streams are append-only log structures."),
            ("Terraform state", "State tracks managed infrastructure."),
            ("Nginx proxy", "Nginx reverse-proxies HTTP traffic."),
            ("WebAssembly memory", "Linear memory is the WASM heap."),
            ("TypeScript generics", "Generics parameterize types."),
            ("Git rebase", "Rebase rewrites commit history."),
        ]
    ):
        episodes.append(_ep(i + 12, None, title, narrative))
    return episodes


def build_synthetic_corpus(db_path: Path) -> tuple[Any, Any, list[Episode]]:
    """Build the synthetic corpus (mechanical CI verification, no model)."""
    episodes = _make_synthetic_episodes()
    facade, storage = build_facade(
        db_path, retrieval_available=True, passage_embedder=HashEmbedder()
    )
    stored = _ingest_episodes(facade, episodes)
    return facade, storage, stored


def _measure(
    facade: Any, episodes: list[Episode], top_k: int
) -> tuple[float, float, int, int, tuple[float, ...], float, str]:
    """Run the entity-centric recall@k measurement.

    For each entity cluster, query "what do we know about <entity>" and compute
    the fraction of the entity's episodes recovered (the golden set is ALL
    episodes about the entity). Returns ``(recall_at_k, entity_recall_fraction,
    n_entities, n_entity_episodes, per_entity_recall, covered_entities,
    regime)``.
    """
    clusters = compute_entity_clusters(episodes)
    if not clusters:
        return 0.0, 0.0, 0, 0, (), 0.0, "hybrid"

    per_entity_recall: list[float] = []
    total_golden = 0
    total_recovered = 0
    regime = "hybrid"

    for cluster in clusters:
        query = f"what do we know about {cluster.entity}"
        # ``session_boost=False``: the synthetic corpus is a single session
        # (``claude-mem-import-syn``), so the engine's session boost would
        # re-rank ALL episodes within it — an artifact, not a real signal. The
        # experiment measures the hybrid ranking's entity recall.
        rows = facade.recall(query, k=top_k, session_boost=False)
        retrieved = [r.ep_id for r in rows]
        if rows and all(r.score == 0.0 for r in rows):
            regime = _FALLBACK_G2
        golden = set(cluster.ep_ids)
        total_golden += len(golden)
        recovered = len(set(retrieved) & golden)
        total_recovered += recovered
        per_entity_recall.append(recovered / len(golden))

    recall_at_k = (
        sum(per_entity_recall) / len(per_entity_recall) if per_entity_recall else 0.0
    )
    entity_recall_fraction = (
        total_recovered / total_golden if total_golden else 0.0
    )
    covered = (
        sum(1 for r in per_entity_recall if r >= ENTITY_RECALL_THRESHOLD)
        / len(per_entity_recall)
        if per_entity_recall
        else 0.0
    )
    return (
        recall_at_k,
        entity_recall_fraction,
        len(clusters),
        total_golden,
        tuple(per_entity_recall),
        covered,
        regime,
    )


def run_entity_centric_experiment(
    *,
    corpus: str = "synthetic",
    db_path: Path | str | None = None,
    top_k: int = ENTITY_TOP_K,
) -> EntityCentricResult:
    """Run the entity-centric recall measurement and return the result.

    ``corpus`` is ``"synthetic"`` (mechanical CI verification) or ``"lmeb-s"``
    (the authoritative run — NOT yet wired: the LMEB-S entity derivation from
    the haystack is a future step). ``db_path`` defaults to a fresh temp DB
    (reproducible).
    """
    if corpus not in ("synthetic", "lmeb-s"):
        raise ValueError(
            f"unknown corpus: {corpus!r} (expected 'synthetic' or 'lmeb-s')"
        )
    if corpus == "lmeb-s":
        raise NotImplementedError(
            "the authoritative LMEB-S entity-centric run needs the entity "
            "derivation from the haystack (not yet wired); run corpus='synthetic' "
            "to verify the harness mechanics"
        )
    tmp = Path(mkdtemp_scoped("seahorse-entity-"))
    db = Path(db_path) if db_path is not None else tmp / "bench.db"
    facade, storage, episodes = build_synthetic_corpus(db)
    try:
        (
            recall_at_k,
            entity_recall_fraction,
            n_entities,
            n_entity_episodes,
            per_entity_recall,
            covered,
            regime,
        ) = _measure(facade, episodes, top_k)
    finally:
        storage.close()
    return EntityCentricResult(
        recall_at_k=recall_at_k,
        entity_recall_fraction=entity_recall_fraction,
        n_entities=n_entities,
        n_entity_episodes=n_entity_episodes,
        n_queries=n_entities,
        per_entity_recall=per_entity_recall,
        covered_entities=covered,
        regime=regime,
    )


def decide_entity_centric(result: EntityCentricResult) -> dict:
    """Apply the decision: does the clustering cover entity-centric without edges?

    Returns a decision dict (``decision``, ``flip``, ``reason``,
    ``recall_at_k``, ``entity_recall_fraction``). Invalid (no decision) when the
    run degraded to ``fallback_g2`` (fail-loud honesty).
    """
    if result.regime == _FALLBACK_G2:
        return {
            "decision": "invalid_regime",
            "flip": False,
            "reason": (
                "the run degraded to the listing regime (hybrid retrieval not wired); "
                "the entity-centric comparison is not meaningful — re-run with the "
                "embeddings extra"
            ),
            "recall_at_k": result.recall_at_k,
            "entity_recall_fraction": result.entity_recall_fraction,
        }
    if result.recall_at_k >= ENTITY_RECALL_THRESHOLD:
        return {
            "decision": "no_rung3",
            "flip": False,
            "reason": (
                f"entity recall@{ENTITY_TOP_K} {result.recall_at_k:.3f} >= "
                f"threshold {ENTITY_RECALL_THRESHOLD:.1f} — the subject/fact_id "
                f"clustering covers entity-centric without edges; the physical graph "
                f"adds no value for this case"
            ),
            "recall_at_k": result.recall_at_k,
            "entity_recall_fraction": result.entity_recall_fraction,
        }
    return {
        "decision": "consider_rung3",
        "flip": True,
        "reason": (
            f"entity recall@{ENTITY_TOP_K} {result.recall_at_k:.3f} < "
            f"threshold {ENTITY_RECALL_THRESHOLD:.1f} — the clustering does NOT "
            f"cover entity-centric; the physical graph might add value (consider "
            f"Rung 3)"
        ),
        "recall_at_k": result.recall_at_k,
        "entity_recall_fraction": result.entity_recall_fraction,
    }


def render_entity_centric_report(result: EntityCentricResult, decision: dict) -> str:
    """Human-readable report for the CLI (metrics + decision)."""
    lines = [
        "# Entity-centric recall experiment: does the graph add value over clustering?",
        "",
        f"regime: {result.regime}",
        f"entities: {result.n_entities}",
        f"entity episodes: {result.n_entity_episodes}",
        f"entity queries: {result.n_queries}",
        f"per-entity recall@{ENTITY_TOP_K}: "
        f"{[f'{r:.3f}' for r in result.per_entity_recall]}",
        f"recall@{ENTITY_TOP_K} (entity-centric): {result.recall_at_k:.3f}",
        f"entity episodes recovered: {result.entity_recall_fraction:.3f}",
        f"covered entities (recall >= {ENTITY_RECALL_THRESHOLD:.1f}): "
        f"{result.covered_entities:.3f}",
        "",
        "## Decision",
        f"decision: {decision.get('decision')}",
        f"flip: {decision.get('flip')}",
        f"reason: {decision.get('reason', '')}",
    ]
    return "\n".join(lines)


__all__ = [
    "ENTITY_RECALL_THRESHOLD",
    "ENTITY_TOP_K",
    "EntityCentricResult",
    "EntityCluster",
    "build_synthetic_corpus",
    "compute_entity_clusters",
    "decide_entity_centric",
    "render_entity_centric_report",
    "run_entity_centric_experiment",
]
