"""Facade payload types (owned by #12).

The canonical Python API shapes for the four memory-native primitives plus the
three progressive-disclosure read levels. ``Provenance`` is a
``TypedDict(total=False)`` so it IS a plain dict at runtime — it passes
straight into the engine's ``by: dict`` parameter and is JSON-serializable for
#13. The payload dataclasses are frozen.

Ownership note (f5-12 §3): ``Provenance`` is **facade-owned**, not
``contracts``-owned. ``contracts/`` is for cross-component frontiers of the
lower layers; #1 (the Pydantic schema authority) has not shipped, so freezing a
``Provenance`` frontier in ``contracts/`` would falsely sign off #1's shape.
When #1 ships, this dissolves into an import. This mirrors
``disclosure/types.py`` (owned by #8).

``COGNITIVE_TYPES`` / ``SOURCE_TYPES`` are **informative** frozensets referenced
by #13/#14 for UI vocabularies. #12 does NOT enforce them at the boundary in
MVP-0 — the engine (#2) and the schema (#1) are the authority for those domain
invariants, and #12 is a clean delegation layer (it does not replicate engine
invariants). This avoids the drift where engine tests use ``cognitive_type=
"fact"`` (a value outside the f5-01 enum).

The canonical home for these vocabularies is ``seahorse/constants.py`` (the
shared module #13 and #14 import, per SO-14-03 / f5-14 §Pins). This module
re-exports them so the #12 public API stays stable.

References:
- f5-12 §3 (payload shapes, Provenance facade-owned)
- f5-01 §2.4 (cognitive_type vocabulary)
- seahorse/disclosure/types.py (PITPoint, TOP_K — re-exported, not redefined)
- seahorse/engine/engine.py (BiTemporalEngine.remember/forget/improve `by: dict`)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypedDict

from seahorse.constants import COGNITIVE_TYPES, SOURCE_TYPES
from seahorse.disclosure.types import TOP_K, PITPoint

ExtractionMode = Literal["skip", "llm"]


class Provenance(TypedDict, total=False):
    """Caller-supplied authority + write-path provenance (a plain dict at runtime).

    ``source_type`` is the caller's authority (``agent``|``human``|``importer``|
    ``system``); #5's ``decide_path`` uses it to route importer payloads down
    the deterministic skip-path. The write-path overwrites ``extraction_mode``/
    ``model_used``/``prompt_hash``/``confidence``; the rest is stored verbatim
    by the engine as ``Episode.provenance``.

    ``degraded_from`` / ``degrade_reason`` are the durable degrade marker
    (C8.7, ADR-10): present ONLY on an llm→skip degrade, never on a genuine
    skip. They distinguish a degraded episode from a real skip in stored
    provenance (no "permanent lie"). Per f5-05 sec 5 line 111 the caller's
    CLAIMED ``model_used``/``prompt_hash`` (the LLM intent that was degraded)
    are LOGUED by #5, NOT stored in core — core stays ``None`` on degrade.
    """

    source_type: str
    agent_id: str
    session_id: str
    extraction_mode: str
    model_used: str | None
    prompt_hash: str | None
    confidence: float
    importer_vendor: str  # importer path (deterministic UUIDv5, SO-4b)
    source_record_id: str  # importer path
    degraded_from: str  # llm→skip degrade marker (C8.7, ADR-10) — degrade-only
    degrade_reason: str  # llm→skip degrade marker (C8.7, ADR-10) — degrade-only


# Informative vocabularies (NOT enforced by #12 in MVP-0; engine/#1 authority).
# Active + reserved cognitive types per f5-01 §2.4. Defined in
# ``seahorse/constants.py`` (shared with #13/#14) and re-exported here.


@dataclass(frozen=True)
class RememberPayload:
    """Payload for the ``remember`` primitive (delegated to #5 ``ingest``).

    ``title`` forwards to ``engine.remember`` (subject derivation: title > H1 >
    None). ``tags`` is a forward-compat field: MVP-0 has no tags write-path, so
    #12 rejects a non-empty ``tags`` at the border (``E_NOT_IN_MVP_0_1``) rather
    than silently dropping it (ADR-10 honesty).
    """

    body: str
    by: Provenance
    valid_at: datetime | None = None
    cognitive_type: str | None = None
    title: str | None = None
    tags: tuple[str, ...] = ()
    schema_version: str = "1.1"


@dataclass(frozen=True)
class RecallPayload:
    """Payload for the ``recall`` primitive (MVP-0 G2 vigente listing).

    In MVP-0 the ``query`` is validated non-empty but is NOT used for ranking —
    the canonical MVP-0 recall is a vigente listing ordered by ``created_at``
    desc. ``pit`` is accepted by the type but refused by the facade in MVP-0
    (``PitRecallNotSupportedMVP0``); the #11 retrieval path is MVP-1.
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


__all__ = [
    "Provenance",
    "ExtractionMode",
    "COGNITIVE_TYPES",
    "SOURCE_TYPES",
    "RememberPayload",
    "RecallPayload",
    "FacadeConfig",
    "PITPoint",
    "TOP_K",
]