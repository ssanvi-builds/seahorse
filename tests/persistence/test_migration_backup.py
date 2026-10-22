"""Pre-migration DB backup (the safety net an upgrade of a live vault needs).

The migration runner is atomic per migration: a failed DDL rolls back. That
protects the SCHEMA, not the data a future migration could strand — a vault
that has been capturing for months has no recoverable copy unless one is made
before the first pending migration runs. These tests pin the contract:

- an initialized DB with pending migrations gets ONE consistent snapshot
  (``<db>.bak-<YYYYmmdd-HHMMSS>``) taken BEFORE any DDL runs;
- a fresh DB and a fully-migrated DB get none;
- a backup failure is not swallowed — the migration must not proceed without
  its safety net.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from seahorse.persistence.migrations.backup import (
    backup_before_migration,
    has_pending_migrations,
)
from seahorse.persistence.migrations.migrator import (
    apply_migrations,
    current_version,
    latest_available_version,
)
from seahorse.persistence.storage import Storage

NOW = datetime(2026, 10, 22, 17, 26, 13)


def _load_vec0(c: sqlite3.Connection) -> None:
    """Load the sqlite-vec extension so migration 010 (``USING vec0``) runs."""
    import sqlite_vec  # type: ignore[import-untyped]

    c.enable_load_extension(True)
    try:
        sqlite_vec.load(c)
    finally:
        c.enable_load_extension(False)


def _legacy_db(path: Path, *, up_to: int = 8) -> sqlite3.Connection:
    """A DB stuck at an older schema, carrying a row that must survive."""
    c = sqlite3.connect(path)
    _load_vec0(c)
    apply_migrations(c, up_to=up_to)
    c.execute("CREATE TABLE operator_marker (label TEXT NOT NULL)")
    c.execute("INSERT INTO operator_marker (label) VALUES ('before-upgrade')")
    c.commit()
    return c


class TestPendingDetection:
    def test_legacy_db_has_pending(self, tmp_path: Path) -> None:
        c = _legacy_db(tmp_path / "seahorse.db")
        try:
            assert has_pending_migrations(c) is True
        finally:
            c.close()

    def test_fully_migrated_db_has_none(self, tmp_path: Path) -> None:
        path = tmp_path / "seahorse.db"
        c = _legacy_db(path)
        apply_migrations(c)
        try:
            assert has_pending_migrations(c) is False
        finally:
            c.close()

    def test_up_to_cap_respects_what_is_pending(self, tmp_path: Path) -> None:
        c = _legacy_db(tmp_path / "seahorse.db", up_to=8)
        try:
            assert has_pending_migrations(c, up_to=8) is False
            assert has_pending_migrations(c, up_to=9) is True
        finally:
            c.close()


class TestBackupBeforeMigration:
    def test_snapshot_is_consistent_and_taken_before_the_ddl(self, tmp_path: Path) -> None:
        path = tmp_path / "seahorse.db"
        c = _legacy_db(path, up_to=8)
        try:
            dest = backup_before_migration(c, path, now=NOW)
            assert dest is not None
            assert dest.name == "seahorse.db.bak-20261022-172613"
            # The snapshot reflects the PRE-migration schema and keeps the data.
            snap = sqlite3.connect(dest)
            try:
                assert current_version(snap) == 8
                assert snap.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert snap.execute("SELECT label FROM operator_marker").fetchall() == [
                    ("before-upgrade",)
                ]
            finally:
                snap.close()
            # ...and the source is untouched: the backup never migrated it.
            assert current_version(c) == 8
        finally:
            c.close()

    def test_fresh_db_gets_no_backup(self, tmp_path: Path) -> None:
        path = tmp_path / "seahorse.db"
        c = sqlite3.connect(path)
        _load_vec0(c)
        try:
            assert backup_before_migration(c, path, now=NOW) is None
        finally:
            c.close()
        assert list(tmp_path.glob("*.bak-*")) == []

    def test_fully_migrated_db_gets_no_backup(self, tmp_path: Path) -> None:
        path = tmp_path / "seahorse.db"
        c = _legacy_db(path)
        apply_migrations(c)
        try:
            assert backup_before_migration(c, path, now=NOW) is None
        finally:
            c.close()
        assert list(tmp_path.glob("*.bak-*")) == []

    def test_same_second_backups_do_not_overwrite_each_other(self, tmp_path: Path) -> None:
        path = tmp_path / "seahorse.db"
        c = _legacy_db(path)
        try:
            first = backup_before_migration(c, path, now=NOW)
            second = backup_before_migration(c, path, now=NOW)
        finally:
            c.close()
        assert first is not None and second is not None
        assert first != second
        assert {first.name, second.name} == {
            "seahorse.db.bak-20261022-172613",
            "seahorse.db.bak-20261022-172613-1",
        }
        assert first.exists() and second.exists()

    def test_backup_failure_does_not_swallow_the_error(self, tmp_path: Path) -> None:
        # The one outcome this module exists to prevent is migrating a vault
        # with no recoverable copy: a broken backup must fail loud.
        path = tmp_path / "seahorse.db"
        real = _legacy_db(path)

        class _BrokenBackup:
            def __init__(self, inner: sqlite3.Connection) -> None:
                self._inner = inner

            def __getattr__(self, name: str):
                return getattr(self._inner, name)

            def backup(self, target: sqlite3.Connection) -> None:
                raise sqlite3.OperationalError("disk I/O error")

        try:
            with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
                backup_before_migration(_BrokenBackup(real), path, now=NOW)  # type: ignore[arg-type]
            assert current_version(real) == 8  # nothing migrated behind our back
        finally:
            real.close()


class TestStorageWiring:
    def test_storage_open_snapshots_before_migrating(self, tmp_path: Path) -> None:
        path = tmp_path / "seahorse.db"
        c = _legacy_db(path, up_to=8)
        c.close()

        with Storage(path) as storage:
            assert storage.last_migration_backup is not None
            assert storage.last_migration_backup.exists()
            assert current_version(storage.connection_manager.writer) == (
                latest_available_version()
            )

        snap = sqlite3.connect(storage.last_migration_backup)
        try:
            assert current_version(snap) == 8  # the pre-upgrade schema
            assert snap.execute("SELECT label FROM operator_marker").fetchall() == [
                ("before-upgrade",)
            ]
        finally:
            snap.close()

    def test_storage_on_a_current_db_creates_no_backup(self, tmp_path: Path) -> None:
        path = tmp_path / "seahorse.db"
        with Storage(path) as first:
            assert first.last_migration_backup is None
        with Storage(path) as second:
            assert second.last_migration_backup is None
        assert list(tmp_path.glob("*.bak-*")) == []

    def test_storage_closes_when_the_backup_fails(self, tmp_path: Path, monkeypatch) -> None:
        path = tmp_path / "seahorse.db"
        c = _legacy_db(path, up_to=8)
        c.close()

        def _boom(*args, **kwargs):
            raise OSError("no space left on device")

        monkeypatch.setattr(
            "seahorse.persistence.storage.backup_before_migration", _boom
        )
        with pytest.raises(OSError, match="no space left"):
            Storage(path)
        # The failed construction must not leave the DB half-migrated.
        check = sqlite3.connect(path)
        try:
            assert current_version(check) == 8
        finally:
            check.close()
