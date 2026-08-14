"""Shared fixtures for distill tests (mirror of the engine conftest).

The distill layer is a client of the engine — tests use the real
persistence stack so the consolidated episodes are verified against the real
storage, not a mock.
"""

from __future__ import annotations

import pytest

from seahorse.engine.engine import BiTemporalEngine
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_audit import SqliteAuditEventRepository
from seahorse.persistence.sqlite_episode_repo import SqliteEpisodeRepository


@pytest.fixture()
def engine(tmp_path):
    """A real ``BiTemporalEngine`` over SQLite (repo + audit on one DB)."""
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=4, extensions=("vec0",))
    mgr.open()
    apply_migrations(mgr.writer)
    repo = SqliteEpisodeRepository(mgr)
    audit = SqliteAuditEventRepository(mgr)
    eng = BiTemporalEngine(repo, audit)
    yield eng, repo, audit
    mgr.close()
