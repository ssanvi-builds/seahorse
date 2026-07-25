"""Migration load-bearing tests (Phase 2, RED-first).

These guard the signed DDL: json_valid enforcement, I5 null-safe bi-temporal
ordering, I11 unique-vigente per fact_id, the SO-1 title/summary columns on
episode_index, the episode_paths %.md CHECK, idempotency, and schema_version
tracking. They run against a fresh in-memory SQLite db with migrations applied.
"""

from __future__ import annotations

import sqlite3

import pytest

from seahorse.persistence.migrations.migrator import apply_migrations, current_version


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    apply_migrations(c)
    yield c
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


# --- I5 null-safe bi-temporal ordering --------------------------------------


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


# --- I11 unique-vigente per fact_id -----------------------------------------


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
    # now a new vigente row for the same fact_id is legitimate (supersession)
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


# --- SO-1: title/summary columns on episode_index ---------------------------


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
    first = apply_migrations(c)
    second = apply_migrations(c)
    assert first > 0
    assert second == 0  # re-running applies nothing
    c.close()


def test_current_version_tracks_migrations() -> None:
    c = sqlite3.connect(":memory:")
    assert current_version(c) == 0  # before schema_version table exists
    apply_migrations(c)
    # 009_supersedes_reason.sql is the highest-numbered migration in MVP-0.
    assert current_version(c) == 9
    c.close()


def test_all_migrations_recorded(conn: sqlite3.Connection) -> None:
    versions = [
        row[0] for row in conn.execute("SELECT version FROM schema_version ORDER BY version")
    ]
    assert versions == [1, 2, 3, 4, 5, 6, 7, 8, 9]


# --- MVP-0 boundary: vec0 / FTS5 NOT created ---------------------------------


def test_vec0_virtual_table_not_created(conn: sqlite3.Connection) -> None:
    # MVP-0: only vec_episodes_meta exists; the vec0 virtual table is deferred to MVP-1.
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "vec_episodes" not in names
    assert "vec_episodes_meta" in names


def test_fts5_table_not_created(conn: sqlite3.Connection) -> None:
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "episode_fts" not in names
    assert "episode_content" not in names
