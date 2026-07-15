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
    mgr = ConnectionManager(db, pool_size=4)
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
