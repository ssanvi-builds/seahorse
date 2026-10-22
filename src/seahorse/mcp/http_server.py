"""Streamable HTTP transport (stateless JSON mode) for the MCP server.

Reuses ``handle_request`` so stdio and HTTP cannot drift: the same JSON-RPC
dispatch decides everything past the envelope. The server enforces an
adversarial pipeline per request (ADR-013): rate limit → auth → path → body
caps → Origin → Accept → protocol version → parse → dispatch. Transport
errors use 4xx/5xx; domain JSON-RPC errors ride in HTTP 200 by design.

The guards themselves single-home in ``request_policy`` (pure, socket-free);
this module is the socket adapter: it extracts the header primitives and does
the ONE body read after the pre-body phase allows it (auth-before-body).
"""

from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TextIO, cast

from seahorse.facade.facade import MemoryFacade
from seahorse.mcp.profile import _SERVER_VERSION, handle_request
from seahorse.mcp.request_policy import (
    Dispatch,
    ReadBody,
    Reject,
    RequestPolicy,
    build_reject,
    post_body_decision,
    pre_body_decision,
)

__all__ = ["HttpServeConfig", "build_http_server", "serve_http"]

_RATE_WINDOW_SECONDS = 60


@dataclass(frozen=True)
class HttpServeConfig:
    """Settings for the HTTP listen socket and per-request guards."""

    host: str = "127.0.0.1"
    port: int = 8767
    token: str = ""  # validated non-empty before binding
    rate_limit_per_minute: int = 60
    max_body_bytes: int = 262_144


def _validate_token(token: str) -> None:
    if not token:
        raise ValueError("http transport requires a non-empty bearer token")


class _RateLimiter:
    """Fixed-window per-IP limiter, in-memory, no persistence."""

    def __init__(self, limit: int, *, clock: Callable[[], float] | None = None) -> None:
        self._limit = limit
        self._clock = clock if clock is not None else time.monotonic
        self._lock = threading.Lock()
        self._hits: dict[str, tuple[float, int]] = {}

    def allow(self, ip: str) -> tuple[bool, int]:
        """Return (allowed, retry_after); a denied hit is not recorded."""
        now = self._clock()
        window_start = now - now % _RATE_WINDOW_SECONDS
        with self._lock:
            start, count = self._hits.get(ip, (window_start, 0))
            if start != window_start:
                start, count = window_start, 0
            if count >= self._limit:
                retry_after = max(int(_RATE_WINDOW_SECONDS - (now - start)), 1)
                return False, retry_after
            self._hits[ip] = (start, count + 1)
            return True, 0


class _MCPHandler(BaseHTTPRequestHandler):
    """Adversarial envelope around ``handle_request`` (ADR-013 pipeline)."""

    protocol_version = "HTTP/1.1"
    server_version = f"seahorse-mcp/{_SERVER_VERSION}"
    timeout = 30

    facade: MemoryFacade
    policy: RequestPolicy
    limiter: _RateLimiter
    request_lock: threading.Lock

    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        self._method_not_allowed()

    def do_PUT(self) -> None:
        self._method_not_allowed()

    def do_PATCH(self) -> None:
        self._method_not_allowed()

    def do_DELETE(self) -> None:
        self._method_not_allowed()

    def do_POST(self) -> None:
        try:
            self._handle_post()
        except (BrokenPipeError, ConnectionError, TimeoutError):
            self.close_connection = True

    def _method_not_allowed(self) -> None:
        self._send_reject(
            build_reject(
                405,
                detail="method not allowed; use POST",
                extra_headers={"Allow": "POST"},
            )
        )

    def _handle_post(self) -> None:
        decision = pre_body_decision(
            authorization=self.headers.get("Authorization"),
            content_length=self.headers.get("Content-Length"),
            client_ip=self.client_address[0],
            path=self.path,
            policy=self.policy,
            limiter=self.limiter,
        )
        if isinstance(decision, Reject):
            self._send_reject(decision)
            return
        decision = cast(ReadBody, decision)  # phase A only yields ReadBody
        body = self.rfile.read(decision.length)  # the ONLY body read
        post = post_body_decision(
            origin=self.headers.get("Origin"),
            host=self.headers.get("Host"),
            accept=self.headers.get("Accept"),
            protocol_version=self.headers.get("MCP-Protocol-Version"),
            body=body,
            policy=self.policy,
        )
        if isinstance(post, Reject):
            self._send_reject(post)
            return
        post = cast(Dispatch, post)  # phase B only yields Dispatch
        request = post.request
        try:
            with self.request_lock:
                response = handle_request(self.facade, request)
        except Exception:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            err = {"code": -32603, "message": "Internal error"}
            payload = json.dumps({"jsonrpc": "2.0", "id": None, "error": err})
            self._send(500, payload.encode("utf-8"))
            return
        if response is None:
            self._send(202, b"")
            return
        self._send(200, json.dumps(response).encode("utf-8"))

    def _send(
        self,
        status: int,
        payload: bytes,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)
        self.close_connection = True

    def _send_reject(self, reject: Reject) -> None:
        self._send(
            reject.status, reject.payload, extra_headers=dict(reject.headers)
        )


def _build_handler_class(
    facade: MemoryFacade,
    *,
    config: HttpServeConfig,
    limiter: _RateLimiter,
) -> type[_MCPHandler]:
    class _Handler(_MCPHandler):
        pass

    _Handler.facade = facade
    _Handler.policy = RequestPolicy(
        token=config.token, max_body_bytes=config.max_body_bytes
    )
    _Handler.limiter = limiter
    _Handler.request_lock = threading.Lock()
    return _Handler


def build_http_server(
    facade: MemoryFacade,
    *,
    config: HttpServeConfig,
    clock: Callable[[], float] | None = None,
) -> ThreadingHTTPServer:
    """Build the HTTP server, validating the config before any bind."""
    _validate_token(config.token)
    limiter = _RateLimiter(config.rate_limit_per_minute, clock=clock)
    handler = _build_handler_class(facade, config=config, limiter=limiter)
    return ThreadingHTTPServer((config.host, config.port), handler)


def serve_http(
    facade: MemoryFacade,
    *,
    config: HttpServeConfig,
    stderr: TextIO | None = None,
) -> None:
    """Serve forever; bind failure and empty token fail fast with ValueError."""
    _validate_token(config.token)
    httpd = build_http_server(facade, config=config)
    target = stderr if stderr is not None else sys.stderr
    message = f"seahorse-mcp: listening on http://{config.host}:{httpd.server_port}"
    print(message, file=target, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()