"""Migration load-bearing tests (RED-first).

These guard the signed DDL: json_valid enforcement, null-safe bi-temporal
ordering, unique currently-valid per fact_id, the title/summary columns on
episode_index, the episode_paths %.md CHECK, idempotency, and schema_version
tracking. They run against a fresh in-memory SQLite db with migrations applied.
"""

from __future__ import annotations

import sqlite3

import pytest

from seahorse.persistence.migrations.migrator import apply_migrations, current_version


def _load_vec0(c: sqlite3.Connection) -> None:
    """Load the sqlite-vec extension so migration 010 (``USING vec0``) runs in-memory."""
    import sqlite_vec  # type: ignore[import-untyped]

    c.enable_load_extension(True)
    try:
        sqlite_vec.load(c)
    finally:
        c.enable_load_extension(False)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    _load_vec0(c)
    apply_migrations(c)
    yield c
    c.close()


def test_apply_migrations_retries_on_concurrent_integrity_error(tmp_path) -> None:
    # Matrix finding (concurrency combo): two processes migrating a FRESH vault
    # simultaneously both see "version N not present", both try to insert it,
    # and one hits `UNIQUE constraint failed: schema_version.version`
    # (IntegrityError → exit 89). apply_migrations must roll back and re-check
    # instead of failing loud on a transient concurrent-migration race.
    # sqlite3.Connection is a C-builtin (cannot be monkeypatched), so wrap it.
    class _FlakyConn:
        def __init__(self, real: sqlite3.Connection, calls: dict) -> None:
            self._real = real
            self._calls = calls

        def __getattr__(self, name: str):
            return getattr(self._real, name)

        def executescript(self, script: str) -> None:
            self._calls["n"] += 1
            # call 1 is the schema_version DDL; call 2 is migration 001's script
            if self._calls["n"] == 2:  # the first migration's executescript loses the race
                raise sqlite3.IntegrityError("UNIQUE constraint failed: schema_version.version")
            return self._real.executescript(script)

    c = sqlite3.connect(tmp_path / "t.db")
    _load_vec0(c)  # migration 010 (USING vec0) needs the extension
    c.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    calls = {"n": 0}
    applied = apply_migrations(_FlakyConn(c, calls))  # type: ignore[arg-type]
    assert applied == 12  # all migrations eventually applied
    assert calls["n"] >= 13  # 12 migrations + at least one retry
    assert current_version(c) == 12
    c.close()


# --- json_valid enforcement -------------------------------------------------


def test_episodes_rejects_malformed_provenance(conn: sqlite3.Connection) -> None:
    # provenance must be valid JSON; storage enforces it, not just the app.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO episodes (id, body_md, created_at, schema_version, provenance) "
            "VALUES ('e1', 'body', '2026-01-01T00:00:00Z', '3.1', 'not-json{')"
        )


def test_episodes_accepts_valid_provenance(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO episodes (id, body_md, created_at, schema_version, provenance) "
        "VALUES ('e1', 'body', '2026-01-01T00:00:00Z', '3.1', '{\"a\":1}')"
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 1


# --- null-safe bi-temporal ordering --------------------------------------


def test_i5_rejects_valid_at_after_invalid_at(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO episodes (id, body_md, created_at, schema_version, provenance, "
            "valid_at, invalid_at) VALUES ('e1', 'b', '2026-01-01T00:00:00Z', '3.1', '{}', "
            "'2026-01-02T00:00:00Z', '2026-01-01T00:00:00Z')"
        )


def test_i5_accepts_both_null(conn: sqlite3.Connection) -> None:
    # PENDING_INGEST: valid_at and invalid_at both NULL is legitimate.
    conn.execute(
        "INSERT INTO episodes (id, body_md, created_at, schema_version, provenance) "
        "VALUES ('e1', 'b', '2026-01-01T00:00:00Z', '3.1', '{}')"
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 1


def test_i5_rejects_expired_at_before_created_at(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO episodes (id, body_md, created_at, schema_version, provenance, "
            "expired_at) VALUES ('e1', 'b', '2026-01-02T00:00:00Z', '3.1', '{}', "
            "'2026-01-01T00:00:00Z')"
        )


# --- unique currently-valid per fact_id -----------------------------------------


def _insert_episode(conn: sqlite3.Connection, ep_id: str, fact_id: str = "fact-1") -> None:
    conn.execute(
        "INSERT INTO episodes (id, body_md, created_at, schema_version, provenance, fact_id) "
        "VALUES (?, 'b', '2026-01-01T00:00:00Z', '3.1', '{}', ?)",
        (ep_id, fact_id),
    )
    conn.commit()


def test_i11_two_vigente_same_fact_id_rejected(conn: sqlite3.Connection) -> None:
    _insert_episode(conn, "e1", "fact-1")
    # second row with the same fact_id, both invalid_at and expired_at NULL -> IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        _insert_episode(conn, "e2", "fact-1")


def test_i11_second_after_invalidation_accepted(conn: sqlite3.Connection) -> None:
    _insert_episode(conn, "e1", "fact-1")
    conn.execute("UPDATE episodes SET invalid_at = '2026-01-03T00:00:00Z' WHERE id = 'e1'")
    conn.commit()
    # now a new currently valid row for the same fact_id is legitimate (supersession)
    _insert_episode(conn, "e2", "fact-1")
    assert conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 2


def test_i11_episode_index_mirrors_unique_vigente(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO episode_index (ep_id, created_at, schema_version, fact_id) "
        "VALUES ('e1', '2026-01-01T00:00:00Z', '3.1', 'fact-1')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO episode_index (ep_id, created_at, schema_version, fact_id) "
            "VALUES ('e2', '2026-01-01T00:00:00Z', '3.1', 'fact-1')"
        )


# --- title/summary columns on episode_index ---------------------------


def test_episode_index_has_title_and_summary_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(episode_index)")}
    assert "title" in cols
    assert "summary" in cols


# --- episode_paths %.md CHECK ----------------------------------------------


def test_episode_paths_rejects_non_md_path(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO episode_paths (ep_id, file_path, mtime_ms, size) "
            "VALUES ('e1', 'notes.txt', 1, 2)"
        )


def test_episode_paths_accepts_md_path(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO episode_paths (ep_id, file_path, mtime_ms, size) "
        "VALUES ('e1', 'notes/ep1.md', 1, 2)"
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM episode_paths").fetchone()[0] == 1


# --- idempotency + schema_version tracking ----------------------------------


def test_apply_migrations_is_idempotent() -> None:
    c = sqlite3.connect(":memory:")
    _load_vec0(c)
    first = apply_migrations(c)
    second = apply_migrations(c)
    assert first > 0
    assert second == 0  # re-running applies nothing
    c.close()


def test_current_version_tracks_migrations() -> None:
    c = sqlite3.connect(":memory:")
    assert current_version(c) == 0  # before schema_version table exists
    _load_vec0(c)
    apply_migrations(c)
    # 012_session_id.sql is the highest-numbered migration.
    assert current_version(c) == 12
    c.close()


def test_all_migrations_recorded(conn: sqlite3.Connection) -> None:
    versions = [
        row[0] for row in conn.execute("SELECT version FROM schema_version ORDER BY version")
    ]
    assert versions == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]


# --- vec0 / FTS5 CREATED by migration 010 -------------------


def test_010_creates_vec0_virtual_table(conn: sqlite3.Connection) -> None:
    # Migration 010 creates the vec0 virtual table alongside the lateral
    # vec_episodes_meta that the first release already shipped.
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "vec_episodes" in names
    assert "vec_episodes_meta" in names


def test_010_creates_fts5_tables(conn: sqlite3.Connection) -> None:
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "episode_fts" in names
    assert "episode_content" in names


def test_010_vec0_schema(conn: sqlite3.Connection) -> None:
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='vec_episodes'"
    ).fetchone()[0]
    assert "USING vec0" in sql
    assert "float[384]" in sql
    for col in ("fact_id", "invalid_at", "cognitive_type", "created_at"):
        assert f"+{col}" in sql


def test_010_fts5_external_content(conn: sqlite3.Connection) -> None:
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='episode_fts'"
    ).fetchone()[0]
    assert "content='episode_content'" in sql
    assert "content_rowid='rowid'" in sql
    assert "unicode61" in sql


# --- each migration is a single transaction (DDL + version row) -----


def test_migration_ddl_and_version_row_are_atomic_on_failure(tmp_path, monkeypatch) -> None:
    # The migration DDL and the schema_version INSERT must commit together. If
    # a migration fails mid-script, the whole migration rolls back — no version
    # row, no partial DDL (closes the half-applied gap 009's header documented).
    # Build a temp migrations dir with a marker migration that creates a table
    # then fails; assert neither the table nor the version row persists.
    import seahorse.persistence.migrations.migrator as migrator

    monkeypatch.setattr(migrator, "_migrations_dir", lambda: tmp_path)
    (tmp_path / "001_marker.sql").write_text(
        "CREATE TABLE marker_tbl (x);\nINSERT INTO no_such_table VALUES (1);",
        encoding="utf-8",
    )
    c = sqlite3.connect(":memory:")
    with pytest.raises(sqlite3.OperationalError):
        migrator.apply_migrations(c)
    # rolled back: no version row recorded, marker table not created.
    versions = [row[0] for row in c.execute("SELECT version FROM schema_version").fetchall()]
    assert versions == []
    tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "marker_tbl" not in tables
    c.close()
