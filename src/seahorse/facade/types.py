"""Facade payload types (owned by the facade).

The canonical Python API shapes for the four memory-native primitives plus the
three progressive-disclosure read levels. ``Provenance`` is a
``TypedDict(total=False)`` so it IS a plain dict at runtime — it passes
straight into the engine's ``by: dict`` parameter and is JSON-serializable for
the MCP server. The payload dataclasses are frozen.

``Provenance`` is **facade-owned**, not ``contracts``-owned. ``contracts/`` is
for cross-component frontiers of the lower layers; the schema module (the
Pydantic schema authority) has not shipped, so freezing a ``Provenance``
frontier in ``contracts/`` would falsely sign off its shape. When the schema
module ships, this dissolves into an import. This mirrors
``disclosure/types.py`` (owned by progressive disclosure).

``COGNITIVE_TYPES`` / ``SOURCE_TYPES`` are **informative** frozensets referenced
by the MCP server and CLI for UI vocabularies. The facade does NOT enforce them
at the boundary in the current release — the engine and the schema are the
authority for those domain invariants, and the facade is a clean delegation layer
(it does not replicate engine invariants). This avoids the drift where engine
tests use ``cognitive_type="fact"`` (a value outside the vocabulary enum).

The canonical home for these vocabularies is ``seahorse/constants.py`` (the
shared module the MCP server and CLI import). This module re-exports them so the
facade public API stays stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypedDict

from seahorse.constants import COGNITIVE_TYPES, SOURCE_TYPES
from seahorse.disclosure.types import TOP_K, PITPoint

# The ``extraction_mode`` schema value set (facade-owned, single source for the
# MCP server).
# - ``skip`` / ``llm`` — single-episode ingestion modes, routable by the write
#   path's ``decide_path``.
# - ``consolidated`` — schema-valid, round-trippable batch-distillation marker
#   (a stable knowledge note). NOT routable by single-episode ingestion: the
#   write path's ``decide_path`` refuses it, because the batch distillation
#   writes via ``engine.remember`` directly, bypassing ``decide_path``. The
#   engine does not produce it yet (schema-valid, not built).
# - ``llm_partial`` — fully reserved (not in the schema value set).
#
# The MCP server's wire enum derives from this Literal
# (``wire_schema._EXTRACTION_MODE_ENUM``), so the two sister projections share
# one definition of the value set.
ExtractionMode = Literal["skip", "llm", "consolidated"]


class Provenance(TypedDict, total=False):
    """Caller-supplied authority + write-path provenance (a plain dict at runtime).

    ``source_type`` is the caller's authority (``agent``|``human``|``importer``|
    ``system``); the write path's ``decide_path`` uses it to route importer
    payloads down the deterministic skip-path. The write-path overwrites
    ``extraction_mode``/``model_used``/``prompt_hash``/``confidence``; the rest
    is stored verbatim by the engine as ``Episode.provenance``.

    ``degraded_from`` / ``degrade_reason`` are the durable degrade marker:
    present ONLY on an llm→skip degrade, never on a genuine skip. They
    distinguish a degraded episode from a real skip in stored provenance (no
    "permanent lie"). The caller's CLAIMED ``model_used``/``prompt_hash`` (the
    LLM intent that was degraded) are LOGGED by the write path, NOT stored in
    core — core stays ``None`` on degrade.
    """

    source_type: str
    agent_id: str
    session_id: str
    extraction_mode: str
    model_used: str | None
    prompt_hash: str | None
    confidence: float
    importer_vendor: str  # importer path (deterministic UUIDv5)
    source_record_id: str  # importer path
    degraded_from: str  # llm→skip degrade marker — degrade-only
    degrade_reason: str  # llm→skip degrade marker — degrade-only


# Informative vocabularies (NOT enforced by the facade in the current release;
# the engine / schema are the authority). Active + reserved cognitive types.
# Defined in ``seahorse/constants.py`` (shared with the MCP server and CLI) and
# re-exported here.


@dataclass(frozen=True)
class RememberPayload:
    """Payload for the ``remember`` primitive (delegated to the write path's ``ingest``).

    ``title`` forwards to ``engine.remember`` (subject derivation: title > H1 >
    None). ``summary`` is an additive editorial field: when ``None``, the write
    path derives a deterministic fallback (first sentence of the body, truncated
    to ``SUMMARY_MAX_CHARS=200``) — zero-LLM, covers 100% of episodes including
    the skip path. ``tags`` is a forward-compat field: the current release has
    no tags write-path, so the facade rejects a non-empty ``tags`` at the border
    (``E_NOT_IN_MVP_0_1``) rather than silently dropping it.
    """

    body: str
    by: Provenance
    valid_at: datetime | None = None
    cognitive_type: str | None = None
    title: str | None = None
    summary: str | None = None
    tags: tuple[str, ...] = ()
    schema_version: str = "1.1"


@dataclass(frozen=True)
class RecallPayload:
    """Payload for the ``recall`` primitive (current-state listing).

    In the current release the ``query`` is validated non-empty but is NOT used
    for ranking — the canonical recall is a current-state listing ordered by
    ``created_at`` desc. ``pit`` is accepted by the type but refused by the
    facade in the current release (``PitRecallNotSupportedMVP0``); the hybrid
    retrieval path is a later release.
    """

    query: str
    pit: PITPoint | None = None
    k: int = TOP_K
    cognitive_type: str | None = None
    subject_filter: str | None = None
    anchor_ep_id: str | None = None
    hops: int = 1


@dataclass(frozen=True)
class FacadeConfig:
    """Defaults for ``MemoryFacade`` (extraction mode, top-k, phase)."""

    default_extraction_mode: ExtractionMode = "skip"
    default_cognitive_type: str | None = None
    top_k: int = TOP_K
    phase: Literal["mvp0", "mvp1", "mediano"] = "mvp0"


@dataclass(frozen=True)
class ContextEpisode:
    """One INDEX-level row of the context bootstrap. No body."""

    ep_id: str
    subject: str | None
    summary: str | None
    created_at: datetime
    session_id: str | None


@dataclass(frozen=True)
class ContextData:
    """The context bootstrap data — assembled by the facade.

    ``recent`` is the top-k currently valid episodes (created_at desc, ep_id asc
    — deterministic sort). ``vigente_count`` is the full currently valid set
    size. ``last_session`` is the most recent session's episodes grouped by
    ``provenance.session_id`` — an INDEX list, NOT an abstractive summary
    (honesty: Seahorse has no session summaries yet). The assembler renders this
    to the bootstrap text.
    """

    recent: list[ContextEpisode]
    vigente_count: int
    last_session_id: str | None
    last_session: list[ContextEpisode]
    total_episodes: int


__all__ = [
    "Provenance",
    "ExtractionMode",
    "COGNITIVE_TYPES",
    "SOURCE_TYPES",
    "RememberPayload",
    "RecallPayload",
    "FacadeConfig",
    "ContextEpisode",
    "ContextData",
    "PITPoint",
    "TOP_K",
]