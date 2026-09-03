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
from seahorse.distill.cluster import Cluster, cluster_episodes
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


def _is_consolidated(ep: Any) -> bool:
    """True iff ``ep`` is a consolidated knowledge note (the distill OUTPUT)."""
    return (
        ep.cognitive_type == "semantic"
        and ep.provenance.get("extraction_mode") == "consolidated"
    )


def _consolidated_body(cluster: Cluster) -> str:
    """The consolidated body: stable clustering-key H1 + representative content.

    The representative's body starts with the tagged H1 (``[session_tag:n]``);
    it is replaced by the stable key so the knowledge note is clean.
    """
    lines = cluster.representative.body.splitlines()
    if lines and lines[0].lstrip().startswith("# "):
        lines = lines[1:]
    content = "\n".join(lines).strip()
    return f"# {cluster.key}\n\n{content}"


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
) -> ConsolidateReport:
    """Consolidate recurrent currently-valid episodes into semantic knowledge notes.

    Reads the currently-valid set via ``facade.get_vigente()``, clusters
    EPISODIC sources by subject recurrence (N≥3), and distills each cluster via
    ``facade.distill``. Idempotent: a cluster whose key already has a
    consolidated knowledge note is SKIPPED — the note is the current knowledge,
    not re-distilled. ``synthesis="llm"`` (with a wired ``llm_client``) adds the
    off-path LLM synthesis: 1 call per cluster, honest degrade to the
    deterministic fallback on failure. Returns a report (deterministic order).

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
    # Cluster only EPISODIC sources — consolidated notes are the OUTPUT, not
    # the input (idempotency).
    sources = [e for e in eps if not _is_consolidated(e)]
    existing_notes = {e.subject: e for e in eps if _is_consolidated(e)}
    clusters = cluster_episodes(sources)
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


__all__ = ["consolidate", "ConsolidateItem", "ConsolidateReport", "CONSOLIDATOR_AGENT"]
