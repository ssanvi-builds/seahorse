"""Tests for ``seahorse context`` — the CLI bootstrap command.

The hook calls the CLI which calls the facade — ``MemoryFacade.context()`` is
the single point of change. The command renders the four INDEX-level blocks and
degrades to "no context" when the DB is empty (the day-1 context is empty until
episodes are indexed).
"""

from __future__ import annotations

import io

from seahorse.cli.primitives import run_context
from seahorse.facade.factory import build_facade
from seahorse.facade.types import RememberPayload


def _out() -> io.StringIO:
    return io.StringIO()


def test_context_empty_db_renders_no_context(tmp_path) -> None:
    facade, storage = build_facade(tmp_path / "seahorse.db")
    try:
        out = _out()
        run_context(facade, fmt="human", out=out)
        text = out.getvalue()
        assert "Seahorse memory context" in text
        assert "Recent episodes (0)" in text
        assert "none yet" in text  # honest empty bootstrap
    finally:
        storage.close()


def test_context_with_episodes_renders_blocks(tmp_path) -> None:
    facade, storage = build_facade(tmp_path / "seahorse.db")
    try:
        facade.remember(
            RememberPayload(
                body="# Fix the flaky recall test\n\nIt fails intermittently.",
                by={"source_type": "agent", "agent_id": "a1", "session_id": "sess-1"},
            )
        )
        out = _out()
        run_context(facade, fmt="human", out=out)
        text = out.getvalue()
        assert "Recent episodes (1)" in text
        assert "fix the flaky recall test" in text
        assert "Last session (sess-1)" in text
        assert "episodes total" in text
    finally:
        storage.close()


def test_context_is_deterministic(tmp_path) -> None:
    facade, storage = build_facade(tmp_path / "seahorse.db")
    try:
        facade.remember(
            RememberPayload(
                body="# A fact\n\nDetails.",
                by={"source_type": "agent", "agent_id": "a1", "session_id": "sess-1"},
            )
        )
        a = _out()
        b = _out()
        run_context(facade, fmt="human", out=a)
        run_context(facade, fmt="human", out=b)
        assert a.getvalue() == b.getvalue()
    finally:
        storage.close()
