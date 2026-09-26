"""Migration 012 — session_id denormalized into episode_index.

The two-stage session→episode retrieval needs session-restricted recall: the
engine fetches a session's episodes with one SQL ``WHERE session_id = ?``.
Before 012, ``session_id`` only lived in ``Episode.provenance`` (episodes
table); this migration denormalizes it into the ``episode_index`` bridge table
+ an index on it. These tests guard the column landing, the index, and the
migration's idempotency model (the runner's ``schema_version`` row — the same
model as 009).
"""

from __future__ import annotations

import sqlite3

from seahorse.persistence.migrations.migrator import apply_migrations, current_version


def _load_vec0(c: sqlite3.Connection) -> None:
    """Load the sqlite-vec extension so migration 010 (``USING vec0``) runs in-memory."""
    import sqlite_vec  # type: ignore[import-untyped]

    c.enable_load_extension(True)
    try:
        sqlite_vec.load(c)
    finally:
        c.enable_load_extension(False)


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {row[1]: row[2] for row in conn.execute(f"PRAGMA table_info({table})")}


def _indexes(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA index_list(episode_index)")}


def test_migration_012_adds_session_id_column_and_index() -> None:
    c = sqlite3.connect(":memory:")
    _load_vec0(c)
    apply_migrations(c)
    index = _columns(c, "episode_index")
    assert "session_id" in index
    assert index["session_id"] == "TEXT"
    assert "ix_episode_index_session_id" in _indexes(c)
    c.close()


def test_migration_012_idempotent_via_runner() -> None:
    # The runner's schema_version row guards re-runs (ALTER has no IF NOT EXISTS).
    c = sqlite3.connect(":memory:")
    _load_vec0(c)
    first = apply_migrations(c)
    second = apply_migrations(c)
    assert first > 0
    assert second == 0  # re-running applies nothing — no 'duplicate column' error
    assert current_version(c) == 12
    c.close()


def test_migration_012_on_legacy_db_v11() -> None:
    # The real legacy upgrade path: pin a DB at v11 (001-011 applied, NO
    # session_id column), then apply 012 in isolation and verify the column
    # lands and the version row advances to 12.
    c = sqlite3.connect(":memory:")
    _load_vec0(c)
    pre = apply_migrations(c, up_to=11)
    assert pre > 0
    assert current_version(c) == 11
    assert "session_id" not in _columns(c, "episode_index")
    n = apply_migrations(c, up_to=12)
    assert n == 1
    assert current_version(c) == 12
    assert _columns(c, "episode_index")["session_id"] == "TEXT"
    assert "ix_episode_index_session_id" in _indexes(c)
    c.close()
