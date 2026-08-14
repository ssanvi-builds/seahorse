"""Observer event envelope — the internal capture contract.

The envelope is an internal contract of one module with one consumer (the
batcher). Parsing is TOLERANT (``.get()`` + defaults) so a harness field
change is a one-line fix, never a crash. There is deliberately NO
``HarnessAdapter`` interface and no ``cursor.py`` (YAGNI): ``harness_id`` is a
free string from day 1; multi-harness is a later phase.

Envelope shape: ``{schema_version, harness_id, session_id, agent_id,
prompt_number, event_type, ts, payload}``. ``session_id`` / ``agent_id`` are
opaque, capped at ``MAX_ID_CHARS`` (256). The engine never sees an envelope —
only ``RememberPayload`` (delegation purity).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

DEFAULT_SCHEMA_VERSION = "1.0"
DEFAULT_HARNESS_ID = "unknown"
DEFAULT_AGENT_ID = "unknown"
MAX_ID_CHARS = 256

# The four Claude Code hooks. ``event_type`` is a free string
# at the protocol level (tolerant parsing); the adapter uses these constants.
EVENT_SESSION_START = "session_start"
EVENT_USER_PROMPT_SUBMIT = "user_prompt_submit"
EVENT_POST_TOOL_USE = "post_tool_use"
EVENT_STOP = "stop"


class EnvelopeError(ValueError):
    """Raised for a malformed envelope at the edge (never swallowed).

    The adapter's job is to never enqueue garbage; a malformed envelope is a
    harness-contract drift and must surface loudly, not be silently dropped or
    defaulted into a wrong episode.
    """


@dataclass(frozen=True)
class Envelope:
    """A single capture event from a harness hook (immutable).

    ``ts`` is the ISO-8601 capture timestamp (informational only — the batcher
    render EXCLUDES it so a reprocess after crash produces the same hash).
    ``payload`` is the already-redacted event content; nothing raw is ever
    persisted.
    """

    session_id: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    schema_version: str = DEFAULT_SCHEMA_VERSION
    harness_id: str = DEFAULT_HARNESS_ID
    agent_id: str = DEFAULT_AGENT_ID
    prompt_number: int = 0
    ts: str = ""


def parse_envelope(raw: object) -> Envelope:
    """Tolerant parse of a raw dict into an ``Envelope``.

    Optional fields default (``schema_version`` / ``harness_id`` / ``agent_id``
    / ``prompt_number`` / ``ts``); ``session_id`` and ``event_type`` are
    required. ``session_id`` / ``agent_id`` are capped at ``MAX_ID_CHARS`` —
    over-cap raises ``EnvelopeError`` (edge validation).
    """
    if not isinstance(raw, dict):
        raise EnvelopeError(f"envelope must be a dict, got {type(raw).__name__}")

    session_id = raw.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise EnvelopeError("envelope requires a non-empty session_id")
    event_type = raw.get("event_type")
    if not isinstance(event_type, str) or not event_type:
        raise EnvelopeError("envelope requires a non-empty event_type")

    agent_id = raw.get("agent_id", DEFAULT_AGENT_ID)
    if not isinstance(agent_id, str):
        agent_id = DEFAULT_AGENT_ID
    if len(session_id) > MAX_ID_CHARS or len(agent_id) > MAX_ID_CHARS:
        raise EnvelopeError(
            f"session_id/agent_id must be opaque and <= {MAX_ID_CHARS} chars"
        )

    schema_version = raw.get("schema_version", DEFAULT_SCHEMA_VERSION)
    if not isinstance(schema_version, str):
        schema_version = DEFAULT_SCHEMA_VERSION
    harness_id = raw.get("harness_id", DEFAULT_HARNESS_ID)
    if not isinstance(harness_id, str):
        harness_id = DEFAULT_HARNESS_ID
    prompt_number = raw.get("prompt_number", 0)
    if not isinstance(prompt_number, int) or isinstance(prompt_number, bool):
        prompt_number = 0
    ts = raw.get("ts", "")
    if not isinstance(ts, str):
        ts = ""
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    return Envelope(
        schema_version=schema_version,
        harness_id=harness_id,
        session_id=session_id,
        agent_id=agent_id,
        prompt_number=prompt_number,
        event_type=event_type,
        ts=ts,
        payload=payload,
    )


def envelope_to_dict(env: Envelope) -> dict[str, Any]:
    """Canonical dict form of an envelope (stable key order)."""
    return {
        "schema_version": env.schema_version,
        "harness_id": env.harness_id,
        "session_id": env.session_id,
        "agent_id": env.agent_id,
        "prompt_number": env.prompt_number,
        "event_type": env.event_type,
        "ts": env.ts,
        "payload": env.payload,
    }


def envelope_to_json(env: Envelope) -> str:
    """Canonical JSON serialization (sort_keys + compact separators).

    Deterministic across runs: the same envelope always serializes to the same
    bytes, which the queue uses for the event fingerprint.
    """
    return json.dumps(
        envelope_to_dict(env), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


__all__ = [
    "DEFAULT_SCHEMA_VERSION",
    "DEFAULT_HARNESS_ID",
    "DEFAULT_AGENT_ID",
    "MAX_ID_CHARS",
    "EVENT_SESSION_START",
    "EVENT_USER_PROMPT_SUBMIT",
    "EVENT_POST_TOOL_USE",
    "EVENT_STOP",
    "Envelope",
    "EnvelopeError",
    "parse_envelope",
    "envelope_to_dict",
    "envelope_to_json",
]
