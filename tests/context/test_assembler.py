"""Tests for ``seahorse.context.assembler`` — the bootstrap renderer.

The assembler is a PURE function of ``ContextData``: it renders the four
bootstrap blocks (recent episodes / current-state / last session / header +
counter + pointer) at INDEX level, no body. Deterministic — the same
data always renders the same text. The last-session block is an INDEX list,
NOT an abstractive summary (honesty).
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.context.assembler import render_context
from seahorse.facade.types import ContextData, ContextEpisode

T0 = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)


def _ep(subject: str, *, session_id: str = "sess-1", summary: str | None = None) -> ContextEpisode:
    return ContextEpisode(
        ep_id=f"ep-{subject}",
        subject=subject,
        summary=summary,
        created_at=T0,
        session_id=session_id,
    )


def _data(**kw) -> ContextData:
    defaults = {
        "recent": [],
        "vigente_count": 0,
        "last_session_id": None,
        "last_session": [],
        "total_episodes": 0,
    }
    defaults.update(kw)
    return ContextData(**defaults)


def test_render_empty_data() -> None:
    text = render_context(_data())
    assert "Seahorse memory context" in text
    assert "Recent episodes (0)" in text
    assert "Current state (0 facts)" in text
    assert "Last session" in text
    assert "episodes total" in text


def test_render_four_blocks() -> None:
    data = _data(
        recent=[_ep("alpha", summary="first fact")],
        vigente_count=1,
        last_session_id="sess-1",
        last_session=[_ep("alpha", summary="first fact")],
        total_episodes=1,
    )
    text = render_context(data)
    assert "## Recent episodes (1)" in text
    assert "## Current state (1 facts)" in text
    assert "## Last session (sess-1)" in text
    assert "## Stats" in text


def test_render_includes_subject_and_summary() -> None:
    data = _data(
        recent=[_ep("alpha", summary="first fact")],
        vigente_count=1,
        total_episodes=1,
    )
    text = render_context(data)
    assert "alpha" in text
    assert "first fact" in text


def test_render_last_session_is_index_list_not_summary() -> None:
    """Honesty: the last-session block lists INDEX rows, not a session
    summary — Seahorse has no session summaries yet."""
    data = _data(
        last_session_id="sess-1",
        last_session=[_ep("alpha", summary="first fact"), _ep("beta", summary="second fact")],
        total_episodes=2,
    )
    text = render_context(data)
    assert "alpha" in text
    assert "beta" in text
    assert "session summary" not in text.lower()


def test_render_includes_pointer_hint() -> None:
    data = _data(total_episodes=1)
    text = render_context(data)
    assert "recall" in text
    assert "recall-full" in text


def test_render_is_deterministic() -> None:
    data = _data(
        recent=[_ep("alpha"), _ep("beta")],
        vigente_count=2,
        last_session_id="sess-1",
        last_session=[_ep("alpha")],
        total_episodes=2,
    )
    assert render_context(data) == render_context(data)


def test_render_no_trailing_dash_for_missing_summary() -> None:
    data = _data(recent=[_ep("alpha")], vigente_count=1, total_episodes=1)
    text = render_context(data)
    assert "alpha —" not in text  # no dangling separator
