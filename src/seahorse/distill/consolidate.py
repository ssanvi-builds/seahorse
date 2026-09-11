"""Consolidate orchestration — the deterministic distillation driver.

``consolidate(facade)`` reads the currently-valid set, clusters by subject
recurrence (N≥3), and distills each cluster into a consolidated semantic
episode via the facade. The consolidated body uses the stable clustering key as
its H1 (no ``[session_tag:n]`` suffix). The sources remain valid (they are the
evidence).

Rival absorb (design review post-v1.0, decision 1): a rival vigent episode
holding the cluster key (e.g. an untagged ``remember`` on the same subject)
used to collide forever — one COLLISION row per run, no note, no progress.
When the collision's rival is a CLUSTER MEMBER (its content is already carried
in the distilled body) with a NON-HUMAN ``source_type``, the rival is absorbed:
soft-invalidated via ``forget`` (reason ``absorbed_by_consolidate`` — the audit
trail and the bi-temporal history keep it queryable at any PIT) and the
distill is retried once. A human-authored rival prevails (editorial authority):
the collision is reported with a resolution hint instead.

The trigger is ON-DEMAND (``seahorse consolidate``) — the session-end signal is
OFF by default (single-session consolidation contradicts the evidence; it is
conditioned on real budget pressure).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from seahorse.contracts.engine import InvalidationConflictError, NotFound
from seahorse.distill.cluster import MIN_CLUSTER_SIZE, Cluster, cluster_episodes
from seahorse.distill.synthesis import synthesize_cluster
from seahorse.engine.errors import E_COLLISION_EXISTS, EngineError
from seahorse.llm import LLMClient

CONSOLIDATOR_AGENT = "consolidator"

# Absorb policy (decision 1): only machine-authored rivals are absorbed — a
# human ``remember`` on the same subject may be a deliberate standalone note.
_ABSORBABLE_SOURCES = frozenset({"agent", "system", "importer"})
_ABSORB_REASON = "absorbed_by_consolidate"
_RESOLUTION_HINT = (
    "human-authored rival holds the cluster key; resolve with "
    "`seahorse forget <rival_id>` or `seahorse improve <rival_id>` "
    "(a body with a different H1)"
)


@dataclass(frozen=True)
class ConsolidateItem:
    """One distilled cluster (per-cluster report row).

    ``synthesis`` is the body provenance mode: ``"skip"`` (deterministic),
    ``"llm"`` (LLM-synthesized) or ``"degraded"`` (LLM failed → honest
    fallback with the ``degraded_from`` marker). ``absorbed_rivals`` lists the
    rival episode ids soft-invalidated by the absorb policy. ``detail`` carries
    a human-readable note on non-success rows (e.g. the resolution hint for a
    human-rival COLLISION).
    """

    key: str
    source_count: int
    status: str
    ep_id: str | None = None
    synthesis: str = "skip"
    absorbed_rivals: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class ConsolidateReport:
    """The consolidate run outcome."""

    clusters_found: int = 0
    items: list[ConsolidateItem] = field(default_factory=list)


def is_consolidated(ep: Any) -> bool:
    """True iff ``ep`` is a consolidated knowledge note (the distill OUTPUT)."""
    return (
        ep.cognitive_type == "semantic"
        and ep.provenance.get("extraction_mode") == "consolidated"
    )


def unconsolidated_sources(
    facade: Any, eps: list[Any] | None = None
) -> list[Any]:
    """The currently-valid EPISODIC sources — consolidate's only input.

    Consolidated knowledge notes are the OUTPUT, not the input (idempotency),
    so they are filtered out here. ``eps`` (the already-fetched
    ``facade.get_vigente()`` list) is accepted so callers holding the list
    avoid a second round-trip.
    """
    if eps is None:
        eps = facade.get_vigente()
    return [e for e in eps if not is_consolidated(e)]


# Deterministic-merge size guards. The fallback body must carry EVERY member's
# content (copying only the representative silently dropped the evidence), but
# turn bodies reach 8 KB and the episode body cap is 32 KB — an uncapped merge
# would be REJECTED wholesale. Excerpts are capped per member, and only the
# EVIDENCE_MAX_MEMBERS most recent members are inlined; the note says so and
# points at recall_full for the rest, instead of failing the whole cluster.
_MEMBER_EXCERPT_MAX_CHARS = 3_000
_EVIDENCE_MAX_MEMBERS = 6


def _body_without_h1(ep: Any) -> str:
    """The member body without its leading H1 (the subject heads the excerpt)."""
    lines = ep.body.splitlines()
    if lines and lines[0].lstrip().startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _excerpt(ep: Any) -> str:
    content = _body_without_h1(ep)
    if len(content) <= _MEMBER_EXCERPT_MAX_CHARS:
        return content
    cut = content[:_MEMBER_EXCERPT_MAX_CHARS].rsplit("\n", 1)[0]
    return (
        f"{cut}\n\n(excerpt truncated at {_MEMBER_EXCERPT_MAX_CHARS} chars — "
        f"`recall_full {ep.id}` for the full body)"
    )


def _consolidated_body(cluster: Cluster) -> str:
    """The deterministic consolidated body: stable-key H1 + EVERY member's content.

    Structure: ``# {key}``, ``## Summary`` (the most recent member — the
    representative — with its tagged H1 stripped), ``## Evidence`` with one
    dated ``###`` subsection per member, newest first. Members beyond
    EVIDENCE_MAX_MEMBERS and excerpts beyond _MEMBER_EXCERPT_MAX_CHARS are
    cut with an in-note pointer (honest degrade, never a silent drop).
    """
    parts = [
        f"# {cluster.key}\n\n## Summary\n\n"
        f"{_body_without_h1(cluster.representative)}\n\n## Evidence"
    ]
    shown = cluster.episodes[:_EVIDENCE_MAX_MEMBERS]
    for ep in shown:
        parts.append(f"\n\n### {ep.created_at:%Y-%m-%d} — {ep.subject}\n\n{_excerpt(ep)}")
    hidden = len(cluster.episodes) - len(shown)
    if hidden > 0:
        parts.append(
            f"\n\n(…and {hidden} earlier episode(s) in this cluster — "
            "`recall_timeline` / `recall_full` for each)"
        )
    return "".join(parts)


def _synthesize_or_fallback(
    cluster: Cluster,
    synthesis: str,
    llm_client: LLMClient | None,
) -> tuple[str, dict[str, Any]]:
    """The consolidated body + effective provenance for one cluster.

    ``synthesis="llm"`` with a wired client → LLM synthesis (1 call per
    cluster); on success the body is the synthesized fact and the provenance
    carries ``model_used`` / ``prompt_hash`` / ``confidence``. On failure the
    body is the deterministic fallback and the provenance carries the honest
    degrade marker (``degraded_from="llm"`` + ``degrade_reason``, C8.7).
    ``synthesis="skip"`` (or no client) → the deterministic body, no LLM.
    """
    if synthesis == "llm" and llm_client is not None:
        result = synthesize_cluster(llm_client, cluster)
        if not result.degraded_to_skip:
            return result.consolidated_body, {
                "model_used": result.model_used,
                "prompt_hash": result.prompt_hash,
                "confidence": result.confidence,
            }
        return _consolidated_body(cluster), {
            "model_used": None,
            "prompt_hash": None,
            "confidence": 1.0,
            "degraded_from": "llm",
            "degrade_reason": result.degrade_reason or "llm_degraded",
        }
    return _consolidated_body(cluster), {}


def consolidate(
    facade: Any,
    *,
    by: dict[str, Any] | None = None,
    synthesis: str = "skip",
    llm_client: LLMClient | None = None,
    supersede: bool = False,
    human_edited: Callable[[Any], bool] | None = None,
    min_cluster_size: int = MIN_CLUSTER_SIZE,
) -> ConsolidateReport:
    """Consolidate recurrent currently-valid episodes into semantic knowledge notes.

    Reads the currently-valid set via ``facade.get_vigente()``, clusters
    EPISODIC sources by subject recurrence (N≥3), and distills each cluster via
    ``facade.distill``. Idempotent: a cluster whose key already has a
    consolidated knowledge note is SKIPPED — the note is the current knowledge,
    not re-distilled. ``synthesis="llm"`` (with a wired ``llm_client``) adds the
    off-path LLM synthesis: 1 call per cluster, honest degrade to the
    deterministic fallback on failure. Returns a report (deterministic order).
    ``min_cluster_size`` overrides the recurrence threshold (default 3 —
    additive CLI knob for experimentation; existing behavior unchanged).

    ``supersede=True`` (F7+ supersession, opt-in) UPDATES an existing note when
    the cluster gains NEW valid episodes: the note supersedes the representative
    at consolidation time, so a changed representative means new episodes → the
    note is re-distilled via ``facade.distill(supersede_ep_id=...)`` (improve:
    invalidate + atomic append) instead of skipped. Default False keeps the
    idempotent skip.

    ``human_edited`` (editorial authority — the human prevails) is a predicate
    over an existing note: when it returns True, the note is NEVER superseded
    (the distiller does not silently overwrite a human-authored fact). The CLI
    wires it to the vault mtime check (a note whose ``.md`` was edited after its
    creation is human-touched).
    """
    effective_by = by or {"source_type": "system", "agent_id": CONSOLIDATOR_AGENT}
    eps = facade.get_vigente()
    sources = unconsolidated_sources(facade, eps=eps)
    existing_notes = {e.subject: e for e in eps if is_consolidated(e)}
    clusters = cluster_episodes(sources, min_size=min_cluster_size)
    items: list[ConsolidateItem] = []
    for cluster in clusters:
        existing = existing_notes.get(cluster.key)
        supersede_ep_id: str | None = None
        if existing is not None:
            if not supersede:
                continue  # the knowledge note already exists — skip (idempotent)
            if existing.supersedes == cluster.representative.id:
                continue  # no new episodes — the note is current
            if human_edited is not None and human_edited(existing):
                continue  # the human prevails — never supersede a human edit
            # New episodes → supersession: update the note via improve.
            supersede_ep_id = existing.id
        consolidated_body, llm_by = _synthesize_or_fallback(
            cluster, synthesis, llm_client
        )
        synthesis_label = (
            "llm"
            if "model_used" in llm_by and llm_by.get("model_used")
            else "degraded"
            if "degraded_from" in llm_by
            else "skip"
        )
        try:
            wr = facade.distill(
                source_ep_ids=[e.id for e in cluster.episodes],
                representative=cluster.representative,
                consolidated_body=consolidated_body,
                by={**effective_by, **llm_by},
                supersede_ep_id=supersede_ep_id,
            )
        except EngineError as exc:
            if exc.code != E_COLLISION_EXISTS:
                raise
            # A rival active episode holds the cluster key (e.g. an untagged
            # remember on the same subject) — a handled, reported collision,
            # never a crash (loop L6b, 2026-09-02). The cluster is skipped;
            # the report surfaces it.
            items.append(
                ConsolidateItem(
                    key=cluster.key,
                    source_count=len(cluster.episodes),
                    status="COLLISION",
                    ep_id=None,
                    synthesis=synthesis_label,
                    detail=_RESOLUTION_HINT,
                )
            )
            continue
        absorbed: tuple[str, ...] = ()
        if wr.status == "COLLISION" and supersede_ep_id is None:
            # Absorb policy (decision 1): the collision names a vigent rival
            # holding the key's fact_id. Cluster members with a non-human
            # source_type are absorbed — their content already lives in the
            # distilled body, the soft invalidation keeps them PIT-queryable.
            # A human-authored (or foreign) rival prevails: reported with the
            # resolution hint. One retry, never a loop.
            rival_ids = [
                e.id
                for e in cluster.episodes
                if e.id in {c.existing_id for c in wr.collisions_detected}
                and e.source_type in _ABSORBABLE_SOURCES
            ]
            if rival_ids:
                try:
                    for rival_id in rival_ids:
                        facade.forget(
                            rival_id, reason=_ABSORB_REASON, by=dict(effective_by)
                        )
                    absorbed = tuple(rival_ids)
                    wr = facade.distill(
                        source_ep_ids=[e.id for e in cluster.episodes],
                        representative=cluster.representative,
                        consolidated_body=consolidated_body,
                        by={**effective_by, **llm_by},
                        supersede_ep_id=supersede_ep_id,
                    )
                except EngineError as exc:
                    if exc.code != E_COLLISION_EXISTS:
                        raise
                except (InvalidationConflictError, NotFound):
                    pass  # a concurrent actor won the race — report honestly
        detail = ""
        if wr.status == "COLLISION":
            detail = _RESOLUTION_HINT if not absorbed else (
                "rival(s) absorbed but the key is still held; resolve with "
                "`seahorse forget <rival_id>`"
            )
        items.append(
            ConsolidateItem(
                key=cluster.key,
                source_count=len(cluster.episodes),
                status=wr.status,
                ep_id=wr.ep_id,
                synthesis=synthesis_label,
                absorbed_rivals=absorbed,
                detail=detail,
            )
        )
    return ConsolidateReport(clusters_found=len(clusters), items=items)


__all__ = [
    "CONSOLIDATOR_AGENT",
    "ConsolidateItem",
    "ConsolidateReport",
    "consolidate",
    "is_consolidated",
    "unconsolidated_sources",
]
