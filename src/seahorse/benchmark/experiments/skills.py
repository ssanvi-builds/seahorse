"""Skills retrieval experiment (h) — does LLM distillation of skills improve recall?

Measures whether the deterministic procedural skills (``record_procedure``,
Sprint C) are retrievable: given a procedural question ("how do I deploy the
service?"), does the hybrid retrieval recover the procedural episode(s) about
that procedure?

Corpus: synthetic procedural episodes (how-to steps). The synthetic corpus
verifies the harness MECHANICS in CI (``HashEmbedder``, no model download) — NOT
the science (fail-loud honesty). The authoritative decision comes from an LMEB-S
run (``--corpus lmeb-s``).

The synthetic corpus is designed to make the hypothesis FALSIFIABLE — it
contains BOTH sides of the claim:

- **Named procedures** (the procedure name in the body): the procedural query
  recovers the episode directly — the deterministic skills are retrievable
  (high recall).
- **Step-referenced procedures** (the body describes steps without naming the
  procedure): the procedural query recovers only the episodes that share surface
  tokens — the deterministic skills do NOT cover them (low recall). This is the
  case where LLM distillation of skills might add value.

Metrics:
- **recall@k** (procedural): for each procedure, the fraction of the procedure's
  episodes recovered by the procedural question. Averages across procedures.
- **recovered fraction**: the fraction of ALL procedural episodes recovered by
  their procedural question.

Decision (``decide_skills``): if recall@k >= ``SKILLS_RECALL_THRESHOLD`` the
deterministic ``record_procedure`` skills are retrievable → no LLM distillation
needed. If recall@k < threshold, LLM distillation of skills might improve
retrieval → consider it. Honest regime detection: all-zero scores =>
``fallback_g2`` => invalid decision (fail-loud honesty).

The threshold is a design choice (the spec does not pin one): the deterministic
skills suffice when, on average, at least half of a procedure's episodes are
recovered by a procedural question.
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

# The k for the procedural recall@k measurement (harness default).
SKILLS_TOP_K = 10

# Decision threshold (design choice, documented): the deterministic skills
# suffice when, on average, >= half of a procedure's episodes are recovered by a
# procedural question.
SKILLS_RECALL_THRESHOLD = 0.5

# The honest detected regime that invalidates a hybrid-regime experiment.
_FALLBACK_G2 = "fallback_g2"


@dataclass(frozen=True)
class ProcedureCluster:
    """A procedure with its episodes (the golden set for the procedural query)."""

    procedure: str
    ep_ids: tuple[str, ...]
    subjects: tuple[str, ...]


@dataclass(frozen=True)
class SkillsExperimentResult:
    """The procedural recall measurement."""

    recall_at_k: float  # mean per-procedure recall@k (the primary metric)
    recovered_fraction: float  # fraction of ALL procedural episodes recovered
    n_procedures: int
    n_procedural_episodes: int
    n_queries: int
    per_procedure_recall: tuple[float, ...]
    covered_procedures: float  # fraction of procedures with recall@k >= threshold
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


def compute_procedure_clusters(episodes: list[Episode]) -> list[ProcedureCluster]:
    """Group episodes by procedure (the ``x-procedure`` provenance marker).

    Only episodes carrying the marker are procedural episodes; the background
    distractors (no marker) are skipped. The cluster's ``ep_ids`` is the golden
    set for the procedural query.
    """
    groups: dict[str, list[Episode]] = {}
    for ep in episodes:
        procedure = ep.provenance.get("x-procedure")
        if procedure is None:
            continue
        groups.setdefault(str(procedure), []).append(ep)
    clusters: list[ProcedureCluster] = []
    for procedure, eps in groups.items():
        clusters.append(
            ProcedureCluster(
                procedure=procedure,
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
    """Deterministic synthetic corpus: named procedures (high procedural recall) +
    step-referenced procedures (low procedural recall) + background distractors —
    verifies the harness MECHANICS.

    With ``HashEmbedder``, episodes sharing the procedure token are retrieved
    together by the procedural query. The named procedures name the procedure in
    every body (recovered as a cluster); the step-referenced procedures describe
    steps without naming the procedure (the episodes are invisible to the query
    — the case where LLM distillation might add value). The background episodes
    (no procedure) make the top-k selective. The exact numbers are NOT the
    science (fail-loud honesty).
    """
    now = datetime(2026, 1, 1, tzinfo=UTC)
    episodes: list[Episode] = []

    def _ep(
        i: int, procedure: str | None, title: str, narrative: str
    ) -> Episode:
        prov: dict[str, Any] = {
            "source_type": "importer",
            "importer_vendor": "claude-mem",
            "extraction_mode": "skip",
            "session_id": "claude-mem-import-syn",
            "x-procedure": procedure,
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

    # Named procedures: the procedure name in EVERY body (distinct subjects so
    # the engine's one-active-per-subject collision does not drop episodes). The
    # procedural query recovers the whole cluster — the deterministic skills are
    # retrievable.
    for i, (procedure, title, narrative) in enumerate(
        [
            ("deploy", "Deploy steps", "To deploy the service: build, test, push."),
            ("deploy", "Deploy checklist", "Deploy requires the build to pass."),
            ("deploy", "Deploy rollback", "Deploy rollback restores the last image."),
            ("backup", "Backup steps", "To backup the database: dump, verify, store."),
            ("backup", "Backup schedule", "Backup runs nightly at 2am."),
            ("backup", "Backup restore", "Backup restore loads the latest dump."),
        ]
    ):
        episodes.append(_ep(i, procedure, title, narrative))
    # Step-referenced procedures: the procedure name in only ONE body (the rest
    # describe steps without naming the procedure). The procedural query recovers
    # only the naming episode — the deterministic skills do NOT cover them (LLM
    # distillation might add value).
    for i, (procedure, title, narrative) in enumerate(
        [
            ("migrate", "Migration steps", "To migrate the schema: run the DDL."),
            ("migrate", "Schema change", "Add the new column to the table."),
            ("migrate", "Data backfill", "Backfill the new column from the old."),
            ("tune", "Tuning steps", "To tune the query: explain, index, retest."),
            ("tune", "Slow query", "The report query scans the whole table."),
            ("tune", "Index choice", "A covering index speeds up the lookup."),
        ]
    ):
        episodes.append(_ep(i + 6, procedure, title, narrative))
    # Background distractors (no procedure): make the top-k selective.
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
    """Run the procedural recall@k measurement.

    For each procedure cluster, query "how do I <procedure>?" and compute the
    fraction of the procedure's episodes recovered (the golden set is ALL
    episodes about the procedure). Returns ``(recall_at_k, recovered_fraction,
    n_procedures, n_procedural_episodes, per_procedure_recall,
    covered_procedures, regime)``.
    """
    clusters = compute_procedure_clusters(episodes)
    if not clusters:
        return 0.0, 0.0, 0, 0, (), 0.0, "hybrid"

    per_procedure_recall: list[float] = []
    total_golden = 0
    total_recovered = 0
    regime = "hybrid"

    for cluster in clusters:
        query = f"how do I {cluster.procedure}?"
        rows = facade.recall(query, k=top_k)
        retrieved = [r.ep_id for r in rows]
        if rows and all(r.score == 0.0 for r in rows):
            regime = _FALLBACK_G2
        golden = set(cluster.ep_ids)
        total_golden += len(golden)
        recovered = len(set(retrieved) & golden)
        total_recovered += recovered
        per_procedure_recall.append(recovered / len(golden))

    recall_at_k = (
        sum(per_procedure_recall) / len(per_procedure_recall)
        if per_procedure_recall
        else 0.0
    )
    recovered_fraction = (
        total_recovered / total_golden if total_golden else 0.0
    )
    covered = (
        sum(1 for r in per_procedure_recall if r >= SKILLS_RECALL_THRESHOLD)
        / len(per_procedure_recall)
        if per_procedure_recall
        else 0.0
    )
    return (
        recall_at_k,
        recovered_fraction,
        len(clusters),
        total_golden,
        tuple(per_procedure_recall),
        covered,
        regime,
    )


def run_skills_experiment(
    *,
    corpus: str = "synthetic",
    db_path: Path | str | None = None,
    top_k: int = SKILLS_TOP_K,
) -> SkillsExperimentResult:
    """Run the procedural recall measurement and return the result.

    ``corpus`` is ``"synthetic"`` (mechanical CI verification) or ``"lmeb-s"``
    (the authoritative run — NOT yet wired: the LMEB-S procedural derivation
    from the haystack is a future step). ``db_path`` defaults to a fresh temp DB
    (reproducible).
    """
    if corpus not in ("synthetic", "lmeb-s"):
        raise ValueError(
            f"unknown corpus: {corpus!r} (expected 'synthetic' or 'lmeb-s')"
        )
    if corpus == "lmeb-s":
        raise NotImplementedError(
            "the authoritative LMEB-S skills run needs the procedural derivation "
            "from the haystack (not yet wired); run corpus='synthetic' to verify "
            "the harness mechanics"
        )
    tmp = Path(tempfile.mkdtemp(prefix="seahorse-skills-"))
    db = Path(db_path) if db_path is not None else tmp / "bench.db"
    facade, storage, episodes = build_synthetic_corpus(db)
    try:
        (
            recall_at_k,
            recovered_fraction,
            n_procedures,
            n_procedural_episodes,
            per_procedure_recall,
            covered,
            regime,
        ) = _measure(facade, episodes, top_k)
    finally:
        storage.close()
    return SkillsExperimentResult(
        recall_at_k=recall_at_k,
        recovered_fraction=recovered_fraction,
        n_procedures=n_procedures,
        n_procedural_episodes=n_procedural_episodes,
        n_queries=n_procedures,
        per_procedure_recall=per_procedure_recall,
        covered_procedures=covered,
        regime=regime,
    )


def decide_skills(result: SkillsExperimentResult) -> dict:
    """Apply the decision: do the deterministic skills suffice?

    Returns a decision dict (``decision``, ``flip``, ``reason``,
    ``recall_at_k``, ``recovered_fraction``). Invalid (no decision) when the run
    degraded to ``fallback_g2`` (fail-loud honesty).
    """
    if result.regime == _FALLBACK_G2:
        return {
            "decision": "invalid_regime",
            "flip": False,
            "reason": (
                "the run degraded to the listing regime (hybrid retrieval not wired); "
                "the skills comparison is not meaningful — re-run with the "
                "embeddings extra"
            ),
            "recall_at_k": result.recall_at_k,
            "recovered_fraction": result.recovered_fraction,
        }
    if result.recall_at_k >= SKILLS_RECALL_THRESHOLD:
        return {
            "decision": "no_llm_distillation",
            "flip": False,
            "reason": (
                f"procedural recall@{SKILLS_TOP_K} {result.recall_at_k:.3f} >= "
                f"threshold {SKILLS_RECALL_THRESHOLD:.1f} — the deterministic "
                f"record_procedure skills are retrievable; no LLM distillation needed"
            ),
            "recall_at_k": result.recall_at_k,
            "recovered_fraction": result.recovered_fraction,
        }
    return {
        "decision": "consider_llm_distillation",
        "flip": True,
        "reason": (
            f"procedural recall@{SKILLS_TOP_K} {result.recall_at_k:.3f} < "
            f"threshold {SKILLS_RECALL_THRESHOLD:.1f} — the deterministic skills "
            f"do NOT cover procedural retrieval; LLM distillation of skills might "
            f"improve it (consider it)"
        ),
        "recall_at_k": result.recall_at_k,
        "recovered_fraction": result.recovered_fraction,
    }


def render_skills_report(result: SkillsExperimentResult, decision: dict) -> str:
    """Human-readable report for the CLI (metrics + decision)."""
    lines = [
        "# Skills retrieval experiment: does LLM distillation of skills improve recall?",
        "",
        f"regime: {result.regime}",
        f"procedures: {result.n_procedures}",
        f"procedural episodes: {result.n_procedural_episodes}",
        f"procedural queries: {result.n_queries}",
        f"per-procedure recall@{SKILLS_TOP_K}: "
        f"{[f'{r:.3f}' for r in result.per_procedure_recall]}",
        f"recall@{SKILLS_TOP_K} (procedural): {result.recall_at_k:.3f}",
        f"procedural episodes recovered: {result.recovered_fraction:.3f}",
        f"covered procedures (recall >= {SKILLS_RECALL_THRESHOLD:.1f}): "
        f"{result.covered_procedures:.3f}",
        "",
        "## Decision",
        f"decision: {decision.get('decision')}",
        f"flip: {decision.get('flip')}",
        f"reason: {decision.get('reason', '')}",
    ]
    return "\n".join(lines)


__all__ = [
    "SKILLS_RECALL_THRESHOLD",
    "SKILLS_TOP_K",
    "SkillsExperimentResult",
    "ProcedureCluster",
    "build_synthetic_corpus",
    "compute_procedure_clusters",
    "decide_skills",
    "render_skills_report",
    "run_skills_experiment",
]
