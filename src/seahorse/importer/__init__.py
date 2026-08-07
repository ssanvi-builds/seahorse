"""#15 Importers — the migration/coexistence bridge (f5-15, obsiforge §15.4).

The importer is the **validador del foso competitivo** (f5-15 §tesis): it
demonstrates that the open F3.1 format absorbs the final state of an existing
memory vendor (claude-mem) without silent loss. It is a one-time, one-way
migration (vendor -> F3.1); there is no ongoing sync (ADR-03).

Two layers (f5-15 §3.1):
- **Pure mapping** — ``import_record(vendor_record, vendor) -> ImporterResult``
  (no state, no store, no LLM, no conflict resolution).
- **Ingestion driver** — ``ImportRunner`` (dry-run/commit, manifest, idempotency
  via deterministic UUIDv5, collisions via ``WriteResult.collisions_detected``).

claude-mem is NEVER a runtime dependency — the importer reads its local SQLite
(``~/.claude-mem/claude-mem.db``) as a one-time migration source.

References:
- f5-15-importers.md (the load-bearing spec)
- obsiforge-evolution-architecture.md §15.4 (importer = migration bridge)
"""

from __future__ import annotations

from seahorse.importer.claude_mem import ClaudeMemReader, import_record
from seahorse.importer.runner import ImportRunner
from seahorse.importer.types import (
    IMPORTER_VERSION,
    MANIFEST_SCHEMA,
    ImporterResult,
    ImportItem,
    ImportManifest,
    LossReport,
)

__all__ = [
    "ClaudeMemReader",
    "IMPORTER_VERSION",
    "ImportItem",
    "ImportManifest",
    "ImportRunner",
    "ImporterResult",
    "LossReport",
    "MANIFEST_SCHEMA",
    "import_record",
]
