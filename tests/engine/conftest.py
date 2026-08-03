"""Shared fixtures + helpers for engine tests.

Reuses the #6 Storage stack (ConnectionManager + apply_migrations +
SqliteEpisodeRepository / SqliteAuditEventRepository) so the engine is tested
against the real persistence layer, not a mock. ``_episode`` mirrors the #6
test helper so engine tests build episodes the same way storage expects.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.contracts.episode import Episode
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_audit import SqliteAuditEventRepository
from seahorse.persistence.sqlite_episode_repo import SqliteEpisodeRepository

_UNSET = object()


@pytest.fixture()
def repo(tmp_path) -> SqliteEpisodeRepository:
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=4, extensions=("vec0",))
    mgr.open()
    apply_migrations(mgr.writer)
    r = SqliteEpisodeRepository(mgr)
    yield r
    mgr.close()


@pytest.fixture()
def audit(tmp_path) -> SqliteAuditEventRepository:
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=4, extensions=("vec0",))
    mgr.open()
    apply_migrations(mgr.writer)
    a = SqliteAuditEventRepository(mgr)
    yield a
    mgr.close()


@pytest.fixture()
def storage(tmp_path):
    """Shared ConnectionManager so repo + audit live on the same DB."""
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=4, extensions=("vec0",))
    mgr.open()
    apply_migrations(mgr.writer)
    r = SqliteEpisodeRepository(mgr)
    a = SqliteAuditEventRepository(mgr)
    yield r, a
    mgr.close()


def _episode(
    ep_id: str = "e1",
    *,
    subject: str | None = "Sergio",
    fact_id: str | None = "fact-1",
    valid_at: datetime | None = None,
    invalid_at: datetime | None = None,
    expired_at: datetime | None = None,
    supersedes: str | None = None,
    body: str = "body",
    title: str | None = "Title",
    summary: str | None = "Summary",
    cognitive_type: str | None = "fact",
    source_type: str | None = "agent",
    created_at: datetime | None | object = _UNSET,
    schema_version: str = "3.1",
    provenance: dict | None = None,
) -> Episode:
    # ``created_at`` is required (non-None) on the Pydantic model (F3.1
    # non-nullable, DDL NOT NULL). Tests that exercise guard I1 / the skip-path
    # validator on a missing created_at pass ``created_at=None``; build with a
    # valid datetime then null it via ``model_copy`` (skips validation) so the
    # guard — not the model — is the enforcement point under test.
    requested = datetime(2026, 1, 1, tzinfo=UTC) if created_at is _UNSET else created_at
    effective = requested if requested is not None else datetime(2026, 1, 1, tzinfo=UTC)
    ep = Episode(
        id=ep_id,
        created_at=effective,
        schema_version=schema_version,
        provenance=provenance if provenance is not None else {"agent": "test"},
        body=body,
        subject=subject,
        fact_id=fact_id,
        valid_at=valid_at,
        invalid_at=invalid_at,
        expired_at=expired_at,
        supersedes=supersedes,
        cognitive_type=cognitive_type,
        source_type=source_type,
        title=title,
        summary=summary,
    )
    if requested is None:
        ep = ep.model_copy(update={"created_at": None})
    return ep