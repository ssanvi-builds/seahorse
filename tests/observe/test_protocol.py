"""Tests for ``seahorse.observe.protocol`` — the event envelope contract.

The envelope is an internal contract of one module (``protocol.py``) with one
consumer (the batcher). Parsing is TOLERANT (``.get()`` + defaults) so a
harness field change is a one-line fix, never a crash. Caps: ``session_id`` /
``agent_id`` are opaque, ≤256 chars. Malformed input raises ``EnvelopeError``
at the edge (the adapter's job is to never enqueue garbage).
"""

from __future__ import annotations

import pytest

from seahorse.observe.protocol import (
    DEFAULT_SCHEMA_VERSION,
    MAX_ID_CHARS,
    Envelope,
    EnvelopeError,
    envelope_to_dict,
    envelope_to_json,
    parse_envelope,
)

# ---------------------------------------------------------------------------
# parse_envelope — tolerant parsing
# ---------------------------------------------------------------------------


def test_parse_envelope_minimal_valid() -> None:
    raw = {
        "session_id": "sess-1",
        "event_type": "user_prompt_submit",
        "payload": {"prompt": "hello"},
    }
    env = parse_envelope(raw)
    assert env.session_id == "sess-1"
    assert env.event_type == "user_prompt_submit"
    assert env.payload == {"prompt": "hello"}
    # Defaults for the optional fields.
    assert env.schema_version == DEFAULT_SCHEMA_VERSION
    assert env.harness_id == "unknown"
    assert env.agent_id == "unknown"
    assert env.prompt_number == 0
    assert env.ts == ""


def test_parse_envelope_full_roundtrip() -> None:
    raw = {
        "schema_version": "1.0",
        "harness_id": "claude-code",
        "session_id": "sess-9",
        "agent_id": "agent-7",
        "prompt_number": 3,
        "event_type": "post_tool_use",
        "ts": "2026-08-10T09:00:00Z",
        "payload": {"tool_name": "Bash", "tool_input": "ls"},
    }
    env = parse_envelope(raw)
    assert env.schema_version == "1.0"
    assert env.harness_id == "claude-code"
    assert env.agent_id == "agent-7"
    assert env.prompt_number == 3
    assert env.event_type == "post_tool_use"
    assert env.ts == "2026-08-10T09:00:00Z"
    assert env.payload["tool_name"] == "Bash"


def test_parse_envelope_missing_payload_defaults_to_empty() -> None:
    env = parse_envelope({"session_id": "s", "event_type": "stop"})
    assert env.payload == {}


def test_parse_envelope_missing_session_id_raises() -> None:
    with pytest.raises(EnvelopeError):
        parse_envelope({"event_type": "stop"})


def test_parse_envelope_missing_event_type_raises() -> None:
    with pytest.raises(EnvelopeError):
        parse_envelope({"session_id": "s"})


# --- schema_version strictness (design review post-v1.0, 3A) -------------------
#
# A string schema_version must be semver-shaped with a KNOWN major: an
# unknown MAJOR means the harness contract changed incompatibly — that drift
# must surface loudly at the edge (400), not silently persist into episodes.
# Any 1.x is accepted (additive evolution); non-string keeps the tolerant
# default (a type drift is a one-line harness fix, never a crash).


@pytest.mark.parametrize("version", ["1.0", "1.2", "1.10.3", "1.0.0-rc.1"])
def test_parse_envelope_accepts_known_major_versions(version) -> None:
    env = parse_envelope(
        {"session_id": "s", "event_type": "stop", "schema_version": version}
    )
    assert env.schema_version == version


@pytest.mark.parametrize("version", ["2.0", "2.0.0", "9.9"])
def test_parse_envelope_rejects_unknown_major(version) -> None:
    with pytest.raises(EnvelopeError):
        parse_envelope(
            {"session_id": "s", "event_type": "stop", "schema_version": version}
        )


@pytest.mark.parametrize("version", ["garbage", "", "1", "v1.0", "1.0 "])
def test_parse_envelope_rejects_malformed_version_string(version) -> None:
    with pytest.raises(EnvelopeError):
        parse_envelope(
            {"session_id": "s", "event_type": "stop", "schema_version": version}
        )


def test_parse_envelope_non_string_version_keeps_tolerant_default() -> None:
    env = parse_envelope(
        {"session_id": "s", "event_type": "stop", "schema_version": 2}
    )
    assert env.schema_version == DEFAULT_SCHEMA_VERSION


def test_parse_envelope_non_dict_raises() -> None:
    with pytest.raises(EnvelopeError):
        parse_envelope("not-a-dict")  # type: ignore[arg-type]


def test_parse_envelope_session_id_over_cap_raises() -> None:
    with pytest.raises(EnvelopeError):
        parse_envelope({"session_id": "x" * (MAX_ID_CHARS + 1), "event_type": "stop"})


def test_parse_envelope_agent_id_over_cap_raises() -> None:
    with pytest.raises(EnvelopeError):
        parse_envelope(
            {
                "session_id": "s",
                "agent_id": "y" * (MAX_ID_CHARS + 1),
                "event_type": "stop",
            }
        )


def test_parse_envelope_prompt_number_non_int_defaults_zero() -> None:
    # Tolerant: a non-int prompt_number degrades to 0 rather than crashing.
    env = parse_envelope({"session_id": "s", "event_type": "stop", "prompt_number": "3"})
    assert env.prompt_number == 0


# ---------------------------------------------------------------------------
# envelope_to_dict / envelope_to_json — canonical serialization
# ---------------------------------------------------------------------------


def test_envelope_to_dict_roundtrip() -> None:
    env = Envelope(
        schema_version="1.0",
        harness_id="claude-code",
        session_id="sess-1",
        agent_id="agent-1",
        prompt_number=2,
        event_type="post_tool_use",
        ts="2026-08-10T09:00:00Z",
        payload={"tool_name": "Bash"},
    )
    d = envelope_to_dict(env)
    assert d["session_id"] == "sess-1"
    assert d["prompt_number"] == 2
    assert d["payload"] == {"tool_name": "Bash"}


def test_envelope_to_json_is_deterministic() -> None:
    env = Envelope(
        schema_version="1.0",
        harness_id="claude-code",
        session_id="sess-1",
        agent_id="agent-1",
        prompt_number=2,
        event_type="post_tool_use",
        ts="2026-08-10T09:00:00Z",
        payload={"tool_name": "Bash", "tool_input": "ls"},
    )
    a = envelope_to_json(env)
    b = envelope_to_json(env)
    assert a == b
    # Canonical: sort_keys + compact separators → stable across runs.
    assert '"tool_input": "ls"' not in a  # compact separators, no space after colon
    assert '"tool_input":"ls"' in a


def test_envelope_to_json_parse_roundtrip() -> None:
    env = Envelope(
        schema_version="1.0",
        harness_id="claude-code",
        session_id="sess-1",
        agent_id="agent-1",
        prompt_number=2,
        event_type="post_tool_use",
        ts="2026-08-10T09:00:00Z",
        payload={"tool_name": "Bash"},
    )
    import json

    reparsed = parse_envelope(json.loads(envelope_to_json(env)))
    assert reparsed == env
