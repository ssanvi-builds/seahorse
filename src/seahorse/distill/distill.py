"""The ``distill_episodes`` primitive — deterministic distillation.

A write-path operation over existing mechanisms, NOT new storage:
``consolidated`` is schema-valid, ``cognitive_type=semantic`` exists, and the
consolidated episode references its representative source via ``supersedes``
WITHOUT invalidating it — the sources remain valid (they are the evidence). The
provenance carries ``extraction_mode=consolidated``. The subject is the stable
clustering key (distinct from the per-turn stored subjects).

The distillation does NOT pass through ``decide_path``: it is a batch operation,
not single-episode ingestion — it writes via ``engine.remember`` directly
(like ``improve``).
"""

from __future__ import annotations

from typing import Any

from seahorse.contracts.engine import WriteResult
from seahorse.distill.cluster import cluster_key
from seahorse.engine.ids import new_uuid7
from seahorse.write_path.extract import derive_summary

SUPERSEDES_REASON_MERGE = "merge"

_EDGE_EVIDENCE = "evidence"
_EDGE_SUPERSEDES = "supersedes"


def _derived_from(
    engine: Any,
    source_ep_ids: list[str],
    supersede_ep_id: str | None,
) -> list[dict[str, str]]:
    """The structured cluster membership (F3.2): one ``{id, edge_kind}`` edge
    per source episode, emitted into the consolidated episode's provenance and
    materialized as ``x-seahorse-derived-from`` — an importer never has to
    re-implement the clusterer to know the complete evidence set.

    On a fresh distill every source episode is an ``evidence`` edge, in source
    order. On supersession (the note is UPDATED via ``engine.improve``) the
    new note INHERITS the superseded note's edges (the membership that does
    not change), adds the new cluster's episodes as ``evidence``, and closes
    the lineage with the superseded note itself as a ``supersedes`` edge.
    Dedupe is by id — an episode that is both inherited evidence and a new
    cluster member lands once, as ``evidence``. The enumeration is frozen at
    format promotion; unknown ``edge_kind`` values are preserved, never
    rejected (the same policy as every ``x-*`` field).
    """
    edges: list[dict[str, str]] = [
        {"id": ep_id, "edge_kind": _EDGE_EVIDENCE} for ep_id in source_ep_ids
    ]
    if supersede_ep_id is None:
        return edges
    old = engine.get(supersede_ep_id)
    if old is not None:
        known = {edge["id"] for edge in edges}
        for edge in old.provenance.get("derived_from") or []:
            if edge.get("id") not in known and edge["id"] != supersede_ep_id:
                edges.append(edge)
    edges.append({"id": supersede_ep_id, "edge_kind": _EDGE_SUPERSEDES})
    return edges


def distill_episodes(
    engine: Any,
    source_ep_ids: list[str],
    representative: Any,
    consolidated_body: str,
    by: dict[str, Any],
    supersede_ep_id: str | None = None,
) -> Any:
    """Write a consolidated semantic episode from source episodes.

    ``representative`` is the most recent source episode (the cluster's
    representative). The consolidated episode:
    - ``cognitive_type=semantic``, ``extraction_mode=consolidated`` (provenance).
    - ``subject`` = the stable clustering key (no ``[session_tag:n]`` suffix).
    - ``supersedes=representative.id``, ``supersedes_reason=merge`` — a soft
      reference; the sources remain valid (they are the evidence).
    - ``session_id`` = a synthetic consolidator session (``consolidate-*``).
    - ``summary`` = the deterministic fallback (first sentence skipping the H1).

    With ``supersede_ep_id`` (F7+ supersession) the existing consolidated note
    is UPDATED via ``engine.improve`` (invalidate + atomic append,
    ``supersedes_reason=CORRECTION``) instead of a fresh ``remember`` — the
    note stays the single current knowledge for its key. ``engine.improve``
    does NOT force ``extraction_mode=skip`` (that is the facade's improve), so
    the consolidated provenance + LLM provenance pass through.

    Returns the engine ``WriteResult`` verbatim (a synthesized one for the
    supersession path, since ``engine.improve`` returns an ``Episode``).
    """
    if representative.id not in source_ep_ids:
        raise ValueError("representative must be one of source_ep_ids")
    # The provenance from ``by`` is respected: a caller that synthesized the
    # body (LLM) or degraded honestly (C8.7) passes ``model_used`` /
    # ``prompt_hash`` / ``confidence`` / ``degraded_from`` / ``degrade_reason``
    # through. Without them, the deterministic defaults apply (None/None/1.0).
    effective_by: dict[str, Any] = {
        **by,
        "extraction_mode": "consolidated",
        "model_used": by.get("model_used"),
        "prompt_hash": by.get("prompt_hash"),
        "confidence": by.get("confidence", 1.0),
    }
    if not effective_by.get("session_id"):
        effective_by["session_id"] = f"consolidate-{new_uuid7()}"
    # F3.2: the structured membership travels in provenance (the persisted
    # JSON column) so the materializer can emit ``x-seahorse-derived-from``
    # on the consolidated note.
    effective_by["derived_from"] = _derived_from(engine, source_ep_ids, supersede_ep_id)
    if supersede_ep_id is not None:
        new_ep = engine.improve(
            supersede_ep_id,
            consolidated_body,
            by=effective_by,
        )
        return WriteResult(
            ep_id=new_ep.id,
            fact_id=new_ep.fact_id,
            status="ACTIVE",
            collisions_detected=[],
        )
    return engine.remember(
        body=consolidated_body,
        by=effective_by,
        cognitive_type="semantic",
        title=representative.subject,
        summary=derive_summary(consolidated_body),
        subject=cluster_key(representative.subject),
        supersedes=representative.id,
        supersedes_reason=SUPERSEDES_REASON_MERGE,
    )


__all__ = ["distill_episodes", "SUPERSEDES_REASON_MERGE"]
