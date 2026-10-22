"""The ADR-013 request guards as a pure, socket-free seam (C11).

The adversarial envelope used to live entirely inside the socket handler, so
every guard was testable only through a bound server. The guards now single-
home here as two pure PHASE functions, mirroring the exact order of ADR-013:

- ``pre_body_decision``  — rate limit → auth → path → body caps. It takes NO
  body parameter: "authenticate before reading the body" is structural, not a
  convention. Returns ``Reject`` or ``ReadBody(length)``.
- ``post_body_decision`` — Origin → Accept → protocol version → JSON parse.
  Returns ``Reject`` or ``Dispatch(request)``.

``build_reject`` is the single reject-payload builder (shared with the 405
path), so the wire bytes and headers are constructed in one place. The socket
handler stays as the adapter: it extracts the header primitives (case-
insensitive ``email.message.Message`` lookups happen there) and performs the
single ``rfile.read`` after ``ReadBody``.
"""

from __future__ import annotations

import hmac
import json
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

_SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25",)
_LOOPBACK_HOSTNAMES = ("localhost", "127.0.0.1", "::1")
_ACCEPTABLE_MEDIA = ("application/json", "application/*", "*/*")
_MCP_PATHS = ("/", "/mcp")
_BEARER_PREFIX = "Bearer "


class RateLimiter(Protocol):
    def allow(self, ip: str) -> tuple[bool, int]: ...


@dataclass(frozen=True)
class RequestPolicy:
    """Immutable per-request policy (no env reads; the server builds it once)."""

    token: str
    max_body_bytes: int
    supported_protocol_versions: tuple[str, ...] = _SUPPORTED_PROTOCOL_VERSIONS
    mcp_paths: tuple[str, ...] = _MCP_PATHS
    acceptable_media: tuple[str, ...] = _ACCEPTABLE_MEDIA
    loopback_hostnames: tuple[str, ...] = _LOOPBACK_HOSTNAMES


@dataclass(frozen=True)
class Reject:
    status: int
    payload: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReadBody:
    """The caller may now read exactly ``length`` bytes (and nothing more)."""

    length: int


@dataclass(frozen=True)
class Dispatch:
    request: Any


PreBodyDecision = Reject | ReadBody
PostBodyDecision = Reject | Dispatch


def build_reject(
    status: int,
    *,
    retry_after: int | None = None,
    detail: str = "request rejected",
    extra_headers: Mapping[str, str] | None = None,
) -> Reject:
    """The single reject builder (payload bytes + headers) — never duplicate."""
    headers = dict(extra_headers) if extra_headers else {}
    if retry_after is not None:
        headers["Retry-After"] = str(max(retry_after, 1))
    payload = json.dumps({"error": detail}).encode("utf-8")
    return Reject(status=status, payload=payload, headers=headers)


def pre_body_decision(
    *,
    authorization: str | None,
    content_length: str | None,
    client_ip: str,
    path: str,
    policy: RequestPolicy,
    limiter: RateLimiter,
) -> PreBodyDecision:
    """ADR-013 phase A — everything that must run BEFORE the body is read."""
    allowed, retry_after = limiter.allow(client_ip)
    if not allowed:
        return build_reject(429, retry_after=retry_after, detail="rate limit exceeded")
    if not _token_ok(authorization, policy.token):
        return build_reject(
            401,
            detail="missing or invalid bearer token",
            extra_headers={"WWW-Authenticate": "Bearer"},
        )
    if path not in policy.mcp_paths:
        return build_reject(404, detail="not found")
    if content_length is None:
        return build_reject(411, detail="Content-Length required")
    try:
        declared = int(content_length)
    except ValueError:
        return build_reject(411, detail="Content-Length required")
    if declared < 0:
        return build_reject(411, detail="Content-Length required")
    if declared > policy.max_body_bytes:
        return build_reject(413, detail="body too large")
    return ReadBody(declared)


def post_body_decision(
    *,
    origin: str | None,
    host: str | None,
    accept: str | None,
    protocol_version: str | None,
    body: bytes,
    policy: RequestPolicy,
) -> PostBodyDecision:
    """ADR-013 phase B — everything that runs AFTER the body is read."""
    if not _origin_ok(origin, host, policy):
        return build_reject(403, detail="origin not allowed")
    if not _accept_ok(accept, policy):
        return build_reject(406, detail="unacceptable Accept header")
    if (
        protocol_version is not None
        and protocol_version not in policy.supported_protocol_versions
    ):
        return build_reject(400, detail="unsupported MCP-Protocol-Version")
    try:
        request = json.loads(body.decode("utf-8"))
    except ValueError:
        err = {"code": -32700, "message": "Parse error"}
        payload = json.dumps({"jsonrpc": "2.0", "error": err}).encode("utf-8")
        return Reject(status=400, payload=payload, headers={})
    return Dispatch(request)


def _token_ok(authorization: str | None, expected: str) -> bool:
    header = authorization or ""
    provided = header[len(_BEARER_PREFIX):] if header.startswith(_BEARER_PREFIX) else ""
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def _origin_ok(origin: str | None, host: str | None, policy: RequestPolicy) -> bool:
    if origin is None:
        return True
    parsed = urllib.parse.urlparse(origin)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if scheme not in ("http", "https") or not hostname:
        return False
    if hostname in policy.loopback_hostnames:
        return True
    host_header = (host or "").split(":", 1)[0].strip("[]").lower()
    return hostname == host_header


def _accept_ok(accept: str | None, policy: RequestPolicy) -> bool:
    if accept is None:
        return False
    for part in accept.split(","):
        media = part.split(";", 1)[0].strip().lower()
        if media in policy.acceptable_media:
            return True
    return False


__all__ = [
    "Dispatch",
    "PostBodyDecision",
    "PreBodyDecision",
    "RateLimiter",
    "ReadBody",
    "Reject",
    "RequestPolicy",
    "build_reject",
    "post_body_decision",
    "pre_body_decision",
]