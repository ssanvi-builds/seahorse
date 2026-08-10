"""Tests for ``seahorse.observe.endpoint`` — the observer's HTTP endpoint.

The endpoint is the edge where envelopes arrive (the hooks POST to the unix
socket, obsiforge §4.4). It validates auth (token, §15.2 redesign 10), parses
the envelope tolerantly (caps, malformed → 400), REDACTS before enqueue
(nothing raw persisted), and applies ``drop_tools`` (Read/Bash never reach the
queue). The unix socket (dir 0700, socket 0600) is the FS-level auth; the token
is the caller-level auth.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import tempfile
import threading
import time

import pytest

from seahorse.observe.endpoint import ObserverEndpoint
from seahorse.observe.protocol import EVENT_POST_TOOL_USE, EVENT_USER_PROMPT_SUBMIT
from seahorse.observe.queue import ObserverQueue


@pytest.fixture
def queue(tmp_path) -> ObserverQueue:
    q = ObserverQueue(tmp_path / "observer.db")
    yield q
    q.close()


def _raw_event(**overrides) -> dict:
    raw = {
        "session_id": "sess-1",
        "event_type": EVENT_USER_PROMPT_SUBMIT,
        "payload": {"prompt": "hello"},
    }
    raw.update(overrides)
    return raw


# ---------------------------------------------------------------------------
# handle_event — the core logic (no socket)
# ---------------------------------------------------------------------------


def test_handle_event_valid_envelope(queue: ObserverQueue, tmp_path) -> None:
    ep = ObserverEndpoint(queue, socket_path=tmp_path / "observer.sock")
    status, message = ep.handle_event(_raw_event())
    assert status == 200
    assert queue.pending_count() == 1


def test_handle_event_malformed_returns_400(queue: ObserverQueue, tmp_path) -> None:
    ep = ObserverEndpoint(queue, socket_path=tmp_path / "observer.sock")
    status, _ = ep.handle_event({"event_type": "stop"})  # missing session_id
    assert status == 400
    assert queue.pending_count() == 0


def test_handle_event_requires_token(queue: ObserverQueue, tmp_path) -> None:
    ep = ObserverEndpoint(queue, socket_path=tmp_path / "observer.sock", token="secret")
    status, _ = ep.handle_event(_raw_event())
    assert status == 401
    assert queue.pending_count() == 0


def test_handle_event_accepts_correct_token(queue: ObserverQueue, tmp_path) -> None:
    ep = ObserverEndpoint(queue, socket_path=tmp_path / "observer.sock", token="secret")
    status, _ = ep.handle_event(_raw_event(), token="secret")
    assert status == 200
    assert queue.pending_count() == 1


def test_handle_event_redacts_payload(queue: ObserverQueue, tmp_path) -> None:
    ep = ObserverEndpoint(queue, socket_path=tmp_path / "observer.sock")
    raw = _raw_event(
        event_type=EVENT_POST_TOOL_USE,
        payload={"tool_name": "Edit", "tool_input": "Bearer sk-abc123"},
    )
    ep.handle_event(raw)
    _, env = queue.pending()[0]
    assert "sk-abc123" not in str(env.payload)


def test_handle_event_drops_read_bash(queue: ObserverQueue, tmp_path) -> None:
    ep = ObserverEndpoint(queue, socket_path=tmp_path / "observer.sock")
    raw = _raw_event(
        event_type=EVENT_POST_TOOL_USE,
        payload={"tool_name": "Read", "tool_input": "secret.txt", "tool_response": "SECRET"},
    )
    status, _ = ep.handle_event(raw)
    assert status == 200
    assert queue.pending_count() == 0  # never enqueued


# ---------------------------------------------------------------------------
# serve_forever — the unix socket HTTP server
# ---------------------------------------------------------------------------


class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection over a unix socket (stdlib has no built-in handler)."""

    def __init__(self, socket_path: str, timeout: float = 5) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self._socket_path)


_socket_counter = 0


def _short_socket_path() -> str:
    """A short AF_UNIX socket path (the ~104-byte limit rejects pytest tmp_path)."""
    global _socket_counter
    _socket_counter += 1
    return os.path.join(tempfile.gettempdir(), f"obs-{os.getpid()}-{_socket_counter}.sock")


def _post(socket_path, body: dict, *, token: str | None = None) -> int:
    """POST a JSON body to the unix socket; return the HTTP status."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Seahorse-Token"] = token
    conn = _UnixHTTPConnection(str(socket_path))
    try:
        conn.request("POST", "/event", body=json.dumps(body).encode(), headers=headers)
        resp = conn.getresponse()
        return resp.status
    finally:
        conn.close()


def test_serve_forever_accepts_post(tmp_path) -> None:
    queue = ObserverQueue(tmp_path / "observer.db")
    socket_path = _short_socket_path()
    ep = ObserverEndpoint(queue, socket_path=socket_path)
    thread = threading.Thread(target=ep.serve_forever, daemon=True)
    thread.start()
    try:
        # Wait for the socket to appear.
        for _ in range(50):
            if os.path.exists(socket_path):
                break
            time.sleep(0.05)
        assert os.path.exists(socket_path)
        # Socket permissions: 0600 (FS-level auth, §4.4).
        assert os.stat(socket_path).st_mode & 0o777 == 0o600
        status = _post(socket_path, _raw_event())
        assert status == 200
        assert queue.pending_count() == 1
    finally:
        ep.shutdown()
        thread.join(timeout=2)
        queue.close()


def test_serve_forever_rejects_bad_token(tmp_path) -> None:
    queue = ObserverQueue(tmp_path / "observer.db")
    socket_path = _short_socket_path()
    ep = ObserverEndpoint(queue, socket_path=socket_path, token="secret")
    thread = threading.Thread(target=ep.serve_forever, daemon=True)
    thread.start()
    try:
        for _ in range(50):
            if os.path.exists(socket_path):
                break
            time.sleep(0.05)
        assert _post(socket_path, _raw_event()) == 401
        assert queue.pending_count() == 0
        assert _post(socket_path, _raw_event(), token="secret") == 200
        assert queue.pending_count() == 1
    finally:
        ep.shutdown()
        thread.join(timeout=2)
        queue.close()
