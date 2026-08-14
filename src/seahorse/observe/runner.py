"""Observer process — HTTP endpoint (thread) + worker drain loop (main).

The observer is a single process: the endpoint serves envelopes on the unix
socket (a daemon thread), and the worker drains the queue in the main loop.
``seahorse observe start`` spawns this in the background; ``observe run`` runs
it in the foreground.

``max_drains`` / ``stop_event`` are test controls: a real observer runs forever
until SIGTERM/KeyboardInterrupt.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from seahorse.observe.endpoint import ObserverEndpoint
from seahorse.observe.queue import ObserverQueue
from seahorse.observe.worker import ObserverConfig, ObserverWorker

_logger = logging.getLogger("seahorse.observe.runner")


def run_observer(
    facade: Any,
    queue: ObserverQueue,
    config: ObserverConfig,
    *,
    socket_path: Path | str,
    token: str | None = None,
    interval_s: float = 5.0,
    stop_event: threading.Event | None = None,
    max_drains: int | None = None,
) -> None:
    """Run the observer: serve the endpoint (thread) + drain the queue (main).

    The endpoint thread receives envelopes on the unix socket; the main loop
    drains the queue every ``interval_s`` seconds. Exits on ``stop_event``,
    ``max_drains`` (test control), or KeyboardInterrupt.
    """
    endpoint = ObserverEndpoint(queue, socket_path=socket_path, token=token)
    worker = ObserverWorker(facade, queue, config=config)
    thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
    thread.start()
    drains = 0
    try:
        while True:
            report = worker.drain()
            if report.episodes_written or report.failures:
                _logger.info(
                    "observe.drain events=%d written=%d skipped=%d failures=%d",
                    report.events_read,
                    report.episodes_written,
                    report.turns_skipped,
                    report.failures,
                )
            drains += 1
            if max_drains is not None and drains >= max_drains:
                break
            if stop_event is not None and stop_event.is_set():
                break
            time.sleep(interval_s)
    except KeyboardInterrupt:
        pass
    finally:
        endpoint.shutdown()
        thread.join(timeout=2)


__all__ = ["run_observer"]
