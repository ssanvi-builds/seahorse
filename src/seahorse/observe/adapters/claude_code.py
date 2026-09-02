"""Claude Code adapter — the first harness adapter.

The adapter is the ONLY piece that touches a harness. It builds envelopes from
hook payloads, REDACTS before enqueue (nothing raw is ever persisted), and
applies ``drop_tools`` (Read/Bash) BEFORE enqueue — their content is entirely
secret and redaction cannot guarantee it is clean. The worker owns
``skip_tools`` (discard from the turn body) + the drop backstop.

The four hooks:
- ``SessionStart`` — registers the session (prompt_number=0).
- ``UserPromptSubmit`` — advances the persisted prompt_number (turn boundary)
  and enqueues the prompt event.
- ``PostToolUse`` — redacts + enqueues the tool event (drop_tools never reach
  the queue).
- ``Stop`` — enqueues the stop marker (drains the open turn).

The engine never sees a hook — only ``RememberPayload`` (delegation purity).
"""

from __future__ import annotations

from collections.abc import Collection

from seahorse.observe.protocol import (
    EVENT_POST_TOOL_USE,
    EVENT_STOP,
    EVENT_USER_PROMPT_SUBMIT,
    Envelope,
)
from seahorse.observe.queue import ObserverQueue
from seahorse.observe.redact import redact_payload
from seahorse.observe.threshold import DEFAULT_DROP_TOOLS, should_drop_event

_HARNESS_ID = "claude-code"


def _envelope(
    queue: ObserverQueue,
    *,
    session_id: str,
    event_type: str,
    payload: dict,
    agent_id: str | None = None,
) -> Envelope:
    return Envelope(
        schema_version="1.0",
        harness_id=_HARNESS_ID,
        session_id=session_id,
        agent_id=agent_id or "unknown",
        prompt_number=queue.current_prompt_number(session_id),
        event_type=event_type,
        ts="",
        payload=payload,
    )


def handle_session_start(
    queue: ObserverQueue, *, session_id: str, agent_id: str | None = None
) -> None:
    """SessionStart hook: register the session at prompt_number=0."""
    queue.register_session(session_id)


def handle_user_prompt_submit(
    queue: ObserverQueue,
    *,
    session_id: str,
    prompt: str,
    agent_id: str | None = None,
) -> None:
    """UserPromptSubmit hook: advance the persisted prompt_number (turn
    boundary) and enqueue the prompt event.

    The prompt is redacted like any other payload — users paste secrets into
    prompts, and "nothing raw is ever persisted" must hold for the prompt path
    too (loop L5a, 2026-09-02: raw API keys survived in the envelope and were
    re-printed by the SessionStart context injection).
    """
    prompt_number = queue.advance_prompt_number(session_id)
    env = Envelope(
        schema_version="1.0",
        harness_id=_HARNESS_ID,
        session_id=session_id,
        agent_id=agent_id or "unknown",
        prompt_number=prompt_number,
        event_type=EVENT_USER_PROMPT_SUBMIT,
        ts="",
        payload=redact_payload({"prompt": prompt}),
    )
    queue.enqueue(env)


def handle_post_tool_use(
    queue: ObserverQueue,
    *,
    session_id: str,
    tool_name: str,
    tool_use_id: str,
    tool_input: str,
    tool_response: str,
    agent_id: str | None = None,
    drop_tools: Collection[str] = DEFAULT_DROP_TOOLS,
) -> None:
    """PostToolUse hook: redact + enqueue the tool event.

    ``drop_tools`` (default Read/Bash, configurable via ``[observe].drop_tools``)
    are dropped BEFORE enqueue — their content is entirely secret and redaction
    cannot guarantee it is clean. Nothing raw is ever persisted.
    """
    if should_drop_event(tool_name, drop_tools=drop_tools):
        return
    payload = redact_payload(
        {
            "tool_name": tool_name,
            "tool_use_id": tool_use_id,
            "tool_input": tool_input,
            "tool_response": tool_response,
        }
    )
    env = _envelope(
        queue,
        session_id=session_id,
        event_type=EVENT_POST_TOOL_USE,
        payload=payload,
        agent_id=agent_id,
    )
    queue.enqueue(env)


def handle_stop(
    queue: ObserverQueue, *, session_id: str, agent_id: str | None = None
) -> None:
    """Stop hook: enqueue the stop marker (drains the open turn)."""
    env = _envelope(
        queue,
        session_id=session_id,
        event_type=EVENT_STOP,
        payload={},
        agent_id=agent_id,
    )
    queue.enqueue(env)


__all__ = [
    "handle_session_start",
    "handle_user_prompt_submit",
    "handle_post_tool_use",
    "handle_stop",
]
