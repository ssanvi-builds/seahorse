"""SqliteAuditEventRepository tests. Append-only, query by target/session/since."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seahorse.contracts.engine import AuditEvent
from seahorse.contracts.persistence import AuditEventRepository
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_audit import SqliteAuditEventRepository


@pytest.fixture()
def audit(tmp_path) -> SqliteAuditEventRepository:
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=4, extensions=("vec0",))
    mgr.open()
    apply_migrations(mgr.writer)
    repo = SqliteAuditEventRepository(mgr)
    yield repo
    mgr.close()


def _event(
    target: str | None = "e1", t: datetime | None = None, primitive: str = "apply"
) -> AuditEvent:
    return AuditEvent(
        primitive=primitive,
        target_id=target,
        transaction_time=t or datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        result="added",
        agent_id="agent-1",
        session_id="sess-1",
    )


def test_structurally_satisfies_protocol(audit: SqliteAuditEventRepository) -> None:
    assert isinstance(audit, AuditEventRepository)
    assert not hasattr(audit, "atomic")  # no own atomic()


def test_append_then_query_by_target(audit: SqliteAuditEventRepository) -> None:
    audit.append(_event(target="e1"))
    audit.append(_event(target="e2"))
    assert {e.target_id for e in audit.query(target_id="e1")} == {"e1"}


def test_query_by_session(audit: SqliteAuditEventRepository) -> None:
    audit.append(_event(target="e1"))
    assert len(audit.query(session_id="sess-1")) == 1
    assert audit.query(session_id="nope") == []


def test_query_since_filters_by_transaction_time(audit: SqliteAuditEventRepository) -> None:
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    audit.append(_event(target="e1", t=base))
    audit.append(_event(target="e2", t=base + timedelta(hours=2)))
    since = base + timedelta(hours=1)
    ids = {e.target_id for e in audit.query(since=since)}
    assert ids == {"e2"}


def test_query_combined_filters(audit: SqliteAuditEventRepository) -> None:
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    audit.append(_event(target="e1", t=base))
    audit.append(_event(target="e2", t=base + timedelta(hours=3)))
    result = audit.query(target_id="e2", since=base + timedelta(hours=1))
    assert [e.target_id for e in result] == ["e2"]


def test_query_returns_in_transaction_time_order(audit: SqliteAuditEventRepository) -> None:
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    audit.append(_event(target="e3", t=base + timedelta(hours=2)))
    audit.append(_event(target="e1", t=base))
    audit.append(_event(target="e2", t=base + timedelta(hours=1)))
    order = [e.target_id for e in audit.query()]
    assert order == ["e1", "e2", "e3"]


def test_round_trip_preserves_fields(audit: SqliteAuditEventRepository) -> None:
    ev = AuditEvent(
        primitive="improve",
        target_id="e1",
        transaction_time=datetime(2026, 1, 1, tzinfo=UTC),
        result="updated",
        agent_id="a",
        session_id="s",
        successor_id="e2",
        valid_time=datetime(2025, 12, 31, tzinfo=UTC),
        reason="superseded",
        cognitive_type="fact",
    )
    audit.append(ev)
    fetched = audit.query(target_id="e1")[0]
    assert fetched.primitive == "improve"
    assert fetched.successor_id == "e2"
    assert fetched.reason == "superseded"
    assert fetched.valid_time == datetime(2025, 12, 31, tzinfo=UTC)
