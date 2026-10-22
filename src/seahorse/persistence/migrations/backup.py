"""Consistent pre-migration snapshot of the sidecar DB.

The migration runner is atomic per migration, but atomicity protects the SCHEMA
— not the data an upgrade could strand. A vault that has been capturing for
months has no recoverable copy unless one is made before the first pending
migration runs, and an operator cannot be expected to remember which of their
vaults is behind.

``backup_before_migration`` snapshots with SQLite's online backup API (WAL
included, one consistent file) and names it ``<db>.bak-<YYYYmmdd-HHMMSS>`` — the
convention already used by hand in the vaults. It is a no-op when nothing is
pending, so the hot path (every CLI/MCP invocation opens ``Storage``) pays only
a version read, and a fresh DB — nothing to lose — never accumulates a
``.bak`` per first run.

A backup failure is deliberately NOT swallowed: continuing the migration
without its safety net is the one outcome this module exists to prevent.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from seahorse.persistence.migrations.migrator import (
    current_version,
    latest_available_version,
)

BACKUP_SUFFIX = ".bak-"
BACKUP_STAMP_FORMAT = "%Y%m%d-%H%M%S"


def has_pending_migrations(conn: sqlite3.Connection, *, up_to: int | None = None) -> bool:
    """True when at least one migration at or below the ``up_to`` cap is pending."""
    ceiling = latest_available_version()
    if up_to is not None:
        ceiling = min(up_to, ceiling)
    return current_version(conn) < ceiling


def backup_before_migration(
    conn: sqlite3.Connection,
    db_path: Path | str,
    *,
    now: datetime | None = None,
    up_to: int | None = None,
) -> Path | None:
    """Snapshot ``db_path`` when migrations are pending; return the snapshot path.

    ``conn`` must be a connection to that same file (the caller's writer). The
    copy is made with the online backup API, so an active WAL is folded into a
    single consistent file — unlike a plain ``cp`` of ``.db`` + ``-wal``, which
    loses whichever pages live only in the WAL.

    Returns ``None`` when there is nothing to do: no pending migration, or a DB
    that was never initialized (``schema_version`` empty), which has no data to
    protect. ``now`` is injectable for deterministic names; it defaults to
    local time so the suffix matches the operator's shell convention.
    """
    path = Path(db_path)
    if not path.exists():
        return None
    if current_version(conn) == 0:
        return None  # never initialized: nothing to lose
    if not has_pending_migrations(conn, up_to=up_to):
        return None

    dest = _unique_backup_path(path, now or datetime.now().astimezone())
    snapshot = sqlite3.connect(dest)
    try:
        conn.backup(snapshot)
    except BaseException:
        snapshot.close()
        # A half-written snapshot is worse than none: it looks like a recovery
        # point and is not one.
        dest.unlink(missing_ok=True)
        raise
    snapshot.close()
    return dest


def _unique_backup_path(db_path: Path, now: datetime) -> Path:
    """``<db>.bak-<stamp>``, with ``-1``, ``-2``… when two runs share a second."""
    base = db_path.with_name(f"{db_path.name}{BACKUP_SUFFIX}{now.strftime(BACKUP_STAMP_FORMAT)}")
    candidate = base
    counter = 1
    while candidate.exists():
        candidate = base.with_name(f"{base.name}-{counter}")
        counter += 1
    return candidate


__all__ = ["BACKUP_SUFFIX", "backup_before_migration", "has_pending_migrations"]
