"""SqliteSidecarIndexRepository tests (Phase 5b). episode_paths upsert + reindex ctx."""

from __future__ import annotations

import pytest

from seahorse.contracts.persistence import SidecarIndexRepository
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_sidecar import SqliteSidecarIndexRepository


@pytest.fixture()
def sidecar(tmp_path) -> SqliteSidecarIndexRepository:
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=4)
    mgr.open()
    apply_migrations(mgr.writer)
    repo = SqliteSidecarIndexRepository(mgr)
    yield repo
    mgr.close()


def test_structurally_satisfies_protocol(sidecar: SqliteSidecarIndexRepository) -> None:
    assert isinstance(sidecar, SidecarIndexRepository)
    assert not hasattr(sidecar, "atomic")  # SO-7a.6


def test_put_then_get_path(sidecar: SqliteSidecarIndexRepository) -> None:
    sidecar.put_path("e1", "notes/e1.md", 111, 42)
    assert sidecar.get_path("e1") == ("notes/e1.md", 111, 42)


def test_get_path_missing_returns_none(sidecar: SqliteSidecarIndexRepository) -> None:
    assert sidecar.get_path("nope") is None


def test_put_path_upsert_on_rename(sidecar: SqliteSidecarIndexRepository) -> None:
    # a rename is an UPDATE (episode_paths is mutable); the UPSERT keeps one row.
    sidecar.put_path("e1", "old.md", 1, 10)
    sidecar.put_path("e1", "new.md", 2, 20)
    assert sidecar.get_path("e1") == ("new.md", 2, 20)


def test_reindex_commits_metadata_with_body(sidecar: SqliteSidecarIndexRepository) -> None:
    # the reindex context commits the path metadata alongside the caller's work;
    # a body exception leaves the metadata uncommitted (atomic rollback).
    class _Boom(Exception):
        pass

    with pytest.raises(_Boom), sidecar.reindex("e1", "notes/e1.md", 5, 50):
        raise _Boom
    assert sidecar.get_path("e1") is None


def test_reindex_success_persists(sidecar: SqliteSidecarIndexRepository) -> None:
    with sidecar.reindex("e1", "notes/e1.md", 5, 50):
        pass
    assert sidecar.get_path("e1") == ("notes/e1.md", 5, 50)


def test_rebuild_all_raises_not_implemented(sidecar: SqliteSidecarIndexRepository) -> None:
    # the vault-backed seam is wired by #3 in a later phase.
    with pytest.raises(NotImplementedError):
        sidecar.rebuild_all()
