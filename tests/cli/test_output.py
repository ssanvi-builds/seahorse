"""``seahorse.cli.output`` — renderers + serialization (mirrors the MCP server's
wire codec).

Owned by the CLI (sister-projection independence): it does NOT import
``seahorse.mcp.serialize``, so its serializer is tested here in its own right.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from seahorse.cli.output import (
    render_audit_log,
    render_episode,
    render_freshness_view,
    render_full_details,
    render_index_rows,
    render_message,
    render_supersedes_chain,
    render_timeline,
    render_write_result,
    to_json,
    to_jsonable,
)
from seahorse.contracts.engine import WriteResult
from tests.cli.builders import (
    make_audit_event,
    make_episode,
    make_freshness_view,
    make_full_detail,
    make_index_row,
    make_timeline_window,
)

# ---------------------------------------------------------------------------
# to_jsonable — type conversions (mirror of the MCP server's to_wire).
# ---------------------------------------------------------------------------


def test_jsonable_none_primitives():
    assert to_jsonable(None) is None
    assert to_jsonable(True) is True
    assert to_jsonable(3) == 3
    assert to_jsonable(3.5) == 3.5
    assert to_jsonable("x") == "x"


def test_jsonable_datetime_is_iso_z():
    dt = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
    assert to_jsonable(dt) == "2026-07-16T12:00:00Z"


def test_jsonable_naive_datetime_gets_utc():
    dt = datetime(2026, 7, 16, 12, 0, 0)  # naive
    # Naive datetimes are assumed UTC by _iso_z.
    assert to_jsonable(dt).endswith("Z")


def test_jsonable_path():
    assert to_jsonable(Path("/a/b")) == "/a/b"


def test_jsonable_uuid():
    """Defensive parity with the MCP server: a UUID serializes canonically."""
    from uuid import UUID

    assert to_jsonable(UUID("00000000-0000-7000-0000-000000000000")) == (
        "00000000-0000-7000-0000-000000000000"
    )


def test_jsonable_tuple_set_become_arrays():
    assert to_jsonable((1, 2, 3)) == [1, 2, 3]
    assert to_jsonable({1, 2}) in ([1, 2], [2, 1])  # set order unspecified


def test_jsonable_dict():
    assert to_jsonable({"a": 1, "b": datetime(2026, 1, 1, tzinfo=UTC)}) == {
        "a": 1,
        "b": "2026-01-01T00:00:00Z",
    }


def test_jsonable_dataclass():
    ep = make_episode("ep-1")
    out = to_jsonable(ep)
    assert isinstance(out, dict)
    assert out["id"] == "ep-1"
    assert out["created_at"] == "2026-07-16T12:00:00Z"


def test_jsonable_episode_exclude_fields_travel_wire():
    # Regression guard for the Pydantic migration: body/subject/fact_id are
    # Field(exclude=True) — model_dump omits them, but the CLI walker reads via
    # getattr so they STILL travel the JSON output (sister parity with the MCP
    # server's to_wire). Locks the invariant; fails if the walker ever switches
    # to model_dump.
    ep = make_episode("ep-1", body="Sergio lives in Madrid", subject="Sergio")
    out = to_jsonable(ep)
    assert out["body"] == "Sergio lives in Madrid"
    assert out["subject"] == "Sergio"
    assert out["fact_id"] == "fact-1"  # hardcoded by the builder
    assert out["supersedes_reason"] is None  # NEW additive wire key


def test_to_json_is_compact_and_nulls_explicit():
    """exclude_none=False: nulls present (shape stable across releases)."""
    ep = make_episode("ep-1")  # subject="Sergio", title=None
    s = to_json(ep)
    assert '"title": null' in s
    assert "\n" not in s  # compact
    # round-trips
    assert json.loads(s)["id"] == "ep-1"


# ---------------------------------------------------------------------------
# render_write_result — remember (no Episode in the first release, honest gap).
# ---------------------------------------------------------------------------


def test_render_write_result_human():
    out = []
    render_write_result(WriteResult("ep-1", "fact-1", "ACTIVE", []), "human", _Sink(out))
    text = "".join(out)
    assert "✓ Remembered" in text
    assert "ep-1" in text
    assert "fact_id" in text


def test_render_write_result_json():
    out = []
    render_write_result(WriteResult("ep-1", "fact-1", "ACTIVE", []), "json", _Sink(out))
    obj = json.loads("".join(out))
    assert obj["ep_id"] == "ep-1"
    assert obj["status"] == "ACTIVE"


# ---------------------------------------------------------------------------
# render_episode — improve / forget.
# ---------------------------------------------------------------------------


def test_render_episode_human_verb():
    out = []
    render_episode(
        make_episode("ep-2", supersedes="ep-1"), fmt="human", out=_Sink(out), verb="Improved"
    )
    text = "".join(out)
    assert "✓ Improved" in text
    assert "ep-2" in text
    assert "supersedes" in text


def test_render_episode_json():
    out = []
    render_episode(make_episode("ep-2"), fmt="json", out=_Sink(out), verb="Forgotten")
    assert json.loads("".join(out))["id"] == "ep-2"


# ---------------------------------------------------------------------------
# render_index_rows — recall.
# ---------------------------------------------------------------------------


def test_render_index_rows_human_empty():
    out = []
    render_index_rows([], fmt="human", out=_Sink(out), query="x")
    assert "(no results)" in "".join(out)


def test_render_index_rows_human_with_rows():
    out = []
    render_index_rows([make_index_row("ep-1")], fmt="human", out=_Sink(out), query="x")
    text = "".join(out)
    assert "ep-1" in text
    assert "recall-timeline" in text  # hint line


def test_render_index_rows_jsonl_one_per_line():
    out = []
    render_index_rows(
        [make_index_row("ep-1"), make_index_row("ep-2")], fmt="jsonl", out=_Sink(out), query="x"
    )
    lines = [line for line in "".join(out).splitlines() if line.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["ep_id"] == "ep-1"


def test_render_index_rows_json_is_array():
    out = []
    render_index_rows([make_index_row("ep-1")], fmt="json", out=_Sink(out), query="x")
    obj = json.loads("".join(out))
    assert isinstance(obj, list) and obj[0]["ep_id"] == "ep-1"


# ---------------------------------------------------------------------------
# render_timeline.
# ---------------------------------------------------------------------------


def test_render_timeline_human():
    out = []
    render_timeline(make_timeline_window(), fmt="human", out=_Sink(out))
    text = "".join(out)
    assert "Timeline:" in text
    assert "ep-2" in text  # anchor


def test_render_timeline_human_hints_recall_full():
    """Progressive disclosure: the middle rung hints at the next."""
    out = []
    render_timeline(make_timeline_window(), fmt="human", out=_Sink(out))
    text = "".join(out)
    assert "recall-full" in text  # hint to hydrate body


def test_render_timeline_jsonl():
    out = []
    render_timeline(make_timeline_window(), fmt="jsonl", out=_Sink(out))
    lines = [line for line in "".join(out).splitlines() if line.strip()]
    assert len(lines) == 1  # one entry
    assert json.loads(lines[0])["ep_id"] == "ep-1"


# ---------------------------------------------------------------------------
# render_full_details.
# ---------------------------------------------------------------------------


def test_render_full_details_human_body_hydrated():
    out = []
    render_full_details([make_full_detail()], fmt="human", out=_Sink(out))
    text = "".join(out)
    assert "--- ep-1 ---" in text
    assert "Sergio lives in Madrid" in text  # body hydrated


def test_render_full_details_jsonl():
    out = []
    render_full_details(
        [make_full_detail(), make_full_detail()], fmt="jsonl", out=_Sink(out)
    )
    lines = [line for line in "".join(out).splitlines() if line.strip()]
    assert len(lines) == 2


# ---------------------------------------------------------------------------
# render_message — generic management output.
# ---------------------------------------------------------------------------


def test_render_message_human_appends_newline():
    out = []
    render_message({"x": 1}, fmt="human", out=_Sink(out), human_text="hello")
    assert "".join(out) == "hello\n"


def test_render_message_human_keeps_existing_newline():
    out = []
    render_message({"x": 1}, fmt="human", out=_Sink(out), human_text="hi\n")
    assert "".join(out) == "hi\n"


def test_render_message_json():
    out = []
    render_message({"x": 1}, fmt="json", out=_Sink(out), human_text="ignored")
    assert json.loads("".join(out)) == {"x": 1}


# ---------------------------------------------------------------------------
# render_freshness_view / render_audit_log / render_supersedes_chain.
# ---------------------------------------------------------------------------


def test_render_freshness_view_human():
    out = []
    render_freshness_view(make_freshness_view(), fmt="human", out=_Sink(out))
    text = "".join(out)
    assert "fact-1" in text
    assert "age_days:       3" in text
    assert "stale:          yes" in text


def test_render_freshness_view_json():
    out = []
    render_freshness_view(make_freshness_view(), fmt="json", out=_Sink(out))
    assert json.loads("".join(out))["fact_id"] == "fact-1"


def test_render_audit_log_human():
    out = []
    render_audit_log(
        [make_audit_event(), make_audit_event(primitive="forget", result="invalidated")],
        fmt="human",
        out=_Sink(out),
    )
    text = "".join(out)
    assert "2 events" in text
    assert "apply" in text
    assert "forget" in text


def test_render_audit_log_jsonl():
    out = []
    render_audit_log(
        [make_audit_event(), make_audit_event(primitive="forget")],
        fmt="jsonl",
        out=_Sink(out),
    )
    lines = "".join(out).strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["primitive"] == "apply"


def test_render_supersedes_chain_human():
    out = []
    render_supersedes_chain(
        [make_episode("ep-1"), make_episode("ep-2", supersedes="ep-1")],
        fmt="human",
        out=_Sink(out),
    )
    text = "".join(out)
    assert "2 episodes" in text
    assert "ep-1" in text
    assert "ep-2" in text


def test_render_supersedes_chain_json():
    out = []
    render_supersedes_chain([make_episode("ep-1")], fmt="json", out=_Sink(out))
    assert json.loads("".join(out))[0]["id"] == "ep-1"


# ---------------------------------------------------------------------------
# Helper: a TextIO that appends to a list (so we can join + assert).
# ---------------------------------------------------------------------------


class _Sink:
    """Minimal TextIO-like writer collecting written strings."""

    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def write(self, s: str) -> int:
        self._sink.append(s)
        return len(s)

    def flush(self) -> None:  # pragma: no cover
        pass