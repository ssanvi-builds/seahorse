"""SqliteAuditEventRepository — append-only audit log (f5-06 §7a.4, SO-3c).

Implements ``seahorse.contracts.persistence.AuditEventRepository``. The
``AuditEvent`` type is defined by #2 (#6 only serializes it to a row). The
``id`` autoincrement PK of ``audit_events`` is storage-generated and is NOT part
of the ``AuditEvent`` type. There is NO own ``atomic()`` (SO-7a.6): writes wrap
``ConnectionManager.atomic()``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from seahorse.contracts.engine import AuditEvent
from seahorse.persistence.connection import ConnectionManager


def _fmt_dt(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _req_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


class SqliteAuditEventRepository:
    """SQLite implementation of the ``AuditEventRepository`` Protocol."""

    def __init__(self, cm: ConnectionManager) -> None:
        self._cm = cm

    def append(self, event: AuditEvent) -> None:
        with self._cm.atomic() as w:
            w.execute(
                "INSERT INTO audit_events (agent_id, session_id, primitive, target_id, "
                "successor_id, valid_time, transaction_time, reason, cognitive_type, result) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    event.agent_id,
                    event.session_id,
                    event.primitive,
                    event.target_id,
                    event.successor_id,
                    _fmt_dt(event.valid_time),
                    _fmt_dt(event.transaction_time),
                    event.reason,
                    event.cognitive_type,
                    event.result,
                ),
            )

    def query(
        self,
        *,
        target_id: str | None = None,
        session_id: str | None = None,
        since: datetime | None = None,
    ) -> list[AuditEvent]:
        clauses: list[str] = []
        params: list[object] = []
        if target_id is not None:
            clauses.append("target_id = ?")
            params.append(target_id)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if since is not None:
            clauses.append("transaction_time >= ?")
            params.append(_fmt_dt(since))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM audit_events{where} ORDER BY transaction_time ASC"
        with self._cm.read() as w:
            rows = w.execute(sql, tuple(params)).fetchall()
        return [self._row_to_event(r) for r in rows]

    def _row_to_event(self, row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(
            primitive=row["primitive"],
            target_id=row["target_id"],
            transaction_time=_req_dt(row["transaction_time"]),
            result=row["result"],
            agent_id=row["agent_id"],
            session_id=row["session_id"],
            successor_id=row["successor_id"],
            valid_time=_parse_dt(row["valid_time"]),
            reason=row["reason"],
            cognitive_type=row["cognitive_type"],
        )


__all__ = ["SqliteAuditEventRepository"]
