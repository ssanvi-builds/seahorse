"""chatgpt importer — pure mapping + vendor reader.

The chatgpt importer is the one-way migration bridge for a ChatGPT data export:
it reads the export's ``memory.json`` (saved memories — durable content) and
``conversations.json`` (raw chat history) and maps each item to an F3.1 episode.
ChatGPT is NEVER a runtime dependency — the importer reads the export files as a
one-time migration source.

``import_record`` is a pure function: vendor record -> F3.1 notes + loss report.
It never fires an LLM (ingestion, not extraction), sets
``provenance.source_type=importer`` + ``importer_vendor=chatgpt`` +
``extraction_mode=skip`` (the write path's ``decide_path`` guard forces skip),
and records ALL loss in ``loss_report`` (auditable). The id is a deterministic
UUIDv5 so re-import is idempotent at the storage layer.

Mapping (ChatGPT export -> F3.1):
- ``memory`` (saved memory) -> ``cognitive_type=semantic``; ``subject`` = the
  text truncated to a short H1; ``body = "# {subject}\\n\\n{text}"``.
- ``conversation`` -> ``cognitive_type=episodic``; ``subject`` = the title
  (fallback "ChatGPT conversation"); ``body`` = a BOUNDED summary (create/update
  time, message count, first user message) — the full ``mapping`` transcript is
  deliberately NOT inlined and is documented as lost.
- ``valid_at`` = the vendor timestamp (the importer is the editorial authority
  and may set an arbitrary ``valid_at`` for ``source_type=importer``).
- ``provenance`` carries the importer contract + the vendor id as
  ``source_record_id`` and ``x-chatgpt-source-id`` (preservation convention).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from seahorse.contracts.episode import Episode
from seahorse.engine.canonical import canonical_body_hash
from seahorse.engine.ids import deterministic_id
from seahorse.importer.types import IMPORTER_VERSIONS, ImporterResult, LossReport

_AGENT_ID = "chatgpt-import"
_SUBJECT_MAX = 80

# Structured memory fields with no mapping (documented as lost).
_MEMORY_UNMAPPED_FIELDS = (
    "attachments",
    "categories",
    "metadata",
)


def _truncate(text: str, limit: int) -> str:
    """Clip ``text`` to ``limit`` chars for a readable H1 subject."""
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 1].rstrip() + "…"


def _parse_timestamp(raw: Any) -> datetime | None:
    """Parse a ChatGPT timestamp: unix seconds (int/float) or ISO-8601 string."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw, tz=UTC)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(raw, str) and raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    return None


def import_record(vendor_record: dict, vendor: str) -> ImporterResult:
    """Pure function: vendor record -> F3.1 notes + loss report.

    Never fires an LLM, never writes to the store, never resolves collisions.
    ``vendor`` must be ``"chatgpt"``.
    """
    if vendor != "chatgpt":
        raise ValueError(
            f"unsupported vendor: {vendor!r} (only 'chatgpt' is materialized here)"
        )
    kind = vendor_record.get("kind")
    if kind == "conversation":
        return _map_conversation(vendor_record)
    return _map_memory(vendor_record)


def _map_memory(record: dict) -> ImporterResult:
    """Map one saved memory to one semantic episode (ADD, snapshot)."""
    text = (record.get("text") or "").strip()
    source_id = str(record.get("id") or "") or text or "chatgpt-memory"
    subject = _truncate(text, _SUBJECT_MAX) or "ChatGPT memory"
    body = f"# {subject}\n\n{text}" if text else f"# {subject}"
    created_at = record.get("created_at")
    loss = _build_loss_report(record, source_id, "memory", created_at)

    provenance: dict[str, Any] = {
        "source_type": "importer",
        "importer_vendor": "chatgpt",
        "extraction_mode": "skip",
        "model_used": None,
        "prompt_hash": None,
        "confidence": 1.0,
        "tool": IMPORTER_VERSIONS["chatgpt"],
        "agent_id": _AGENT_ID,
        "session_id": "chatgpt-import",  # runner overrides with the run-scoped id
        "source_record_id": source_id,
        "importer_loss": loss,
        "x-chatgpt-source-id": source_id,
        "x-chatgpt-kind": "memory",
    }

    ep = Episode(
        id=deterministic_id("chatgpt", source_id, canonical_body_hash(body)),
        created_at=created_at or datetime.now(UTC),
        schema_version="1.1",
        provenance=provenance,
        body=body,
        valid_at=created_at,
        cognitive_type="semantic",
        source_type="importer",
        title=subject or None,
    )
    return ImporterResult(notes=[ep], loss_report=loss)


def _map_conversation(record: dict) -> ImporterResult:
    """Map one conversation to one episodic episode (bounded body)."""
    source_id = str(record.get("id") or "") or "chatgpt-conversation"
    title = (record.get("title") or "").strip()
    subject = title or "ChatGPT conversation"
    create_time = record.get("create_time")
    update_time = record.get("update_time")
    message_count = record.get("message_count")
    first_user_text = (record.get("first_user_text") or "").strip()

    lines = [f"# {subject}", ""]
    if create_time is not None:
        lines.append(f"- create_time: {create_time.isoformat()}")
    if update_time is not None:
        lines.append(f"- update_time: {update_time.isoformat()}")
    lines.append(f"- message_count: {message_count}")
    if first_user_text:
        lines.append(f"- first_user_message: {_truncate(first_user_text, 200)}")
    body = "\n".join(lines)

    loss = _build_loss_report(record, source_id, "conversation", create_time)

    provenance: dict[str, Any] = {
        "source_type": "importer",
        "importer_vendor": "chatgpt",
        "extraction_mode": "skip",
        "model_used": None,
        "prompt_hash": None,
        "confidence": 1.0,
        "tool": IMPORTER_VERSIONS["chatgpt"],
        "agent_id": _AGENT_ID,
        "session_id": "chatgpt-import",  # runner overrides with the run-scoped id
        "source_record_id": source_id,
        "importer_loss": loss,
        "x-chatgpt-source-id": source_id,
        "x-chatgpt-conversation-id": source_id,
        "x-chatgpt-kind": "conversation",
    }

    ep = Episode(
        id=deterministic_id("chatgpt", source_id, canonical_body_hash(body)),
        created_at=create_time or datetime.now(UTC),
        schema_version="1.1",
        provenance=provenance,
        body=body,
        valid_at=create_time,
        cognitive_type="episodic",
        source_type="importer",
        title=title or None,
    )
    return ImporterResult(notes=[ep], loss_report=loss)


def _build_loss_report(
    record: dict, source_id: str, kind: str, created_at: datetime | None
) -> LossReport:
    """Document every loss/synthesis for the record (auditable)."""
    fields_lost: list[str] = []
    fields_synthesized: list[str] = []
    structural_loss: list[str] = []

    if kind == "memory":
        for field in _MEMORY_UNMAPPED_FIELDS:
            if record.get(field) not in (None, "", [], {}):
                fields_lost.append(f"{field}:not_mapped_to_f3_1")
    else:
        # The full transcript (mapping) is never inlined into a bounded body.
        fields_lost.append("transcript:not_inlined_bounded_body")
        fields_lost.append("mapping:not_inlined_bounded_body")

    fields_lost.append("created_at:engine_overwrites_with_now_i1")
    fields_lost.append("vector_internals:not_imported")

    if created_at is None:
        fields_synthesized.append("valid_at:missing_timestamp_fallback_now")
    else:
        fields_synthesized.append("valid_at:synthesized_from_vendor_timestamp")
    fields_synthesized.append("cognitive_type:mapped_from_kind")
    fields_synthesized.append("session_id:synthesized_run_scoped")

    notes = (
        f"ADD: 1 current-state episode (kind={kind}, "
        f"cognitive_type={'semantic' if kind == 'memory' else 'episodic'})"
    )
    return LossReport(
        vendor="chatgpt",
        source_record_id=source_id,
        fields_lost=fields_lost,
        fields_synthesized=fields_synthesized,
        structural_loss=structural_loss,
        notes=notes,
    )


class ChatGPTReader:
    """Reads a ChatGPT data export into normalized vendor records.

    ``source_dir`` is the unpacked export directory containing ``memory.json``
    and ``conversations.json``. The reader is defensive: it tolerates both a
    JSON list and a dict wrapper for ``memory.json``, and skips items it cannot
    normalize (they are surfaced by the runner as per-item failures, not here).
    """

    def __init__(self, source_dir: Path | str) -> None:
        self.source_dir = Path(source_dir)

    def iter_records(self) -> list[dict]:
        """Return normalized records: saved memories then conversations."""
        records: list[dict] = []
        records.extend(self._read_memories())
        records.extend(self._read_conversations())
        return records

    def _read_memories(self) -> list[dict]:
        path = self.source_dir / "memory.json"
        if not path.exists():
            return []
        raw = _load_json(path)
        items = raw if isinstance(raw, list) else raw.get("memories", [])
        records: list[dict] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            record = _normalize_memory(item)
            if record is not None:
                records.append(record)
        return records

    def _read_conversations(self) -> list[dict]:
        path = self.source_dir / "conversations.json"
        if not path.exists():
            return []
        raw = _load_json(path)
        items = raw if isinstance(raw, list) else []
        records: list[dict] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            records.append(_normalize_conversation(item))
        return records


def _load_json(path: Path) -> Any:
    """Load a JSON export file (raise loudly — the runner treats it as fatal)."""
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_memory(item: dict) -> dict | None:
    """Normalize a saved-memory item; None when it has no usable text."""
    text = (item.get("text") or item.get("content") or item.get("value") or "").strip()
    if not text:
        return None
    return {
        "kind": "memory",
        "id": str(item.get("id") or ""),
        "text": text,
        "created_at": _parse_timestamp(item.get("created_at") or item.get("created")),
        "attachments": item.get("attachments"),
        "categories": item.get("categories"),
        "metadata": item.get("metadata"),
    }


def _normalize_conversation(item: dict) -> dict:
    """Normalize a conversation item to a bounded record (no full transcript)."""
    mapping = item.get("mapping") or {}
    return {
        "kind": "conversation",
        "id": str(item.get("id") or ""),
        "title": item.get("title") or "",
        "create_time": _parse_timestamp(item.get("create_time")),
        "update_time": _parse_timestamp(item.get("update_time")),
        "message_count": len(mapping) if isinstance(mapping, dict) else 0,
        "first_user_text": _first_user_text(mapping),
    }


def _first_user_text(mapping: dict) -> str:
    """The first user message text from the mapping (earliest create_time)."""
    candidates: list[tuple[float, str]] = []
    for node in mapping.values():
        if not isinstance(node, dict):
            continue
        msg = node.get("message") or {}
        if not isinstance(msg, dict):
            continue
        author = msg.get("author") or {}
        if author.get("role") != "user":
            continue
        parts = (msg.get("content") or {}).get("parts") or []
        text = " ".join(p for p in parts if isinstance(p, str)).strip()
        if text:
            candidates.append((float(msg.get("create_time") or 0.0), text))
    if not candidates:
        return ""
    candidates.sort(key=lambda t: t[0])
    return candidates[0][1]


__all__ = ["ChatGPTReader", "import_record"]
