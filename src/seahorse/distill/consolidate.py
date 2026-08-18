"""Consolidate orchestration — the deterministic distillation driver.

``consolidate(facade)`` reads the currently-valid set, clusters by subject
recurrence (N≥3), and distills each cluster into a consolidated semantic
episode via the facade. The consolidated body uses the stable clustering key as
its H1 (no ``[session_tag:n]`` suffix). The sources remain valid (they are the
evidence).

The trigger is ON-DEMAND (``seahorse consolidate``) — the session-end signal is
OFF by default (single-session consolidation contradicts the evidence; it is
conditioned on real budget pressure).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from seahorse.distill.cluster import Cluster, cluster_episodes
from seahorse.distill.synthesis import synthesize_cluster
from seahorse.llm import LLMClient

CONSOLIDATOR_AGENT = "consolidator"


@dataclass(frozen=True)
class ConsolidateItem:
    """One distilled cluster (per-cluster report row).

    ``synthesis`` is the body provenance mode: ``"skip"`` (deterministic),
    ``"llm"`` (LLM-synthesized) or ``"degraded"`` (LLM failed → honest
    fallback with the ``degraded_from`` marker).
    """

    key: str
    source_count: int
    status: str
    ep_id: str | None = None
    synthesis: str = "skip"


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
        wr = facade.distill(
            source_ep_ids=[e.id for e in cluster.episodes],
            representative=cluster.representative,
            consolidated_body=consolidated_body,
            by={**effective_by, **llm_by},
            supersede_ep_id=supersede_ep_id,
        )
        items.append(
            ConsolidateItem(
                key=cluster.key,
                source_count=len(cluster.episodes),
                status=wr.status,
                ep_id=wr.ep_id,
                synthesis=(
                    "llm"
                    if "model_used" in llm_by and llm_by.get("model_used")
                    else "degraded"
                    if "degraded_from" in llm_by
                    else "skip"
                ),
            )
        )
    return ConsolidateReport(clusters_found=len(clusters), items=items)


__all__ = ["consolidate", "ConsolidateItem", "ConsolidateReport", "CONSOLIDATOR_AGENT"]
