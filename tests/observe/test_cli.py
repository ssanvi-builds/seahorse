"""Tests for ``seahorse.observe.cli`` — ``seahorse observe start|stop|status``.

The observer is a single-writer background process (§4.5). ``start`` spawns a
detached subprocess + writes the PID; ``stop`` SIGTERMs it; ``status`` reports
whether it is running. A second ``start`` while running fails loud
(``CliObserverRunning``, exit 95).
"""

from __future__ import annotations

import io
import os

import pytest

from seahorse.cli.config import SeahorseConfig
from seahorse.cli.errors import CliObserverRunning
from seahorse.observe.cli import (
    pid_file,
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
