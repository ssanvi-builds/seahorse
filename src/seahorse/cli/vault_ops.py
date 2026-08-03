"""Real vault-management commands for the CLI (#14, commit 5 of F3.3 #3).

The three management commands whose dependencies ARE built in MVP-0:

- ``run_migrate``      — apply SCHEMA migrations (DDL 001–009) to the sidecar DB.
  This is the SCHEMA migrations runner, NOT the frontmatter vault migrator: it
  reuses the ``apply_migrations(up_to=)`` seam added in commit 4. ``--up-to`` is
  a CAP (not a requirement): a value beyond ``latest_available`` applies all
  available migrations rather than erroring, and ``latest_available`` is reported
  so the operator sees the ceiling. Exit 0 on success; ``--up-to < 0`` →
  ``CliUsageError`` (Cat C, exit 2).
- ``run_inspect``      — read-only sidecar snapshot (schema_version + episode /
  episode_index counts + the two bi-temporal predicates vigente vs activo-ahora
  + last file mtime). Opens the DB ``mode=ro`` only when it exists; a missing DB
  is reported honestly (``db_exists=False``, all zeros) and NO file is created
  (read-only). The SQL is owned by #6 (``persistence.sidecar_status``) so this
  module stays free of raw persistence SQL.
- ``run_index_rebuild`` — regenerate the sidecar from the vault's ``.md`` notes
  via ``frontmatter.rebuild.rebuild_from_vault`` (commit 4). ADR-10 honesty: the
  rebuild pre-pass detects conflicting facts (duplicate vigent ``fact_id`` /
  duplicate ``ep_id``) and refuses to auto-pick a winner. The report is rendered
  to stdout FIRST, then ``CliRebuildConflicts`` is raised (exit 94) so the
  operator sees the conflict list AND the error. A parse failure surfaces as
  ``FrontmatterInvalid`` (Cat A exit 90) — never a silent skip.

Ruamel-confinement invariant: only ``run_index_rebuild`` transitively imports
ruamel (via ``frontmatter.rebuild``); ``run_migrate`` / ``run_inspect`` are
stdlib + #6 only.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TextIO

from seahorse.cli.config import SeahorseConfig
from seahorse.cli.errors import CliRebuildConflicts, CliUsageError
from seahorse.cli.output import OutputFormat, render_message
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import (
    apply_migrations,
    current_version,
    latest_available_version,
)
from seahorse.persistence.sidecar_status import SidecarSnapshot, read_sidecar_status
from seahorse.persistence.storage import Storage


def run_migrate(
    config: SeahorseConfig,
    *,
    up_to: int | None,
    fmt: OutputFormat = "human",
    out: TextIO,
) -> None:
    """``seahorse migrate`` — apply SCHEMA migrations to the sidecar DB.

    ``up_to`` caps the highest migration version (inclusive); ``None`` applies
    all pending. Negative ``up_to`` is a CLI usage error (exit 2). The DB parent
    dir is created if missing (bootstrap semantics, matching ``init``).
    """
    if up_to is not None and up_to < 0:
        raise CliUsageError(f"--up-to must be a non-negative integer, got {up_to}")
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    # M1-A.1: run_migrate opts into vec0 so migration 010 (``USING vec0``) can be
    # applied on a legacy DB without going through Storage. sqlite-vec is core.
    mgr = ConnectionManager(config.db_path, pool_size=0, extensions=("vec0",))
    mgr.open()
    try:
        applied = apply_migrations(mgr.writer, up_to=up_to)
        schema_version = current_version(mgr.writer)
        latest = latest_available_version()
    finally:
        mgr.close()
    payload = {
        "command": "migrate",
        "db_path": str(config.db_path),
        "applied": applied,
        "schema_version": schema_version,
        "up_to": up_to,
        "latest_available": latest,
    }
    human = (
        f"Migrate: {config.db_path}\n"
        f"  applied:          {applied}\n"
        f"  schema_version:    {schema_version}\n"
        f"  up_to:             {up_to if up_to is not None else 'latest'}\n"
        f"  latest_available:  {latest}\n"
    )
    render_message(payload, fmt=fmt, out=out, human_text=human)


def run_inspect(
    config: SeahorseConfig,
    *,
    now: datetime | None = None,
    fmt: OutputFormat = "human",
    out: TextIO,
) -> None:
    """``seahorse inspect`` — read-only sidecar snapshot.

    Opens the DB read-only (``mode=ro``) only when it exists; a missing DB is
    reported as ``db_exists=False`` with all-zero counts and NO file is created.
    """
    if now is None:
        now = datetime.now(UTC)
    db_exists = config.db_path.exists()
    if db_exists:
        conn = sqlite3.connect(f"file:{config.db_path}?mode=ro", uri=True)
        try:
            snap = read_sidecar_status(conn, now=now)
        finally:
            conn.close()
    else:
        snap = SidecarSnapshot(
            schema_version=0,
            episodes=0,
            episode_index=0,
            vigentes=0,
            activos_ahora=0,
            last_mtime_ms=None,
        )
    payload = {
        "command": "inspect",
        "db_path": str(config.db_path),
        "db_exists": db_exists,
        **asdict(snap),
    }
    human = (
        f"Inspect: {config.db_path}\n"
        f"  db_exists:       {db_exists}\n"
        f"  schema_version:   {snap.schema_version}\n"
        f"  episodes:         {snap.episodes}\n"
        f"  episode_index:    {snap.episode_index}\n"
        f"  vigentes:         {snap.vigentes}\n"
        f"  activos_ahora:    {snap.activos_ahora}\n"
        f"  last_mtime_ms:    {snap.last_mtime_ms}\n"
    )
    render_message(payload, fmt=fmt, out=out, human_text=human)


def run_index_rebuild(
    config: SeahorseConfig,
    *,
    fmt: OutputFormat = "human",
    out: TextIO,
) -> None:
    """``seahorse index rebuild`` — regenerate the sidecar from the vault.

    Delegates to ``frontmatter.rebuild.rebuild_from_vault`` (commit 4) over the
    real ``Storage`` sidecar, with the vec0/FTS secondary-index wipes (M1-A.6)
    so a rebuild leaves no ghost vector/BM25 hits. The report is rendered to
    stdout BEFORE any error is raised so the operator sees the conflict list.
    ADR-10: a non-empty ``skipped`` raises ``CliRebuildConflicts`` (exit 94) —
    NO auto-pick. A parse failure surfaces as ``FrontmatterInvalid`` (Cat A exit
    90) — NO silent skip.
    """
    # Lazy import: frontmatter.rebuild transitively pulls ruamel (via
    # frontmatter.adapter). Importing it at module top would leak ruamel into
    # every CLI command (app.py imports vault_ops eagerly). Keeping it lazy
    # confines ruamel to the rebuild entry point ONLY — run_migrate / run_inspect
    # stay stdlib + #6 (ruamel-confinement invariant, vault_ops docstring). The
    # wipe hooks live in the lazy vector/fts modules (M1-A.6) — same pattern.
    from seahorse.frontmatter.rebuild import rebuild_from_vault
    from seahorse.persistence.fts_index import fts_wipe
    from seahorse.persistence.vector_index import vec_wipe

    storage = Storage(config.db_path)
    try:
        report = rebuild_from_vault(
            config.vault,
            storage.sidecar,
            secondary_index_wipes=(vec_wipe, fts_wipe),
        )
    finally:
        storage.close()
    conflicts = [asdict(c) for c in report.skipped]
    payload = {
        "command": "index rebuild",
        "db_path": str(config.db_path),
        "indexed": report.indexed,
        "skipped": len(conflicts),
        "conflicts": conflicts,
    }
    human_lines = [
        f"Index rebuild: {config.db_path}",
        f"  indexed:   {report.indexed}",
        f"  skipped:   {len(conflicts)}",
    ]
    if conflicts:
        human_lines.append("  conflicts (ADR-10: no auto-pick, human resolution required):")
        for c in conflicts:
            human_lines.append(f"    - {c['file_path']} ({c['reason']})")
    human = "\n".join(human_lines) + "\n"
    render_message(payload, fmt=fmt, out=out, human_text=human)
    if report.skipped:
        raise CliRebuildConflicts(len(report.skipped))


__all__ = ["run_migrate", "run_inspect", "run_index_rebuild"]