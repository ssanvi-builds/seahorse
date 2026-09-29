"""Envelope tests for the Streamable HTTP transport (stateless JSON mode).

One test per pipeline branch (ADR-013): rate limit → auth → path → body caps
→ Origin → Accept → protocol version → parse → ``handle_request``. The server
runs in-process on an ephemeral port and the client is stdlib ``urllib``; raw
sockets cover the requests ``http.client`` cannot express (missing
``Content-Length``, auth-before-body). Domain JSON-RPC errors ride in HTTP 200
by design; only transport-level rejections use 4xx/5xx.
"""

import http.client
import io
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable

import pytest

from seahorse.mcp.http_server import HttpServeConfig, _RateLimiter, build_http_server, serve_http

_REQUEST_TIMEOUT = 5.0
_LISTEN_TIMEOUT = 5.0

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "http-envelope-test", "version": "0.0.0"},
    },
}
_TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


def _json(payload: object) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _start_server(facade, config, *, clock: Callable[[], float] | None = None):
    httpd = build_http_server(facade, config=config, clock=clock)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread, f"http://127.0.0.1:{httpd.server_port}/mcp"


def _stop_server(httpd, thread: threading.Thread) -> None:
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=_REQUEST_TIMEOUT)


def _post(
    url,
    *,
    body: bytes = b"",
    token: str | None = "test-token",
    headers: dict[str, str | None] | None = None,
    method: str = "POST",
):
    request = urllib.request.Request(url, data=body if method == "POST" else None, method=method)
    merged: dict[str, str | None] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        **(headers or {}),
    }
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    for key, value in merged.items():
        if value is not None:
            request.add_header(key, value)
    try:
        response = urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()
    return response.status, response.headers, response.read()


def _raw_post(port: int, payload: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=_REQUEST_TIMEOUT) as sock:
        sock.sendall(payload)
        chunks: list[bytes] = []
        while True:
            try:
                chunk = sock.recv(4096)
            except TimeoutError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)


@pytest.fixture()
def http_server(real_facade):
    config = HttpServeConfig(token="test-token", port=0)
    httpd, thread, url = _start_server(real_facade, config)
    yield httpd, url
    _stop_server(httpd, thread)


class TestAuthAndHappyPath:
    def test_missing_bearer_returns_401(self, http_server):
        _, url = http_server
        code, headers, _ = _post(url, token=None, body=_json(_INITIALIZE))
        assert code == 401
        assert headers.get("WWW-Authenticate") == "Bearer"

    def test_wrong_bearer_returns_401(self, http_server):
        _, url = http_server
        code, _, _ = _post(url, token="wrong-token", body=_json(_INITIALIZE))
        assert code == 401

    def test_initialize_returns_200_with_server_info(self, http_server):
        _, url = http_server
        code, headers, body = _post(url, body=_json(_INITIALIZE))
        assert code == 200
        assert headers.get("Content-Type") == "application/json"
        payload = json.loads(body)
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 1
        assert payload["result"]["protocolVersion"] == "2025-11-25"
        assert payload["result"]["serverInfo"]["name"] == "seahorse-memory"

    def test_tools_list_returns_fifteen_tools(self, http_server):
        _, url = http_server
        code, _, body = _post(url, body=_json(_TOOLS_LIST))
        assert code == 200
        payload = json.loads(body)
        assert len(payload["result"]["tools"]) == 15

    def test_notification_returns_202_empty_body(self, http_server):
        _, url = http_server
        notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        code, headers, body = _post(url, body=_json(notification))
        assert code == 202
        assert headers.get("Content-Length") == "0"
        assert body == b""


class TestMethodAndPath:
    def test_get_returns_405_with_allow_post(self, http_server):
        _, url = http_server
        code, headers, _ = _post(url, method="GET")
        assert code == 405
        assert headers.get("Allow") == "POST"

    def test_delete_returns_405_with_allow_post(self, http_server):
        _, url = http_server
        code, headers, _ = _post(url, method="DELETE")
        assert code == 405
        assert headers.get("Allow") == "POST"

    def test_unknown_path_returns_404(self, http_server):
        _, url = http_server
        code, _, _ = _post(url.replace("/mcp", "/other"), body=_json(_INITIALIZE))
        assert code == 404


class TestBodyCaps:
    def test_oversized_declared_body_returns_413(self, http_server):
        httpd, _ = http_server
        conn = http.client.HTTPConnection(
            "127.0.0.1", httpd.server_port, timeout=_REQUEST_TIMEOUT
        )
        conn.putrequest("POST", "/mcp")
        conn.putheader("Host", f"127.0.0.1:{httpd.server_port}")
        conn.putheader("Authorization", "Bearer test-token")
        conn.putheader("Accept", "application/json")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(HttpServeConfig().max_body_bytes + 1))
        conn.endheaders(message_body=b"{}")
        response = conn.getresponse()
        assert response.status == 413
        conn.close()

    def test_missing_content_length_returns_411(self, http_server):
        httpd, _ = http_server
        payload = (
            b"POST /mcp HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Authorization: Bearer test-token\r\n"
            b"Accept: application/json\r\n"
            b"\r\n"
        )
        raw = _raw_post(httpd.server_port, payload)
        assert raw.split(b"\r\n", 1)[0].startswith(b"HTTP/1.1 411")

    def test_auth_checked_before_body_read(self, http_server):
        # Content-Length beyond the cap + no body: if the body-cap check ran
        # before auth we would see 413 (or a hang); 401 proves auth-first.
        httpd, _ = http_server
        payload = (
            b"POST /mcp HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Length: 1000000\r\n"
            b"Accept: application/json\r\n"
            b"\r\n"
        )
        raw = _raw_post(httpd.server_port, payload)
        assert raw.split(b"\r\n", 1)[0].startswith(b"HTTP/1.1 401")


class TestRateLimit:
    def test_third_request_returns_429_with_retry_after(self, real_facade):
        def frozen_clock() -> float:
            return 1000.0  # window aligned at 960.0 → all three hits share it

        config = HttpServeConfig(token="test-token", port=0, rate_limit_per_minute=2)
        httpd, thread, url = _start_server(real_facade, config, clock=frozen_clock)
        try:
            assert _post(url, body=_json(_INITIALIZE))[0] == 200
            assert _post(url, body=_json(_INITIALIZE))[0] == 200
            code, headers, _ = _post(url, body=_json(_INITIALIZE))
            assert code == 429
            assert int(headers.get("Retry-After", "0")) >= 1
        finally:
            _stop_server(httpd, thread)

    def test_rate_limit_window_resets(self):
        now = [1000.0]

        def clock() -> float:
            return now[0]

        limiter = _RateLimiter(2, clock=clock)
        assert limiter.allow("1.2.3.4") == (True, 0)
        assert limiter.allow("1.2.3.4") == (True, 0)
        allowed, retry_after = limiter.allow("1.2.3.4")
        assert allowed is False
        assert retry_after >= 1
        now[0] += 61
        assert limiter.allow("1.2.3.4") == (True, 0)


class TestOriginAndHeaders:
    def test_foreign_origin_returns_403(self, http_server):
        _, url = http_server
        code, _, _ = _post(
            url, body=_json(_INITIALIZE), headers={"Origin": "http://evil.example"}
        )
        assert code == 403

    def test_loopback_origin_allowed(self, http_server):
        _, url = http_server
        code, _, _ = _post(
            url, body=_json(_INITIALIZE), headers={"Origin": "http://localhost:9999"}
        )
        assert code == 200

    def test_host_matching_origin_allowed(self, http_server):
        httpd, url = http_server
        code, _, _ = _post(
            url,
            body=_json(_INITIALIZE),
            headers={"Origin": f"http://127.0.0.1:{httpd.server_port}"},
        )
        assert code == 200

    def test_event_stream_only_accept_returns_406(self, http_server):
        _, url = http_server
        code, _, _ = _post(
            url, body=_json(_INITIALIZE), headers={"Accept": "text/event-stream"}
        )
        assert code == 406

    def test_missing_accept_returns_406(self, http_server):
        _, url = http_server
        code, _, _ = _post(url, body=_json(_INITIALIZE), headers={"Accept": None})
        assert code == 406

    def test_unsupported_protocol_version_returns_400(self, http_server):
        _, url = http_server
        code, _, _ = _post(
            url,
            body=_json(_INITIALIZE),
            headers={"MCP-Protocol-Version": "2024-01-01"},
        )
        assert code == 400


class TestParseAndDispatch:
    def test_malformed_body_returns_parse_error_without_id(self, http_server):
        _, url = http_server
        code, _, body = _post(url, body=b"not json")
        assert code == 400
        payload = json.loads(body)
        assert payload["error"]["code"] == -32700
        assert "id" not in payload

    def test_batch_array_returns_invalid_request_with_null_id(self, http_server):
        _, url = http_server
        code, _, body = _post(url, body=_json([_TOOLS_LIST]))
        assert code == 200
        payload = json.loads(body)
        assert payload["error"]["code"] == -32600
        assert payload["id"] is None

    def test_unknown_method_returns_200_with_method_not_found(self, http_server):
        _, url = http_server
        request = {"jsonrpc": "2.0", "id": 3, "method": "no/such/method"}
        code, _, body = _post(url, body=_json(request))
        assert code == 200
        payload = json.loads(body)
        assert payload["error"]["code"] == -32601
        assert payload["id"] == 3

    def test_mcp_session_id_header_ignored(self, http_server):
        _, url = http_server
        code, _, _ = _post(url, body=_json(_INITIALIZE), headers={"Mcp-Session-Id": "abc"})
        assert code == 200


class TestLifecycle:
    def test_empty_token_rejected_before_bind(self, real_facade):
        config = HttpServeConfig(token="", port=0)
        with pytest.raises(ValueError):
            build_http_server(real_facade, config=config)
        with pytest.raises(ValueError):
            serve_http(real_facade, config=config, stderr=io.StringIO())

    def test_serve_http_prints_listening_line_and_serves(self, real_facade):
        err = io.StringIO()
        config = HttpServeConfig(token="test-token", port=0)
        thread = threading.Thread(
            target=serve_http,
            args=(real_facade,),
            kwargs={"config": config, "stderr": err},
            daemon=True,
        )
        thread.start()
        deadline = time.monotonic() + _LISTEN_TIMEOUT
        while "listening on" not in err.getvalue():
            if not thread.is_alive():
                pytest.fail("serve_http thread died before printing the listening line")
            if time.monotonic() > deadline:
                pytest.fail("serve_http never printed the listening line")
            time.sleep(0.05)
        match = re.search(r"http://127\.0\.0\.1:(\d+)", err.getvalue())
        assert match is not None
        url = f"http://127.0.0.1:{match.group(1)}/mcp"
        code, _, _ = _post(url, body=_json(_INITIALIZE))
        assert code == 200