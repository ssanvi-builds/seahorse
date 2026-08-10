"""Tests for ``seahorse.observe.worker`` — the observer worker.

The worker drains the queue, batches by SESSION (F7 decision (d) — por_sesion:
the turn is NOT a recoverable unit, f7-experiment-batch), renders each turn via
the deterministic batcher, and writes episodes via ``facade.remember``
(skip-first, ADR-09). Thresholding (§4.3): a turn without a user prompt → no
episode; body < 40 chars → no episode; skip_tools discard the event;
drop_tools discard the event entirely. The OQ3 summary skips the H1 (§15.2
redesign 4).
"""

from __future__ import annotations

import pytest

from seahorse.facade.factory import build_facade
from seahorse.observe.protocol import (
    EVENT_POST_TOOL_USE,
    EVENT_STOP,
    EVENT_USER_PROMPT_SUBMIT,
    Envelope,
)
from seahorse.observe.queue import ObserverQueue
from seahorse.observe.worker import ObserverConfig, ObserverWorker

SESSION = "sess-12345678"


def _env(
    event_type: str,
    *,
    session_id: str = SESSION,
    prompt_number: int = 1,
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
        payload=payload or {},
    )


def _enqueue_turn(
    queue: ObserverQueue,
    *,
    prompt: str = "Fix the flaky recall test",
    tools: list[dict] | None = None,
    session_id: str = SESSION,
    prompt_number: int = 1,
) -> None:
    queue.enqueue(
        _env(
            EVENT_USER_PROMPT_SUBMIT,
            session_id=session_id,
            prompt_number=prompt_number,
            payload={"prompt": prompt},
        )
    )
    for tool in tools or []:
        queue.enqueue(
            _env(
                EVENT_POST_TOOL_USE,
                session_id=session_id,
                prompt_number=prompt_number,
                payload=tool,
            )
        )
    queue.enqueue(_env(EVENT_STOP, session_id=session_id, prompt_number=prompt_number))


@pytest.fixture
def facade_and_queue(tmp_path):
    facade, storage = build_facade(tmp_path / "seahorse.db")
    queue = ObserverQueue(tmp_path / "observer.db")
    yield facade, queue
    queue.close()
    storage.close()


def _worker(facade, queue, **cfg) -> ObserverWorker:
    return ObserverWorker(facade, queue, config=ObserverConfig(**cfg))


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_drain_writes_one_episode_per_turn(facade_and_queue) -> None:
    facade, queue = facade_and_queue
    _enqueue_turn(
        queue,
        prompt="Fix the flaky recall test",
        tools=[
            {
            "tool_name": "Bash",
            "tool_use_id": "tu-1",
            "tool_input": "pytest -q",
            "tool_response": "1 passed",
        }
        ],
    )
    report = _worker(facade, queue).drain()
    assert report.episodes_written == 1
    assert report.events_read == 3
    assert queue.pending_count() == 0  # all acked


def test_drain_episode_body_is_deterministic(facade_and_queue) -> None:
    facade, queue = facade_and_queue
    _enqueue_turn(
        queue,
        prompt="Fix the flaky recall test",
        tools=[
            {
            "tool_name": "Bash",
            "tool_use_id": "tu-1",
            "tool_input": "pytest -q",
            "tool_response": "1 passed",
        }
        ],
    )
    _worker(facade, queue).drain()
    rows = facade.recall("flaky recall", k=10)
    assert len(rows) == 1
    # The episode is recoverable by its subject (the prompt's first line).
    assert rows[0].subject is not None
    assert "flaky recall" in rows[0].subject


def test_drain_summary_skips_h1(facade_and_queue) -> None:
    facade, queue = facade_and_queue
    _enqueue_turn(
        queue,
        prompt="Fix the flaky recall test\nIt fails intermittently on CI.",
        tools=[
            {
            "tool_name": "Bash",
            "tool_use_id": "tu-1",
            "tool_input": "pytest -q",
            "tool_response": "1 passed",
        }
        ],
    )
    _worker(facade, queue).drain()
    rows = facade.recall("flaky recall", k=10)
    summary = rows[0].summary or ""
    # §15.2 redesign 4: the summary must NOT be the tagged H1.
    assert "[sess" not in summary
    assert "flaky recall" in summary  # first sentence of the content


def test_drain_multiple_sessions(facade_and_queue) -> None:
    facade, queue = facade_and_queue
    _enqueue_turn(queue, prompt="Session A prompt", session_id="sess-aaaa")
    _enqueue_turn(queue, prompt="Session B prompt", session_id="sess-bbbb")
    report = _worker(facade, queue).drain()
    assert report.episodes_written == 2
    assert queue.pending_count() == 0


def test_drain_multiple_turns_same_session(facade_and_queue) -> None:
    facade, queue = facade_and_queue
    _enqueue_turn(queue, prompt="First turn", prompt_number=1)
    _enqueue_turn(queue, prompt="Second turn", prompt_number=2)
    report = _worker(facade, queue).drain()
    assert report.episodes_written == 2
    assert queue.pending_count() == 0


def test_drain_empty_queue(facade_and_queue) -> None:
    facade, queue = facade_and_queue
    report = _worker(facade, queue).drain()
    assert report.events_read == 0
    assert report.episodes_written == 0


# ---------------------------------------------------------------------------
# thresholding (§4.3)
# ---------------------------------------------------------------------------


def test_drain_turn_without_user_prompt_is_skipped(facade_and_queue) -> None:
    facade, queue = facade_and_queue
    queue.enqueue(
        _env(EVENT_POST_TOOL_USE, payload={"tool_name": "Bash", "tool_input": "ls"})
    )
    queue.enqueue(_env(EVENT_STOP))
    report = _worker(facade, queue).drain()
    assert report.turns_skipped == 1
    assert report.episodes_written == 0
    assert queue.pending_count() == 0  # consumed, not retried


def test_drain_body_below_min_chars_is_skipped(facade_and_queue) -> None:
    facade, queue = facade_and_queue
    _enqueue_turn(queue, prompt="hi")  # body < 40 chars
    report = _worker(facade, queue).drain()
    assert report.turns_skipped == 1
    assert report.episodes_written == 0


def _full_body(facade, query: str) -> str:
    """Hydrate the FULL body of the first recall hit (INDEX has no body)."""
    rows = facade.recall(query, k=10)
    assert rows, f"no recall hit for {query!r}"
    full = facade.recall_full([rows[0].ep_id])
    return full[0].episode.body or ""


def test_drain_skip_tools_excluded_from_body(facade_and_queue) -> None:
    facade, queue = facade_and_queue
    _enqueue_turn(
        queue,
        prompt="Research the topic",
        tools=[
            {
            "tool_name": "WebSearch",
            "tool_use_id": "tu-1",
            "tool_input": "query",
            "tool_response": "results",
        },
            {
            "tool_name": "Edit",
            "tool_use_id": "tu-2",
            "tool_input": "fix",
            "tool_response": "ok",
        },
        ],
    )
    report = _worker(facade, queue).drain()
    assert report.events_skipped == 1
    assert report.episodes_written == 1
    body = _full_body(facade, "Research the topic")
    assert "WebSearch" not in body
    assert "Edit" in body


def test_drain_drop_tools_excluded_from_body(facade_and_queue) -> None:
    facade, queue = facade_and_queue
    _enqueue_turn(
        queue,
        prompt="Read the config",
        tools=[
            {
            "tool_name": "Read",
            "tool_use_id": "tu-1",
            "tool_input": "secret.txt",
            "tool_response": "SECRET CONTENT",
        },
            {
            "tool_name": "Edit",
            "tool_use_id": "tu-2",
            "tool_input": "fix",
            "tool_response": "ok",
        },
        ],
    )
    report = _worker(facade, queue).drain()
    assert report.events_dropped == 1
    assert report.episodes_written == 1
    body = _full_body(facade, "Read the config")
    assert "SECRET CONTENT" not in body  # Read content never persisted
    assert "Edit" in body


# ---------------------------------------------------------------------------
# ack semantics
# ---------------------------------------------------------------------------


def test_drain_acks_processed_events(facade_and_queue) -> None:
    facade, queue = facade_and_queue
    _enqueue_turn(queue, prompt="A turn")
    _worker(facade, queue).drain()
    assert queue.pending_count() == 0


def test_drain_does_not_ack_on_failure(facade_and_queue) -> None:
    facade, queue = facade_and_queue
    _enqueue_turn(queue, prompt="A turn")  # 2 events: prompt + stop

    class _Boom:
        def remember(self, *a, **k):
            raise RuntimeError("boom")

    report = _worker(_Boom(), queue).drain()  # type: ignore[arg-type]
    assert report.failures == 1
    assert queue.pending_count() == 2  # not acked → retried next drain


def test_drain_collision_acks_and_counts(facade_and_queue) -> None:
    facade, queue = facade_and_queue
    _enqueue_turn(queue, prompt="A turn")

    from seahorse.contracts.engine import WriteResult

    class _Collide:
        def remember(self, *a, **k):
            return WriteResult(
                ep_id="ep-1", fact_id="fact-1", status="COLLISION", collisions_detected=[]
            )

    report = _worker(_Collide(), queue).drain()  # type: ignore[arg-type]
    assert report.collisions == 1
    assert report.episodes_written == 0
    assert queue.pending_count() == 0  # consumed, not retried


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_worker_custom_skip_tools(facade_and_queue) -> None:
    facade, queue = facade_and_queue
    _enqueue_turn(
        queue,
        prompt="A turn",
        tools=[{
            "tool_name": "Edit",
            "tool_use_id": "tu-1",
            "tool_input": "x",
            "tool_response": "y",
        }],
    )
    report = _worker(facade, queue, skip_tools=frozenset({"Edit"})).drain()
    assert report.events_skipped == 1
    assert report.episodes_written == 1
