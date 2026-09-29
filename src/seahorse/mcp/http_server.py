"""Streamable HTTP transport (stateless JSON mode) for the MCP server.

Reuses ``handle_request`` so stdio and HTTP cannot drift: the same JSON-RPC
dispatch decides everything past the envelope. The server enforces an
adversarial pipeline per request (ADR-013): rate limit → auth → path → body
caps → Origin → Accept → protocol version → parse → dispatch. Transport
errors use 4xx/5xx; domain JSON-RPC errors ride in HTTP 200 by design.
"""

from __future__ import annotations

import hmac
import json
import sys
import threading
import time
import traceback
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TextIO

from seahorse.facade.facade import MemoryFacade
from seahorse.mcp.profile import _SERVER_VERSION, handle_request

__all__ = ["HttpServeConfig", "build_http_server", "serve_http"]

_SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25",)
_LOOPBACK_HOSTNAMES = ("localhost", "127.0.0.1", "::1")
_RATE_WINDOW_SECONDS = 60
_ACCEPTABLE_MEDIA = ("application/json", "application/*", "*/*")
_MCP_PATHS = ("/", "/mcp")
_BEARER_PREFIX = "Bearer "


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
    config: HttpServeConfig
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
        self._reject(
            405,
            detail="method not allowed; use POST",
            extra_headers={"Allow": "POST"},
        )

    def _handle_post(self) -> None:
        allowed, retry_after = self.limiter.allow(self.client_address[0])
        if not allowed:
            self._reject(429, retry_after=retry_after, detail="rate limit exceeded")
            return
        if not self._token_ok():
            self._reject(
                401,
                detail="missing or invalid bearer token",
                extra_headers={"WWW-Authenticate": "Bearer"},
            )
            return
        if self.path not in _MCP_PATHS:
            self._reject(404, detail="not found")
            return
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self._reject(411, detail="Content-Length required")
            return
        try:
            declared = int(raw_length)
        except ValueError:
            self._reject(411, detail="Content-Length required")
            return
        if declared < 0:
            self._reject(411, detail="Content-Length required")
            return
        if declared > self.config.max_body_bytes:
            self._reject(413, detail="body too large")
            return
        body = self.rfile.read(declared)
        if not self._origin_ok():
            self._reject(403, detail="origin not allowed")
            return
        if not self._accept_ok():
            self._reject(406, detail="unacceptable Accept header")
            return
        version = self.headers.get("MCP-Protocol-Version")
        if version is not None and version not in _SUPPORTED_PROTOCOL_VERSIONS:
            self._reject(400, detail="unsupported MCP-Protocol-Version")
            return
        try:
            request = json.loads(body.decode("utf-8"))
        except ValueError:
            err = {"code": -32700, "message": "Parse error"}
            self._send(400, json.dumps({"jsonrpc": "2.0", "error": err}).encode("utf-8"))
            return
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

    def _token_ok(self) -> bool:
        header = self.headers.get("Authorization", "")
        provided = header[len(_BEARER_PREFIX):] if header.startswith(_BEARER_PREFIX) else ""
        return hmac.compare_digest(provided.encode("utf-8"), self.config.token.encode("utf-8"))

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        parsed = urllib.parse.urlparse(origin)
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower()
        if scheme not in ("http", "https") or not hostname:
            return False
        if hostname in _LOOPBACK_HOSTNAMES:
            return True
        host_header = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
        return hostname == host_header

    def _accept_ok(self) -> bool:
        header = self.headers.get("Accept")
        if header is None:
            return False
        for part in header.split(","):
            media = part.split(";", 1)[0].strip().lower()
            if media in _ACCEPTABLE_MEDIA:
                return True
        return False

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

    def _reject(
        self,
        status: int,
        *,
        retry_after: int | None = None,
        detail: str = "request rejected",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        headers = dict(extra_headers) if extra_headers else {}
        if retry_after is not None:
            headers["Retry-After"] = str(max(retry_after, 1))
        payload = json.dumps({"error": detail}).encode("utf-8")
        self._send(status, payload, extra_headers=headers)


def _build_handler_class(
    facade: MemoryFacade,
    *,
    config: HttpServeConfig,
    limiter: _RateLimiter,
) -> type[_MCPHandler]:
    class _Handler(_MCPHandler):
        pass

    _Handler.facade = facade
    _Handler.config = config
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