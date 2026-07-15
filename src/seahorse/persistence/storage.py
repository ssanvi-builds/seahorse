"""Storage — the composition root for the persistence layer (#6).

A single ``Storage`` owns the one ``ConnectionManager`` and constructs every
repository sharing it. There is exactly ONE transaction boundary (SO-7a.6):
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

from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.fts_index import SqliteFullTextIndexRepository
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_audit import SqliteAuditEventRepository
from seahorse.persistence.sqlite_embeddings_cache import (
    SqliteEmbeddingsCacheRepository,
)
from seahorse.persistence.sqlite_episode_index import SqliteEpisodeIndexRepository
from seahorse.persistence.sqlite_episode_repo import SqliteEpisodeRepository
from seahorse.persistence.sqlite_reindex_jobs import SqliteReindexJobRepository
from seahorse.persistence.sqlite_sidecar import SqliteSidecarIndexRepository
from seahorse.persistence.vector_index import SqliteVectorIndexRepository


class Storage:
    """Composition root: one ``ConnectionManager`` + all repositories + one ``atomic()``."""

    def __init__(self, db_path: Path | str, *, pool_size: int = 4) -> None:
        self._cm = ConnectionManager(db_path, pool_size=pool_size)
        self._cm.open()
        try:
            apply_migrations(self._cm.writer)  # clonar-y-ejecutar: schema ready on construct
        except BaseException:
            # If migrations fail the Storage is unusable; close the manager so
            # the writer + reader pool do not leak (no caller reference exists).
            self._cm.close()
            raise
        # build all repositories sharing the one connection manager.
        self._episodes = SqliteEpisodeRepository(self._cm)
        self._episode_index = SqliteEpisodeIndexRepository(self._cm)
        self._audit = SqliteAuditEventRepository(self._cm)
        self._sidecar = SqliteSidecarIndexRepository(self._cm)
        self._embeddings_cache = SqliteEmbeddingsCacheRepository(self._cm)
        self._reindex_jobs = SqliteReindexJobRepository(self._cm)
        self._vector = SqliteVectorIndexRepository(self._cm)
        self._fts = SqliteFullTextIndexRepository(self._cm)

    # -- the single shared atomic (SO-7a.6) -------------------------------------

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
        return self._vector

    @property
    def fts(self) -> SqliteFullTextIndexRepository:
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
