"""Tests for ``seahorse.observe.cli`` — ``seahorse observe start|stop|status``.

The observer is a single-writer background process. ``start`` spawns a detached
subprocess + writes the PID; ``stop`` SIGTERMs it; ``status`` reports whether it
is running. A second ``start`` while running fails loud (``CliObserverRunning``,
exit 95).
"""

from __future__ import annotations

import io
import json
import os

import pytest

from seahorse.cli.config import ObserveConfig, SeahorseConfig
from seahorse.cli.errors import CliError, CliObserverRunning
from seahorse.cli.exit_codes import CLI_CONFIG_INVALID
from seahorse.observe.cli import (
    _context_command,
    _ensure_running,
    _wait_for_observer,
    acquire_observer_lock,
    lock_file,
    pid_file,
    run_observe_event,
    run_observe_run,
    run_observe_start,
    run_observe_status,
    run_observe_stop,
    socket_path,
    spool_dir,
)


class _FakeCompleted:
    """Minimal subprocess.CompletedProcess stand-in for injection tests."""

    def __init__(self, *, returncode: int, stdout: bytes) -> None:
        self.returncode = returncode
        self.stdout = stdout


def _cfg(tmp_path) -> SeahorseConfig:
    seahorse_dir = tmp_path / ".seahorse"
    seahorse_dir.mkdir(parents=True, exist_ok=True)
    return SeahorseConfig(
        vault=tmp_path,
        seahorse_dir=seahorse_dir,
        db_path=seahorse_dir / "seahorse.db",
    )


def _cfg_observe(tmp_path) -> SeahorseConfig:
    """Config with the observer set up (the ``run_observe_run`` precondition)."""
    base = _cfg(tmp_path)
    return SeahorseConfig(
        vault=base.vault,
        seahorse_dir=base.seahorse_dir,
        db_path=base.db_path,
        observe=ObserveConfig(),
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


def test_start_uses_spawn_observer_helper(tmp_path, monkeypatch) -> None:
    """``run_observe_start`` delegates the spawn to ``_spawn_observer``.

    The hook respawn path (dead observer → spawn from ``observe event``) shares
    this helper; this pins the delegation so a refactor of one cannot silently
    diverge from the other.
    """
    cfg = _cfg(tmp_path)

    def _fake_spawn(cfg_arg):
        assert cfg_arg is cfg
        # The real helper's contract: spawn detached AND write the pid file.
        pid_file(cfg_arg).parent.mkdir(parents=True, exist_ok=True)
        pid_file(cfg_arg).write_text("4243")
        return 4243

    monkeypatch.setattr("seahorse.observe.cli._spawn_observer", _fake_spawn)
    out = _out()
    run_observe_start(cfg, fmt="human", out=out)
    assert "started" in out.getvalue()
    assert pid_file(cfg).read_text() == "4243"


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
    run_observe_event(_cfg_observe(tmp_path), fmt="human", out=_out())
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
    run_observe_event(_cfg_observe(tmp_path), fmt="human", out=_out())
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
    run_observe_event(_cfg_observe(tmp_path), fmt="human", out=_out())
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


def test_observe_event_stdin_payload(tmp_path, monkeypatch) -> None:
    """The Claude Code contract: the hook payload arrives as stdin JSON."""
    hook_input = {
        "session_id": "sess-stdin",
        "hook_event_name": "PostToolUse",
        "tool_name": "Grep",
        "tool_input": {"pattern": "secret", "path": "src/"},
        "tool_response": {"content": "match"},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_input)))
    posted: list = []
    _capture_post(monkeypatch, posted)
    run_observe_event(_cfg_observe(tmp_path), fmt="human", out=_out())
    assert posted == [
        {
            "session_id": "sess-stdin",
            "event_type": "post_tool_use",
            "payload": {
                "tool_name": "Grep",
                "tool_use_id": "",
                # Structured stdin fields are serialized so the redactor's
                # string walk covers nested secrets.
                "tool_input": '{"pattern": "secret", "path": "src/"}',
                "tool_response": '{"content": "match"}',
            },
        }
    ]


def test_observe_event_stdin_prompt(tmp_path, monkeypatch) -> None:
    """UserPromptSubmit delivered on stdin maps to the prompt payload."""
    hook_input = {
        "session_id": "sess-stdin-2",
        "hook_event_name": "UserPromptSubmit",
        "prompt": "recall the limine fix",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_input)))
    posted: list = []
    _capture_post(monkeypatch, posted)
    run_observe_event(_cfg_observe(tmp_path), fmt="human", out=_out())
    assert posted == [
        {
            "session_id": "sess-stdin-2",
            "event_type": "user_prompt_submit",
            "payload": {"prompt": "recall the limine fix"},
        }
    ]


def test_observe_event_stdin_precedence(tmp_path, monkeypatch) -> None:
    """stdin JSON wins over legacy env vars when both are present."""
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "UserPromptSubmit")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-env")
    monkeypatch.setenv("CLAUDE_PROMPT", "from env")
    hook_input = {
        "session_id": "sess-stdin-3",
        "hook_event_name": "UserPromptSubmit",
        "prompt": "from stdin",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_input)))
    posted: list = []
    _capture_post(monkeypatch, posted)
    run_observe_event(_cfg_observe(tmp_path), fmt="human", out=_out())
    assert posted == [
        {
            "session_id": "sess-stdin-3",
            "event_type": "user_prompt_submit",
            "payload": {"prompt": "from stdin"},
        }
    ]


def test_observe_event_malformed_stdin_env_fallback(tmp_path, monkeypatch) -> None:
    """Unparseable stdin is a silent no-op of itself — env vars still work."""
    monkeypatch.setattr("sys.stdin", io.StringIO("not json {"))
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "UserPromptSubmit")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-7")
    monkeypatch.setenv("CLAUDE_PROMPT", "legacy path")
    posted: list = []
    _capture_post(monkeypatch, posted)
    run_observe_event(_cfg_observe(tmp_path), fmt="human", out=_out())
    assert posted == [
        {
            "session_id": "sess-7",
            "event_type": "user_prompt_submit",
            "payload": {"prompt": "legacy path"},
        }
    ]


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


# ---------------------------------------------------------------------------
# single-writer lock (flock)
# ---------------------------------------------------------------------------


class _FakeStorage:
    def close(self) -> None:
        pass


class _FakeFacade:
    pass


def test_acquire_observer_lock_returns_fd(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    fd = acquire_observer_lock(cfg)
    assert fd is not None
    assert lock_file(cfg).exists()
    os.close(fd)


def test_acquire_observer_lock_fails_when_held(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    fd1 = acquire_observer_lock(cfg)
    assert fd1 is not None
    try:
        assert acquire_observer_lock(cfg) is None
    finally:
        os.close(fd1)
    # Kernel releases the flock on close: the lock is acquirable again.
    fd2 = acquire_observer_lock(cfg)
    assert fd2 is not None
    os.close(fd2)


def test_observe_run_fails_loud_when_lock_held(tmp_path, monkeypatch) -> None:
    """A second ``observe run`` fails loud BEFORE building the facade.

    Regression: a competing foreground run used to bind over the live socket
    (``serve_forever`` unlinks and re-binds) and steal it silently. The flock
    makes the loser exit 95 without touching the facade.
    """
    cfg = _cfg_observe(tmp_path)
    fd = acquire_observer_lock(cfg)
    assert fd is not None
    try:

        def _boom(*args, **kwargs):
            raise AssertionError("loser must not build the facade")

        monkeypatch.setattr("seahorse.facade.factory.build_facade", _boom)
        with pytest.raises(CliObserverRunning):
            run_observe_run(cfg, fmt="human", out=_out())
    finally:
        os.close(fd)


def test_observe_run_acquires_lock_and_releases(tmp_path, monkeypatch) -> None:
    """The winner holds the lock only for the run; it releases on return."""
    cfg = _cfg_observe(tmp_path)
    ran = {}

    def _fake_run_observer(facade, queue, config, *, socket_path, token, **kwargs):
        ran["socket_path"] = socket_path
        ran["token"] = token

    monkeypatch.setattr(
        "seahorse.facade.factory.build_facade",
        lambda *a, **k: (_FakeFacade(), _FakeStorage()),
    )
    monkeypatch.setattr("seahorse.observe.runner.run_observer", _fake_run_observer)
    run_observe_run(cfg, fmt="human", out=_out())
    assert ran["token"] is None  # ObserveConfig default
    # The lock was released: it is acquirable again.
    fd = acquire_observer_lock(cfg)
    assert fd is not None
    os.close(fd)


# ---------------------------------------------------------------------------
# respawn from the hook path (_ensure_running / _wait_for_observer)
# ---------------------------------------------------------------------------


def test_ensure_running_noop_when_pid_alive(tmp_path, monkeypatch) -> None:
    cfg = _cfg_observe(tmp_path)
    _write_pid(cfg, os.getpid())
    spawned = {}

    def _boom(cfg_arg):
        spawned["called"] = True

    monkeypatch.setattr("seahorse.observe.cli._spawn_observer", _boom)
    _ensure_running(cfg)
    assert spawned == {}  # zero cost on the healthy path


def test_ensure_running_spawns_when_pid_dead(tmp_path, monkeypatch) -> None:
    cfg = _cfg_observe(tmp_path)
    _write_pid(cfg, 999999999)  # dead pid

    def _fake_spawn(cfg_arg):
        assert cfg_arg is cfg
        return 4244

    monkeypatch.setattr("seahorse.observe.cli._spawn_observer", _fake_spawn)
    _ensure_running(cfg)  # must not raise


def test_ensure_running_noop_without_observe_config(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)  # no ObserveConfig

    def _boom(cfg_arg):
        raise AssertionError("must not spawn without [observe]")

    monkeypatch.setattr("seahorse.observe.cli._spawn_observer", _boom)
    _ensure_running(cfg)


def test_ensure_running_swallows_spawn_errors(tmp_path, monkeypatch) -> None:
    """The hook respawn must never raise: CliError, OSError, all swallowed."""
    for exc in (CliError(exit_code=CLI_CONFIG_INVALID, name="X", detail="d"), OSError("no")):
        cfg = _cfg_observe(tmp_path)
        monkeypatch.setattr(
            "seahorse.observe.cli._spawn_observer", lambda _c, _e=exc: (_ for _ in ()).throw(_e)
        )
        _ensure_running(cfg)  # must not raise


def test_wait_for_observer_true_when_socket_appears(tmp_path, monkeypatch) -> None:
    cfg = _cfg_observe(tmp_path)

    def _appear():
        socket_path(cfg).parent.mkdir(parents=True, exist_ok=True)
        socket_path(cfg).touch()

    # The socket appears after the first poll: a timer, not a fixture race.
    import threading

    t = threading.Timer(0.005, _appear)
    t.start()
    try:
        assert _wait_for_observer(cfg, wait_s=1.0, poll_s=0.01)
    finally:
        t.join()


def test_wait_for_observer_false_on_budget(tmp_path) -> None:
    cfg = _cfg_observe(tmp_path)
    assert not _wait_for_observer(cfg, wait_s=0.05, poll_s=0.01)


def test_event_respawns_and_retries_on_session_start(tmp_path, monkeypatch) -> None:
    """SessionStart with a dead observer: ensure → wait → one retry POST."""
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "SessionStart")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-e2e")
    cfg = _cfg_observe(tmp_path)
    posts: list = []
    spawns: list = []

    def _stateful_post(cfg_arg, raw):
        posts.append(raw)
        return (0, "observer not running") if len(posts) == 1 else (200, "")

    monkeypatch.setattr("seahorse.observe.cli._post_event", _stateful_post)
    monkeypatch.setattr(
        "seahorse.observe.cli._spawn_observer", lambda cfg_arg: spawns.append(1) or 4245
    )
    # The socket "appears" right after the spawn (the fake child binds).
    sock = tmp_path / ".seahorse" / "observer.sock"
    monkeypatch.setattr(
        "seahorse.observe.cli._wait_for_observer",
        lambda _cfg, *, wait_s, poll_s: (sock.touch() or True),
    )
    injected: list = []
    _capture_inject(monkeypatch, injected)
    out = _out()
    run_observe_event(cfg, fmt="human", out=out)
    assert len(posts) == 2  # initial POST + one advisory retry
    assert len(spawns) == 1
    assert injected == [cfg]  # SessionStart always injects, capture succeeded too


def test_event_no_respawn_when_post_succeeds(tmp_path, monkeypatch) -> None:
    """Healthy path cost is zero: a 200 POST never touches pid nor spawn."""
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "SessionStart")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-healthy")

    def _boom(cfg_arg):
        raise AssertionError("healthy path must not spawn")

    monkeypatch.setattr("seahorse.observe.cli._spawn_observer", _boom)
    monkeypatch.setattr("seahorse.observe.cli._ensure_running", _boom)
    posted: list = []
    _capture_post(monkeypatch, posted)
    injected: list = []
    _capture_inject(monkeypatch, injected)
    run_observe_event(_cfg_observe(tmp_path), fmt="human", out=_out())
    assert len(posted) == 1
    assert len(injected) == 1  # injection is independent of the capture plane


def test_event_respawns_on_oserror_mid_session_without_wait(tmp_path, monkeypatch) -> None:
    """Mid-session OSError: respawn fire-and-forget — no wait, no retry."""
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "UserPromptSubmit")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-mid")
    monkeypatch.setenv("CLAUDE_PROMPT", "x")
    cfg = _cfg_observe(tmp_path)
    spawns: list = []
    waited = {}

    def _oserror_post(cfg_arg, raw):
        raise OSError("socket gone")

    monkeypatch.setattr("seahorse.observe.cli._post_event", _oserror_post)
    monkeypatch.setattr(
        "seahorse.observe.cli._spawn_observer", lambda cfg_arg: spawns.append(1) or 4246
    )

    def _no_wait(_cfg, *, wait_s, poll_s):
        waited["called"] = True
        return True

    monkeypatch.setattr("seahorse.observe.cli._wait_for_observer", _no_wait)
    run_observe_event(cfg, fmt="human", out=_out())
    assert len(spawns) == 1
    assert waited == {}  # no wait on mid-session events


def test_event_no_respawn_on_non_200(tmp_path, monkeypatch) -> None:
    """A 400/401 means the worker is alive: never respawn, never retry."""
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "SessionStart")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-auth")

    def _boom(cfg_arg):
        raise AssertionError("non-200 is a live worker — do not respawn")

    monkeypatch.setattr("seahorse.observe.cli._spawn_observer", _boom)

    def _unauthorized(cfg_arg, raw):
        return 401, "bad token"

    monkeypatch.setattr("seahorse.observe.cli._post_event", _unauthorized)
    injected: list = []
    _capture_inject(monkeypatch, injected)
    run_observe_event(_cfg_observe(tmp_path), fmt="human", out=_out())
    assert len(injected) == 1  # a live-but-rejecting worker still gets injection


# --- lossless spool (design review post-v1.0, 3B) ------------------------------


def test_event_spools_when_delivery_fails(tmp_path, monkeypatch) -> None:
    """A mid-session POST failure (status 0) spools the envelope — the event
    survives the observer downtime and is drained into the queue at the next
    startup. Used to be lost: the envelope existed only in memory."""
    import json

    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "UserPromptSubmit")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-spool")
    monkeypatch.setenv("CLAUDE_PROMPT", "remember me")
    cfg = _cfg_observe(tmp_path)
    monkeypatch.setattr("seahorse.observe.cli._post_event", lambda _c, _r: (0, "down"))
    monkeypatch.setattr("seahorse.observe.cli._spawn_observer", lambda _c: 4247)
    run_observe_event(cfg, fmt="human", out=_out())
    files = list(spool_dir(cfg).glob("*.json"))
    assert len(files) == 1
    raw = json.loads(files[0].read_text(encoding="utf-8"))
    assert raw["session_id"] == "sess-spool"
    assert raw["payload"] == {"prompt": "remember me"}


def test_event_no_spool_when_delivered(tmp_path, monkeypatch) -> None:
    """A 200 POST means the event reached the queue (durable) — nothing to
    spool, zero cost on the healthy path."""
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "UserPromptSubmit")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-ok")
    monkeypatch.setenv("CLAUDE_PROMPT", "hello")
    cfg = _cfg_observe(tmp_path)

    def _boom(_path):
        raise AssertionError("healthy path must not touch the spool")

    monkeypatch.setattr("seahorse.observe.cli._post_event", lambda _c, _r: (200, ""))
    monkeypatch.setattr("seahorse.observe.cli.spool_event", _boom)
    run_observe_event(cfg, fmt="human", out=_out())
    assert not spool_dir(cfg).exists()


def test_event_no_spool_when_worker_rejects(tmp_path, monkeypatch) -> None:
    """A 400/401 means the worker is ALIVE and explicitly rejected the
    envelope — spooling it would create a file that can never be delivered
    (a poison file). The worker logs its own errors."""
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "UserPromptSubmit")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-reject")
    cfg = _cfg_observe(tmp_path)
    monkeypatch.setattr("seahorse.observe.cli._post_event", lambda _c, _r: (400, "bad"))
    run_observe_event(cfg, fmt="human", out=_out())
    assert not spool_dir(cfg).exists() or list(spool_dir(cfg).glob("*.json")) == []


def test_event_spools_when_session_start_retry_also_fails(
    tmp_path, monkeypatch
) -> None:
    """SessionStart where even the advisory retry cannot reach the worker:
    the envelope is spooled (both attempts failed)."""
    import json

    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "SessionStart")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-ss")
    cfg = _cfg_observe(tmp_path)
    monkeypatch.setattr("seahorse.observe.cli._post_event", lambda _c, _r: (0, "down"))
    monkeypatch.setattr("seahorse.observe.cli._spawn_observer", lambda _c: 4248)
    monkeypatch.setattr(
        "seahorse.observe.cli._wait_for_observer", lambda _c, *, wait_s, poll_s: True
    )
    injected: list = []
    _capture_inject(monkeypatch, injected)
    run_observe_event(cfg, fmt="human", out=_out())
    files = list(spool_dir(cfg).glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8"))["session_id"] == "sess-ss"
    assert injected == [cfg]  # injection is independent of the capture plane


def test_event_missing_observe_config_is_silent_noop(tmp_path, monkeypatch) -> None:
    """Regression: without [observe], ``socket_path`` raised AttributeError.

    The hook only caught OSError, so the process exited non-zero and the
    Claude Code session saw a hook failure. The guard makes it exit 0.
    """
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "SessionStart")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-noobs")
    posted: list = []
    _capture_post(monkeypatch, posted)
    run_observe_event(_cfg(tmp_path), fmt="human", out=_out())  # no [observe]
    assert posted == []


# ---------------------------------------------------------------------------
# context injection (SessionStart → hookSpecificOutput)
# ---------------------------------------------------------------------------


def _capture_inject(monkeypatch, calls: list) -> None:
    def _fake_inject(cfg, out):
        calls.append(cfg)

    monkeypatch.setattr("seahorse.observe.cli._inject_context", _fake_inject)


def test_session_start_emits_hook_specific_output_json(tmp_path, monkeypatch) -> None:
    """SessionStart writes exactly one stdout line: the hookSpecificOutput JSON."""
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "SessionStart")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-inject")
    cfg = _cfg_observe(tmp_path)
    posted: list = []
    _capture_post(monkeypatch, posted)
    monkeypatch.setattr(
        "seahorse.observe.cli.subprocess.run",
        lambda *a, **k: _FakeCompleted(returncode=0, stdout=b"Seahorse memory context\n"),
    )
    out = _out()
    run_observe_event(cfg, fmt="human", out=out)
    import json

    lines = out.getvalue().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Seahorse memory context" in payload["hookSpecificOutput"]["additionalContext"]


def test_session_start_no_injection_on_nonzero_exit(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "SessionStart")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-fail")
    _capture_post(monkeypatch, [])
    monkeypatch.setattr(
        "seahorse.observe.cli.subprocess.run",
        lambda *a, **k: _FakeCompleted(returncode=1, stdout=b"boom"),
    )
    out = _out()
    run_observe_event(_cfg_observe(tmp_path), fmt="human", out=out)
    assert out.getvalue() == ""


def test_session_start_no_injection_on_timeout(tmp_path, monkeypatch) -> None:
    import subprocess as subprocess_module

    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "SessionStart")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-timeout")
    _capture_post(monkeypatch, [])

    def _hang(*a, **k):
        raise subprocess_module.TimeoutExpired(cmd="seahorse", timeout=2.0)

    monkeypatch.setattr("seahorse.observe.cli.subprocess.run", _hang)
    out = _out()
    run_observe_event(_cfg_observe(tmp_path), fmt="human", out=out)
    assert out.getvalue() == ""  # degrades to no injection, never raises


def test_session_start_no_injection_on_empty_output(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "SessionStart")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-empty")
    _capture_post(monkeypatch, [])
    monkeypatch.setattr(
        "seahorse.observe.cli.subprocess.run",
        lambda *a, **k: _FakeCompleted(returncode=0, stdout=b"   \n"),
    )
    out = _out()
    run_observe_event(_cfg_observe(tmp_path), fmt="human", out=out)
    assert out.getvalue() == ""


def test_non_session_start_events_never_emit_stdout(tmp_path, monkeypatch) -> None:
    """The hookSpecificOutput gate is load-bearing: only SessionStart emits."""
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "UserPromptSubmit")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-gate")
    monkeypatch.setenv("CLAUDE_PROMPT", "x")
    _capture_post(monkeypatch, [])
    monkeypatch.setattr(
        "seahorse.observe.cli.subprocess.run",
        lambda *a, **k: _FakeCompleted(returncode=0, stdout=b"leak"),
    )
    out = _out()
    run_observe_event(_cfg_observe(tmp_path), fmt="human", out=out)
    assert out.getvalue() == ""


def test_session_start_injects_even_when_observer_down(tmp_path, monkeypatch) -> None:
    """Injection is independent of the capture plane: down worker still injects."""
    monkeypatch.setenv("CLAUDE_HOOK_EVENT_NAME", "SessionStart")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-down")
    cfg = _cfg_observe(tmp_path)

    def _dead_post(cfg_arg, raw):
        return 0, "observer not running"

    monkeypatch.setattr("seahorse.observe.cli._post_event", _dead_post)
    monkeypatch.setattr("seahorse.observe.cli._spawn_observer", lambda cfg_arg: 4247)
    monkeypatch.setattr(
        "seahorse.observe.cli._wait_for_observer", lambda _cfg, *, wait_s, poll_s: False
    )
    monkeypatch.setattr(
        "seahorse.observe.cli.subprocess.run",
        lambda *a, **k: _FakeCompleted(returncode=0, stdout=b"Seahorse memory context"),
    )
    out = _out()
    run_observe_event(cfg, fmt="human", out=out)
    import json

    payload = json.loads(out.getvalue())
    assert "Seahorse memory context" in payload["hookSpecificOutput"]["additionalContext"]


def test_context_command_vault_precedes_subcommand(tmp_path) -> None:
    """--vault is a GLOBAL option and must precede the ``context`` subcommand."""
    cfg = _cfg_observe(tmp_path)
    cmd = _context_command(cfg)
    assert cmd.index("--vault") < cmd.index("context")
    assert str(cfg.vault) in cmd
    assert "seahorse.cli.app" in cmd
