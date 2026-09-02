"""Tests for ``seahorse.observe.adapters.claude_code`` — the Claude Code adapter.

The adapter is the ONLY piece that touches a harness ("Claude Code is first an
adapter, not a binding"). It builds envelopes from hook payloads, REDACTS before
enqueue (nothing raw is ever persisted), and applies ``drop_tools`` (Read/Bash)
BEFORE enqueue — their content is entirely secret and redaction cannot guarantee
it is clean. The worker owns ``skip_tools`` (discard from the turn body) + the
drop backstop.

The engine never sees a hook — only ``RememberPayload`` (delegation purity).
"""

from __future__ import annotations

import pytest

from seahorse.facade.factory import build_facade
from seahorse.observe.adapters.claude_code import (
    handle_post_tool_use,
    handle_session_start,
    handle_stop,
    handle_user_prompt_submit,
)
from seahorse.observe.protocol import (
    EVENT_POST_TOOL_USE,
    EVENT_STOP,
    EVENT_USER_PROMPT_SUBMIT,
)
from seahorse.observe.queue import ObserverQueue
from seahorse.observe.worker import ObserverWorker

SESSION = "sess-12345678"


@pytest.fixture
def queue(tmp_path) -> ObserverQueue:
    q = ObserverQueue(tmp_path / "observer.db")
    yield q
    q.close()


# ---------------------------------------------------------------------------
# session lifecycle
# ---------------------------------------------------------------------------


def test_handle_session_start_registers_session(queue: ObserverQueue) -> None:
    handle_session_start(queue, session_id=SESSION)
    assert queue.current_prompt_number(SESSION) == 0


def test_handle_user_prompt_submit_advances_and_enqueues(queue: ObserverQueue) -> None:
    handle_session_start(queue, session_id=SESSION)
    handle_user_prompt_submit(queue, session_id=SESSION, prompt="Fix the bug")
    assert queue.current_prompt_number(SESSION) == 1
    pending = queue.pending()
    assert len(pending) == 1
    _, env = pending[0]
    assert env.event_type == EVENT_USER_PROMPT_SUBMIT
    assert env.prompt_number == 1
    assert env.payload["prompt"] == "Fix the bug"


def test_handle_user_prompt_submit_increments_per_prompt(queue: ObserverQueue) -> None:
    handle_session_start(queue, session_id=SESSION)
    handle_user_prompt_submit(queue, session_id=SESSION, prompt="First")
    handle_user_prompt_submit(queue, session_id=SESSION, prompt="Second")
    assert queue.current_prompt_number(SESSION) == 2


def test_handle_user_prompt_submit_redacts_before_enqueue(queue: ObserverQueue) -> None:
    """The prompt path redacts like every other payload: users paste secrets
    into prompts, and the SessionStart context injection re-prints episodes —
    a raw secret here would flow straight back into Claude Code context
    (loop L5a, 2026-09-02)."""
    handle_session_start(queue, session_id=SESSION)
    handle_user_prompt_submit(
        queue,
        session_id=SESSION,
        prompt="exports ANTHROPIC_API_KEY: sk-ant-secret123456",
    )
    pending = queue.pending()
    assert len(pending) == 1
    _, env = pending[0]
    assert env.event_type == EVENT_USER_PROMPT_SUBMIT
    assert "sk-ant-secret123456" not in str(env.payload)
    assert "[REDACTED]" in env.payload["prompt"]


# ---------------------------------------------------------------------------
# post_tool_use — redact + drop_tools
# ---------------------------------------------------------------------------


def test_handle_post_tool_use_redacts_before_enqueue(queue: ObserverQueue) -> None:
    handle_session_start(queue, session_id=SESSION)
    handle_user_prompt_submit(queue, session_id=SESSION, prompt="A prompt")
    handle_post_tool_use(
        queue,
        session_id=SESSION,
        tool_name="Edit",
        tool_use_id="tu-1",
        tool_input="curl -H 'Authorization: Bearer sk-abc123' https://api.example.com",
        tool_response="ok",
    )
    pending = queue.pending()
    tool_envs = [env for _, env in pending if env.event_type == EVENT_POST_TOOL_USE]
    assert len(tool_envs) == 1
    assert "sk-abc123" not in str(tool_envs[0].payload)
    assert "Bearer" in str(tool_envs[0].payload)  # label survives


def test_handle_post_tool_use_drops_read_bash(queue: ObserverQueue) -> None:
    handle_session_start(queue, session_id=SESSION)
    handle_user_prompt_submit(queue, session_id=SESSION, prompt="A prompt")
    handle_post_tool_use(
        queue,
        session_id=SESSION,
        tool_name="Read",
        tool_use_id="tu-1",
        tool_input="secret.txt",
        tool_response="SECRET CONTENT",
    )
    # Read is in drop_tools → never enqueued (nothing raw persisted).
    pending = queue.pending()
    tool_envs = [env for _, env in pending if env.event_type == EVENT_POST_TOOL_USE]
    assert tool_envs == []


def test_handle_post_tool_use_drops_configured_tools(queue: ObserverQueue) -> None:
    # D2: a tool added to [observe].drop_tools must be dropped BEFORE enqueue —
    # the configured set, not just the DEFAULT_DROP_TOOLS (Read/Bash).
    handle_session_start(queue, session_id=SESSION)
    handle_user_prompt_submit(queue, session_id=SESSION, prompt="A prompt")
    handle_post_tool_use(
        queue,
        session_id=SESSION,
        tool_name="Edit",
        tool_use_id="tu-1",
        tool_input="secret.txt",
        tool_response="SECRET CONTENT",
        drop_tools=frozenset({"Edit"}),
    )
    # Edit is in the configured drop_tools → never enqueued (nothing raw persisted).
    pending = queue.pending()
    tool_envs = [env for _, env in pending if env.event_type == EVENT_POST_TOOL_USE]
    assert tool_envs == []


def test_handle_post_tool_use_uses_current_prompt_number(queue: ObserverQueue) -> None:
    handle_session_start(queue, session_id=SESSION)
    handle_user_prompt_submit(queue, session_id=SESSION, prompt="A prompt")
    handle_post_tool_use(
        queue,
        session_id=SESSION,
        tool_name="Edit",
        tool_use_id="tu-1",
        tool_input="fix",
        tool_response="ok",
    )
    pending = queue.pending()
    tool_envs = [env for _, env in pending if env.event_type == EVENT_POST_TOOL_USE]
    assert tool_envs[0].prompt_number == 1


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def test_handle_stop_enqueues_stop_event(queue: ObserverQueue) -> None:
    handle_session_start(queue, session_id=SESSION)
    handle_user_prompt_submit(queue, session_id=SESSION, prompt="A prompt")
    handle_stop(queue, session_id=SESSION)
    pending = queue.pending()
    stop_envs = [env for _, env in pending if env.event_type == EVENT_STOP]
    assert len(stop_envs) == 1
    assert stop_envs[0].prompt_number == 1


# ---------------------------------------------------------------------------
# end-to-end: hooks → queue → worker → episode
# ---------------------------------------------------------------------------


def test_full_flow_hooks_to_episode(tmp_path) -> None:
    facade, storage = build_facade(tmp_path / "seahorse.db")
    queue = ObserverQueue(tmp_path / "observer.db")
    try:
        handle_session_start(queue, session_id=SESSION)
        handle_user_prompt_submit(queue, session_id=SESSION, prompt="Fix the flaky recall test")
        handle_post_tool_use(
            queue,
            session_id=SESSION,
            tool_name="Bash",
            tool_use_id="tu-1",
            tool_input="pytest -q",
            tool_response="1 passed",
        )
        handle_stop(queue, session_id=SESSION)

        report = ObserverWorker(facade, queue).drain()
        assert report.episodes_written == 1
        rows = facade.recall("flaky recall", k=10)
        assert len(rows) == 1
        assert "flaky recall" in (rows[0].subject or "")
    finally:
        queue.close()
        storage.close()


def test_full_flow_redacts_secret_in_tool(queue, tmp_path) -> None:
    facade, storage = build_facade(tmp_path / "seahorse.db")
    try:
        handle_session_start(queue, session_id=SESSION)
        handle_user_prompt_submit(queue, session_id=SESSION, prompt="Deploy the service")
        handle_post_tool_use(
            queue,
            session_id=SESSION,
            tool_name="Bash",
            tool_use_id="tu-1",
            tool_input="export API_KEY=sk-abc123 && deploy",
            tool_response="done",
        )
        handle_stop(queue, session_id=SESSION)
        ObserverWorker(facade, queue).drain()
        rows = facade.recall("deploy", k=10)
        full = facade.recall_full([rows[0].ep_id])
        body = full[0].episode.body or ""
        assert "sk-abc123" not in body  # the secret never reached the episode
    finally:
        storage.close()
