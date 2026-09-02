"""Temp workspace allocation for benchmark runs, with exit-time cleanup.

Every ``mkdtemp`` in this package goes through ``mkdtemp_scoped`` so a run
cannot leak its workspace: the L11 hardening loop found 209 stale
``seahorse-bench-``/``-template-``/``-decay-``... directories (~313MB)
accumulated on a quota'd tmpfs. Cleanup registers once with ``atexit`` so it
covers both the success path and the error paths (where the leak was worst).
``SIGKILL`` still leaks — nothing can clean up after it.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path

_SCOPED: list[Path] = []
_REGISTERED = False


def _cleanup_scoped() -> None:
    for path in _SCOPED:
        shutil.rmtree(path, ignore_errors=True)
    _SCOPED.clear()


def mkdtemp_scoped(prefix: str) -> Path:
    """``tempfile.mkdtemp`` whose directory is removed at interpreter exit."""
    global _REGISTERED
    if not _REGISTERED:
        atexit.register(_cleanup_scoped)
        _REGISTERED = True
    path = Path(tempfile.mkdtemp(prefix=prefix))
    _SCOPED.append(path)
    return path