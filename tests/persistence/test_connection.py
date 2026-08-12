"""ConnectionManager load-bearing tests (Phase 3).

Guards the signed threading model (f5-06 §3.5): PRAGMAs on every connection,
reentrant RLock that does not deadlock under nested atomic(), the depth counter
1→2→1→0, a single BEGIN IMMEDIATE / COMMIT per outermost atomic(), and a WAL
reader pool that is read-only and round-robins.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations


@pytest.fixture()
def manager(tmp_path) -> ConnectionManager:
    db = tmp_path / "seahorse.db"
    # M1-A.2: migration 010 (USING vec0) requires the extension on the connection.
    mgr = ConnectionManager(db, pool_size=4, extensions=("vec0",))
    mgr.open()
    apply_migrations(mgr.writer)
    yield mgr
    mgr.close()


def _pragma(conn: sqlite3.Connection, name: str) -> object:
    return conn.execute(f"PRAGMA {name}").fetchone()[0]


# --- PRAGMAs applied to writer and every reader ----------------------------


def test_writer_pragmas_applied(manager: ConnectionManager) -> None:
    w = manager.writer
    assert _pragma(w, "journal_mode") == "wal"
    assert _pragma(w, "synchronous") == 1  # NORMAL
    assert _pragma(w, "busy_timeout") == 5000
    assert _pragma(w, "foreign_keys") == 1


def test_reader_pragmas_applied(manager: ConnectionManager) -> None:
    # each reader must also carry the WAL/synchronous/busy/foreign_keys pragmas
    for _ in range(manager._pool_size):  # noqa: SLF001 — exercise the pool
        with manager.reader() as r:
            assert _pragma(r, "journal_mode") == "wal"
            assert _pragma(r, "synchronous") == 1
            assert _pragma(r, "busy_timeout") == 5000
            assert _pragma(r, "foreign_keys") == 1


def test_writer_row_factory_is_row(manager: ConnectionManager) -> None:
    row = manager.writer.execute("SELECT 1 AS one").fetchone()
    assert row["one"] == 1


def test_reader_is_read_only(manager: ConnectionManager) -> None:
    # mode=ro: any write must be rejected by SQLite.
    with manager.reader() as r, pytest.raises(sqlite3.OperationalError):
        r.execute("CREATE TABLE should_fail (x INTEGER)")


# --- reentrant RLock: nested atomic() does not deadlock ---------------------


def test_nested_atomic_does_not_deadlock(manager: ConnectionManager) -> None:
    with manager.atomic(), manager.atomic(), manager.atomic():
        # if the lock were non-reentrant, this would deadlock.
        manager.writer.execute(
            "INSERT INTO episodes (id, body_md, created_at, schema_version, "
            "provenance) VALUES ('e1', 'b', '2026-01-01T00:00:00Z', '3.1', '{}')"
        )


def test_depth_counter_transitions(manager: ConnectionManager) -> None:
    assert manager.depth == 0
    with manager.atomic():
        assert manager.depth == 1
        with manager.atomic():
            assert manager.depth == 2
        assert manager.depth == 1
    assert manager.depth == 0


# --- single BEGIN/COMMIT per outermost atomic() ----------------------------


def test_single_begin_commit_per_outer_atomic(manager: ConnectionManager) -> None:
    statements: list[str] = []
    manager.writer.set_trace_callback(lambda s: statements.append(s))
    with manager.atomic(), manager.atomic():
        manager.writer.execute(
            "INSERT INTO episodes (id, body_md, created_at, schema_version, "
            "provenance) VALUES ('e1', 'b', '2026-01-01T00:00:00Z', '3.1', '{}')"
        )
    begins = [s for s in statements if s.upper().startswith("BEGIN")]
    commits = [s for s in statements if s.upper().startswith("COMMIT")]
    assert begins == ["BEGIN IMMEDIATE"]
    assert len(commits) == 1


def test_rollback_on_inner_exception_rolls_back_outer(manager: ConnectionManager) -> None:
    class _Boom(Exception):
        pass

    with pytest.raises(_Boom), manager.atomic():
        manager.writer.execute(
            "INSERT INTO episodes (id, body_md, created_at, schema_version, "
            "provenance) VALUES ('e1', 'b', '2026-01-01T00:00:00Z', '3.1', '{}')"
        )
        with manager.atomic():
            raise _Boom

    # the outer rollback dropped the row.
    count = manager.writer.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    assert count == 0


def test_atomic_commits_persist(manager: ConnectionManager) -> None:
    with manager.atomic():
        manager.writer.execute(
            "INSERT INTO episodes (id, body_md, created_at, schema_version, "
            "provenance) VALUES ('e1', 'b', '2026-01-01T00:00:00Z', '3.1', '{}')"
        )
    count = manager.writer.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    assert count == 1


# --- reader pool round-robin -----------------------------------------------


def test_reader_pool_round_robins(manager: ConnectionManager) -> None:
    seen: list[int] = []
    for _ in range(manager._pool_size * 2):  # noqa: SLF001 — assert round-robin order
        with manager.reader() as r:
            seen.append(id(r))
    # the first four are distinct, the cycle repeats.
    assert len(set(seen[:4])) == 4
    assert seen[:4] == seen[4:8]


def test_concurrent_readers_do_not_share_connection(manager: ConnectionManager) -> None:
    barrier = threading.Barrier(manager._pool_size)  # noqa: SLF001
    held_ids: list[int] = []
    lock = threading.Lock()

    def grab() -> None:
        with manager.reader() as r:
            barrier.wait()
            with lock:
                held_ids.append(id(r))

    threads = [threading.Thread(target=grab) for _ in range(manager._pool_size)]  # noqa: SLF001
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # each of the N concurrent readers got a distinct connection.
    assert len(set(held_ids)) == manager._pool_size  # noqa: SLF001


# --- not-open guards -------------------------------------------------------


def test_writer_raises_when_not_open(tmp_path) -> None:
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=4)
    with pytest.raises(RuntimeError, match="not open"):
        _ = mgr.writer
    with pytest.raises(RuntimeError, match="not open"), mgr.reader() as _r:
        pass


def test_open_twice_raises_rather_than_orphaning(tmp_path) -> None:
    # open() is not idempotent: a second open() without close() must raise so it
    # does not overwrite _writer and orphan the first reader pool.
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=4)
    mgr.open()
    try:
        with pytest.raises(RuntimeError, match="already open"):
            mgr.open()
        assert len(mgr._readers) == 4  # noqa: SLF001 — first pool intact, not doubled
    finally:
        mgr.close()


def test_open_after_close_reopens_cleanly(tmp_path) -> None:
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=4)
    mgr.open()
    mgr.close()
    # close() reset _closed; a fresh open() must succeed and re-arm the pool.
    mgr.open()
    try:
        assert len(mgr._readers) == 4  # noqa: SLF001
        assert not mgr.is_closed
    finally:
        mgr.close()


def test_close_is_idempotent_and_clears_pool(manager: ConnectionManager) -> None:
    manager.close()
    # closing again must not raise (writer is None the second time).
    manager.close()


# --- C8.2 extension-loading seam (MVP-1 forward-compat) ---------------------


def test_extensions_param_defaults_empty(tmp_path) -> None:
    # MVP-0 default: no extensions requested. The seam is opt-in via the
    # `extensions` ctor arg so MVP-0 never depends on a vec0 / sqlite-vec binary.
    mgr = ConnectionManager(tmp_path / "seahorse.db")
    assert mgr._extensions == ()  # noqa: SLF001 — pin the default


def test_extensions_param_accepted(tmp_path) -> None:
    mgr = ConnectionManager(tmp_path / "seahorse.db", extensions=("vec0",))
    assert mgr._extensions == ("vec0",)  # noqa: SLF001 — seam carries the name


def test_open_without_extensions_never_calls_load_extension(tmp_path, monkeypatch) -> None:
    # MVP-0 invariant: with extensions=() (the default), open() must NOT touch
    # the extension-loading seam at all. Guards that MVP-0 never depends on the
    # Python build having extension loading compiled in, and that the seam stays
    # dormant until MVP-1 flips the flag. Spy on the instance method (sqlite3
    # connection types are C-builtins and cannot be monkeypatched at the class
    # level); a tripwire on `_load_extensions` pins the gate where it lives.
    calls: list[tuple] = []

    def _tripwire(*args, **kwargs) -> None:
        calls.append(args)
        raise AssertionError("_load_extensions must not be called with extensions=()")

    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=2)
    monkeypatch.setattr(mgr, "_load_extensions", _tripwire)
    mgr.open()
    mgr.close()
    assert calls == []


def test_open_retries_pragmas_on_database_locked(tmp_path, monkeypatch) -> None:
    # Matrix finding (concurrency combo): two processes opening a FRESH vault
    # simultaneously race on `PRAGMA journal_mode = WAL` (needs an exclusive
    # lock, does not honor busy_timeout) → one fails with "database is locked".
    # open() must retry the pragma application a bounded number of times.
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=2)
    calls = {"n": 0}
    orig = ConnectionManager._apply_pragmas  # noqa: SLF001 — spy on the seam

    def flaky(self, conn) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return orig(self, conn)

    monkeypatch.setattr(ConnectionManager, "_apply_pragmas", flaky)
    mgr.open()
    try:
        assert calls["n"] >= 2  # writer retried after the transient lock
        assert not mgr.is_closed
    finally:
        mgr.close()


def test_open_gives_up_after_retries_on_database_locked(tmp_path, monkeypatch) -> None:
    # A persistent lock (not transient) must still fail loud after the bounded
    # retries — no infinite loop, no silent swallow.
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=2)

    def always_locked(self, conn) -> None:  # noqa: ARG001
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ConnectionManager, "_apply_pragmas", always_locked)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        mgr.open()


def test_load_extensions_raises_actionable_error_when_unsupported(tmp_path) -> None:
    # Matrix finding (uv sync dev on a pyenv Python without
    # SQLITE_ENABLE_LOAD_EXTENSION): the seam used to crash with a cryptic
    # `AttributeError: 'sqlite3.Connection' object has no attribute
    # 'enable_load_extension'`. It must fail with an actionable message instead
    # (doctor already reports this as a FAIL; the DB commands must not lie).
    class _NoExtensionLoading:
        """A stand-in for a sqlite3.Connection on a build without the method."""

    mgr = ConnectionManager(tmp_path / "seahorse.db", extensions=("vec0",))
    with pytest.raises(RuntimeError, match="enable_load_extension"):
        mgr._load_extensions(_NoExtensionLoading())  # type: ignore[arg-type]


def test_extensions_vec0_loads_sqlite_vec_and_creates_virtual_table(tmp_path) -> None:
    # M1-A.1: with extensions=("vec0",), open() must load sqlite-vec so a vec0
    # virtual table can be created (the migration 010 path). Requires the
    # sqlite-vec package (core dep landing in M1-A.1) — RED until the dep + the
    # "vec0" special-case in _load_extensions are in.
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=2, extensions=("vec0",))
    mgr.open()
    try:
        mgr.writer.execute("CREATE VIRTUAL TABLE probe USING vec0(x float[4])")
        row = mgr.writer.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='probe'"
        ).fetchone()
        assert row is not None
    finally:
        mgr.close()


def test_storage_passes_vec0_to_connection_manager(tmp_path) -> None:
    # M1-A.1: the composition root (Storage) must propagate the vec0 extension to
    # the ConnectionManager so migration 010 (USING vec0) runs on open(). Storage
    # always requests vec0 (sqlite-vec is a core dep from M1-A.1); the CM default
    # stays () — only Storage opts in.
    from seahorse.persistence.storage import Storage

    storage = Storage(tmp_path / "seahorse.db")
    try:
        assert storage._cm._extensions == ("vec0",)  # noqa: SLF001 — seam pin
    finally:
        storage.close()
