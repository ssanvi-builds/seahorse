"""Idempotent SQL migration runner for the Seahorse SQLite schema.

Migrations are plain ``.sql`` files in this package, named ``NNN_*.sql`` (zero-padded
three-digit prefix). They are applied in lexical order. Each migration runs inside
its own implicit transaction (``executescript`` issues a COMMIT first, then runs
the script in autocommit); the ``schema_version`` row is inserted immediately after
a successful run so a half-applied migration leaves no version row.

The schema_version table itself is created here (not a numbered file) so it always
exists before any migration is recorded.

MVP-0 scope: only the relational tables + the three SO-7 lateral tables. The
``vec0`` virtual table and the FTS5 external-content table are NOT created here
(they require ``sqlite-vec``/FTS5 and are deferred to MVP-1).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _migrations_dir() -> Path:
    return Path(__file__).resolve().parent


def _ordered_migrations() -> list[tuple[int, Path]]:
    files = sorted(_migrations_dir().glob("[0-9][0-9][0-9]_*.sql"))
    return [(int(f.stem.split("_", 1)[0]), f) for f in files]


def apply_migrations(conn: sqlite3.Connection, *, up_to: int | None = None) -> int:
    """Apply pending migrations to ``conn``.

    Returns the number of newly applied migrations. Idempotent: re-running on an
    already-migrated database applies zero migrations and returns 0.

    ``up_to`` caps the highest migration version to apply (inclusive). ``None``
    (default) applies all pending migrations. This seam lets tests pin a
    database at an older schema version (e.g. ``up_to=8``) and then apply the
    next migration in isolation — exercising the real legacy upgrade path that
    existing deployments hit, rather than only the fresh-DB path.
    """
    conn.executescript(_SCHEMA_VERSION_DDL)
    applied = 0
    for version, path in _ordered_migrations():
        if up_to is not None and version > up_to:
            break
        already = conn.execute(
            "SELECT 1 FROM schema_version WHERE version = ?", (version,)
        ).fetchone()
        if already is not None:
            continue
        sql = path.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        conn.commit()
        applied += 1
    return applied


def current_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied migration version, or 0 if none applied yet."""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return 0
    value = row[0] if row is not None else None
    return int(value) if value is not None else 0


def latest_available_version() -> int:
    """Highest migration version shipped in this build (no connection needed).

    Used by ``seahorse migrate`` to report the ceiling so the operator can see
    whether ``--up-to`` capped below the build's available set (honest reporting:
    ``up_to`` is a CAP, not a requirement — a value beyond ``latest_available``
    applies all available migrations rather than erroring).
    """
    versions = [v for v, _ in _ordered_migrations()]
    return max(versions) if versions else 0


__all__ = ["apply_migrations", "current_version", "latest_available_version"]
