"""Idempotent SQL migration runner for the Seahorse SQLite schema.

Migrations are plain ``.sql`` files in this package, named ``NNN_*.sql`` (zero-padded
three-digit prefix). They are applied in lexical order. Each migration's DDL and
its ``schema_version`` row commit as ONE transaction: the runner wraps
the migration SQL in ``BEGIN; ... INSERT INTO schema_version ...; COMMIT;`` and
runs it via a single ``executescript``. If any statement fails, the transaction
rolls back — no half-applied migration. The schema_version table itself is
created here (not a numbered file) so it always exists before any migration is
recorded.

The current release ships only the relational tables. A later release
(migration 010 onward) also creates the ``vec0`` virtual table and the FTS5
external-content tables; those require ``sqlite-vec`` loaded on the connection
before ``apply_migrations`` runs (the ConnectionManager opts in via
``extensions=("vec0",)`` at the composition root).
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

# Concurrent first-migration on a fresh DB: two processes can both see a
# version as "not present" and both try to insert it; the loser hits the UNIQUE
# constraint. Retry a bounded number of times. A persistent conflict still
# fails loud.
_MIGRATION_RETRIES = 5

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
    (default) applies all pending migrations. This extension point lets tests pin a
    database at an older schema version (e.g. ``up_to=8``) and then apply the
    next migration in isolation — exercising the real legacy upgrade path that
    existing deployments hit, rather than only the fresh-DB path.

    Each migration runs as a single transaction (DDL + version row). The
    migration SQL is wrapped in ``BEGIN; <sql>; INSERT INTO schema_version ...;
    COMMIT;`` and executed via one ``executescript`` (which issues a COMMIT first
    — a no-op with no pending tx — then runs the script). On any statement
    failure the transaction is left open and is rolled back, so neither the DDL
    nor the version row persists. ``version`` is a parsed int inlined into the
    script (executescript takes no parameters); it is never user-supplied, so
    there is no injection surface.
    """
    conn.executescript(_SCHEMA_VERSION_DDL)
    applied = 0
    for version, path in _ordered_migrations():
        if up_to is not None and version > up_to:
            break
        for attempt in range(_MIGRATION_RETRIES):
            already = conn.execute(
                "SELECT 1 FROM schema_version WHERE version = ?", (version,)
            ).fetchone()
            if already is not None:
                break  # applied by us or a concurrent process
            sql = path.read_text(encoding="utf-8")
            script = (
                "BEGIN;\n"
                f"{sql}\n"
                f"INSERT INTO schema_version (version) VALUES ({version});\n"
                "COMMIT;"
            )
            try:
                conn.executescript(script)
                applied += 1
                break
            except sqlite3.IntegrityError:
                # Concurrent migration: two
                # processes both saw "version N not present" and both tried to
                # insert it; the loser hits the UNIQUE constraint. Roll back and
                # re-check — the winner's version row is now visible.
                with contextlib.suppress(sqlite3.OperationalError):
                    conn.execute("ROLLBACK")
                if attempt == _MIGRATION_RETRIES - 1:
                    raise
            except BaseException:
                # The failed statement left the BEGIN transaction open; roll it
                # back so the DDL does not persist without the version row.
                # Suppress a "no transaction is active" error (e.g. if the BEGIN
                # itself failed).
                with contextlib.suppress(sqlite3.OperationalError):
                    conn.execute("ROLLBACK")
                raise
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
