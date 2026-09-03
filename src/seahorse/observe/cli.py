"""Observer CLI commands — ``seahorse observe start|stop|status|run``.

The observer is a single-writer background process:
- ``start`` — spawns ``observe run`` as a detached subprocess, writes the PID.
- ``stop`` — SIGTERM the PID, removes the PID file.
- ``status`` — reports whether the observer is running.
- ``run`` — the foreground process (endpoint thread + worker drain loop).

The PID file lives in ``{seahorse_dir}/observer/observer.pid``. A second
``start`` while running fails loud (``CliObserverRunning``, exit 95) — a
competing writer would break single-writer semantics.

References:
- seahorse/cli/errors.py (CliObserverRunning)
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Literal, TextIO, cast

from seahorse.cli.config import SeahorseConfig
from seahorse.cli.errors import CliError, CliObserverRunning
from seahorse.cli.exit_codes import CLI_CONFIG_INVALID
from seahorse.cli.output import OutputFormat
from seahorse.observe.endpoint import validate_socket_path
from seahorse.observe.spool import spool_event
from seahorse.observe.worker import ObserverConfig

OBSERVER_DIR_NAME = "observer"
PID_FILENAME = "observer.pid"
LOG_FILENAME = "observer.log"
QUEUE_FILENAME = "observer.db"
LOCK_FILENAME = "observer.lock"
SPOOL_DIRNAME = "spool"
_LOCK_MODE = 0o600

# Hook-path tuning. The SessionStart respawn waits at most this long for the
# child to bind its socket before giving up (degrade honestly — the session
# starts regardless); mid-session hooks spawn fire-and-forget instead.
_POST_TIMEOUT_S = 5.0
_ENSURE_RUNNING_WAIT_S = 1.0
_ENSURE_RUNNING_POLL_INTERVAL_S = 0.1
_CONTEXT_TIMEOUT_S = 2.0


def observer_dir(cfg: SeahorseConfig) -> Path:
    """The observer's own directory: ``{seahorse_dir}/observer/``."""
    return cfg.seahorse_dir / OBSERVER_DIR_NAME


def pid_file(cfg: SeahorseConfig) -> Path:
    return observer_dir(cfg) / PID_FILENAME


def lock_file(cfg: SeahorseConfig) -> Path:
    return observer_dir(cfg) / LOCK_FILENAME


def queue_path(cfg: SeahorseConfig) -> Path:
    return observer_dir(cfg) / QUEUE_FILENAME


def spool_dir(cfg: SeahorseConfig) -> Path:
    """The hook-side spool directory (``{observer_dir}/spool/``).

    The hook writes undeliverable envelopes here (lossless spool, 3B); the
    observer drains the directory into the queue DB at startup.
    """
    return observer_dir(cfg) / SPOOL_DIRNAME


def socket_path(cfg: SeahorseConfig) -> Path:
    """The unix socket path (``socket_path`` is relative to ``.seahorse/``).

    Precondition: ``cfg.observe`` is not None (the observer is set up).
    """
    return cfg.seahorse_dir / cfg.observe.socket_path  # type: ignore[union-attr]


def _read_pid(cfg: SeahorseConfig) -> int | None:
    path = pid_file(cfg)
    if not path.is_file():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def observer_liveness(cfg: SeahorseConfig) -> tuple[bool, int | None]:
    """``(running, pid)`` from the pid file + a kernel liveness check.

    Socket presence is NOT liveness: a killed worker leaves the socket file
    behind until the next start unlinks it. Callers that must distinguish
    "running" from "stale socket" (status, doctor) go through here.
    """
    pid = _read_pid(cfg)
    running = pid is not None and _pid_alive(pid)
    return running, pid if running else None


def _write_pid(cfg: SeahorseConfig, pid: int) -> None:
    observer_dir(cfg).mkdir(parents=True, exist_ok=True)
    pid_file(cfg).write_text(str(pid), encoding="utf-8")


def _remove_pid(cfg: SeahorseConfig) -> None:
    path = pid_file(cfg)
    if path.exists():
        path.unlink()


def _emit(cfg: SeahorseConfig, fmt: OutputFormat, out: TextIO, payload: dict) -> None:
    if fmt == "human":
        if payload["running"]:
            out.write(f"observer: running (pid {payload['pid']})\n")
        else:
            out.write("observer: not running\n")
    else:
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")


def acquire_observer_lock(cfg: SeahorseConfig) -> int | None:
    """Take the observer single-writer lock with ``flock``; None if held.

    The lock is an advisory ``flock`` on ``{observer_dir}/observer.lock``
    (mode 0o600). The kernel releases it when the holder dies, so an orphaned
    ``.lock`` file is harmless — liveness comes from the kernel, not the file.
    The returned fd is the lock; keep it open for the writer's lifetime and
    ``os.close(fd)`` to release.
    """
    observer_dir(cfg).mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_file(cfg), os.O_CREAT | os.O_RDWR, _LOCK_MODE)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def run_observe_status(cfg: SeahorseConfig, *, fmt: OutputFormat, out: TextIO) -> None:
    """Report whether the observer is running."""
    running, pid = observer_liveness(cfg)
    _emit(cfg, fmt, out, {"running": running, "pid": pid})


def _spawn_observer(cfg: SeahorseConfig) -> int:
    """Spawn ``observe run`` as a detached subprocess; return the child pid.

    Shared by ``observe start`` (user-facing, fails loud) and the hook respawn
    path (``_ensure_running``, swallows errors). Validates the socket path
    first: a vault over the AF_UNIX limit would otherwise spawn a child whose
    endpoint thread dies silently while ``observe status`` reports "running".
    """
    # Fail loud before spawning: a socket path over the AF_UNIX limit would
    # kill the child's endpoint thread silently and drop every envelope while
    # ``observe status`` still reports "running". (Only when [observe] is
    # configured — without it the child fails loud on its own.)
    if cfg.observe is not None:
        try:
            validate_socket_path(socket_path(cfg))
        except ValueError as exc:
            raise CliError(
                exit_code=CLI_CONFIG_INVALID,
                name="CLI_CONFIG_INVALID",
                detail=str(exc),
            ) from exc
    log_path = observer_dir(cfg) / LOG_FILENAME
    observer_dir(cfg).mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "seahorse.cli.app",
                # --vault is a GLOBAL option and must precede the subcommand.
                "--vault",
                str(cfg.vault),
                "observe",
                "run",
            ],
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    _write_pid(cfg, proc.pid)
    return proc.pid


def run_observe_start(cfg: SeahorseConfig, *, fmt: OutputFormat, out: TextIO) -> None:
    """Spawn the observer as a detached subprocess (single-writer)."""
    pid = _read_pid(cfg)
    if pid is not None and _pid_alive(pid):
        raise CliObserverRunning(pid)
    child_pid = _spawn_observer(cfg)
    if fmt == "human":
        out.write(f"observer: started (pid {child_pid})\n")
    else:
        out.write(json.dumps({"started": True, "pid": child_pid}) + "\n")


def run_observe_stop(cfg: SeahorseConfig, *, fmt: OutputFormat, out: TextIO) -> None:
    """Stop the observer (SIGTERM) and remove the PID file."""
    pid = _read_pid(cfg)
    if pid is None or not _pid_alive(pid):
        _remove_pid(cfg)
        _emit(cfg, fmt, out, {"running": False, "pid": None})
        return
    os.kill(pid, signal.SIGTERM)
    _remove_pid(cfg)
    if fmt == "human":
        out.write(f"observer: stopped (pid {pid})\n")
    else:
        out.write(json.dumps({"stopped": True, "pid": pid}) + "\n")


# Hook event name → envelope event_type (Claude Code hook env vars).
_HOOK_EVENT_TYPES: dict[str, str] = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "user_prompt_submit",
    "PostToolUse": "post_tool_use",
    "Stop": "stop",
}


def _build_payload(event_name: str) -> dict:
    """Build the envelope payload from the Claude Code hook env vars."""
    if event_name == "UserPromptSubmit":
        return {"prompt": os.environ.get("CLAUDE_PROMPT", "")}
    if event_name == "PostToolUse":
        return {
            "tool_name": os.environ.get("CLAUDE_TOOL_NAME", ""),
            "tool_use_id": os.environ.get("CLAUDE_TOOL_USE_ID", ""),
            "tool_input": os.environ.get("CLAUDE_TOOL_INPUT", ""),
            "tool_response": os.environ.get("CLAUDE_TOOL_RESPONSE", ""),
        }
    return {}


def _ensure_running(cfg: SeahorseConfig) -> None:
    """Respawn a dead observer from the hook path. Never raises.

    Cheap checks only: without ``[observe]`` there is nothing to spawn, and a
    live pid means the worker is up (a missing socket alone can mean "still
    binding" — the caller's wait handles that). Spawn errors (invalid socket
    path, fork failure) are swallowed: the hook must never abort the session.
    """
    if cfg.observe is None:
        return
    pid = _read_pid(cfg)
    if pid is not None and _pid_alive(pid):
        return
    try:
        _spawn_observer(cfg)
    except (CliError, OSError, ValueError):
        return


def _wait_for_observer(cfg: SeahorseConfig, *, wait_s: float, poll_s: float) -> bool:
    """Poll for the observer socket to appear; False once the budget expires.

    Readiness signal is the same one ``_post_event`` uses (``socket_path``
    existence). Known limitation: the child takes the lock and builds the
    facade before binding, so with a large or cold DB the socket may not
    appear within the budget — the caller degrades honestly.
    """
    import time

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if socket_path(cfg).exists():
            return True
        time.sleep(poll_s)
    return socket_path(cfg).exists()


def _post_event(cfg: SeahorseConfig, raw: dict) -> tuple[int, str]:
    """POST an envelope to the observer unix socket; return (status, message)."""
    import http.client
    import socket as socket_module

    sp = socket_path(cfg)
    if not sp.exists():
        return 0, "observer not running"

    class _UnixHTTPConnection(http.client.HTTPConnection):
        def __init__(self, socket_path: str, timeout: float = _POST_TIMEOUT_S) -> None:
            super().__init__("localhost", timeout=timeout)
            self._socket_path = socket_path

        def connect(self) -> None:
            self.sock = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
            self.sock.connect(self._socket_path)

    headers = {"Content-Type": "application/json"}
    if cfg.observe is not None and cfg.observe.token:
        headers["X-Seahorse-Token"] = cfg.observe.token
    conn = _UnixHTTPConnection(str(sp))
    try:
        conn.request("POST", "/event", body=json.dumps(raw).encode(), headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.read().decode()
    finally:
        conn.close()


def _context_command(cfg: SeahorseConfig) -> list[str]:
    """The ``seahorse context`` command line — ``--vault`` precedes the subcommand."""
    return [
        sys.executable,
        "-m",
        "seahorse.cli.app",
        "--vault",
        str(cfg.vault),
        "context",
    ]


def _inject_context(cfg: SeahorseConfig, out: TextIO) -> None:
    """Emit the SessionStart ``hookSpecificOutput`` with the bootstrap context.

    Runs ``seahorse context`` as a subprocess, not in-process: a hard timeout
    (a hung SQLite would otherwise stall session start with no ceiling), crash
    isolation, and the exact CLI output — no render drift between the README's
    promise and the injection. Degrades to emitting nothing on timeout, spawn
    failure, non-zero exit, or empty output. Never raises; writes nothing on
    failure. With ``--quiet`` the write lands in the discard sink (consistent
    with quiet semantics; the installed hook never passes --quiet).
    """
    try:
        proc = subprocess.run(
            _context_command(cfg),
            capture_output=True,
            timeout=_CONTEXT_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return
    if proc.returncode != 0:
        return
    text = proc.stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }
    out.write(json.dumps(payload, ensure_ascii=False) + "\n")


def run_observe_event(cfg: SeahorseConfig, *, fmt: OutputFormat, out: TextIO) -> None:
    """POST a hook event to the observer socket (called by the Claude Code hooks).

    Reads the hook env vars (``CLAUDE_HOOK_EVENT_NAME`` / ``CLAUDE_SESSION_ID``
    / ``CLAUDE_PROMPT`` / ``CLAUDE_TOOL_*``) and POSTs the envelope to the unix
    socket. The hook must NEVER abort the agent session: any observer failure
    is a silent no-op (exit 0).

    Self-healing: when the POST cannot reach the worker (socket absent →
    status 0, or OSError), the hook respawns a dead observer. On SessionStart
    it waits up to ``_ENSURE_RUNNING_WAIT_S`` for the socket and retries once
    (advisory); mid-session events respawn fire-and-forget — the next hook
    wins. A non-200 status (400/401) means the worker is ALIVE: no respawn.

    Context injection: on SessionStart (regardless of capture success), the
    bootstrap context is emitted as a single ``hookSpecificOutput`` JSON line
    — the only stdout the hook ever produces.
    """
    if cfg.observe is None:
        return  # observer not set up — silent no-op (never abort the session)
    event_name = os.environ.get("CLAUDE_HOOK_EVENT_NAME", "")
    event_type = _HOOK_EVENT_TYPES.get(event_name)
    if event_type is None:
        return  # unknown hook event — the observer ignores it
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if not session_id:
        return  # no session — nothing to capture
    is_session_start = event_name == "SessionStart"
    raw: dict = {
        "session_id": session_id,
        "event_type": event_type,
        "payload": _build_payload(event_name),
    }
    agent_id = os.environ.get("CLAUDE_AGENT_ID")
    if agent_id:
        raw["agent_id"] = agent_id
    try:
        status, _message = _post_event(cfg, raw)
    except OSError:
        status = 0  # observer not reachable — same recovery path as no socket
    if status == 0:
        _ensure_running(cfg)
        if is_session_start and _wait_for_observer(
            cfg, wait_s=_ENSURE_RUNNING_WAIT_S, poll_s=_ENSURE_RUNNING_POLL_INTERVAL_S
        ):
            with contextlib.suppress(OSError):
                status, _message = _post_event(cfg, raw)  # advisory retry
    # Lossless spool (3B): delivery failed everywhere (status 0) — persist the
    # envelope so the observer drains it into the queue at the next startup.
    # A status >= 200 means the worker received (or explicitly rejected) the
    # envelope — it is ALIVE and logs its own errors; nothing to spool.
    if status == 0:
        spool_event(spool_dir(cfg), raw)
    if is_session_start:
        _inject_context(cfg, out)


def run_observe_run(cfg: SeahorseConfig, *, fmt: OutputFormat, out: TextIO) -> None:
    """Run the observer in the foreground (endpoint thread + worker loop).

    Builds the facade + queue from the resolved config and delegates to
    ``run_observer``. The observer is a client of the facade — the engine never
    sees a hook, only ``RememberPayload``. Requires the ``[observe]`` section
    (written by ``seahorse setup``); a missing section fails loud.
    """
    from seahorse.facade.factory import build_facade
    from seahorse.facade.types import FacadeConfig
    from seahorse.observe.queue import ObserverQueue
    from seahorse.observe.runner import run_observer

    if cfg.observe is None:
        raise CliError(
            exit_code=83,
            name="CLI_CONFIG_INVALID",
            detail="observer is not set up; run `seahorse setup` first",
        )
    # Single-writer, enforced by the kernel: a competing foreground run used to
    # bind over the live socket (``serve_forever`` unlinks and re-binds) and
    # steal it silently. Fail loud before building anything.
    lock_fd = acquire_observer_lock(cfg)
    if lock_fd is None:
        raise CliObserverRunning()
    facade, storage = build_facade(
        cfg.db_path,
        config=FacadeConfig(
            default_extraction_mode=cast("Literal['skip', 'llm']", cfg.default_extraction_mode),
            top_k=cfg.top_k,
        ),
    )
    queue = ObserverQueue(queue_path(cfg))
    config = ObserverConfig(
        skip_tools=frozenset(cfg.observe.skip_tools),
        drop_tools=frozenset(cfg.observe.drop_tools),
        extraction_mode=cfg.observe.extraction,
    )
    try:
        run_observer(
            facade,
            queue,
            config,
            socket_path=socket_path(cfg),
            token=cfg.observe.token,
            spool_dir=spool_dir(cfg),
        )
    finally:
        queue.close()
        storage.close()
        os.close(lock_fd)


__all__ = [
    "acquire_observer_lock",
    "observer_dir",
    "pid_file",
    "queue_path",
    "spool_dir",
    "socket_path",
    "lock_file",
    "run_observe_status",
    "run_observe_start",
    "run_observe_stop",
    "run_observe_event",
    "run_observe_run",
]
