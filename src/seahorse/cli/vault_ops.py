"""Real vault-management commands for the CLI (#14, commit 5 of F3.3 #3).

The four management commands whose dependencies ARE built in MVP-0:

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
- ``run_frontmatter_migrate`` — the FRONTMATTER vault migrator (gap closure,
  2026-08-13): converts legacy Obsidian notes to F3.1 frontmatter (cases
  A/B/C/D) via ``frontmatter.migrator.VaultMigrator``. This is the command the
  original commit-5 plan intended for ``migrate`` before that slot was taken by
  the schema runner. ADR-10 honesty: apply meeting case-D notes renders the
  manifest summary to stdout FIRST, then raises ``CliMigrationDeferred``
  (exit 97) — the vault is not fully migrated and scripts must see it. Dry-run
  is always exit 0 (preview). Works before ``seahorse init`` (no config needed).

Ruamel-confinement invariant: only ``run_index_rebuild`` and
``run_frontmatter_migrate`` transitively import ruamel (via ``frontmatter.rebuild``
/ ``frontmatter.migrator``); ``run_migrate`` / ``run_inspect`` are stdlib + #6
only.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

if TYPE_CHECKING:
    from seahorse.embeddings.types import Embedder

from seahorse.cli.config import SEAHORSE_DIR_NAME, SeahorseConfig
from seahorse.cli.errors import CliMigrationDeferred, CliRebuildConflicts, CliUsageError
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


def _try_build_passage_embedder() -> Embedder | None:
    """Build the FastEmbed passage embedder if the ``embeddings`` extra is present.

    Returns ``None`` (honest G2) when fastembed is unavailable or construction
    fails — the backfill is skipped, not failed. Lazy import keeps the CLI
    command free of the heavy stack unless it can actually serve.
    """
    try:
        from seahorse.embeddings.fastembed_backend import build_fastembed_embedder

        return build_fastembed_embedder()
    except Exception:  # noqa: BLE001 — embedder absence is an honest skip
        return None


def _run_backfill(vault: Path, storage: Storage, *, embed_mode: str = "body+summary") -> str:
    """M1-B.5: best-effort vec0/FTS backfill over the rebuilt index.

    ``embed_mode`` (F7 enabler (c)) selects the passage text. Default
    ``body+summary`` is the F3 flip (f7-experiment-embed §decide); re-running
    under a new mode re-embeds honestly (new content hash → cache miss, f5-16
    §5.4). Returns an honest report line; never raises (the episode_index
    rebuild is the primary op — the index backfill is derived/best-effort,
    ADR-10).
    """
    from seahorse.embeddings.indexer import RetrievalIndexer
    from seahorse.frontmatter.adapter import parse_file
    from seahorse.frontmatter.discovery import discover_notes

    embedder = _try_build_passage_embedder()
    if embedder is None:
        return "skipped (embedder unavailable)"
    indexer = RetrievalIndexer(
        embedder, storage.vector, storage.fts, storage.episodes, storage._cm,  # noqa: SLF001
        embed_mode=embed_mode,
    )
    count = 0
    for path in discover_notes(vault):
        _cm, body, ep = parse_file(path)
        if body and body.strip():
            indexer.index_episode_from_note(ep, body)
            count += 1
    return f"{count} episodes embedded"


def run_index_rebuild(
    config: SeahorseConfig,
    *,
    fmt: OutputFormat = "human",
    out: TextIO,
    embed_mode: str = "body+summary",
) -> None:
    """``seahorse index rebuild`` — regenerate the sidecar from the vault.

    Delegates to ``frontmatter.rebuild.rebuild_from_vault`` (commit 4) over the
    real ``Storage`` sidecar, with the vec0/FTS secondary-index wipes (M1-A.6)
    so a rebuild leaves no ghost vector/BM25 hits. The report is rendered to
    stdout BEFORE any error is raised so the operator sees the conflict list.
    ADR-10: a non-empty ``skipped`` raises ``CliRebuildConflicts`` (exit 94) —
    NO auto-pick. A parse failure surfaces as ``FrontmatterInvalid`` (Cat A exit
    90) — NO silent skip.

    ``embed_mode`` (F7 enabler (c)) drives the vec0/FTS backfill — the F3 flip
    makes ``body+summary`` the default (f7-experiment-embed §decide). The
    ``episode_index`` rebuild itself is embed-mode-independent.
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
    backfill: str | None = None
    try:
        report = rebuild_from_vault(
            config.vault,
            storage.sidecar,
            secondary_index_wipes=(vec_wipe, fts_wipe),
        )
        # M1-B.5: best-effort vec0/FTS backfill over the rebuilt index.
        backfill = _run_backfill(config.vault, storage, embed_mode=embed_mode)
    finally:
        storage.close()
    conflicts = [asdict(c) for c in report.skipped]
    payload = {
        "command": "index rebuild",
        "db_path": str(config.db_path),
        "indexed": report.indexed,
        "skipped": len(conflicts),
        "conflicts": conflicts,
        "backfill": backfill,
    }
    human_lines = [
        f"Index rebuild: {config.db_path}",
        f"  indexed:   {report.indexed}",
        f"  skipped:   {len(conflicts)}",
        f"  backfill:  {backfill}",
    ]
    if conflicts:
        human_lines.append("  conflicts (ADR-10: no auto-pick, human resolution required):")
        for c in conflicts:
            human_lines.append(f"    - {c['file_path']} ({c['reason']})")
    human = "\n".join(human_lines) + "\n"
    render_message(payload, fmt=fmt, out=out, human_text=human)
    if report.skipped:
        raise CliRebuildConflicts(len(report.skipped))


def run_frontmatter_migrate(
    vault: Path,
    *,
    dry_run: bool = False,
    resume: bool = False,
    batch_size: int | None = None,
    fmt: OutputFormat = "human",
    out: TextIO,
) -> None:
    """``seahorse frontmatter migrate`` — convert legacy notes to F3.1 (A/B/C/D).

    Delegates to ``frontmatter.migrator.VaultMigrator`` (gap closure: the
    migrator had no CLI surface — the ``migrate`` slot went to the schema DDL
    runner). ``dry_run`` classifies + builds the manifest but never writes;
    ``resume`` skips notes unchanged since the last manifest (mtime hint, hash
    truth). ``batch_size`` checkpoints the manifest every N notes (default 500).

    ADR-10 honesty (index-rebuild pattern): the manifest summary is rendered to
    stdout FIRST, then apply meeting case-D notes raises ``CliMigrationDeferred``
    (exit 97) — the vault is not fully migrated and scripts must see it. Dry-run
    is always exit 0 (preview). Works before ``seahorse init`` (no config
    needed — the migrator only touches ``.md`` files + the manifest).

    Ruamel-confinement: ``VaultMigrator`` transitively imports ruamel (via
    ``frontmatter.adapter``), so the import is lazy inside this function — the
    same pattern as ``run_index_rebuild`` → ``rebuild_from_vault``. Importing
    it at module top would leak ruamel into every CLI command.
    """
    from seahorse.facade import new_uuid7  # core, ruamel-free
    from seahorse.frontmatter.migrator import DEFAULT_BATCH_SIZE, VaultMigrator  # lazy

    if batch_size is None:
        batch_size = DEFAULT_BATCH_SIZE
    if batch_size < 0:
        raise CliUsageError(
            f"--batch-size must be a non-negative integer, got {batch_size}"
        )

    session_id = new_uuid7()
    manifest_path = vault / SEAHORSE_DIR_NAME / "migration_manifest.json"
    migrator = VaultMigrator(vault, session_id)
    manifest = migrator.run(
        dry_run=dry_run,
        resume=resume,
        batch_size=batch_size,
        manifest_path=manifest_path,
    )
    _render_migration_payload(
        manifest, manifest_path, vault, dry_run=dry_run, resume=resume, fmt=fmt, out=out
    )
    if (not dry_run) and manifest.stats.errors > 0:
        raise CliMigrationDeferred(manifest.stats.errors)


def _render_migration_payload(
    manifest: Any,
    manifest_path: Path,
    vault: Path,
    *,
    dry_run: bool,
    resume: bool,
    fmt: OutputFormat,
    out: TextIO,
) -> None:
    """Render the migration manifest summary (human or JSON) to ``out``.

    The deferred list (case-D notes) is always included so the operator sees
    which notes were refused and why — the error is raised by the caller AFTER
    this render (ADR-10: report first, then fail loud).

    ``manifest`` is typed ``Any`` to keep the ``frontmatter.manifest`` import
    lazy (it is ruamel-free, but the module is part of the frontmatter package
    and the CLI keeps all frontmatter imports lazy for the confinement guard).
    """
    deferred = [
        {"path": e.path, "case": e.case, "error": e.error}
        for e in manifest.notes.values()
        if e.error is not None
    ]
    payload = {
        "command": "frontmatter migrate",
        "vault": str(vault),
        "dry_run": dry_run,
        "resume": resume,
        "manifest_path": str(manifest_path),
        "session_id": manifest.session_id,
        "total_notes": manifest.stats.total_notes,
        "migrated": manifest.stats.migrated,
        "already_f31": manifest.stats.already_f31,
        "errors": manifest.stats.errors,
        "collisions": manifest.stats.collisions,
        "deferred": deferred,
    }
    human_lines = [
        f"Frontmatter migrate: {vault}",
        f"  dry_run:      {str(dry_run).lower()}",
        f"  total:        {manifest.stats.total_notes}",
        f"  migrated:     {manifest.stats.migrated}",
        f"  already_f31:  {manifest.stats.already_f31}",
        f"  errors:       {manifest.stats.errors}",
        f"  collisions:   {manifest.stats.collisions}",
        f"  manifest:     {manifest_path}",
    ]
    if deferred:
        human_lines.append("  deferred (case D — manual resolution required):")
        for d in deferred:
            human_lines.append(f"    - {d['path']} ({d['error']})")
    human = "\n".join(human_lines) + "\n"
    render_message(payload, fmt=fmt, out=out, human_text=human)


__all__ = [
    "run_migrate",
    "run_inspect",
    "run_index_rebuild",
    "run_frontmatter_migrate",
]