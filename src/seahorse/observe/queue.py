"""Observer SQLite queue — the durable capture buffer.

The queue is the observer's OWN SQLite DB (``{vault}/.seahorse/observer/
observer.db``), NOT the engine sidecar. It reuses the ``ConnectionManager``
pattern (single-writer, WAL + ack). Only already-redacted envelopes are stored —
nothing raw is ever persisted.

Dedup layer 1 (queue-level): unique ``(session_id, prompt_number,
event_fingerprint)`` with ``INSERT OR IGNORE`` → a re-emitted event is a no-op.
``event_fingerprint = canonical_body_hash(redacted payload)``. Layers 2
(store-level uniqueness backstop) and 3 (``deterministic_id`` NOT extended to
agent) are the engine's job — the queue only owns layer 1.

``prompt_number`` is PERSISTED in the DB (not memory) so a resumed session
continues from the last prompt_number — a new byte-identical turn is never
falsely deduped by ``INSERT OR IGNORE``.

References:
- seahorse/persistence/connection.py (ConnectionManager pattern)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from seahorse.observe.batcher import event_fingerprint
from seahorse.observe.protocol import Envelope, envelope_to_json, parse_envelope
from seahorse.persistence.connection import ConnectionManager

_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS observer_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    prompt_number INTEGER NOT NULL,
    event_fingerprint TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    acked_at TEXT,
    UNIQUE(session_id, prompt_number, event_fingerprint)
)
"""

_CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS observer_sessions (
    session_id TEXT PRIMARY KEY,
    prompt_number INTEGER NOT NULL DEFAULT 0
)
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ObserverQueue:
    """Durable capture buffer: enqueue redacted envelopes, drain + ack.

    Single-writer (``ConnectionManager`` with one writer + WAL). The caller owns
    the lifecycle: ``close()`` releases the SQLite connections.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._cm = ConnectionManager(db_path, pool_size=1)
        self._cm.open()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._cm.atomic() as conn:
            conn.execute(_CREATE_EVENTS)
            conn.execute(_CREATE_SESSIONS)

    # ------------------------------------------------------------- sessions

    def register_session(self, session_id: str) -> None:
        """Register a session at ``prompt_number=0`` (idempotent)."""
        with self._cm.atomic() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO observer_sessions (session_id, prompt_number) "
                "VALUES (?, 0)",
                (session_id,),
            )

    def current_prompt_number(self, session_id: str) -> int:
        """The persisted prompt_number for ``session_id`` (0 if unknown)."""
        with self._cm.reader() as conn:
            row = conn.execute(
                "SELECT prompt_number FROM observer_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return int(row["prompt_number"]) if row is not None else 0

    def advance_prompt_number(self, session_id: str) -> int:
        """Increment the persisted prompt_number and return the new value.

        Persisted, not in-memory — a resumed session continues from the last
        prompt_number, so a new byte-identical turn is never falsely deduped.
        """
        with self._cm.atomic() as conn:
            conn.execute(
                "INSERT INTO observer_sessions (session_id, prompt_number) VALUES (?, 1) "
                "ON CONFLICT(session_id) DO UPDATE SET prompt_number = prompt_number + 1",
                (session_id,),
            )
            row = conn.execute(
                "SELECT prompt_number FROM observer_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return int(row["prompt_number"])

    # -------------------------------------------------------------- enqueue

    def enqueue(self, envelope: Envelope) -> bool:
        """Persist a redacted envelope. Returns True if inserted, False if dedup.

        Dedup layer 1: ``INSERT OR IGNORE`` on ``(session_id, prompt_number,
        event_fingerprint)`` — a re-emitted event is a no-op.
        """
        fingerprint = event_fingerprint(envelope.payload)
        created_at = envelope.ts or _now_iso()
        with self._cm.atomic() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO observer_events "
                "(session_id, prompt_number, event_fingerprint, envelope_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    envelope.session_id,
                    envelope.prompt_number,
                    fingerprint,
                    envelope_to_json(envelope),
                    created_at,
                ),
            )
            return cur.rowcount > 0

    # -------------------------------------------------------------- drain

    def pending(self) -> list[tuple[int, Envelope]]:
        """Unacked envelopes in arrival order (``(event_id, envelope)``)."""
        with self._cm.reader() as conn:
            rows = conn.execute(
                "SELECT id, envelope_json FROM observer_events "
                "WHERE acked_at IS NULL ORDER BY id ASC"
            ).fetchall()
        return [(int(row["id"]), parse_envelope(json.loads(row["envelope_json"]))) for row in rows]

    def ack(self, event_id: int) -> None:
        """Mark an event as acked (idempotent)."""
        with self._cm.atomic() as conn:
            conn.execute(
                "UPDATE observer_events SET acked_at = ? WHERE id = ? AND acked_at IS NULL",
                (_now_iso(), event_id),
            )

    def pending_count(self) -> int:
        """Number of unacked events."""
        with self._cm.reader() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM observer_events WHERE acked_at IS NULL"
            ).fetchone()
            return int(row["c"])

    # ------------------------------------------------------------- lifecycle

    def close(self) -> None:
        self._cm.close()

    def __enter__(self) -> ObserverQueue:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


__all__ = ["ObserverQueue"]
