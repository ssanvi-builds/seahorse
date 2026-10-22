"""Pure ADR-013 request-guard tests (C11) — no sockets.

The guards used to live inside ``_MCPHandler._handle_post``, so the only way
to exercise them was a bound server. They now single-home as two pure phase
functions: ``pre_body_decision`` (rate limit → auth → path → body caps; NO body
parameter — the phase cannot read what it must not read) and
``post_body_decision`` (Origin → Accept → protocol version → parse). These
tests pin the exact guard ORDER pairwise, the reject wire bytes, the
constant-time comparison, and the policy defaults. The socket suite
(tests/mcp/test_http_server.py) stays the end-to-end oracle.
"""

from __future__ import annotations

import json

import pytest

from seahorse.mcp.request_policy import (
    Dispatch,
    ReadBody,
    Reject,
    RequestPolicy,
    build_reject,
    post_body_decision,
    pre_body_decision,
)

TOKEN = "test-token"


class _Limiter:
    def __init__(self, allowed: bool = True, retry_after: int = 0) -> None:
        self.allowed = allowed
        self.retry_after = retry_after
        self.calls: list[str] = []

    def allow(self, ip: str) -> tuple[bool, int]:
        self.calls.append(ip)
        return self.allowed, self.retry_after


def _policy(**overrides) -> RequestPolicy:
    base = {"token": TOKEN, "max_body_bytes": 64}
    base.update(overrides)
    return RequestPolicy(**base)


def _pre(**overrides):
    """pre_body_decision with valid defaults; override per test."""
    base = {
        "authorization": f"Bearer {TOKEN}",
        "content_length": "2",
        "client_ip": "127.0.0.1",
        "path": "/mcp",
        "policy": _policy(),
        "limiter": _Limiter(),
    }
    base.update(overrides)
    return pre_body_decision(**base)


def _post(**overrides):
    """post_body_decision with valid defaults; override per test."""
    base = {
        "origin": None,
        "host": "127.0.0.1:8767",
        "accept": "application/json",
        "protocol_version": None,
        "body": b"{}",
        "policy": _policy(),
    }
    base.update(overrides)
    return post_body_decision(**base)


# ---------------------------------------------------------------------------
# Pre-body ordering (pairwise on multi-fault requests)
# ---------------------------------------------------------------------------


def test_rate_limit_wins_over_everything() -> None:
    decision = _pre(
        limiter=_Limiter(allowed=False, retry_after=42),
        authorization="Bearer wrong",
        path="/other",
        content_length=None,
    )
    assert isinstance(decision, Reject)
    assert decision.status == 429
    assert decision.headers["Retry-After"] == "42"


def test_auth_wins_over_path() -> None:
    decision = _pre(authorization="Bearer wrong", path="/other")
    assert isinstance(decision, Reject)
    assert decision.status == 401


def test_auth_wins_over_missing_content_length() -> None:
    decision = _pre(authorization="Bearer wrong", content_length=None)
    assert isinstance(decision, Reject)
    assert decision.status == 401


def test_auth_wins_over_oversized_body() -> None:
    decision = _pre(authorization="Bearer wrong", content_length="999")
    assert isinstance(decision, Reject)
    assert decision.status == 401


def test_path_wins_over_content_length() -> None:
    decision = _pre(path="/other", content_length=None)
    assert isinstance(decision, Reject)
    assert decision.status == 404


def test_content_length_syntax_wins_over_cap() -> None:
    decision = _pre(content_length="not-an-int")
    assert isinstance(decision, Reject)
    assert decision.status == 411


@pytest.mark.parametrize("raw", [None, "", "abc", "-1"])
def test_missing_or_malformed_content_length_411(raw: str | None) -> None:
    decision = _pre(content_length=raw)
    assert isinstance(decision, Reject)
    assert decision.status == 411
    assert decision.payload == b'{"error": "Content-Length required"}'


def test_declared_equal_to_cap_returns_read_body() -> None:
    decision = _pre(content_length="64")
    assert decision == ReadBody(64)


def test_declared_one_over_cap_413() -> None:
    decision = _pre(content_length="65")
    assert isinstance(decision, Reject)
    assert decision.status == 413


def test_pre_body_decision_never_returns_dispatch() -> None:
    assert isinstance(_pre(), ReadBody)


# ---------------------------------------------------------------------------
# Token semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "authorization",
    [
        f"Bearer {TOKEN}",
    ],
)
def test_exact_bearer_accepted(authorization: str) -> None:
    assert isinstance(_pre(authorization=authorization), ReadBody)


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer wrong",
        f"bearer {TOKEN}",  # scheme is case-sensitive in the header contract
        "Bearer ",
        f"Bearer x{TOKEN}",
        f"Bearer {TOKEN}x",
        None,
        "",
    ],
)
def test_non_exact_bearer_rejected(authorization: str | None) -> None:
    decision = _pre(authorization=authorization)
    assert isinstance(decision, Reject)
    assert decision.status == 401
    assert decision.headers["WWW-Authenticate"] == "Bearer"


def test_compare_digest_is_used(monkeypatch) -> None:
    """Constant-time comparison, not string equality."""
    import seahorse.mcp.request_policy as rp

    seen: list[tuple[bytes, bytes]] = []

    def spy(a: bytes, b: bytes) -> bool:
        seen.append((a, b))
        return a == b

    monkeypatch.setattr(rp.hmac, "compare_digest", spy)
    _pre(authorization=f"Bearer {TOKEN}")
    assert seen == [(TOKEN.encode(), TOKEN.encode())]


# ---------------------------------------------------------------------------
# Post-body ordering (pairwise on multi-fault requests)
# ---------------------------------------------------------------------------


def test_foreign_origin_wins_over_accept_and_version() -> None:
    decision = _post(
        origin="https://evil.example",
        accept="text/plain",
        protocol_version="2024-01-01",
    )
    assert isinstance(decision, Reject)
    assert decision.status == 403


def test_bad_accept_wins_over_bad_version() -> None:
    decision = _post(accept="text/plain", protocol_version="2024-01-01")
    assert isinstance(decision, Reject)
    assert decision.status == 406


def test_bad_version_wins_over_malformed_json() -> None:
    decision = _post(protocol_version="2024-01-01", body=b"not json")
    assert isinstance(decision, Reject)
    assert decision.status == 400
    assert decision.payload == b'{"error": "unsupported MCP-Protocol-Version"}'


def test_malformed_json_returns_parse_error_without_id() -> None:
    decision = _post(body=b"not json")
    assert isinstance(decision, Reject)
    assert decision.status == 400
    assert decision.payload == (
        b'{"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}'
    )
    assert decision.headers == {}


def test_non_utf8_body_is_parse_error() -> None:
    decision = _post(body=b"\xff\xfe")
    assert isinstance(decision, Reject)
    assert decision.status == 400


def test_valid_json_returns_dispatch_with_parsed_object() -> None:
    decision = _post(body=json.dumps({"jsonrpc": "2.0", "method": "ping"}).encode())
    assert isinstance(decision, Dispatch)
    assert decision.request == {"jsonrpc": "2.0", "method": "ping"}


# ---------------------------------------------------------------------------
# Origin / Accept / version matrices
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "origin, host, ok",
    [
        (None, "127.0.0.1:8767", True),
        ("http://localhost:9999", "127.0.0.1:8767", True),
        ("http://127.0.0.1:8767", "127.0.0.1:8767", True),
        ("https://Public.Example", "public.example:443", True),
        ("http://[::1]:8767", "127.0.0.1:8767", True),
        ("ftp://x", "x", False),
        ("http://", "x", False),
        ("https://evil.example", "127.0.0.1:8767", False),
    ],
)
def test_origin_matrix(origin: str | None, host: str, ok: bool) -> None:
    decision = _post(origin=origin, host=host)
    assert isinstance(decision, Dispatch) is ok


@pytest.mark.parametrize(
    "accept, ok",
    [
        ("application/json", True),
        ("application/json;q=0.9", True),
        ("application/*", True),
        ("*/*", True),
        ("text/plain, application/json", True),
        ("text/event-stream", False),
        (None, False),
    ],
)
def test_accept_matrix(accept: str | None, ok: bool) -> None:
    decision = _post(accept=accept)
    assert isinstance(decision, Dispatch) is ok


@pytest.mark.parametrize(
    "version, ok",
    [(None, True), ("2025-11-25", True), ("2024-01-01", False)],
)
def test_protocol_version_matrix(version: str | None, ok: bool) -> None:
    decision = _post(protocol_version=version)
    assert isinstance(decision, Dispatch) is ok


# ---------------------------------------------------------------------------
# Reject wire contract (bytes + headers), table-driven
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reject, status, payload, headers",
    [
        (
            build_reject(429, retry_after=42, detail="rate limit exceeded"),
            429,
            b'{"error": "rate limit exceeded"}',
            {"Retry-After": "42"},
        ),
        (
            build_reject(
                401,
                detail="missing or invalid bearer token",
                extra_headers={"WWW-Authenticate": "Bearer"},
            ),
            401,
            b'{"error": "missing or invalid bearer token"}',
            {"WWW-Authenticate": "Bearer"},
        ),
        (build_reject(404, detail="not found"), 404, b'{"error": "not found"}', {}),
        (
            build_reject(411, detail="Content-Length required"),
            411,
            b'{"error": "Content-Length required"}',
            {},
        ),
        (
            build_reject(413, detail="body too large"),
            413,
            b'{"error": "body too large"}',
            {},
        ),
        (
            build_reject(403, detail="origin not allowed"),
            403,
            b'{"error": "origin not allowed"}',
            {},
        ),
        (
            build_reject(406, detail="unacceptable Accept header"),
            406,
            b'{"error": "unacceptable Accept header"}',
            {},
        ),
    ],
)
def test_reject_bytes_and_headers(
    reject: Reject, status: int, payload: bytes, headers: dict[str, str]
) -> None:
    assert reject.status == status
    assert reject.payload == payload
    assert dict(reject.headers) == headers


def test_retry_after_is_floored_at_one() -> None:
    assert build_reject(429, retry_after=0, detail="x").headers["Retry-After"] == "1"


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def test_policy_is_frozen() -> None:
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        _policy().token = "other"  # type: ignore[misc]


def test_policy_defaults() -> None:
    policy = RequestPolicy(token=TOKEN, max_body_bytes=1)
    assert policy.mcp_paths == ("/", "/mcp")
    assert policy.acceptable_media == ("application/json", "application/*", "*/*")
    assert policy.loopback_hostnames == ("localhost", "127.0.0.1", "::1")
    assert policy.supported_protocol_versions == ("2025-11-25",)