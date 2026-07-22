"""F3.1 schema surface for the frontmatter layer (f5-03 §7.2).

Re-exports the canonical ``Episode`` (owned by #1, materialized in
``contracts/episode.py``) and adds the F3.1 enums + ``Provenance`` helper that
the migrator (commit 3) and the write-path validator need. The canonical model
stays permissive (``cognitive_type: str | None``, ``provenance: dict``, no
UUIDv7/self-supersede validators) so the engine and its fixtures keep working;
the strict write-time checks that would break those fixtures live here in
``validate_for_write``, applied only on the write/migrate path.

Two validation surfaces (f5-03 §4.1/§7.2):

- **Read path** — ``parse_file`` calls ``Episode.model_validate(fm_plain,
  context={"mvp": mvp})`` directly. The canonical model's ``_reject_naive`` and
  context-gated ``_expired_null_mvp0`` fire here (naive timestamps and MVP-0
  non-null ``expired_at`` become a loud ``FrontmatterInvalid``). UUIDv7 shape and
  self-supersede are NOT checked on the read path: a hand-edited or pre-migration
  note must still be readable, and the migrator is what assigns UUIDv7 ids.

- **Write path** — ``validate_for_write`` adds the UUIDv7 and self-supersede
  checks the canonical model omits (they would break the engine's ``id="e1"``
  fixtures and ``supersedes == id`` cycle tests), then delegates to
  ``model_validate(context={"mvp": mvp})`` for the shared naive/I4 checks. The
  migrator (commit 3) calls this before writing a note.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from seahorse.contracts.episode import Episode
from seahorse.frontmatter.errors import FrontmatterInvalid

__all__ = [
    "CognitiveType",
    "Episode",
    "Provenance",
    "SupersedesReason",
    "validate_for_write",
]


class CognitiveType(str):
    """F3.1 cognitive type vocabulary (f5-03 §7.2).

    Held as plain string values (not an ``Enum``) so the canonical ``Episode``
    field ``cognitive_type: str | None`` accepts them without coupling the model
    to an enum (the engine fixtures use ``"fact"`` and other non-enum strings).
    The migrator (``defaults.py``) picks from these; MVP-1 write validation
    rejects values outside this set.
    """

    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    SOCIAL = "social"
    PROJECT_DOC = "project_doc"
    PROCEDURAL = "procedural"
    WORKING = "working"  # reserved


class SupersedesReason(str):
    """Portable ``supersedes_reason`` vocabulary (f5-03 §12.3)."""

    CONTRADICTION = "contradiction"
    CORRECTION = "correction"
    MERGE = "merge"
    REVALIDATION = "revalidation"
    DECAY = "decay"  # reserved (mediano)


# UUIDv7: version nibble 7, variant nibble 8/9/a/b.
_UUIDV7_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


class Provenance(BaseModel):
    """Migrator-side provenance helper (f5-03 §7.2).

    The canonical ``Episode.provenance`` is a freeform ``dict[str, Any]`` (SO-2:
    #6 uses freeform dicts, not a sub-model). This helper gives the migrator a
    typed shape to build with; it is dumped to a plain dict before constructing
    the ``Episode``. ``extra="allow"`` lets the migrator carry importer-specific
    fields (``importer_vendor``/``importer_loss``/custom ``x-*``) through.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    agent_id: str
    session_id: str
    source_type: str = "agent"
    extraction_mode: str  # skip | llm
    model_used: str | None = None
    tool: str | None = None
    prompt_hash: str | None = None
    confidence: float | None = None
    importer_vendor: str | None = None
    importer_loss: dict[str, Any] | None = None

    @field_validator("prompt_hash")
    @classmethod
    def _hex64(cls, v: str | None) -> str | None:
        if v is not None and (len(v) != 64 or not re.fullmatch(r"[0-9a-f]{64}", v)):
            raise ValueError("prompt_hash must be 64 hex chars")
        return v


def validate_for_write(data: dict[str, Any], *, mvp: str = "0") -> Episode:
    """Write-path validation: UUIDv7 id + no self-supersede + the shared checks.

    Applies the two strict checks the canonical ``Episode`` omits (UUIDv7 shape,
    self-supersede) BEFORE delegating to ``Episode.model_validate(..., context=
    {"mvp": mvp})`` for the naive-datetime and MVP-0 ``expired_at``-null checks.
    Any failure raises ``FrontmatterInvalid`` (the single loud-rejection type
    the migrator and write path catch). ``source_path`` (if supplied in ``data``
    under ``_source_path``) is attached to the error for diagnostics.
    """
    source_path = _path_from_data(data)
    ep_id = data.get("id")
    if not isinstance(ep_id, str) or not _UUIDV7_RE.fullmatch(ep_id):
        raise FrontmatterInvalid(
            source_path, ValueError(f"id must be a valid UUIDv7, got {ep_id!r}")
        )
    supersedes = data.get("supersedes")
    if supersedes is not None and supersedes == ep_id:
        raise FrontmatterInvalid(
            source_path,
            ValueError(f"supersedes must differ from own id ({ep_id})"),
        )
    try:
        return Episode.model_validate(data, context={"mvp": mvp})
    except ValidationError as e:
        raise FrontmatterInvalid(source_path, e) from e


def _path_from_data(data: dict[str, Any]) -> Path:
    p = data.get("_source_path")
    return Path(p) if isinstance(p, str) else Path("<write-path>")