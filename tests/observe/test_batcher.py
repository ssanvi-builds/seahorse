"""Tests for ``seahorse.observe.batcher`` — the deterministic turn render.

The batcher is a PURE function of the turn: the body does NOT include ``ts``,
``session_id``, ``prompt_number`` or ``cwd`` — a reprocess after crash must
produce the same hash. Order = arrival sequence. Truncation is deterministic by
byte. Redaction happens BEFORE the render (at enqueue), so the render itself is
pure.

The H1 carries collision uniqueness: ``title = "{first line of prompt truncated}
[{session_tag}:{prompt_number}]"`` — stable across reprocess, distinct between
turns of the same session, distinct between sessions → collisions only occur for
the SAME turn re-emitted, never for legitimate turns.
"""

from __future__ import annotations

from seahorse.observe.batcher import (
    BODY_MAX_CHARS,
    TITLE_MAX_CHARS,
    build_title,
    canonical_body_hash,
    render_turn_body,
)

PROMPT = "Fix the flaky recall test\nIt fails intermittently on CI."
EVENTS = [
    {
        "tool_name": "Bash",
        "tool_use_id": "tu-1",
        "tool_input": "pytest -q",
        "tool_response": "1 passed",
    },
    {"tool_name": "Edit", "tool_use_id": "tu-2", "tool_input": "fix", "tool_response": "ok"},
]


# ---------------------------------------------------------------------------
# build_title — H1 with collision uniqueness
# ---------------------------------------------------------------------------


def test_build_title_uses_first_line_of_prompt() -> None:
    title = build_title(PROMPT, session_tag="sess-1", prompt_number=3)
    assert title.startswith("Fix the flaky recall test")
    assert "[sess-1:3]" in title


def test_build_title_is_deterministic() -> None:
    a = build_title(PROMPT, session_tag="sess-1", prompt_number=3)
    b = build_title(PROMPT, session_tag="sess-1", prompt_number=3)
    assert a == b


def test_build_title_distinct_between_turns() -> None:
    a = build_title(PROMPT, session_tag="sess-1", prompt_number=1)
    b = build_title(PROMPT, session_tag="sess-1", prompt_number=2)
    assert a != b


def test_build_title_distinct_between_sessions() -> None:
    a = build_title(PROMPT, session_tag="sess-1", prompt_number=1)
    b = build_title(PROMPT, session_tag="sess-2", prompt_number=1)
    assert a != b


def test_build_title_truncates_long_first_line() -> None:
    long_prompt = "x" * 500
    title = build_title(long_prompt, session_tag="sess-1", prompt_number=1)
    assert len(title) <= TITLE_MAX_CHARS
    assert "[sess-1:1]" in title  # the tag always survives


def test_build_title_empty_first_line_falls_back() -> None:
    title = build_title("\n\nsecond line", session_tag="sess-1", prompt_number=1)
    assert "[sess-1:1]" in title
    assert title.strip()  # not blank


# ---------------------------------------------------------------------------
# render_turn_body — deterministic turn render
# ---------------------------------------------------------------------------


def test_render_turn_body_includes_prompt_and_events_in_order() -> None:
    body = render_turn_body(PROMPT, EVENTS, session_tag="sess-1", prompt_number=1)
    assert "Fix the flaky recall test" in body
    assert "pytest -q" in body
    assert "1 passed" in body
    # Arrival order preserved.
    assert body.index("tu-1") < body.index("tu-2")


def test_render_turn_body_h1_is_title() -> None:
    body = render_turn_body(PROMPT, EVENTS, session_tag="sess-1", prompt_number=1)
    first_line = body.split("\n", 1)[0]
    assert first_line == f"# {build_title(PROMPT, session_tag='sess-1', prompt_number=1)}"


def test_render_turn_body_excludes_metadata() -> None:
    body = render_turn_body(PROMPT, EVENTS, session_tag="sess-1", prompt_number=7)
    # ts / cwd must NOT appear in the body (a reprocess after crash must
    # produce the same hash). The H1 tag [sess-1:7] IS expected (collision
    # uniqueness) — but no separate metadata fields.
    assert "cwd" not in body
    assert "2026-" not in body
    assert "session_id:" not in body
    assert "prompt_number:" not in body
    # The H1 carries the tag (expected, not a leak).
    assert "[sess-1:7]" in body.split("\n", 1)[0]


def test_render_turn_body_is_deterministic() -> None:
    a = render_turn_body(PROMPT, EVENTS, session_tag="sess-1", prompt_number=1)
    b = render_turn_body(PROMPT, EVENTS, session_tag="sess-1", prompt_number=1)
    assert a == b


def test_render_turn_body_byte_truncation() -> None:
    big_events = [
        {
            "tool_name": "Bash",
            "tool_use_id": "tu-1",
            "tool_input": "y" * 20000,
            "tool_response": "z" * 20000,
        }
    ]
    body = render_turn_body(PROMPT, big_events, session_tag="sess-1", prompt_number=1)
    assert len(body.encode("utf-8")) <= BODY_MAX_CHARS
    # Truncation is deterministic.
    again = render_turn_body(PROMPT, big_events, session_tag="sess-1", prompt_number=1)
    assert body == again


def test_render_turn_body_empty_events() -> None:
    body = render_turn_body(PROMPT, [], session_tag="sess-1", prompt_number=1)
    assert "Fix the flaky recall test" in body


def test_render_turn_body_does_not_cut_utf8_codepoint() -> None:
    # A multi-byte char at the truncation boundary must not be split.
    events = [
        {"tool_name": "Bash", "tool_use_id": "tu-1", "tool_input": "é" * 100, "tool_response": ""}
    ]
    body = render_turn_body(PROMPT, events, session_tag="sess-1", prompt_number=1, max_chars=200)
    body.encode("utf-8")  # must not raise UnicodeEncodeError


# ---------------------------------------------------------------------------
# canonical_body_hash
# ---------------------------------------------------------------------------


def test_canonical_body_hash_is_stable() -> None:
    body = render_turn_body(PROMPT, EVENTS, session_tag="sess-1", prompt_number=1)
    assert canonical_body_hash(body) == canonical_body_hash(body)


def test_canonical_body_hash_differs_for_different_turns() -> None:
    a = render_turn_body(PROMPT, EVENTS, session_tag="sess-1", prompt_number=1)
    b = render_turn_body(PROMPT, EVENTS, session_tag="sess-1", prompt_number=2)
    assert canonical_body_hash(a) != canonical_body_hash(b)
