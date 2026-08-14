"""claude-mem importer — pure mapping + vendor reader.

The claude-mem importer is the **migration/coexistence bridge**: it reads
claude-mem's local data (``~/.claude-mem/claude-mem.db``, worker
``127.0.0.1:37701``) and maps each observation to an F3.1 episode. claude-mem
is NEVER a runtime dependency — the importer reads its SQLite as a one-time
migration source.

``import_record`` is a pure function: vendor record -> F3.1 notes + loss
report. It never fires an LLM (ingestion, not extraction), sets
``provenance.source_type=importer`` + ``importer_vendor=claude-mem`` +
``extraction_mode=skip`` (the write path's ``decide_path`` guard forces skip),
and records ALL loss in ``loss_report`` (auditable). The id is a deterministic
UUIDv5 so re-import is idempotent at the storage layer.

Mapping (claude-mem ``observations`` table -> F3.1):
- ``body`` = ``# {title}\n\n{narrative}`` (H1 = title so the engine derives
  ``subject``). The importer guarantees a body with H1.
- ``valid_at`` = the observation's ``created_at`` (the importer is the
  editorial authority and may set an arbitrary ``valid_at`` for
  ``source_type=importer``).
- ``cognitive_type`` = conservative heuristic from the observation ``type``:
  decision/feature/bugfix/refactor -> ``semantic``; discovery/change ->
  ``episodic``; unknown -> ``semantic``.
- ``provenance`` carries the importer contract + the vendor id as
  ``source_record_id`` (the engine's deterministic-UUIDv5 input) and
  ``x-claude-mem-source-id`` (preservation convention).
- ``provenance`` also preserves the vendor **turn structure**: the
  ``memory_session_id`` and ``prompt_number`` survive as
  ``x-claude-mem-session-id`` / ``x-claude-mem-prompt-number`` so episodes can
  be grouped by turn. The provenance ``session_id`` itself stays run-scoped.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from seahorse.contracts.episode import Episode
from seahorse.engine.canonical import canonical_body_hash
from seahorse.engine.ids import deterministic_id
from seahorse.importer.types import IMPORTER_VERSION, ImporterResult, LossReport

# claude-mem observation types -> cognitive_type (conservative heuristic).
_SEMANTIC_TYPES = frozenset({"decision", "feature", "bugfix", "refactor"})
_EPISODIC_TYPES = frozenset({"discovery", "change"})

# Structured claude-mem fields with no mapping (documented as lost).
_UNMAPPED_FIELDS = (
    "facts",
    "concepts",
    "files_read",
    "files_modified",
    "content_hash",
    "generated_by_model",
    "metadata",
)


def _infer_cognitive_type(obs_type: str) -> str:
    """Conservative heuristic: semantic default, episodic for
    session-event types."""
    if obs_type in _EPISODIC_TYPES:
        return "episodic"
    return "semantic"


def _parse_created_at(raw: Any) -> datetime | None:
    """Parse the claude-mem ``created_at`` ISO-8601 string (aware UTC)."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def import_record(vendor_record: dict, vendor: str) -> ImporterResult:
    """Pure function: vendor record -> F3.1 notes + loss report.

    Never fires an LLM, never writes to the store, never resolves collisions.
    ``vendor`` must be ``"claude-mem"`` (the only materialized importer; the
    contract is extensible to Mem0/Zep).
    """
    if vendor != "claude-mem":
        raise ValueError(
            f"unsupported vendor: {vendor!r} (only 'claude-mem' is materialized)"
        )
    return _map_claude_mem(vendor_record)


def _map_claude_mem(record: dict) -> ImporterResult:
    """Map one claude-mem observation to one F3.1 episode (ADD, snapshot)."""
    source_id = str(record.get("id") or "")
    title = (record.get("title") or "").strip()
    narrative = (record.get("narrative") or "").strip()
    obs_type = (record.get("type") or "").strip()
    created_at = _parse_created_at(record.get("created_at"))
    agent_id = (record.get("agent_id") or "claude-mem-import").strip()

    # Body with H1 = title (the engine derives subject from H1). The importer
    # guarantees a body with H1 so the skip path never falls to
    # deterministic_extract's loud SubjectDerivationError.
    subject = title or obs_type or "claude-mem observation"
    body = f"# {subject}\n\n{narrative}" if narrative else f"# {subject}"

    cognitive_type = _infer_cognitive_type(obs_type)
    loss = _build_loss_report(record, source_id, obs_type, created_at, agent_id)

    provenance: dict[str, Any] = {
        "source_type": "importer",
        "importer_vendor": "claude-mem",
        "extraction_mode": "skip",
        "model_used": None,
        "prompt_hash": None,
        "confidence": 1.0,
        "tool": IMPORTER_VERSION,
        "agent_id": agent_id,
        "session_id": "claude-mem-import",  # runner overrides with the run-scoped id
        "source_record_id": source_id,
        "importer_loss": loss,
        "x-claude-mem-source-id": source_id,
        # Turn structure: the vendor session + prompt_number survive so episodes
        # can be grouped by turn. The provenance ``session_id`` stays
        # run-scoped; these preservation fields carry the vendor's own grouping.
        "x-claude-mem-session-id": record.get("memory_session_id"),
        "x-claude-mem-prompt-number": record.get("prompt_number"),
    }

    ep = Episode(
        id=deterministic_id("claude-mem", source_id, canonical_body_hash(body)),
        created_at=created_at or datetime.now(UTC),
        schema_version="1.1",
        provenance=provenance,
        body=body,
        valid_at=created_at,
        cognitive_type=cognitive_type,
        source_type="importer",
        title=title or None,
    )
    return ImporterResult(notes=[ep], loss_report=loss)


def _build_loss_report(
    record: dict, source_id: str, obs_type: str, created_at: datetime | None, agent_id: str
) -> LossReport:
    """Document every loss/synthesis for the record (auditable)."""
    fields_lost: list[str] = []
    fields_synthesized: list[str] = []
    structural_loss: list[str] = []

    for field in _UNMAPPED_FIELDS:
        if record.get(field) not in (None, "", [], {}):
            fields_lost.append(f"{field}:not_mapped_to_f3_1")
    fields_lost.append("created_at:engine_overwrites_with_now_i1")
    fields_lost.append("vector_internals:not_imported")

    if created_at is None:
        fields_synthesized.append("valid_at:missing_created_at_fallback_now")
    else:
        fields_synthesized.append("valid_at:synthesized_from_created_at")
    fields_synthesized.append("cognitive_type:inferred_heuristic")
    fields_synthesized.append("session_id:synthesized_run_scoped")
    if record.get("memory_session_id") is not None:
        fields_synthesized.append("session_id:vendor_session_preserved_x_claude_mem_session_id")
    if record.get("prompt_number") is not None:
        fields_synthesized.append("prompt_number:preserved_x_claude_mem_prompt_number")
    if not record.get("agent_id"):
        fields_synthesized.append("agent_id:defaulted_no_vendor_agent_id")

    notes = (
        f"ADD: 1 current-state episode (type={obs_type or 'unknown'}, "
        f"cognitive_type={_infer_cognitive_type(obs_type)})"
    )
    return LossReport(
        vendor="claude-mem",
        source_record_id=source_id,
        fields_lost=fields_lost,
        fields_synthesized=fields_synthesized,
        structural_loss=structural_loss,
        notes=notes,
    )


class ClaudeMemReader:
    """Reads claude-mem observations from its local SQLite (one-time migration).

    ``claude-mem`` is NOT a runtime dependency: this reader opens the vendor DB
    read-only (``mode=ro``) and yields plain dict rows. The default path is
    ``~/.claude-mem/claude-mem.db`` (the local worker's store).
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        default = Path.home() / ".claude-mem" / "claude-mem.db"
        self.db_path = Path(db_path) if db_path is not None else default

    def iter_observations(self, *, project: str | None = None) -> list[dict]:
        """Return all observations (optionally filtered by ``project``)."""
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"claude-mem DB not found at {self.db_path} (is claude-mem installed?)"
            )
        query = "SELECT * FROM observations"
        params: tuple[Any, ...] = ()
        if project:
            query += " WHERE project = ?"
            params = (project,)
        query += " ORDER BY id ASC"
        with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


__all__ = ["ClaudeMemReader", "import_record"]
