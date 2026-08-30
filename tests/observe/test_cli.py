"""Tests for ``seahorse.observe.cli`` — ``seahorse observe start|stop|status``.

The observer is a single-writer background process. ``start`` spawns a detached
subprocess + writes the PID; ``stop`` SIGTERMs it; ``status`` reports whether it
is running. A second ``start`` while running fails loud (``CliObserverRunning``,
exit 95).
"""

from __future__ import annotations

import io
import os

import pytest

from seahorse.cli.config import ObserveConfig, SeahorseConfig
from seahorse.cli.errors import CliError, CliObserverRunning
from seahorse.cli.exit_codes import CLI_CONFIG_INVALID
from seahorse.observe.cli import (
    pid_file,
    run_observe_event,
    run_observe_start,
    run_observe_status,
    run_observe_stop,
)


def _cfg(tmp_path) -> SeahorseConfig:
    seahorse_dir = tmp_path / ".seahorse"
    seahorse_dir.mkdir(parents=True, exist_ok=True)
    return SeahorseConfig(
        vault=tmp_path,
        seahorse_dir=seahorse_dir,
        db_path=seahorse_dir / "seahorse.db",
    )


def _out() -> io.StringIO:
    return io.StringIO()


def _write_pid(cfg, pid: int) -> None:
    pid_file(cfg).parent.mkdir(parents=True, exist_ok=True)
    pid_file(cfg).write_text(str(pid))


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_not_running_without_pid(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    out = _out()
    run_observe_status(cfg, fmt="human", out=out)
    assert "not running" in out.getvalue()


def test_status_running_with_live_pid(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    _write_pid(cfg, os.getpid())  # this process is alive
    out = _out()
    run_observe_status(cfg, fmt="human", out=out)
    assert "running" in out.getvalue()
    assert str(os.getpid()) in out.getvalue()


def test_status_not_running_with_dead_pid(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    _write_pid(cfg, 999999999)  # unlikely to be alive
    out = _out()
    run_observe_status(cfg, fmt="human", out=out)
    assert "not running" in out.getvalue()


def test_status_json(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    out = _out()
    run_observe_status(cfg, fmt="json", out=out)
    import json

    payload = json.loads(out.getvalue())
    assert payload == {"running": False, "pid": None}


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


def test_start_when_running_raises(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    _write_pid(cfg, os.getpid())
    with pytest.raises(CliObserverRunning):
        run_observe_start(cfg, fmt="human", out=_out())


def test_stop_not_running_is_noop(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    out = _out()
    run_observe_stop(cfg, fmt="human", out=out)
    assert "not running" in out.getvalue()


def test_stop_kills_live_pid(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    # Spawn a real child process to kill.
    import subprocess

    proc = subprocess.Popen(["sleep", "30"])
    _write_pid(cfg, proc.pid)
    out = _out()
    run_observe_stop(cfg, fmt="human", out=out)
    assert "stopped" in out.getvalue()
    assert not pid_file(cfg).exists()
    # Reap the zombie, then the child is gone.
    proc.wait(timeout=5)
    with pytest.raises(ProcessLookupError):
        os.kill(proc.pid, 0)


def test_start_fails_loud_on_long_socket_path(tmp_path) -> None:
    """A vault whose observer socket exceeds the AF_UNIX limit fails loud.

    Regression: the endpoint thread used to die silently (daemon) on
    ``AF_UNIX path too long`` while ``observe status`` still reported
    "running" — every envelope was dropped with no error.
    """
    long_vault = tmp_path / ("v" * 60)
    cfg = SeahorseConfig(
        vault=long_vault,
        seahorse_dir=long_vault / ".seahorse",
        db_path=long_vault / ".seahorse" / "seahorse.db",
        observe=ObserveConfig(),
    )
    with pytest.raises(CliError) as exc:
        run_observe_start(cfg, fmt="human", out=_out())
    assert exc.value.exit_code == CLI_CONFIG_INVALID
    assert "socket path too long" in exc.value.detail


def test_start_spawns_and_writes_pid(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    spawned = {}

    class _FakeProc:
        pid = 4242

    def _fake_popen(cmd, **kwargs):
        spawned["cmd"] = cmd
        spawned["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr("seahorse.observe.cli.subprocess.Popen", _fake_popen)
    out = _out()
    run_observe_start(cfg, fmt="human", out=out)
    assert "started" in out.getvalue()
    assert pid_file(cfg).read_text() == "4242"
    # The spawn uses the python interpreter + the CLI module (not PATH).
    assert spawned["cmd"][0] == os.sys.executable
    assert "seahorse.cli.app" in spawned["cmd"]
    assert "observe" in spawned["cmd"]
    assert "run" in spawned["cmd"]
    # --vault is a GLOBAL option and must precede the subcommand (observe run);
    # a regression here made the observer die with "No such option: --vault".
    assert spawned["cmd"].index("--vault") < spawned["cmd"].index("observe")


# ---------------------------------------------------------------------------
# observe event (hook injection — the self-evolving loop's capture path)
# ---------------------------------------------------------------------------


def _capture_post(monkeypatch, posted: list) -> None:
    """Route ``_post_event`` into a list so tests can assert the envelope."""

    def _fake_post(cfg, raw):
        posted.append(raw)
        return 200, ""

    monkeypatch.setattr("seahorse.observe.cli._post_event", _fake_post)


def test_observe_event_user_prompt_submit(tmp_path, monkeypatch) -> None:
    """UserPromptSubmit maps to user_prompt_submit with the prompt payload."""
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "UserPromptSubmit")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-1")
    monkeypatch.setenv("CLAUDE_PROMPT", "remember this")
    posted: list = []
    _capture_post(monkeypatch, posted)
    run_observe_event(_cfg(tmp_path), fmt="human", out=_out())
    assert posted == [
        {
            "session_id": "sess-1",
            "event_type": "user_prompt_submit",
            "payload": {"prompt": "remember this"},
        }
    ]


def test_observe_event_post_tool_use(tmp_path, monkeypatch) -> None:
    """PostToolUse maps to post_tool_use with the tool payload."""
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "PostToolUse")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-2")
    monkeypatch.setenv("CLAUDE_TOOL_NAME", "Read")
    monkeypatch.setenv("CLAUDE_TOOL_USE_ID", "call_1")
    monkeypatch.setenv("CLAUDE_TOOL_INPUT", '{"path": "x"}')
    monkeypatch.setenv("CLAUDE_TOOL_RESPONSE", "ok")
    posted: list = []
    _capture_post(monkeypatch, posted)
    run_observe_event(_cfg(tmp_path), fmt="human", out=_out())
    assert posted == [
        {
            "session_id": "sess-2",
            "event_type": "post_tool_use",
            "payload": {
                "tool_name": "Read",
                "tool_use_id": "call_1",
                "tool_input": '{"path": "x"}',
                "tool_response": "ok",
            },
        }
    ]


def test_observe_event_includes_agent_id(tmp_path, monkeypatch) -> None:
    """CLAUDE_AGENT_ID is carried on the envelope when present."""
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "SessionStart")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-3")
    monkeypatch.setenv("CLAUDE_AGENT_ID", "agent-7")
    posted: list = []
    _capture_post(monkeypatch, posted)
    run_observe_event(_cfg(tmp_path), fmt="human", out=_out())
    assert posted[0]["agent_id"] == "agent-7"
    assert posted[0]["event_type"] == "session_start"


def test_observe_event_unknown_event_ignored(tmp_path, monkeypatch) -> None:
    """An unmapped hook event is a silent no-op (never POSTs)."""
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "PreToolUse")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-4")
    posted: list = []
    _capture_post(monkeypatch, posted)
    run_observe_event(_cfg(tmp_path), fmt="human", out=_out())
    assert posted == []


def test_observe_event_missing_env_noop(tmp_path, monkeypatch) -> None:
    """Missing hook env vars are a silent no-op (never POSTs)."""
    monkeypatch.delenv("CLAUDE_HOOK_EVENT_NAME", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    posted: list = []
    _capture_post(monkeypatch, posted)
    run_observe_event(_cfg(tmp_path), fmt="human", out=_out())
    assert posted == []


def test_observe_event_observer_not_running_noop(tmp_path, monkeypatch) -> None:
    """A missing observer socket is a silent no-op (real _post_event path)."""
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "UserPromptSubmit")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-5")
    monkeypatch.setenv("CLAUDE_PROMPT", "x")
    # Observer configured but no socket file exists → (0, "not running").
    cfg = SeahorseConfig(
        vault=tmp_path,
        seahorse_dir=tmp_path / ".seahorse",
        db_path=tmp_path / ".seahorse" / "seahorse.db",
        observe=ObserveConfig(),
    )
    run_observe_event(cfg, fmt="human", out=_out())


def test_observe_event_post_failure_noop(tmp_path, monkeypatch) -> None:
    """A failed POST (OSError) is a silent no-op — the hook must not abort."""
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "UserPromptSubmit")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-6")
    monkeypatch.setenv("CLAUDE_PROMPT", "x")

    def _raise_oserror(cfg, raw):
        raise OSError("socket gone")

    monkeypatch.setattr("seahorse.observe.cli._post_event", _raise_oserror)
    run_observe_event(_cfg(tmp_path), fmt="human", out=_out())
