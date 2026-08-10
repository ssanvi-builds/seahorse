"""Observer CLI commands — ``seahorse observe start|stop|status|run``.

The observer is a single-writer background process (obsiforge §4.5):
- ``start`` — spawns ``observe run`` as a detached subprocess, writes the PID.
- ``stop`` — SIGTERM the PID, removes the PID file.
- ``status`` — reports whether the observer is running.
- ``run`` — the foreground process (endpoint thread + worker drain loop).

The PID file lives in ``{seahorse_dir}/observer/observer.pid``. A second
``start`` while running fails loud (``CliObserverRunning``, exit 95) — a
competing writer would break single-writer semantics.

References:
- obsiforge-evolution-architecture.md §4.5 (single-writer queue)
- seahorse/cli/errors.py (CliObserverRunning)
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Literal, TextIO, cast

from seahorse.cli.config import SeahorseConfig
from seahorse.cli.errors import CliError, CliObserverRunning
from seahorse.cli.output import OutputFormat
from seahorse.observe.worker import ObserverConfig

OBSERVER_DIR_NAME = "observer"
PID_FILENAME = "observer.pid"
LOG_FILENAME = "observer.log"
QUEUE_FILENAME = "observer.db"


def observer_dir(cfg: SeahorseConfig) -> Path:
    """The observer's own directory: ``{seahorse_dir}/observer/``."""
    return cfg.seahorse_dir / OBSERVER_DIR_NAME


def pid_file(cfg: SeahorseConfig) -> Path:
    return observer_dir(cfg) / PID_FILENAME


def queue_path(cfg: SeahorseConfig) -> Path:
    return observer_dir(cfg) / QUEUE_FILENAME


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


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def run_observe_status(cfg: SeahorseConfig, *, fmt: OutputFormat, out: TextIO) -> None:
    """Report whether the observer is running."""
    pid = _read_pid(cfg)
    running = pid is not None and _pid_alive(pid)
    _emit(cfg, fmt, out, {"running": running, "pid": pid if running else None})


def run_observe_start(cfg: SeahorseConfig, *, fmt: OutputFormat, out: TextIO) -> None:
    """Spawn the observer as a detached subprocess (single-writer, §4.5)."""
    pid = _read_pid(cfg)
    if pid is not None and _pid_alive(pid):
        raise CliObserverRunning(pid)
    log_path = observer_dir(cfg) / LOG_FILENAME
    observer_dir(cfg).mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "seahorse.cli.app",
                "observe",
                "run",
                "--vault",
                str(cfg.vault),
            ],
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    _write_pid(cfg, proc.pid)
    if fmt == "human":
        out.write(f"observer: started (pid {proc.pid})\n")
    else:
        out.write(json.dumps({"started": True, "pid": proc.pid}) + "\n")


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


def run_observe_run(cfg: SeahorseConfig, *, fmt: OutputFormat, out: TextIO) -> None:
    """Run the observer in the foreground (endpoint thread + worker loop).

    Builds the facade + queue from the resolved config and delegates to
    ``run_observer``. The observer is a client of #12 — the engine never sees a
    hook, only ``RememberPayload``. Requires the ``[observe]`` section (written
    by ``seahorse setup``); a missing section fails loud (ADR-10).
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
        )
    finally:
        queue.close()
        storage.close()


__all__ = [
    "observer_dir",
    "pid_file",
    "queue_path",
    "socket_path",
    "run_observe_status",
    "run_observe_start",
    "run_observe_stop",
    "run_observe_run",
]
