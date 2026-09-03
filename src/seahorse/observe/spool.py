"""Hook-side lossless spool (design review post-v1.0, 3B).

When the hook cannot reach the observer socket (status 0 — the worker is down
or dying), the envelope exists only in memory and is lost. The spool is the
durability floor: the hook writes the envelope as a JSON file under
``{seahorse_dir}/observer/spool/``; the observer drains the directory into the
queue DB at startup (the hook writes files; the worker stays the single DB
writer — the ROADMAP "lossless spool" backlog item).

Once an event reaches the queue it is durable (SQLite, ack-after-consume,
fingerprint dedup): the spool covers only the gap between the hook and the
queue. Degradation is honest and bounded — over ``_MAX_FILES`` the hook skips
spooling (an intentionally stopped observer must not grow the directory
without limit), and a spool file that fails to parse can never become valid,
so the drain deletes it (logged).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from seahorse.observe.protocol import EnvelopeError, parse_envelope
from seahorse.observe.queue import ObserverQueue

_logger = logging.getLogger("seahorse.observe.spool")

_MAX_FILES = 1000


def spool_event(spool_dir: Path, raw: dict[str, Any]) -> bool:
    """Persist one hook envelope to the spool directory; False when skipped.

    Atomic write (temp file + ``os.replace``) so the drain never reads a torn
    file. The caller (the hook) treats False as honest degradation — never a
    crash (the hook must never abort the session).
    """
    try:
        spool_dir.mkdir(parents=True, exist_ok=True)
        if sum(1 for _ in spool_dir.glob("*.json")) >= _MAX_FILES:
            return False
        name = f"{os.getpid()}-{uuid.uuid4().hex[:12]}.json"
        tmp = spool_dir / (name + ".tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, spool_dir / name)
        return True
    except OSError:
        return False


def drain_spool(spool_dir: Path, queue: ObserverQueue) -> int:
    """Enqueue every spool file into the queue; return the number enqueued.

    Called by the observer at startup (before the first drain loop) — the
    worker is the single DB writer. A file that parses is enqueued (the
    queue's fingerprint dedup makes a re-spooled duplicate a no-op) and
    deleted; a file that fails to parse can never become valid and is deleted
    (logged); a file that fails to enqueue (transient DB error) is left for
    the next startup.
    """
    if not spool_dir.is_dir():
        return 0
    drained = 0
    for path in sorted(spool_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            envelope = parse_envelope(raw)
        except (OSError, ValueError, EnvelopeError):
            _logger.warning("observe.spool discarding unparseable file %s", path.name)
            with contextlib.suppress(OSError):
                path.unlink()
            continue
        try:
            queue.enqueue(envelope)
        except (OSError, sqlite3.Error):
            _logger.warning("observe.spool deferring file %s", path.name)
            continue
        with contextlib.suppress(OSError):
            path.unlink()
        drained += 1
    return drained


__all__ = ["spool_event", "drain_spool"]