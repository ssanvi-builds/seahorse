"""Observer HTTP endpoint — the edge where envelopes arrive.

The hooks POST envelopes to a unix socket (``{seahorse_dir}/observer.sock``,
dir 0700, socket 0600). The endpoint validates auth (token), parses the
envelope tolerantly (caps, malformed → 400), REDACTS before enqueue (nothing raw
persisted), and applies ``drop_tools`` (Read/Bash never reach the queue — their
content is entirely secret).

The unix socket is the FS-level auth (only the owning user can connect); the
token is the caller-level auth (a same-user process that can reach the socket
but does not know the token cannot fabricate envelopes). No TCP surface — never
non-loopback.
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Collection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from seahorse.observe.adapters.claude_code import (
    handle_post_tool_use,
    handle_session_start,
    handle_stop,
    handle_user_prompt_submit,
)
from seahorse.observe.protocol import (
    EVENT_POST_TOOL_USE,
    EVENT_SESSION_START,
    EVENT_STOP,
    EVENT_USER_PROMPT_SUBMIT,
    EnvelopeError,
    parse_envelope,
)
from seahorse.observe.queue import ObserverQueue
from seahorse.observe.threshold import DEFAULT_DROP_TOOLS

_SOCKET_MODE = 0o600


class _UnixHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_UNIX
    daemon_threads = True


class _Handler(BaseHTTPRequestHandler):
    """POST-only handler: read the envelope, delegate to the endpoint."""

    def do_POST(self) -> None:  # noqa: N802 — http.server API
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            raw = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._respond(400, "invalid json")
            return
        if not isinstance(raw, dict):
            self._respond(400, "envelope must be a JSON object")
            return
        token = self.headers.get("X-Seahorse-Token")
        status, message = self.server.endpoint.handle_event(raw, token=token)  # type: ignore[attr-defined]
        self._respond(status, message)

    def _respond(self, status: int, message: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": message}).encode("utf-8"))

    def log_message(self, *args: object) -> None:
        pass  # quiet — the observer is a background service


class ObserverEndpoint:
    """Receives envelopes over a unix socket, redacts, and enqueues."""

    def __init__(
        self,
        queue: ObserverQueue,
        *,
        socket_path: Path | str,
        token: str | None = None,
        drop_tools: Collection[str] = DEFAULT_DROP_TOOLS,
    ) -> None:
        self._queue = queue
        self._socket_path = Path(socket_path)
        self._token = token
        # The configured ``[observe].drop_tools`` — applied at enqueue so a tool
        # added to the set never reaches the queue (its content is entirely
        # secret; redaction alone cannot guarantee it is clean).
        self._drop_tools = drop_tools
        self._server: _UnixHTTPServer | None = None

    # ------------------------------------------------------------- core logic

    def handle_event(self, raw: dict[str, Any], *, token: str | None = None) -> tuple[int, str]:
        """Process one envelope. Returns ``(status_code, message)``.

        Auth first (token), then tolerant parse (caps, malformed → 400), then
        dispatch to the adapter functions — which REDACTS before enqueue (nothing
        raw persisted), applies ``drop_tools`` (Read/Bash never reach the queue),
        and manages the persisted ``prompt_number`` (turn boundary).
        """
        if self._token is not None and token != self._token:
            return 401, "unauthorized"
        try:
            env = parse_envelope(raw)
        except EnvelopeError as exc:
            return 400, str(exc)
        session_id = env.session_id
        agent_id = env.agent_id if env.agent_id != "unknown" else None
        if env.event_type == EVENT_SESSION_START:
            handle_session_start(self._queue, session_id=session_id, agent_id=agent_id)
        elif env.event_type == EVENT_USER_PROMPT_SUBMIT:
            handle_user_prompt_submit(
                self._queue,
                session_id=session_id,
                prompt=env.payload.get("prompt", ""),
                agent_id=agent_id,
            )
        elif env.event_type == EVENT_POST_TOOL_USE:
            handle_post_tool_use(
                self._queue,
                session_id=session_id,
                tool_name=env.payload.get("tool_name", ""),
                tool_use_id=env.payload.get("tool_use_id", ""),
                tool_input=env.payload.get("tool_input", ""),
                tool_response=env.payload.get("tool_response", ""),
                agent_id=agent_id,
                drop_tools=self._drop_tools,
            )
        elif env.event_type == EVENT_STOP:
            handle_stop(self._queue, session_id=session_id, agent_id=agent_id)
        else:
            return 400, f"unknown event_type: {env.event_type}"
        return 200, "ok"

    # ------------------------------------------------------------- lifecycle

    def serve_forever(self) -> None:
        """Bind the unix socket (socket 0600) and serve until shutdown.

        The parent dir is created by ``seahorse setup`` with 0700 (the observer
        dir); the endpoint only chmods the socket to 0600 (FS-level auth).
        """
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self._socket_path.exists():
            self._socket_path.unlink()
        # AF_UNIX: ``server_address`` is the path STRING, not a (host, port)
        # tuple. The stdlib type stub only knows the TCP shape.
        server = _UnixHTTPServer(str(self._socket_path), _Handler)  # type: ignore[arg-type]
        server.endpoint = self  # type: ignore[attr-defined]
        os.chmod(self._socket_path, _SOCKET_MODE)
        self._server = server
        try:
            server.serve_forever()
        finally:
            server.server_close()
            if self._socket_path.exists():
                self._socket_path.unlink()
            self._server = None

    def shutdown(self) -> None:
        """Stop the server (safe to call from another thread)."""
        if self._server is not None:
            self._server.shutdown()


__all__ = ["ObserverEndpoint"]
