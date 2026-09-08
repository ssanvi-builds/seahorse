"""Tests for ``seahorse.observe.threshold`` — skip/drop tool policy.

Two distinct policies:
- ``skip_tools`` — DISCARD the event (it never reaches the turn body).
- ``drop_tools`` — DISCARD the event entirely (the whole turn is dropped).

Default ``skip_tools``: WebSearch/WebFetch (network results are noise, not
memory). Default ``drop_tools``: Read/Bash (their content is entirely secret —
the "stronger than claude-mem" claim only holds if Read/Bash content is
covered).
"""

from __future__ import annotations

from seahorse.observe.threshold import (
    DEFAULT_DROP_TOOLS,
    DEFAULT_SKIP_TOOLS,
    SYNTHETIC_PROMPT_PREFIXES,
    is_synthetic_prompt,
    should_drop_event,
    should_skip_event,
)


def test_default_skip_tools_are_web() -> None:
    assert frozenset({"WebSearch", "WebFetch"}) == DEFAULT_SKIP_TOOLS


def test_default_drop_tools_are_read_bash() -> None:
    assert frozenset({"Read", "Bash"}) == DEFAULT_DROP_TOOLS


def test_should_skip_web_search() -> None:
    assert should_skip_event("WebSearch") is True
    assert should_skip_event("WebFetch") is True


def test_should_not_skip_other_tools() -> None:
    assert should_skip_event("Bash") is False
    assert should_skip_event("Read") is False
    assert should_skip_event("Edit") is False


def test_should_drop_read_bash() -> None:
    assert should_drop_event("Read") is True
    assert should_drop_event("Bash") is True


def test_should_not_drop_other_tools() -> None:
    assert should_drop_event("Edit") is False
    assert should_drop_event("WebSearch") is False


def test_custom_skip_tools() -> None:
    assert should_skip_event("Edit", skip_tools=frozenset({"Edit"})) is True
    assert should_skip_event("Edit") is False  # default unchanged


def test_custom_drop_tools() -> None:
    assert should_drop_event("Write", drop_tools=frozenset({"Write"})) is True
    assert should_drop_event("Write") is False  # default unchanged


def test_skip_and_drop_are_independent() -> None:
    # A tool can be in both lists (skip wins for the event, drop for the turn).
    assert should_skip_event("Bash", skip_tools=frozenset({"Bash"})) is True
    assert should_drop_event("Bash") is True


def test_synthetic_prompt_prefixes_cover_harness_injections() -> None:
    # Harness-generated user turns (verified 2026-09-08: background-task
    # completions arrive as user_prompt_submit events whose whole prompt is the
    # <task-notification> wrapper). Prefixes are the known injection family.
    assert "<task-notification>" in SYNTHETIC_PROMPT_PREFIXES
    assert "<system-reminder>" in SYNTHETIC_PROMPT_PREFIXES
    assert "<local-command-stdout>" in SYNTHETIC_PROMPT_PREFIXES
    assert "<local-command-stderr>" in SYNTHETIC_PROMPT_PREFIXES


def test_is_synthetic_prompt_matches_first_line_prefix() -> None:
    assert is_synthetic_prompt("<task-notification>\n<task-id>bg2ui5ele</task-id>")
    assert is_synthetic_prompt("<system-reminder>plan mode</system-reminder>")
    assert is_synthetic_prompt("<local-command-stdout>ok</local-command-stdout>")
    # Leading whitespace on the first line is tolerated.
    assert is_synthetic_prompt("\n<task-notification>\n<task-id>x</task-id>")


def test_is_synthetic_prompt_rejects_real_prompts() -> None:
    assert is_synthetic_prompt("Fix the flaky recall test") is False
    assert is_synthetic_prompt("") is False
    assert is_synthetic_prompt("note about <task-notification> handling") is False
    # The prefix must open the prompt — mid-prompt tags are user content.
    assert is_synthetic_prompt("see <task-notification> below") is False
