"""Migration 009 — supersedes_reason column lands in storage (f5-03 §12.3, 5.3=(a)).

The Episode model has carried ``supersedes_reason`` since commit 1 (model+wire
only). Migration 009 persists it: ``ALTER TABLE episodes ADD COLUMN
supersedes_reason TEXT`` + the same on ``episode_index``. These tests guard the
column landing and the migration's idempotency model.

Idempotency note: SQLite ``ALTER TABLE ADD COLUMN`` has no ``IF NOT EXISTS`` and
cannot be made conditional in raw SQL (``executescript`` is non-procedural). The
PRIMARY idempotency mechanism is the migration runner's ``schema_version`` row
(each NNN runs at most once per DB). Since C8.3 #8 the runner wraps EACH
migration (DDL + the ``schema_version`` INSERT) in a single ``BEGIN``/``COMMIT``
transaction, so 009's two ALTERs are atomic WITH EACH OTHER AND with the version
row: if either ALTER or the INSERT fails, nothing commits and re-running retries
the whole migration (009 no longer carries its own ``BEGIN``/``COMMIT``, which
would nest-fail inside the runner's transaction).
"""

from __future__ import annotations

import sqlite3

from seahorse.persistence.migrations.migrator import apply_migrations, current_version


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {row[1]: row[2] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_migration_009_adds_supersedes_reason_columns() -> None:
    c = sqlite3.connect(":memory:")
    apply_migrations(c)
    episodes = _columns(c, "episodes")
    index = _columns(c, "episode_index")
    assert "supersedes_reason" in episodes
    assert episodes["supersedes_reason"] == "TEXT"
    assert "supersedes_reason" in index
    assert index["supersedes_reason"] == "TEXT"
    c.close()


def test_migration_009_idempotent_via_runner() -> None:
    # The runner's schema_version row guards re-runs (ALTER has no IF NOT EXISTS).
    c = sqlite3.connect(":memory:")
    first = apply_migrations(c)
    second = apply_migrations(c)
    assert first > 0
    assert second == 0  # re-running applies nothing — no 'duplicate column' error
    assert current_version(c) == 9
    c.close()


def test_migration_009_on_legacy_db_v8() -> None:
    # The real legacy upgrade path: pin a DB at v8 (001-008 applied, NO
    # supersedes_reason column), then apply 009 in isolation and verify both
    # columns land and the version row advances to 9. This exercises the path
    # existing deployments hit when upgrading — NOT the fresh-DB path the test
    # above already covers. (apply_migrations(up_to=8) is the test seam.)
    c = sqlite3.connect(":memory:")
    pre = apply_migrations(c, up_to=8)
    assert pre > 0
    assert current_version(c) == 8
    assert "supersedes_reason" not in _columns(c, "episodes")
    assert "supersedes_reason" not in _columns(c, "episode_index")
    # now apply 009 alone.
    n = apply_migrations(c)  # applies only 009 (001-008 already recorded)
    assert n == 1
    assert current_version(c) == 9
    assert _columns(c, "episodes")["supersedes_reason"] == "TEXT"
    assert _columns(c, "episode_index")["supersedes_reason"] == "TEXT"
    c.close()