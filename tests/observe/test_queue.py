"""Tests for ``seahorse.observe.queue`` — the observer's SQLite queue.

The queue is the observer's OWN SQLite DB (``{vault}/.seahorse/observer/
observer.db``), NOT the engine sidecar. It reuses the ``ConnectionManager``
pattern (single-writer, WAL + ack). Three dedup layers: (1) queue-level unique
``(session_id, prompt_number, event_fingerprint)`` with ``INSERT OR IGNORE`` →
reprocess is a no-op; (2) store-level backstop; (3) ``deterministic_id`` NOT
extended to agent (UUIDv7 fresh).

Persisting ``prompt_number`` is load-bearing here: it is stored in the DB (not
memory) so a resumed session continues from the last prompt_number — a new
byte-identical turn is never falsely deduped.
"""

from __future__ import annotations

import json

import pytest

from seahorse.observe.protocol import Envelope, envelope_to_json, parse_envelope
from seahorse.observe.queue import ObserverQueue


def _env(
    session_id: str,
    prompt_number: int,
    event_type: str = "post_tool_use",
    payload: dict | None = None,
) -> Envelope:
    return Envelope(
        schema_version="1.0",
        harness_id="claude-code",
        session_id=session_id,
        agent_id="agent-1",
        prompt_number=prompt_number,
        event_type=event_type,
        ts="2026-08-10T09:00:00Z",
        payload=payload or {"tool_name": "Bash", "tool_input": "ls"},
    )


@pytest.fixture
def queue(tmp_path) -> ObserverQueue:
    q = ObserverQueue(tmp_path / "observer.db")
    yield q
    q.close()


# ---------------------------------------------------------------------------
# session prompt_number persistence
# ---------------------------------------------------------------------------


def test_register_session_starts_at_zero(queue: ObserverQueue) -> None:
    queue.register_session("sess-1")
    assert queue.current_prompt_number("sess-1") == 0


def test_advance_prompt_number_increments(queue: ObserverQueue) -> None:
    queue.register_session("sess-1")
    assert queue.advance_prompt_number("sess-1") == 1
    assert queue.advance_prompt_number("sess-1") == 2
    assert queue.current_prompt_number("sess-1") == 2


def test_prompt_number_persists_across_reopen(tmp_path) -> None:
    db = tmp_path / "observer.db"
    q1 = ObserverQueue(db)
    q1.register_session("sess-1")
    q1.advance_prompt_number("sess-1")
    q1.advance_prompt_number("sess-1")
    q1.close()

    # A resumed session continues from the persisted prompt_number, so a new
    # byte-identical turn gets a fresh prompt_number → never falsely deduped.
    q2 = ObserverQueue(db)
    assert q2.current_prompt_number("sess-1") == 2
    assert q2.advance_prompt_number("sess-1") == 3
    q2.close()


def test_advance_prompt_number_unknown_session_starts_at_one(queue: ObserverQueue) -> None:
    assert queue.advance_prompt_number("sess-new") == 1


# ---------------------------------------------------------------------------
# enqueue + dedup (layer 1: queue-level unique)
# ---------------------------------------------------------------------------


def test_enqueue_inserts(queue: ObserverQueue) -> None:
    assert queue.enqueue(_env("sess-1", 1)) is True
    assert queue.pending_count() == 1


def test_enqueue_dedup_same_event_is_noop(queue: ObserverQueue) -> None:
    assert queue.enqueue(_env("sess-1", 1)) is True
    assert queue.enqueue(_env("sess-1", 1)) is False  # INSERT OR IGNORE
    assert queue.pending_count() == 1


def test_enqueue_different_prompt_number_both_inserted(queue: ObserverQueue) -> None:
    assert queue.enqueue(_env("sess-1", 1)) is True
    assert queue.enqueue(_env("sess-1", 2)) is True
    assert queue.pending_count() == 2


def test_enqueue_different_session_both_inserted(queue: ObserverQueue) -> None:
    assert queue.enqueue(_env("sess-1", 1)) is True
    assert queue.enqueue(_env("sess-2", 1)) is True
    assert queue.pending_count() == 2


def test_enqueue_different_payload_same_turn_both_inserted(queue: ObserverQueue) -> None:
    a = _env("sess-1", 1, payload={"tool_name": "Bash", "tool_input": "ls"})
    b = _env("sess-1", 1, payload={"tool_name": "Edit", "tool_input": "fix"})
    assert queue.enqueue(a) is True
    assert queue.enqueue(b) is True
    assert queue.pending_count() == 2


# ---------------------------------------------------------------------------
# pending / ack
# ---------------------------------------------------------------------------


def test_pending_returns_in_arrival_order(queue: ObserverQueue) -> None:
    queue.enqueue(_env("sess-1", 1, payload={"tool_name": "Bash", "tool_input": "a"}))
    queue.enqueue(_env("sess-1", 1, payload={"tool_name": "Edit", "tool_input": "b"}))
    pending = queue.pending()
    assert [p.payload["tool_input"] for _, p in pending] == ["a", "b"]


def test_ack_removes_from_pending(queue: ObserverQueue) -> None:
    queue.enqueue(_env("sess-1", 1))
    event_id, _ = queue.pending()[0]
    queue.ack(event_id)
    assert queue.pending_count() == 0


def test_ack_is_idempotent(queue: ObserverQueue) -> None:
    queue.enqueue(_env("sess-1", 1))
    event_id, _ = queue.pending()[0]
    queue.ack(event_id)
    queue.ack(event_id)  # no-op, no crash
    assert queue.pending_count() == 0


def test_pending_roundtrips_envelope(queue: ObserverQueue) -> None:
    env = _env("sess-1", 3, payload={"tool_name": "Bash", "tool_input": "ls -la"})
    queue.enqueue(env)
    _, parsed = queue.pending()[0]
    assert parsed == env


def test_queue_stores_envelope_verbatim(queue: ObserverQueue) -> None:
    """The queue stores what it is given — redaction is the adapter's job
    (nothing raw is ever persisted because the adapter redacts BEFORE enqueue)."""
    env = _env("sess-1", 1)
    queue.enqueue(env)
    with queue._cm.reader() as conn:  # noqa: SLF001 — test inspects storage
        row = conn.execute("SELECT envelope_json FROM observer_events").fetchone()
    assert parse_envelope(json.loads(row["envelope_json"])) == env
    assert envelope_to_json(env) == row["envelope_json"]
