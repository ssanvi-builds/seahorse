"""Storage — the composition root for the persistence layer.

A single ``Storage`` owns the one ``ConnectionManager`` and constructs every
repository sharing it. There is exactly ONE transaction boundary:
the ``atomic()`` on ``ConnectionManager``, exposed here as ``Storage.atomic()``.
No repository opens its own ``BEGIN``; a delegating ``atomic()`` that reuses
``ConnectionManager.atomic()`` is permitted (the ``episodes`` repo exposes one
so the Engine's ``improve`` pattern reads naturally). All other repositories
MUST NOT expose ``atomic()``. A multi-repo write (``episodes.append`` +
``audit.append`` + ``sidecar.put_path``) commits as a single
``BEGIN IMMEDIATE`` / ``COMMIT``.

``open()`` applies migrations idempotently (``schema_version`` tracked). Closing
the storage closes the connection manager (writer + reader pool). Use as a
context manager::

    with Storage(path) as s:
        with s.atomic():
            s.episodes.append(ep)
            s.audit.append(ev)
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_audit import SqliteAuditEventRepository
from seahorse.persistence.sqlite_embeddings_cache import (
    SqliteEmbeddingsCacheRepository,
)
from seahorse.persistence.sqlite_episode_index import SqliteEpisodeIndexRepository
from seahorse.persistence.sqlite_episode_repo import SqliteEpisodeRepository
from seahorse.persistence.sqlite_reindex_jobs import SqliteReindexJobRepository
from seahorse.persistence.sqlite_sidecar import SqliteSidecarIndexRepository

if TYPE_CHECKING:
    # The two later-release concrete repos (vector_index / fts_index) are
    # imported lazily inside the ``vector`` / ``fts`` property accessors, NOT at
    # module top. When the real sqlite-vec + FTS5 implementations land, those
    # modules will ``import sqlite_vec`` (and possibly ``numpy``) at module top;
    # a top-level import here would cascade into every ``import seahorse.facade``
    # / ``import seahorse.mcp`` (factory -> storage) loading the heavy deps at
    # import time. The type-only import keeps the return-type annotations
    # precise for static analysis without executing the modules. The runtime
    # import lives in the property accessors (lazy, on first ``.vector`` /
    # ``.fts`` access — never in the current release since the stubs are unused).
    from seahorse.persistence.fts_index import SqliteFullTextIndexRepository
    from seahorse.persistence.vector_index import SqliteVectorIndexRepository


class Storage:
    """Composition root: one ``ConnectionManager`` + all repositories + one ``atomic()``."""

    def __init__(self, db_path: Path | str, *, pool_size: int = 4) -> None:
        # Storage opts into the vec0 extension (sqlite-vec is a core dep) so
        # migration 010 (``CREATE VIRTUAL TABLE ... USING vec0``) runs on
        # ``open()``. The CM default stays ``()`` — only the composition root
        # enables the extension. ``sqlite_vec`` is loaded lazily inside
        # ``ConnectionManager._load_extensions``, never at import time.
        self._cm = ConnectionManager(db_path, pool_size=pool_size, extensions=("vec0",))
        self._cm.open()
        try:
            apply_migrations(self._cm.writer)  # clone-and-run: schema ready on construct
        except BaseException:
            # If migrations fail the Storage is unusable; close the manager so
            # the writer + reader pool do not leak (no caller reference exists).
            self._cm.close()
            raise
        # build the current-release repositories sharing the one connection
        # manager. The two later-release repos (vector / fts) are constructed
        # lazily on first property access so importing this module does not pull
        # their (future, heavy) dependencies. They stay None until accessed.
        self._episodes = SqliteEpisodeRepository(self._cm)
        self._episode_index = SqliteEpisodeIndexRepository(self._cm)
        self._audit = SqliteAuditEventRepository(self._cm)
        self._sidecar = SqliteSidecarIndexRepository(self._cm)
        self._embeddings_cache = SqliteEmbeddingsCacheRepository(self._cm)
        self._reindex_jobs = SqliteReindexJobRepository(self._cm)
        self._vector: SqliteVectorIndexRepository | None = None
        self._fts: SqliteFullTextIndexRepository | None = None

    # -- the single shared atomic ------------------------------------------------

    @contextmanager
    def atomic(self) -> Iterator[None]:
        with self._cm.atomic():
            yield

    # -- repository accessors --------------------------------------------------

    @property
    def episodes(self) -> SqliteEpisodeRepository:
        return self._episodes

    @property
    def episode_index(self) -> SqliteEpisodeIndexRepository:
        return self._episode_index

    @property
    def audit(self) -> SqliteAuditEventRepository:
        return self._audit

    @property
    def sidecar(self) -> SqliteSidecarIndexRepository:
        return self._sidecar

    @property
    def embeddings_cache(self) -> SqliteEmbeddingsCacheRepository:
        return self._embeddings_cache

    @property
    def reindex_jobs(self) -> SqliteReindexJobRepository:
        return self._reindex_jobs

    @property
    def vector(self) -> SqliteVectorIndexRepository:
        # Lazy import + construct. The later-release concrete repo is pulled in
        # only when a caller actually needs it; ``import seahorse.facade`` /
        # ``import seahorse.mcp`` never reach here in the current release (the
        # stub is unused until the persistence layer wires the real vector
        # backend). Cached after first access.
        if self._vector is None:
            from seahorse.persistence.vector_index import SqliteVectorIndexRepository

            self._vector = SqliteVectorIndexRepository(self._cm)
        return self._vector

    @property
    def fts(self) -> SqliteFullTextIndexRepository:
        # Lazy import + construct (see ``vector``).
        if self._fts is None:
            from seahorse.persistence.fts_index import SqliteFullTextIndexRepository

            self._fts = SqliteFullTextIndexRepository(self._cm)
        return self._fts

    # -- lifecycle -------------------------------------------------------------

    def open(self) -> None:
        """Apply migrations idempotently. Idempotent: re-applying is a no-op."""
        apply_migrations(self._cm.writer)

    def close(self) -> None:
        self._cm.close()

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


__all__ = ["Storage"]
