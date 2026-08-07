"""#15 Importer — shared types (f5-15 §2.1/§3.3).

The importer is the **migration/coexistence bridge** (obsiforge §15.4): a pure
``import_record`` mapping a vendor record to F3.1 notes + a loss report, wrapped
by an operational runner (dry-run/commit, manifest, idempotency, collisions).
claude-mem is NEVER a runtime dependency — the importer reads its data
(``~/.claude-mem/claude-mem.db``) as a one-time migration source.

The manifest schema ``seahorse.importer.manifest/1.0`` is an importer artifact
(f5-15 §3.3), NOT part of the F3.1 core contract nor the #6 store.

References:
- f5-15-importers.md §2.1 (ImporterResult/LossReport contract), §3.3 (manifest)
- obsiforge-evolution-architecture.md §15.4 (importer = migration bridge)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from seahorse.contracts.episode import Episode

MANIFEST_SCHEMA = "seahorse.importer.manifest/1.0"
IMPORTER_VERSION = "importer@claude-mem@1.0.0"


class LossReport(TypedDict):
    """Per-record loss report (f5-15 §2.1). ALWAYS present, even if empty."""

    vendor: str
    source_record_id: str
    fields_lost: list[str]
    fields_synthesized: list[str]
    structural_loss: list[str]
    notes: str


class ImporterResult(TypedDict):
    """Pure mapping result: 0+ F3.1 notes per vendor record + loss report."""

    notes: list[Episode]
    loss_report: LossReport


@dataclass(frozen=True)
class ImportItem:
    """Per-record manifest item (f5-15 §3.3 item level)."""

    seq: int
    source_record_id: str
    vendor_operation: str
    notes_emitted: list[dict[str, str]]  # [{"ep_id": ..., "action": "create"}]
    loss_report: LossReport
    status: str  # committed | skipped_idempotent | skipped_collision | failed | noop
    error: str | None = None


@dataclass(frozen=True)
class ImportManifest:
    """Batch manifest (f5-15 §3.3 batch level). Schema ``seahorse.importer.manifest/1.0``."""

    manifest_schema: str = MANIFEST_SCHEMA
    run_id: str = ""
    vendor: str = "claude-mem"
    importer_version: str = IMPORTER_VERSION
    mode: str = "dry-run"  # dry-run | commit
    started_at: str = ""
    ended_at: str = ""
    source: dict[str, Any] = field(default_factory=dict)
    aggregate: dict[str, int] = field(default_factory=dict)
    losses_aggregated: dict[str, Any] = field(default_factory=dict)
    items: list[ImportItem] = field(default_factory=list)
    integrity_ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable manifest dict (f5-15 §3.3 batch shape)."""
        return {
            "manifest_schema": self.manifest_schema,
            "run_id": self.run_id,
            "vendor": self.vendor,
            "importer_version": self.importer_version,
            "mode": self.mode,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "source": self.source,
            "aggregate": self.aggregate,
            "losses_aggregated": self.losses_aggregated,
            "items": [item.__dict__ for item in self.items],
            "integrity_ok": self.integrity_ok,
        }


__all__ = [
    "IMPORTER_VERSION",
    "MANIFEST_SCHEMA",
    "ImportItem",
    "ImportManifest",
    "ImporterResult",
    "LossReport",
]
