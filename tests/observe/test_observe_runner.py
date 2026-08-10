"""Tests for ``seahorse.observe.runner`` — the observer process.

The observer process = HTTP endpoint (thread, receives envelopes on the unix
socket) + worker drain loop (main). ``run_observer`` is the foreground runner
that ``seahorse observe start`` spawns in the background. ``max_drains`` /
``stop_event`` make it testable without killing a real process.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import tempfile
import threading
import time

from seahorse.facade.factory import build_facade
from seahorse.observe.protocol import EVENT_USER_PROMPT_SUBMIT
from seahorse.observe.queue import ObserverQueue
from seahorse.observe.runner import run_observer
from seahorse.observe.worker import ObserverConfig

_socket_counter = 0


def _short_socket_path() -> str:
    global _socket_counter
    _socket_counter += 1
    return os.path.join(tempfile.gettempdir(), f"obs-run-{os.getpid()}-{_socket_counter}.sock")


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 5) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self._socket_path)


def _post(socket_path, body: dict) -> int:
    conn = _UnixHTTPConnection(str(socket_path))
    try:
        conn.request(
            "POST",
            "/event",
            body=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        return conn.getresponse().status
    finally:
        conn.close()


def test_run_observer_drains_and_writes_episode(tmp_path) -> None:
    facade, storage = build_facade(tmp_path / "seahorse.db")
    queue = ObserverQueue(tmp_path / "observer.db")
    socket_path = _short_socket_path()
    try:
        stop = threading.Event()
        thread = threading.Thread(
            target=run_observer,
            kwargs={
                "facade": facade,
                "queue": queue,
                "config": ObserverConfig(),
                "socket_path": socket_path,
                "stop_event": stop,
                "interval_s": 0.05,
            },
            daemon=True,
        )
        thread.start()
        try:
            for _ in range(50):
                if os.path.exists(socket_path):
                    break
                time.sleep(0.05)
            # POST a full turn: prompt + stop.
            prompt_event = {
                "session_id": "sess-1",
                "event_type": EVENT_USER_PROMPT_SUBMIT,
                "payload": {"prompt": "Fix the flaky recall test"},
            }
            assert _post(socket_path, prompt_event) == 200
            stop_event = {"session_id": "sess-1", "event_type": "stop", "payload": {}}
            assert _post(socket_path, stop_event) == 200
            # Wait for the worker to drain and write the episode.
            for _ in range(50):
                rows = facade.recall("flaky recall", k=10)
                if rows:
                    break
                time.sleep(0.05)
            assert len(facade.recall("flaky recall", k=10)) == 1
        finally:
            stop.set()
            thread.join(timeout=2)
    finally:
        queue.close()
        storage.close()


def test_run_observer_max_drains_exits(tmp_path) -> None:
    facade, storage = build_facade(tmp_path / "seahorse.db")
    queue = ObserverQueue(tmp_path / "observer.db")
    socket_path = _short_socket_path()
    try:
        run_observer(
            facade,
            queue,
            ObserverConfig(),
            socket_path=socket_path,
            max_drains=1,
            interval_s=0.01,
        )
        # Exited after one drain without hanging.
        assert queue.pending_count() == 0
    finally:
        queue.close()
        storage.close()
