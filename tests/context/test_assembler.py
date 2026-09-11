"""Tests for ``seahorse.context.assembler`` — the bootstrap renderer.

The assembler is a PURE function of ``ContextData``: it renders the four
bootstrap blocks (recent episodes / current-state / last session / header +
counter + pointer) at INDEX level, no body. Deterministic — the same
data always renders the same text. The last-session block is an INDEX list,
NOT an abstractive summary (honesty).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.context.assembler import render_context
from seahorse.facade.types import ContextData, ContextEpisode

T0 = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)


def _ep(
    subject: str, *, session_id: str = "sess-1", summary: str | None = None
) -> ContextEpisode:
    return ContextEpisode(
        ep_id=f"ep-{subject}",
        subject=subject,
        summary=summary,
        created_at=T0,
        session_id=session_id,
    )


def _kep(
    subject: str,
    *,
    summary: str | None = None,
    cognitive_type: str = "semantic",
) -> ContextEpisode:
    """A knowledge-note row (consolidated / project_doc)."""
    return ContextEpisode(
        ep_id=f"ep-{subject}",
        subject=subject,
        summary=summary,
        created_at=T0,
        session_id="sess-1",
        cognitive_type=cognitive_type,
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


def test_pointer_leads_with_mcp_tools() -> None:
    """The agentic use case is primary: the pointer names the seahorse-mcp
    tools first, CLI as fallback."""
    data = _data(total_episodes=1)
    text = render_context(data)
    pointer_line = next(line for line in text.splitlines() if "seahorse-mcp" in line)
    assert "MCP" in pointer_line or "mcp" in pointer_line
    assert pointer_line.index("seahorse-mcp") < pointer_line.index("seahorse recall")


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


# ---------------------------------------------------------------------------
# Row rendering contract (``_entry`` observed through public output)
# ---------------------------------------------------------------------------


def _ep_raw(
    subject: str | None,
    summary: str | None,
    *,
    ep_id: str = "ep-1",
    session_id: str | None = None,
) -> ContextEpisode:
    return ContextEpisode(
        ep_id=ep_id,
        subject=subject,
        summary=summary,
        created_at=T0,
        session_id=session_id,
    )


def test_none_subject_renders_placeholder() -> None:
    data = _data(recent=[_ep_raw(None, "s")], vigente_count=1, total_episodes=1)
    text = render_context(data)
    assert "- (no subject) — s" in text
    assert "(no subject)" in text


def test_empty_string_subject_renders_placeholder() -> None:
    """Empty string is falsy like None: `or` semantics pin the placeholder."""
    data = _data(recent=[_ep_raw("", None)], vigente_count=1, total_episodes=1)
    text = render_context(data)
    assert "- (no subject)" in text.splitlines()


def test_whitespace_subject_rendered_verbatim() -> None:
    """No strip/sanitize step: the padded subject is rendered verbatim."""
    data = _data(recent=[_ep_raw("  padded  ", "s")], vigente_count=1, total_episodes=1)
    text = render_context(data)
    assert "-   padded   — s" in text


def test_empty_string_summary_omits_dash_like_none() -> None:
    data = _data(recent=[_ep_raw("alpha", "")], vigente_count=1, total_episodes=1)
    text = render_context(data)
    assert "- alpha" in text.splitlines()
    assert "alpha —" not in text


def test_whitespace_summary_keeps_dash_with_trailing_space() -> None:
    """Whitespace-only summary is truthy: the dash survives, untrimmed."""
    data = _data(recent=[_ep_raw("alpha", " ")], vigente_count=1, total_episodes=1)
    text = render_context(data)
    assert "- alpha —  " in text
    assert "alpha —" in text


def test_summary_rendered_verbatim_no_escaping() -> None:
    summary = "a — b ## head - item `code` recall"
    data = _data(recent=[_ep_raw("alpha", summary)], vigente_count=1, total_episodes=1)
    text = render_context(data)
    assert summary in text


def test_multiline_summary_rendered_verbatim() -> None:
    """Documents the no-escaping decision: a newline passes straight through."""
    data = _data(recent=[_ep_raw("alpha", "line one\nline two")], vigente_count=1, total_episodes=1)
    text = render_context(data)
    assert "line one\nline two" in text


def test_unicode_subject_and_summary_round_trip() -> None:
    data = _data(
        recent=[_ep_raw("café ☕", "naïve — ünïcode ✓")], vigente_count=1, total_episodes=1
    )
    text = render_context(data)
    assert "café ☕" in text
    assert "naïve — ünïcode ✓" in text


def test_very_long_summary_never_truncated() -> None:
    summary = "A" + "x" * 100_000 + "Z"
    data = _data(recent=[_ep_raw("alpha", summary)], vigente_count=1, total_episodes=1)
    text = render_context(data)
    assert "A" + "x" * 100_000 in text
    assert "x" * 100_000 + "Z" in text


def test_duplicate_entries_each_rendered() -> None:
    """Distinct-but-equal episodes are never collapsed by the renderer."""
    data = _data(
        recent=[_ep("dup", summary="same fact"), _ep("dup", summary="same fact")],
        vigente_count=2,
        total_episodes=2,
    )
    text = render_context(data)
    assert text.count("- dup — same fact") == 2


def test_row_order_is_input_list_order() -> None:
    """The facade sorts deterministically; the assembler must not re-sort."""
    data = _data(
        recent=[_ep("zulu"), _ep("alpha"), _ep("mike")],
        last_session_id="sess-1",
        last_session=[_ep("zulu"), _ep("alpha"), _ep("mike")],
        vigente_count=3,
        total_episodes=3,
    )
    text = render_context(data)
    assert text.index("zulu") < text.index("alpha") < text.index("mike")


@pytest.mark.parametrize(
    ("subject", "summary", "expected_row"),
    [
        ("alpha", None, "- alpha"),
        (None, "s", "- (no subject) — s"),
        ("alpha", "fact", "- alpha — fact"),
    ],
)
def test_row_contract_table_driven_through_both_blocks(
    subject: str | None, summary: str | None, expected_row: str
) -> None:
    episode = _ep_raw(subject, summary)
    recent_text = render_context(_data(recent=[episode], vigente_count=1, total_episodes=1))
    last_session_text = render_context(
        _data(last_session_id="sess-1", last_session=[episode], total_episodes=1)
    )
    assert expected_row in recent_text.splitlines()
    assert expected_row in last_session_text.splitlines()


# ---------------------------------------------------------------------------
# Block headers and counts
# ---------------------------------------------------------------------------


def test_recent_header_counts_list_and_state_counts_vigente_count() -> None:
    """In production vigente_count >= len(recent) whenever the valid set
    exceeds top_k — the two counts diverge by design."""
    data = _data(
        recent=[_ep("alpha"), _ep("beta")],
        vigente_count=5,
        total_episodes=5,
    )
    text = render_context(data)
    assert "## Recent episodes (2)" in text
    assert "## Current state (5 facts)" in text
    assert "Recent episodes (5)" not in text


def test_vigente_count_renders_exact_value_no_singularization() -> None:
    text_one = render_context(_data(recent=[_ep("alpha")], vigente_count=1, total_episodes=1))
    assert "## Current state (1 facts)" in text_one  # no singularization
    text_big = render_context(_data(vigente_count=12345, total_episodes=12345))
    assert "## Current state (12345 facts)" in text_big  # no thousand separators


def test_empty_recent_block_emits_placeholder_line() -> None:
    text = render_context(_data())
    assert "(none yet — the context is empty until episodes are indexed)" in text


def test_placeholder_line_absent_once_episodes_exist() -> None:
    text = render_context(_data(recent=[_ep("alpha")], vigente_count=1, total_episodes=1))
    assert "(none yet — the context is empty until episodes are indexed)" not in text


def test_last_session_none_renders_none_variant() -> None:
    text = render_context(_data())
    assert "(none)" in text
    assert "## Last session (" not in text
    assert "## Last session" in text.splitlines()


def test_last_session_id_with_empty_list_renders_id_header_no_rows() -> None:
    """The id header keys on last_session_id alone; the list is read
    independently (ContextData is public API)."""
    text = render_context(_data(last_session_id="sess-9"))
    assert "## Last session (sess-9)" in text
    assert "(none)" not in text
    lines = text.splitlines()
    header_index = lines.index("## Last session (sess-9)")
    assert lines[header_index + 1] == ""  # block ends with no row lines


def test_empty_string_last_session_id_takes_none_branch() -> None:
    """Pins truthiness vs `is not None`: '' is treated like None."""
    text = render_context(_data(last_session_id=""))
    assert "(none)" in text
    assert "## Last session ()" not in text


def test_stats_counters_render_exact_field_values() -> None:
    text_zero = render_context(_data())
    assert "- 0 episodes total" in text_zero.splitlines()
    text_big = render_context(_data(total_episodes=12345))
    assert "- 12345 episodes total" in text_big.splitlines()


def test_vigente_count_and_total_episodes_render_independently() -> None:
    text = render_context(_data(vigente_count=3, total_episodes=10))
    assert "## Current state (3 facts)" in text
    assert "- 10 episodes total" in text
    assert "(10 facts)" not in text
    assert "3 episodes total" not in text


def test_current_state_block_always_carries_explanation_line() -> None:
    explanation = "The recent list above is the current-state set (created_at desc)."
    assert explanation in render_context(_data())
    populated = render_context(
        _data(recent=[_ep("alpha")], vigente_count=1, total_episodes=1)
    )
    assert explanation in populated


# ---------------------------------------------------------------------------
# Knowledge notes block (the distilled surface)
# ---------------------------------------------------------------------------


def test_knowledge_notes_block_renders_rows_with_type() -> None:
    data = _data(
        knowledge=[_kep("ADR: storage engine", summary="SQLite WAL + sqlite-vec", cognitive_type="project_doc")],
        total_episodes=1,
    )
    text = render_context(data)
    assert "## Knowledge notes (1)" in text
    assert (
        "- ADR: storage engine — SQLite WAL + sqlite-vec (project_doc)"
        in text.splitlines()
    )


def test_knowledge_notes_rows_render_in_input_order() -> None:
    data = _data(
        knowledge=[_kep("newest"), _kep("oldest")],
        total_episodes=2,
    )
    text = render_context(data)
    assert text.index("newest") < text.index("oldest")


def test_knowledge_notes_empty_is_honest() -> None:
    text = render_context(_data())
    assert "## Knowledge notes (0)" in text
    assert "(none yet" in text
    assert "knowledge notes appear" in text


def test_knowledge_notes_block_precedes_stats() -> None:
    data = _data(knowledge=[_kep("ADR: ui")], total_episodes=1)
    text = render_context(data)
    assert text.index("## Knowledge notes") < text.index("## Stats")


def test_knowledge_row_without_summary_has_no_dangling_dash() -> None:
    data = _data(knowledge=[_kep("ADR: no summary")], total_episodes=1)
    text = render_context(data)
    assert "- ADR: no summary (semantic)" in text.splitlines()
    assert "ADR: no summary —" not in text


def test_knowledge_row_empty_type_falls_back_to_label() -> None:
    data = _data(knowledge=[_kep("ADR: untyped", cognitive_type="")], total_episodes=1)
    text = render_context(data)
    assert "- ADR: untyped (knowledge)" in text.splitlines()


def test_knowledge_header_counts_list_length() -> None:
    data = _data(
        knowledge=[_kep("a"), _kep("b"), _kep("c")],
        total_episodes=3,
    )
    text = render_context(data)
    assert "## Knowledge notes (3)" in text


# ---------------------------------------------------------------------------
# Output structure and determinism
# ---------------------------------------------------------------------------


def test_output_has_no_trailing_newline() -> None:
    """render_context returns no trailing '\\n'; the CLI layer adds exactly one."""
    data = _data(recent=[_ep("alpha")], vigente_count=1, total_episodes=1)
    text = render_context(data)
    assert not text.endswith("\n")
    final_line = text.splitlines()[-1]
    assert final_line.startswith("- ")
    assert "recall-full" in final_line  # the pointer is the last line


def test_exactly_one_blank_line_separates_blocks() -> None:
    data = _data(
        recent=[_ep("alpha", summary="first fact")],
        vigente_count=1,
        last_session_id="sess-1",
        last_session=[_ep("alpha", summary="first fact")],
        total_episodes=1,
    )
    text = render_context(data)
    assert "\n\n\n" not in text
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## "):
            assert lines[i - 1] == ""  # every block header follows a blank line


def test_header_shaped_subject_cannot_inject_block_heading() -> None:
    """Injection safety: a subject starting with '## ' stays a row, never a
    document heading."""
    data = _data(
        recent=[_ep_raw("## Stats", "(none yet)", ep_id="ep-inj")],
        vigente_count=1,
        total_episodes=1,
    )
    text = render_context(data)
    assert "- ## Stats — (none yet)" in text.splitlines()
    assert sum(1 for line in text.splitlines() if line.startswith("## ")) == 5


def test_golden_full_output_snapshot() -> None:
    """Full-string equality for one representative input: block order,
    spacing, row shape and pointer placement are all pinned here."""
    data = _data(
        recent=[_ep("alpha", summary="first fact"), _ep("beta")],
        vigente_count=2,
        last_session_id="sess-1",
        last_session=[_ep("alpha", summary="first fact")],
        total_episodes=2,
    )
    expected = (
        "# Seahorse memory context\n"
        "\n"
        "## Recent episodes (2)\n"
        "- alpha — first fact\n"
        "- beta\n"
        "\n"
        "## Current state (2 facts)\n"
        "The recent list above is the current-state set (created_at desc).\n"
        "\n"
        "## Last session (sess-1)\n"
        "- alpha — first fact\n"
        "\n"
        "## Knowledge notes (0)\n"
        "(none yet — knowledge notes appear as the agent writes project_doc "
        "notes or `seahorse consolidate` distills)\n"
        "\n"
        "## Stats\n"
        "- 2 episodes total\n"
        "- Prefer the `seahorse-mcp` MCP tools (`recall`, `recall_full`) when "
        "available; otherwise `seahorse recall <query>` / `seahorse recall-full "
        "<ep_id>`. Chain `recall_full` (batches of up to 5) on the top hits to "
        "read the full bodies before answering design questions."
    )
    assert render_context(data) == expected


def test_determinism_across_equal_distinct_instances() -> None:
    """Data equality, not object equality: equal ContextData renders equal
    text, and rendering never mutates the input."""
    data_a = _data(
        recent=[_ep("alpha", summary="first fact"), _ep("beta")],
        vigente_count=2,
        last_session_id="sess-1",
        last_session=[_ep("alpha", summary="first fact")],
        total_episodes=2,
    )
    data_b = _data(
        recent=[_ep("alpha", summary="first fact"), _ep("beta")],
        vigente_count=2,
        last_session_id="sess-1",
        last_session=[_ep("alpha", summary="first fact")],
        total_episodes=2,
    )
    text_a = render_context(data_a)
    text_b = render_context(data_b)
    assert text_a == text_b
    # No in-place sort/mutation of the input list by rendering.
    assert [ep.ep_id for ep in data_a.recent] == ["ep-alpha", "ep-beta"]
    assert [ep.ep_id for ep in data_a.last_session] == ["ep-alpha"]
