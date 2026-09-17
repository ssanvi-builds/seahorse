"""The lazy-recall metric — recall call/skip rate over observer traces.

P1a of the pre-Fase-2 sprint (``Claude/backlog-lazy-recall``): before
building a task-scoped read gate, MEASURE whether recall pays for its
read. The observer already captures every prompt and tool event, so the
real call/skip rate is observable over captured traces — this module is
the measurement, not the gate. Its numbers are the gate's entry
criterion: without a number, the gate is not built.

A **task** is one distinct ``(session_id, prompt_number)`` key across all
captured events — usually one user prompt (``user_prompt_submit``), plus
any task the observer joined mid-flight (its tool events still attribute
to their task key: honesty over tidiness — a call is never dropped for
lack of a captured prompt).

A recall call is a ``post_tool_use`` whose tool name contains ``recall``
(the MCP tool ``mcp__seahorse-mcp__recall``) or whose tool input invokes
the CLI mirror over Bash (``seahorse recall``). A call "returns rows into
context" when its tool response carries at least one result row —
detected across both surfaces: the MCP tool returns the row list
verbatim (empty list = nothing served), the CLI text carries
``(N results)``. An unparseable response is an honest no-rows: the call
still happened and still cost its read.

Both rates are FLOORS, never ceilings: a hook that serializes the
response in a shape the detectors do not recognize, or redaction acting
on the envelope, can only lower a rate (a real call/row missed by the
detector), never inflate one.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# The recall invocations the metric recognizes: an MCP tool whose name
# carries ``recall``, or the CLI mirror over Bash.
_RECALL_TOOL_MARKER = "recall"
_RECALL_CLI_MARKER = "seahorse recall"

# The CLI's count line: ``Recall: 'query' (2 results)`` (cli/output.py).
_RESULTS_RE = re.compile(r"\((\d+) results?\)")


@dataclass(frozen=True)
class LazyRecallReport:
    """The recall call/skip rate over captured observer traces.

    ``tasks_with_rows`` is the decision signal (``row_rate``): the share of
    tasks where recall actually returned rows to the context — if it is
    high, a read gate would silence useful memory; if low, the gate saves
    real cost.
    """

    sessions: int = 0
    tasks: int = 0
    recall_calls: int = 0
    tasks_with_recall: int = 0
    tasks_with_rows: int = 0

    @property
    def call_rate(self) -> float:
        """Share of tasks with >=1 recall call, rounded to a 0.1pp percent."""
        return _pct(self.tasks_with_recall, self.tasks)

    @property
    def row_rate(self) -> float:
        """Share of tasks where recall returned rows, rounded to 0.1pp percent."""
        return _pct(self.tasks_with_rows, self.tasks)


def measure(events: Iterable[dict]) -> LazyRecallReport:
    """Compute the report over parsed observer envelopes.

    An envelope is the ``observer_events`` payload dict: ``session_id``,
    ``prompt_number``, ``event_type``, ``payload`` (``load_events``).
    """
    sessions: set[str] = set()
    tasks: set[tuple[str, int]] = set()
    recall_tasks: set[tuple[str, int]] = set()
    row_tasks: set[tuple[str, int]] = set()
    recall_calls = 0
    for event in events:
        session = str(event.get("session_id", ""))
        number = int(event.get("prompt_number") or 0)
        task = (session, number)
        sessions.add(session)
        tasks.add(task)
        if event.get("event_type") != "post_tool_use":
            continue
        payload = event.get("payload") or {}
        if not _is_recall(payload):
            continue
        recall_calls += 1
        recall_tasks.add(task)
        if _rows_served(payload) > 0:
            row_tasks.add(task)
    return LazyRecallReport(
        sessions=len(sessions),
        tasks=len(tasks),
        recall_calls=recall_calls,
        tasks_with_recall=len(recall_tasks),
        tasks_with_rows=len(row_tasks),
    )


def load_events(db_path: Path) -> list[dict]:
    """Read ``observer.db`` into the parsed envelopes ``measure`` consumes.

    The row's ``session_id`` / ``prompt_number`` columns are the
    attribution source (the envelope's own fields are the fallback).
    """
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT session_id, prompt_number, envelope_json FROM observer_events"
        ).fetchall()
    finally:
        con.close()
    events = []
    for session, number, envelope in rows:
        event = json.loads(envelope)
        event["session_id"] = session
        event["prompt_number"] = number
        events.append(event)
    return events


def format_report(r: LazyRecallReport) -> str:
    """The published-number text (one line per decision signal)."""
    return (
        f"lazy-recall: {r.tasks} tasks in {r.sessions} sessions, "
        f"{r.recall_calls} recall calls — "
        f"call rate {r.call_rate}%, rows-into-context rate {r.row_rate}%"
    )


def _is_recall(payload: dict) -> bool:
    """True when the tool event is a recall read (MCP tool or CLI over Bash)."""
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input")
    input_text = tool_input if isinstance(tool_input, str) else json.dumps(tool_input)
    if _RECALL_TOOL_MARKER in tool_name.lower():
        return True
    return "bash" in tool_name.lower() and _RECALL_CLI_MARKER in str(input_text)


def _rows_served(payload: dict) -> int:
    """Count result rows in the tool response — 0 when unverifiable.

    Both call surfaces, honestly: the MCP tool returns the row list
    verbatim (``[]`` = nothing served); the CLI text carries
    ``(N results)``. Anything else (an error envelope, a foreign shape) is
    an honest zero — the read still happened.
    """
    response = str(payload.get("tool_response") or "")
    try:
        parsed = json.loads(response)
    except (ValueError, TypeError):
        match = _RESULTS_RE.search(response)
        return int(match.group(1)) if match else 0
    if isinstance(parsed, list):
        return len(parsed)
    return 0


def _pct(part: int, whole: int) -> float:
    if not whole:
        return 0.0
    return round(100.0 * part / whole, 1)


__all__ = ["LazyRecallReport", "format_report", "load_events", "measure"]
