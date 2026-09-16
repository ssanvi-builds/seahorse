"""``lazy_recall`` — the recall call/skip-rate metric over observer traces.

P1a of the pre-Fase-2 sprint (``Claude/backlog-lazy-recall``): before
building a task-scoped read gate, MEASURE whether recall pays for its read.
The observer already captures every prompt and tool event, so the real
call/skip rate is observable over captured traces — this experiment is the
measurement, not the gate.

A **task** is one user prompt (``user_prompt_submit``); the trace attributes
every event to its task via ``(session_id, prompt_number)``. A recall
"returns rows into context" when its tool response carries at least one
result row — detected across both surfaces an agent may call it through:
the MCP tool (``mcp__seahorse-mcp__recall``, a JSON row list) and the CLI
mirror invoked over Bash (``seahorse recall ...`` text, ``(N results)``).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from seahorse.benchmark.experiments.lazy_recall import measure

# A session/prompt pair ids every trace event (the observer's
# ``observer_events.prompt_number`` column).


def _prompt(session: str, number: int) -> dict:
    return {
        "session_id": session,
        "prompt_number": number,
        "event_type": "user_prompt_submit",
        "payload": {"prompt": f"task {number}"},
    }


def _tool(session: str, number: int, tool_name: str, tool_input: str, response: str):
    return {
        "session_id": session,
        "prompt_number": number,
        "event_type": "post_tool_use",
        "payload": {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_response": response,
        },
    }


def test_empty_trace_is_an_honest_zero() -> None:
    r = measure([])
    assert (r.tasks, r.sessions, r.recall_calls, r.tasks_with_recall, r.tasks_with_rows) == (
        0,
        0,
        0,
        0,
        0,
    )


def test_tasks_are_counted_from_prompts() -> None:
    events = [
        _prompt("s1", 1),
        _prompt("s1", 2),
        _prompt("s2", 1),
    ]
    r = measure(events)
    assert (r.tasks, r.sessions, r.recall_calls, r.tasks_with_recall) == (3, 2, 0, 0)


def test_mcp_recall_with_rows_counts_as_task_with_rows() -> None:
    events = [
        _prompt("s1", 1),
        _tool(
            "s1",
            1,
            "mcp__seahorse-mcp__recall",
            json.dumps({"query": "deploy design", "k": 10}),
            json.dumps([{"ep_id": "0198", "subject": "deploy"}]),
        ),
    ]
    r = measure(events)
    assert (r.recall_calls, r.tasks_with_recall, r.tasks_with_rows) == (1, 1, 1)


def test_mcp_recall_with_empty_list_yields_no_rows() -> None:
    """``facade.recall`` returns the row list verbatim — empty means the
    recall cost its read and returned nothing to the context."""
    events = [
        _prompt("s1", 1),
        _tool(
            "s1",
            1,
            "mcp__seahorse-mcp__recall",
            json.dumps({"query": "nothing"}),
            "[]",
        ),
    ]
    r = measure(events)
    assert (r.recall_calls, r.tasks_with_recall, r.tasks_with_rows) == (1, 1, 0)


def test_cli_recall_over_bash_is_detected() -> None:
    """The CLI mirror invoked over Bash is the same recall read — the text
    output carries ``(N results)``."""
    events = [
        _prompt("s1", 1),
        _tool(
            "s1",
            1,
            "Bash",
            json.dumps({"command": "seahorse recall 'deploy design'"}),
            "Recall: 'deploy design' (2 results)\n\n  1  ... ",
        ),
    ]
    r = measure(events)
    assert (r.recall_calls, r.tasks_with_recall, r.tasks_with_rows) == (1, 1, 1)


def test_cli_recall_with_zero_results_yields_no_rows() -> None:
    events = [
        _prompt("s1", 1),
        _tool(
            "s1",
            1,
            "Bash",
            json.dumps({"command": "seahorse recall 'deploy design'"}),
            "Recall: 'deploy design' (0 results)\n\n  (no results)\n",
        ),
    ]
    r = measure(events)
    assert (r.recall_calls, r.tasks_with_recall, r.tasks_with_rows) == (1, 1, 0)


def test_unparseable_recall_response_counts_the_call_not_the_rows() -> None:
    """An error envelope (or a foreign shape) is an honest no-rows — the call
    still happened and still cost its read."""
    events = [
        _prompt("s1", 1),
        _tool("s1", 1, "mcp__seahorse-mcp__recall", "{}", "<boom>"),
    ]
    r = measure(events)
    assert (r.recall_calls, r.tasks_with_recall, r.tasks_with_rows) == (1, 1, 0)


def test_two_recall_calls_in_one_task_count_both() -> None:
    events = [
        _prompt("s1", 1),
        _tool("s1", 1, "mcp__seahorse-mcp__recall", "{}", "[]"),
        _tool(
            "s1",
            1,
            "mcp__seahorse-mcp__recall",
            "{}",
            json.dumps([{"ep_id": "e1"}]),
        ),
    ]
    r = measure(events)
    assert (r.recall_calls, r.tasks_with_recall, r.tasks_with_rows) == (2, 1, 1)


def test_tool_events_without_a_prompt_still_attribute_to_their_task() -> None:
    """A session the observer joined mid-flight (no captured prompt yet) has
    its own task key — attributed, never dropped (honesty over tidiness)."""
    events = [
        _tool("s1", 0, "mcp__seahorse-mcp__recall", "{}", "[]"),
    ]
    r = measure(events)
    assert (r.tasks, r.recall_calls, r.tasks_with_recall) == (1, 1, 1)


def test_rates_are_rounded_percentages() -> None:
    events = [_prompt("s1", i) for i in range(1, 4)]
    events.append(
        _tool(
            "s1",
            2,
            "mcp__seahorse-mcp__recall",
            "{}",
            json.dumps([{"ep_id": "e1"}]),
        )
    )
    r = measure(events)
    assert (r.call_rate, r.row_rate) == (33.3, 33.3)


def test_measure_over_a_real_observer_db(tmp_path: Path) -> None:
    """The metric reads ``observer.db`` directly (our own vault is the real
    data) — load + measure compose."""
    from seahorse.benchmark.experiments.lazy_recall import load_events

    db = tmp_path / "observer.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE observer_events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, "
        "prompt_number INTEGER NOT NULL, event_fingerprint TEXT NOT NULL, "
        "envelope_json TEXT NOT NULL, created_at TEXT NOT NULL, acked_at TEXT)"
    )
    for e in (
        _prompt("s1", 1),
        _tool(
            "s1",
            1,
            "mcp__seahorse-mcp__recall",
            "{}",
            json.dumps([{"ep_id": "e1"}]),
        ),
    ):
        con.execute(
            "INSERT INTO observer_events"
            "(session_id, prompt_number, event_fingerprint, envelope_json,"
            " created_at) VALUES (?, ?, 'fp', ?, '')",
            (e["session_id"], e["prompt_number"], json.dumps(e)),
        )
    con.commit()
    con.close()

    r = measure(load_events(db))
    assert (r.tasks, r.recall_calls, r.tasks_with_rows) == (1, 1, 1)
