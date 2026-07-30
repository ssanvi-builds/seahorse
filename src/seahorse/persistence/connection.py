"""ConnectionManager — single writer (reentrant RLock) + N readers (WAL pool).

Owns the SQLite file for the whole process (ADR-04 / f5-06 §3.5). All connections
use ``check_same_thread=False`` because the repository API is sync and the caller
(FastAPI) offloads calls to a threadpool — the connection may be touched from a
different thread than the one that created it.

Threading model (signed in f5-06 §3.5):

- **One writer connection.** Every mutation goes through it, guarded by a
  reentrant ``threading.RLock`` so the ``improve`` pattern
  ``atomic() + append() + set_invalid_at()`` nests without deadlock. The write
  transaction opens with ``BEGIN IMMEDIATE`` (acquires the write lock upfront,
  avoids ``SQLITE_BUSY_SNAPSHOT`` on the SHARED→EXCLUSIVE upgrade).
- **N reader connections** (default 4). WAL lets readers see a consistent snapshot
  without blocking the writer or vice-versa. Each reader is guarded by its own
  ``Lock``: a single ``sqlite3.Connection`` is not safe for concurrent use even with
  ``check_same_thread=False``.
- **``atomic()`` is reentrant.** A single ``BEGIN IMMEDIATE`` / ``COMMIT`` (or
  ``ROLLBACK`` on exception) is issued per *outermost* ``atomic()``; nested
  acquisitions only bump the depth counter.

The RLock serializes agent writes against each other; it does NOT coordinate with
the human co-editor (Obsidian, ADR-03). Lost-update against the human is a declared
MVP limitation.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA mmap_size = 268435456",
    "PRAGMA foreign_keys = ON",
)


class ConnectionManager:
    """Owner of the SQLite file: one writer (reentrant RLock) + N read-only readers."""

    def __init__(
        self,
        db_path: str | Path,
        pool_size: int = 4,
        *,
        extensions: Sequence[str] = (),
    ) -> None:
        self._db_path = str(db_path)
        self._pool_size = pool_size
        # C8.2: MVP-1 forward-compat seam. SQLite loadable extensions (e.g.
        # ``vec0`` from sqlite-vec) are needed when the vector / FTS5-on-vec
        # backends land in #6. MVP-0 ships zero runtime deps and the binary is
        # absent, so the default is empty -> ``_load_extensions`` is never
        # called and MVP-0 never depends on extension-loading being compiled
        # into the sqlite3 module. MVP-1 passes ``extensions=("vec0",)`` at the
        # composition root; ``open()`` then loads each named extension on the
        # writer AND every reader (kNN runs on the WAL reader pool, so the
        # extension must be present on each connection that touches vec0 tables).
        self._extensions = tuple(extensions)
        self._lock = threading.RLock()
        self._depth = 0
        self._writer: sqlite3.Connection | None = None
        self._readers: list[sqlite3.Connection] = []
        self._reader_locks: list[threading.Lock] = []
        self._reader_idx = 0
        self._reader_picker = threading.Lock()
        self._closed = False

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> None:
        """Open the writer + the reader pool and apply PRAGMAs to each.

        Not idempotent: a second ``open()`` without an intervening ``close()``
        raises rather than orphaning the first connection set. When
        ``extensions`` is non-empty, each named extension is loaded on the writer
        and every reader (C8.2 seam; default empty = no-op, MVP-0 safe).
        """
        with self._lock:
            if self._writer is not None or self._readers:
                raise RuntimeError("ConnectionManager is already open")
            self._writer = sqlite3.connect(
                self._db_path, isolation_level=None, check_same_thread=False
            )
            self._apply_pragmas(self._writer)
            if self._extensions:
                self._load_extensions(self._writer)
            for _ in range(self._pool_size):
                reader = sqlite3.connect(
                    f"file:{self._db_path}?mode=ro",
                    uri=True,
                    isolation_level=None,
                    check_same_thread=False,
                )
                self._apply_pragmas(reader)
                if self._extensions:
                    self._load_extensions(reader)
                self._readers.append(reader)
                self._reader_locks.append(threading.Lock())
            self._closed = False
            self._reader_idx = 0

    def close(self) -> None:
        """Close the writer + reader pool.

        Acquires the write lock so an in-flight ``atomic()`` finishes before the
        connections drop; once closed, ``atomic()`` / ``reader()`` raise. A
        second ``close()`` is a no-op.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for reader in self._readers:
                reader.close()
            self._readers.clear()
            self._reader_locks.clear()
            if self._writer is not None:
                self._writer.close()
                self._writer = None

    def __enter__(self) -> ConnectionManager:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- pragmas + extensions -----------------------------------------------

    def _apply_pragmas(self, conn: sqlite3.Connection) -> None:
        for pragma in _PRAGMAS:
            conn.execute(pragma)
        conn.row_factory = sqlite3.Row

    def _load_extensions(self, conn: sqlite3.Connection) -> None:
        """Load each named SQLite extension on ``conn`` (C8.2 MVP-1 seam).

        Only called from ``open()`` when ``self._extensions`` is non-empty, so
        MVP-0 (default empty) never reaches this path. Enables extension
        loading, loads each name, then re-disables loading so arbitrary SQL
        cannot ``load_extension`` later. A missing binary or a Python build
        without extension-loading support raises here — MVP-1 must surface
        that (MVP-0 is unaffected because it never sets ``extensions``).
        """
        conn.enable_load_extension(True)
        try:
            for ext in self._extensions:
                conn.load_extension(ext)
        finally:
            # Re-lock extension loading: only the composition root may load
            # extensions, not arbitrary runtime SQL.
            with contextlib.suppress(sqlite3.OperationalError):
                conn.enable_load_extension(False)

    # -- writer / transaction -------------------------------------------------

    @property
    def writer(self) -> sqlite3.Connection:
        if self._writer is None:
            raise RuntimeError("ConnectionManager is not open")
        return self._writer

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def depth(self) -> int:
        """Reentrancy depth of the current thread's outermost ``atomic()``."""
        return self._depth

    @contextmanager
    def atomic(self) -> Iterator[sqlite3.Connection]:
        """Reentrant write transaction.

        Issues ``BEGIN IMMEDIATE`` on the outermost call and ``COMMIT`` (or
        ``ROLLBACK`` if the body raises) on its exit. Nested acquisitions only bump
        the depth counter and reuse the same writer connection + transaction.
        A failed ``COMMIT`` is followed by ``ROLLBACK`` so the writer is not left
        mid-transaction (which would poison the next ``atomic()``).
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("ConnectionManager is closed")
            is_outer = self._depth == 0
            self._depth += 1
            try:
                if is_outer:
                    self.writer.execute("BEGIN IMMEDIATE")
                try:
                    yield self.writer
                except BaseException:
                    if is_outer:
                        self._safe_rollback()
                    raise
                else:
                    if is_outer:
                        self._safe_commit()
            finally:
                self._depth -= 1

    def _safe_commit(self) -> None:
        """COMMIT; on failure ROLLBACK so the writer exits the transaction."""
        try:
            self.writer.execute("COMMIT")
        except BaseException:
            # COMMIT failed (e.g. deferred FK violation, disk full). Issue
            # ROLLBACK to leave the writer idle; suppress rollback errors so
            # the original COMMIT failure propagates.
            with contextlib.suppress(BaseException):
                self.writer.execute("ROLLBACK")
            raise

    def _safe_rollback(self) -> None:
        """ROLLBACK; suppress errors so the original exception propagates."""
        with contextlib.suppress(BaseException):
            self.writer.execute("ROLLBACK")

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """Acquire the writer connection under the reentrant lock WITHOUT a tx.

        For FULL-level reads that must serialize with concurrent writes on the same
        writer connection (the hot INDEX-level path uses the reader pool instead).
        Reentrant: safe to call inside an outer ``atomic()`` on the same thread.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("ConnectionManager is closed")
            yield self.writer

    # -- readers --------------------------------------------------------------

    @contextmanager
    def reader(self) -> Iterator[sqlite3.Connection]:
        """Yield a read-only connection from the WAL reader pool (round-robin).

        Each reader is guarded by its own lock so two callers never share one
        ``sqlite3.Connection`` concurrently.
        """
        if self._closed or not self._readers:
            raise RuntimeError("ConnectionManager is not open")
        with self._reader_picker:
            idx = self._reader_idx
            self._reader_idx = (self._reader_idx + 1) % self._pool_size
        with self._reader_locks[idx]:
            yield self._readers[idx]


__all__ = ["ConnectionManager"]
