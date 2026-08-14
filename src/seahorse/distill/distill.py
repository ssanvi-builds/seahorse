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

from seahorse.distill.cluster import cluster_key
from seahorse.engine.ids import new_uuid7
from seahorse.write_path.extract import derive_summary

SUPERSEDES_REASON_MERGE = "merge"


def distill_episodes(
    engine: Any,
    source_ep_ids: list[str],
    representative: Any,
    consolidated_body: str,
    by: dict[str, Any],
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

    Returns the engine ``WriteResult`` verbatim.
    """
    if representative.id not in source_ep_ids:
        raise ValueError("representative must be one of source_ep_ids")
    effective_by: dict[str, Any] = {
        **by,
        "extraction_mode": "consolidated",
        "model_used": None,
        "prompt_hash": None,
        "confidence": 1.0,
    }
    if not effective_by.get("session_id"):
        effective_by["session_id"] = f"consolidate-{new_uuid7()}"
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
