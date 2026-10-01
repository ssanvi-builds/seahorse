"""PID-file helpers for CLI-managed background processes.

Extracted from the observer's process machinery (``seahorse.observe.cli``)
so every CLI-managed daemon — the observer today, ``seahorse remote`` next —
shares ONE implementation of the pid-file format (an integer pid, one
line, utf-8) and the kernel liveness semantics.

The functions are path-based, not config-based: the caller decides where
the pid file lives; this module only knows the file format and the
liveness check (signal 0 — a ``PermissionError`` means "alive but not
ours", which counts as running so a stop never kills a foreign process).
"""

from __future__ import annotations

import os
from pathlib import Path


def read_pid(path: Path) -> int | None:
    """The pid recorded in ``path``, or ``None`` when absent or corrupt."""
    if not path.is_file():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def pid_alive(pid: int) -> bool:
    """Kernel liveness via signal 0.

    ``ProcessLookupError`` — the pid is gone. ``PermissionError`` — the
    process exists but belongs to another user; it is alive.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def write_pid(path: Path, pid: int) -> None:
    """Record ``pid`` in ``path``, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def remove_pid(path: Path) -> None:
    """Unlink ``path`` when present (idempotent)."""
    if path.exists():
        path.unlink()