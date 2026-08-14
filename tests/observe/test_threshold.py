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
