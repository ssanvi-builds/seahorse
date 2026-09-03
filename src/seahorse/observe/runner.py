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
import signal
import threading
import time
from pathlib import Path
from typing import Any

from seahorse.observe.endpoint import ObserverEndpoint, validate_socket_path
from seahorse.observe.queue import ObserverQueue
from seahorse.observe.spool import drain_spool
from seahorse.observe.worker import ObserverConfig, ObserverWorker

_logger = logging.getLogger("seahorse.observe.runner")


def _on_sigterm(signum: int, frame: Any) -> None:
    """Route SIGTERM through KeyboardInterrupt so the endpoint cleanup runs."""
    raise KeyboardInterrupt


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
    spool_dir: Path | None = None,
) -> None:
    """Run the observer: serve the endpoint (thread) + drain the queue (main).

    The endpoint thread receives envelopes on the unix socket; the main loop
    drains the queue every ``interval_s`` seconds. Exits on ``stop_event``,
    ``max_drains`` (test control), or KeyboardInterrupt.

    With ``spool_dir`` (lossless spool, 3B), any envelopes the hook spooled
    while the observer was down are enqueued into the queue first — the
    worker is the single DB writer, so the drain happens here, before the
    endpoint thread starts.
    """
    # Fail loud before the endpoint thread starts: a socket path over the
    # AF_UNIX limit would kill the thread silently (daemon) and drop every
    # envelope while the worker keeps draining an empty queue.
    validate_socket_path(socket_path)
    if spool_dir is not None:
        spooled = drain_spool(spool_dir, queue)
        if spooled:
            _logger.info("observe.spool drained=%d", spooled)
    endpoint = ObserverEndpoint(
        queue, socket_path=socket_path, token=token, drop_tools=config.drop_tools
    )
    worker = ObserverWorker(facade, queue, config=config)
    thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
    thread.start()
    drains = 0
    # ``observe stop`` sends plain SIGTERM: without a handler the default
    # action kills the process without unwinding, so the endpoint's ``finally``
    # never unlinks the socket and a stale ``observer.sock`` survives every
    # stop (loop L4a, 2026-09-02). SIGTERM reuses the KeyboardInterrupt path.
    # Only installable from the main thread — tests drive it from workers too.
    prev_sigterm: Any = None
    if threading.current_thread() is threading.main_thread():
        prev_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, _on_sigterm)
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
        if prev_sigterm is not None:
            signal.signal(signal.SIGTERM, prev_sigterm)
        endpoint.shutdown()
        thread.join(timeout=2)


__all__ = ["run_observer"]
