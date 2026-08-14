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

from dataclasses import dataclass, field
from typing import Any

from seahorse.distill.cluster import Cluster, cluster_episodes

CONSOLIDATOR_AGENT = "consolidator"


@dataclass(frozen=True)
class ConsolidateItem:
    """One distilled cluster (per-cluster report row)."""

    key: str
    source_count: int
    status: str
    ep_id: str | None = None


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


def consolidate(facade: Any, *, by: dict[str, Any] | None = None) -> ConsolidateReport:
    """Consolidate recurrent currently-valid episodes into semantic knowledge notes.

    Reads the currently-valid set via ``facade.get_vigente()``, clusters
    EPISODIC sources by subject recurrence (N≥3), and distills each cluster via
    ``facade.distill``. Idempotent: a cluster whose key already has a
    consolidated knowledge note is SKIPPED — the note is the current knowledge,
    not re-distilled. Returns a report (deterministic order).
    """
    effective_by = by or {"source_type": "system", "agent_id": CONSOLIDATOR_AGENT}
    eps = facade.get_vigente()
    # Cluster only EPISODIC sources — consolidated notes are the OUTPUT, not
    # the input (idempotency).
    sources = [e for e in eps if not _is_consolidated(e)]
    existing_keys = {e.subject for e in eps if _is_consolidated(e)}
    clusters = cluster_episodes(sources)
    items: list[ConsolidateItem] = []
    for cluster in clusters:
        if cluster.key in existing_keys:
            continue  # the knowledge note already exists — skip (idempotent)
        wr = facade.distill(
            source_ep_ids=[e.id for e in cluster.episodes],
            representative=cluster.representative,
            consolidated_body=_consolidated_body(cluster),
            by=effective_by,
        )
        items.append(
            ConsolidateItem(
                key=cluster.key,
                source_count=len(cluster.episodes),
                status=wr.status,
                ep_id=wr.ep_id,
            )
        )
    return ConsolidateReport(clusters_found=len(clusters), items=items)


__all__ = ["consolidate", "ConsolidateItem", "ConsolidateReport", "CONSOLIDATOR_AGENT"]
