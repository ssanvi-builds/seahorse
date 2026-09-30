"""The shared MCP protocol session — one behavior, two transports (ADR-013).

Both stdio (``profile.serve``) and Streamable HTTP (``http_server``) drive the
same pure ``handle_request`` seam, so ONE session asserts the identical
protocol behavior over each transport: the stdio smoke test pipes it through a
real subprocess, the HTTP contract test posts it to the in-process server.

The two DIVERGENT behaviors stay OUT of this session, each proven in its
transport's own test file (docstring pointers, no duplicate tests):

- Notifications (``id`` member absent): stdio writes NOTHING to stdout — proven
  by reply ordering in ``test_mcp_stdio_smoke.py``; HTTP answers ``202`` +
  ``Content-Length: "0"`` + empty body — proven by
  ``test_http_server.py::test_notification_returns_202_empty_body``.
- Malformed JSON: stdio answers ``-32700`` with ``id: null`` on stdout —
  proven in ``test_mcp_stdio_smoke.py``; HTTP answers ``400`` + ``-32700``
  with NO ``id`` member — proven by
  ``test_http_server.py::test_malformed_body_returns_parse_error_without_id``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import Any

_SendFn = Callable[[dict], None]
_RecvFn = Callable[[], dict]

_AGENT_BY = {"agent_id": "a", "session_id": "s", "source_type": "agent"}
_HUMAN_BY = {"agent_id": "s", "session_id": "s2", "source_type": "human"}
_BODY_MADRID = "Sergio lives in Madrid"
_BODY_BARCELONA = "Sergio lives in Barcelona"


def tool_result(response: dict) -> Any:
    return json.loads(response["result"]["content"][0]["text"])


def _server_version() -> str:
    try:
        return _pkg_version("seahorse-memory")
    except PackageNotFoundError:
        return "0.0.0"


def _request(
    send: _SendFn, recv: _RecvFn, request_id: int, method: str, params: dict | None = None
) -> dict:
    request: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        request["params"] = params
    send(request)
    reply = recv()
    assert reply["id"] == request_id, f"expected reply for id {request_id}, got {reply!r}"
    return reply


def _call(send: _SendFn, recv: _RecvFn, request_id: int, name: str, arguments: dict) -> dict:
    return _request(send, recv, request_id, "tools/call", {"name": name, "arguments": arguments})


def run_contract_session(send: _SendFn, recv: _RecvFn) -> tuple[str, str]:
    """Drive the 9-step parity session; return ``(ep_id, new_id)``.

    ① initialize (asserted by FULL equality — pins the whole handshake shape)
    → ② tools/list (exactly 15) → ③ remember → ④ recall → ⑤ improve (new id +
    supersedes) → ⑥ forget (invalidated) → ⑦ build_pit all-None (→ null) →
    ⑧ unknown tool (-32601 + unknown_tool) → ⑨ recall_full (superseded
    episodes stay hydratable).
    """
    # ① initialize — full-equality assert pins the exact handshake contract.
    init = _request(send, recv, 1, "initialize")
    assert init["result"] == {
        "protocolVersion": "2025-11-25",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "seahorse-memory", "version": _server_version()},
    }

    # ② tools/list → exactly the current 15-tool surface
    listing = _request(send, recv, 2, "tools/list")
    names = {t["name"] for t in listing["result"]["tools"]}
    assert len(names) == 15

    # ③ remember → ACTIVE ep_id
    wr = tool_result(_call(send, recv, 3, "remember", {"body": _BODY_MADRID, "by": _AGENT_BY}))
    assert wr["status"] == "ACTIVE"
    ep_id = wr["ep_id"]
    assert ep_id

    # ④ recall → the row shows up
    rows = tool_result(_call(send, recv, 4, "recall", {"query": "madrid"}))
    assert ep_id in [r["ep_id"] for r in rows]

    # ⑤ improve → a NEW episode superseding the old (arguments dict split for
    # the 100-char line limit)
    new_ep = tool_result(
        _call(
            send,
            recv,
            5,
            "improve",
            {
                "ep_id": ep_id,
                "new_body": _BODY_BARCELONA,
                "by": _HUMAN_BY,
                "reason": "correction",
            },
        )
    )
    assert new_ep["id"] != ep_id
    assert new_ep["supersedes"] == ep_id
    new_id = new_ep["id"]

    # ⑥ forget → invalidated
    forgotten = tool_result(
        _call(send, recv, 6, "forget", {"ep_id": new_id, "reason": "wrong", "by": _AGENT_BY})
    )
    assert forgotten["invalid_at"] is not None

    # ⑦ build_pit all-None → the JSON wire text is "null"
    assert tool_result(_call(send, recv, 7, "build_pit", {})) is None

    # ⑧ unknown tool → -32601 (expire is still outside the MCP surface)
    unknown = _call(send, recv, 8, "expire", {})
    assert unknown["error"]["code"] == -32601
    assert unknown["error"]["data"] == {"unknown_tool": "expire"}

    # ⑨ recall_full → the superseded episode stays hydratable
    hydrated = tool_result(_call(send, recv, 9, "recall_full", {"ep_ids": [ep_id]}))
    assert isinstance(hydrated, list) and hydrated
    assert hydrated[0]["episode"]["id"] == ep_id
    assert hydrated[0]["episode"]["body"] == _BODY_MADRID
    return ep_id, new_id