"""Batch experiment (per-turn batching).

Measures whether the turn ``(session_id, prompt_number)`` is a recoverable
unit: given a query about one observation's topic, does the hybrid retrieval
recover the rest of the turn cluster (all observations of the turn) or only the
individual observation?

Corpus: real claude-mem observations imported as episodes with turn structure
preserved in provenance (``x-claude-mem-session-id`` +
``x-claude-mem-prompt-number``, via the claude-mem importer). The synthetic
corpus verifies the harness MECHANICS in CI (``HashEmbedder``, no model
download) — NOT the science (fail-loud honesty).

Metrics:
- **cluster recall@k** (per-turn): leave-one-out — for each observation
  in a turn with >= 2 observations, recall@k of the OTHER observations of the
  turn given the observation's subject query. Averages across all observations.
- **individual recall@k** (per-session): for each observation, recall@k of that
  observation given its own subject query.

Decision (``decide_batch``): per-turn batching if cluster recall@k >=
``BATCH_RECALL_THRESHOLD``, else per-session batching. Honest regime detection:
all-zero scores => ``fallback_g2`` => invalid decision (fail-loud honesty).

The threshold is a design choice (the spec does not pin one): a turn is a
recoverable unit when, on average, at least half the turn cluster is retrieved
by a query about one observation's topic. The turn-size distribution is
reported so the reader can weigh the recall@k cap (a turn of size N has a
golden set of N-1; recall@k is capped at k/(N-1)).
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
from seahorse.importer.claude_mem import ClaudeMemReader, import_record

# The k for the cluster recall@k measurement (harness default).
BATCH_TOP_K = 10

# Decision threshold (design choice, documented): the turn is a
# recoverable unit when, on average, >= half the turn cluster is retrieved by a
# query about one observation's topic.
BATCH_RECALL_THRESHOLD = 0.5

# The honest detected regime that invalidates a hybrid-regime experiment.
_FALLBACK_G2 = "fallback_g2"


@dataclass(frozen=True)
class TurnCluster:
    """A turn ``(session_id, prompt_number)`` with its observations."""

    session_id: str
    prompt_number: int
    ep_ids: tuple[str, ...]
    subjects: tuple[str, ...]


@dataclass(frozen=True)
class BatchExperimentResult:
    """The per-turn batching measurement."""

    cluster_recall_at_k: float
    individual_recall_at_k: float
    n_turns: int
    n_observations: int
    n_cluster_queries: int
    turn_sizes: tuple[int, ...]
    recoverable_turns: float  # fraction of turns with cluster recall@k >= threshold
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


def compute_turn_clusters(episodes: list[Episode]) -> list[TurnCluster]:
    """Group episodes by turn ``(x-claude-mem-session-id, x-claude-mem-prompt-number)``.

    Only turns with >= 2 observations are clusters (the per-turn batching unit).
    Episodes without the preservation fields are skipped (they carry no turn).
    """
    groups: dict[tuple[str, int], list[Episode]] = {}
    for ep in episodes:
        sid = ep.provenance.get("x-claude-mem-session-id")
        pn = ep.provenance.get("x-claude-mem-prompt-number")
        if sid is None or pn is None:
            continue
        groups.setdefault((str(sid), int(pn)), []).append(ep)
    clusters: list[TurnCluster] = []
    for (sid, pn), eps in groups.items():
        if len(eps) < 2:
            continue
        clusters.append(
            TurnCluster(
                session_id=sid,
                prompt_number=pn,
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
    """Deterministic synthetic corpus: coherent turns (high cluster recall) +
    diverse turns (low cluster recall) + background distractors — verifies the
    harness MECHANICS.

    With ``HashEmbedder``, semantically overlapping texts share buckets, so a
    coherent turn's observations are retrieved together and a diverse turn's are
    not. The background episodes (no turn structure) make the top-k selective
    (more episodes than ``BATCH_TOP_K``). The exact numbers are NOT the science
    (fail-loud honesty).
    """
    now = datetime(2026, 1, 1, tzinfo=UTC)
    episodes: list[Episode] = []

    def _ep(
        i: int, session: str | None, prompt: int | None, title: str, narrative: str
    ) -> Episode:
        prov: dict[str, Any] = {
            "source_type": "importer",
            "importer_vendor": "claude-mem",
            "extraction_mode": "skip",
            "session_id": "claude-mem-import-syn",
        }
        if session is not None and prompt is not None:
            prov["x-claude-mem-session-id"] = session
            prov["x-claude-mem-prompt-number"] = prompt
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

    # Coherent turn 1: 3 observations about France's capital.
    for i, (t, n) in enumerate(
        [
            ("France capital", "The capital of France is Paris."),
            ("France capital city", "Paris is the capital city of France."),
            ("Capital of France", "France's capital is Paris, on the Seine."),
        ]
    ):
        episodes.append(_ep(i, "syn-s1", 1, t, n))
    # Coherent turn 2: 3 observations about Python decorators.
    for i, (t, n) in enumerate(
        [
            ("Python decorators", "A decorator wraps a function with extra behavior."),
            ("Decorator syntax", "Python decorators use the @ syntax above a function."),
            ("Function decorators", "Decorators in Python modify function behavior."),
        ]
    ):
        episodes.append(_ep(i + 3, "syn-s1", 2, t, n))
    # Diverse turn 3: 3 observations about unrelated topics.
    for i, (t, n) in enumerate(
        [
            ("Weather forecast", "Tomorrow it will rain in Madrid."),
            ("Pasta recipe", "Cook the pasta for nine minutes in salted water."),
            ("Football match", "The final is on Sunday at the stadium."),
        ]
    ):
        episodes.append(_ep(i + 6, "syn-s2", 1, t, n))
    # Background distractors (no turn structure): make the top-k selective.
    for i, (t, n) in enumerate(
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
        episodes.append(_ep(i + 9, None, None, t, n))
    return episodes


def build_synthetic_corpus(db_path: Path) -> tuple[Any, Any, list[Episode]]:
    """Build the synthetic corpus (mechanical CI verification, no model)."""
    episodes = _make_synthetic_episodes()
    facade, storage = build_facade(
        db_path, retrieval_available=True, passage_embedder=HashEmbedder()
    )
    stored = _ingest_episodes(facade, episodes)
    return facade, storage, stored


def build_real_corpus(
    db_path: Path, *, project: str = "seahorse", reader: ClaudeMemReader | None = None
) -> tuple[Any, Any, list[Episode]]:
    """Build the real claude-mem corpus: read observations, import, ingest.

    Uses the real fastembed backend (the model downloads on the first embed —
    the faithful hybrid path). ``reader`` is injectable for tests.
    """
    reader = reader or ClaudeMemReader()
    observations = reader.iter_observations(project=project)
    episodes: list[Episode] = []
    for obs in observations:
        result = import_record(obs, "claude-mem")
        episodes.extend(result["notes"])
    facade, storage = build_facade(db_path, retrieval_available=True)
    stored = _ingest_episodes(facade, episodes)
    return facade, storage, stored


def _measure(
    facade: Any, episodes: list[Episode], top_k: int
) -> tuple[float, float, int, int, tuple[int, ...], float, str]:
    """Run the leave-one-out cluster recall@k + individual recall@k measurement.

    Returns ``(cluster_recall, individual_recall, n_turns, n_cluster_queries,
    turn_sizes, recoverable_turns, regime)``.
    """
    clusters = compute_turn_clusters(episodes)
    if not clusters:
        return 0.0, 0.0, 0, 0, (), 0.0, "hybrid"

    cluster_recalls: list[float] = []
    individual_recalls: list[float] = []
    turn_recalls: list[float] = []
    regime = "hybrid"

    for cluster in clusters:
        turn_hits: list[float] = []
        for i, ep_id in enumerate(cluster.ep_ids):
            query = cluster.subjects[i]
            rows = facade.recall(query, k=top_k)
            retrieved = [r.ep_id for r in rows]
            if rows and all(r.score == 0.0 for r in rows):
                regime = _FALLBACK_G2
            golden = set(cluster.ep_ids) - {ep_id}
            if golden:
                hit = len(set(retrieved) & golden) / len(golden)
                cluster_recalls.append(hit)
                turn_hits.append(hit)
            individual_recalls.append(1.0 if ep_id in retrieved else 0.0)
        if turn_hits:
            turn_recalls.append(sum(turn_hits) / len(turn_hits))

    cluster_recall = (
        sum(cluster_recalls) / len(cluster_recalls) if cluster_recalls else 0.0
    )
    individual_recall = (
        sum(individual_recalls) / len(individual_recalls) if individual_recalls else 0.0
    )
    recoverable = (
        sum(1 for r in turn_recalls if r >= BATCH_RECALL_THRESHOLD) / len(turn_recalls)
        if turn_recalls
        else 0.0
    )
    return (
        cluster_recall,
        individual_recall,
        len(clusters),
        len(cluster_recalls),
        tuple(len(c.ep_ids) for c in clusters),
        recoverable,
        regime,
    )


def run_batch_experiment(
    *,
    corpus: str = "synthetic",
    db_path: Path | str | None = None,
    top_k: int = BATCH_TOP_K,
    project: str = "seahorse",
    reader: ClaudeMemReader | None = None,
) -> BatchExperimentResult:
    """Run the per-turn batching measurement and return the result.

    ``corpus`` is ``"synthetic"`` (mechanical CI verification) or
    ``"claude-mem"`` (the real corpus, authoritative). ``db_path`` defaults to
    a fresh temp DB (reproducible). ``reader`` is injectable for tests.
    """
    if corpus not in ("synthetic", "claude-mem"):
        raise ValueError(
            f"unknown corpus: {corpus!r} (expected 'synthetic' or 'claude-mem')"
        )
    tmp = Path(tempfile.mkdtemp(prefix="seahorse-batch-"))
    db = Path(db_path) if db_path is not None else tmp / "bench.db"
    if corpus == "synthetic":
        facade, storage, episodes = build_synthetic_corpus(db)
    else:
        facade, storage, episodes = build_real_corpus(db, project=project, reader=reader)
    try:
        (
            cluster_recall,
            individual_recall,
            n_turns,
            n_cluster_queries,
            turn_sizes,
            recoverable,
            regime,
        ) = _measure(facade, episodes, top_k)
    finally:
        storage.close()
    return BatchExperimentResult(
        cluster_recall_at_k=cluster_recall,
        individual_recall_at_k=individual_recall,
        n_turns=n_turns,
        n_observations=len(episodes),
        n_cluster_queries=n_cluster_queries,
        turn_sizes=turn_sizes,
        recoverable_turns=recoverable,
        regime=regime,
    )


def decide_batch(result: BatchExperimentResult) -> dict:
    """Apply the decision: per-turn batching vs per-session batching.

    Returns a decision dict (``decision``, ``flip``, ``reason``,
    ``cluster_recall_at_k``, ``individual_recall_at_k``). Invalid (no decision)
    when the run degraded to ``fallback_g2`` (fail-loud honesty).
    """
    if result.regime == _FALLBACK_G2:
        return {
            "decision": "invalid_regime",
            "flip": False,
            "reason": (
                "the run degraded to the listing regime (hybrid retrieval not wired); "
                "the batch comparison is not meaningful — re-run with the embeddings extra"
            ),
            "cluster_recall_at_k": result.cluster_recall_at_k,
            "individual_recall_at_k": result.individual_recall_at_k,
        }
    if result.cluster_recall_at_k >= BATCH_RECALL_THRESHOLD:
        return {
            "decision": "batch_por_turno",
            "flip": True,
            "reason": (
                f"cluster recall@{BATCH_TOP_K} {result.cluster_recall_at_k:.3f} >= "
                f"threshold {BATCH_RECALL_THRESHOLD:.1f} — the turn is a recoverable "
                f"unit; batching groups by turn"
            ),
            "cluster_recall_at_k": result.cluster_recall_at_k,
            "individual_recall_at_k": result.individual_recall_at_k,
        }
    return {
        "decision": "por_sesion",
        "flip": False,
        "reason": (
            f"cluster recall@{BATCH_TOP_K} {result.cluster_recall_at_k:.3f} < "
            f"threshold {BATCH_RECALL_THRESHOLD:.1f} — the turn is NOT a recoverable "
            f"unit; batching degrades to per-session"
        ),
        "cluster_recall_at_k": result.cluster_recall_at_k,
        "individual_recall_at_k": result.individual_recall_at_k,
    }


def render_batch_report(result: BatchExperimentResult, decision: dict) -> str:
    """Human-readable report for the CLI (metrics + decision)."""
    lines = [
        "# Batch experiment: per-turn batching",
        "",
        f"regime: {result.regime}",
        f"turns (>=2 obs): {result.n_turns}",
        f"observations: {result.n_observations}",
        f"cluster queries (leave-one-out): {result.n_cluster_queries}",
        f"turn sizes: {sorted(result.turn_sizes)}",
        f"cluster recall@{BATCH_TOP_K} (per-turn): {result.cluster_recall_at_k:.3f}",
        f"individual recall@{BATCH_TOP_K} (per-session): {result.individual_recall_at_k:.3f}",
        f"recoverable turns (cluster recall >= {BATCH_RECALL_THRESHOLD:.1f}): "
        f"{result.recoverable_turns:.3f}",
        "",
        "## Decision",
        f"decision: {decision.get('decision')}",
        f"flip: {decision.get('flip')}",
        f"reason: {decision.get('reason', '')}",
    ]
    return "\n".join(lines)


__all__ = [
    "BATCH_RECALL_THRESHOLD",
    "BATCH_TOP_K",
    "BatchExperimentResult",
    "TurnCluster",
    "build_real_corpus",
    "build_synthetic_corpus",
    "compute_turn_clusters",
    "decide_batch",
    "render_batch_report",
    "run_batch_experiment",
]
