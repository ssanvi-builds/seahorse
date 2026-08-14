"""Read-only sidecar snapshot for ``seahorse inspect``.

The CLI ``inspect`` command reports a snapshot of the sidecar SQLite DB:
schema_version + episode/episode_index counts + the two bi-temporal predicates
(current-state vs currently-active) + the last known file mtime. The SQL lives
here, in the persistence layer, so the CLI does not own raw SQL against the
persistence schema (management commands may touch the persistence layer
directly, but the SQL belongs to it).

The two predicates mirror the engine's bi-temporal definitions VERBATIM (no
drift — these are bi-temporal fundamentals, not policy):

- **current-state** = ``invalid_at IS NULL AND expired_at IS NULL``
  — mirrors ``SqliteEpisodeIndexRepository.find_vigent_row_by_fact_id``
  (sqlite_episode_index.py): a row whose valid-time AND transaction-time axes
  are both open (neither invalidated nor decayed).
- **currently-active** = ``(valid_at IS NULL OR valid_at <= now)
  AND (invalid_at IS NULL OR invalid_at > now)``
  — mirrors ``_pit_predicate("state_at", now)`` (sqlite_episode_index.py): a
  row effective in the valid-time axis NOW. This is the ``state_at`` PIT
  predicate, which ignores the ``expired_at`` (transaction-time decay) axis —
  a decayed-but-valid row is still ``currently-active``. ``valid_at IS
  NULL`` ("from forever") is valid at any ``t`` and is INCLUDED, mirroring the
  canonical ``get_vigente`` / ``is_valid_at``.

The two measure DIFFERENT axes, so a row can be ``currently-active`` but NOT
``current-state`` (a future-scheduled invalidation, or a decayed-but-valid
row). Reporting both lets the operator see the difference.

Read-only: the caller passes a read-only connection (``mode=ro``); this module
never writes. Missing tables (a partially-migrated DB) are tolerated — each
count degrades to 0 rather than raising, and ``schema_version`` is 0 when the
``schema_version`` table does not exist yet.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from seahorse.persistence.migrations.migrator import current_version

# current-state: both bi-temporal axes open (mirrors find_vigent_row_by_fact_id).
_VIGENTE_WHERE = "invalid_at IS NULL AND expired_at IS NULL"
# currently-active: state_at(now) — valid-time active now (mirrors _pit_predicate
# state_at). NB: ignores expired_at (transaction-time decay is a separate axis).
# valid_at IS NULL ("from forever") is valid at any t → INCLUDED.
_ACTIVOS_AHORA_WHERE = (
    "(valid_at IS NULL OR valid_at <= ?) AND (invalid_at IS NULL OR invalid_at > ?)"
)


@dataclass(frozen=True)
class SidecarSnapshot:
    """Read-only snapshot of the sidecar SQLite DB (``seahorse inspect``)."""

    schema_version: int
    episodes: int
    episode_index: int
    vigentes: int
    activos_ahora: int
    last_mtime_ms: int | None


def _count(conn: sqlite3.Connection, sql: str, *params: object) -> int:
    """Run a ``SELECT COUNT(*)``; tolerate a missing table (partial migration)."""
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        return 0
    if row is None:
        return 0
    return int(row[0])


def read_sidecar_status(conn: sqlite3.Connection, *, now: datetime) -> SidecarSnapshot:
    """Build a ``SidecarSnapshot`` from ``conn`` (read-only).

    ``now`` drives the ``currently-active`` (state_at) predicate. The connection is
    used read-only; this function never writes and never opens a transaction.
    """
    iso = now.isoformat()
    return SidecarSnapshot(
        schema_version=current_version(conn),
        episodes=_count(conn, "SELECT COUNT(*) FROM episodes"),
        episode_index=_count(conn, "SELECT COUNT(*) FROM episode_index"),
        vigentes=_count(conn, f"SELECT COUNT(*) FROM episode_index WHERE {_VIGENTE_WHERE}"),
        activos_ahora=_count(
            conn,
            f"SELECT COUNT(*) FROM episode_index WHERE {_ACTIVOS_AHORA_WHERE}",
            iso,
            iso,
        ),
        last_mtime_ms=_max_mtime(conn),
    )


def _max_mtime(conn: sqlite3.Connection) -> int | None:
    """``MAX(mtime_ms)`` over ``episode_paths``; ``None`` when empty/missing."""
    try:
        row = conn.execute("SELECT MAX(mtime_ms) FROM episode_paths").fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    value = row[0]
    return int(value) if value is not None else None


__all__ = ["SidecarSnapshot", "read_sidecar_status"]