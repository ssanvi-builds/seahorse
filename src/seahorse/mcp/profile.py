"""stdio JSON-RPC 2.0 server for the ``io.seahorse.memory/v1`` profile (#13).

Stdlib-only framing (no ``mcp``/FastMCP SDK): newline-delimited JSON-RPC 2.0
over stdio. The MCP protocol is JSON-RPC 2.0 standard and SDK-independent; the
code repo is stdlib-only (``dependencies = []``) and ``main`` is clone-and-run,
so we hand-roll the framing with ``json`` + ``sys``. MCP spec pinned
``2025-11-25``.

Methods handled (MVP-0): ``initialize``, ``notifications/initialized``,
``tools/list``, ``tools/call``. Notifications (no ``id``) get NO response;
EOF on stdin ends the loop. Unknown methods → ``-32601``; malformed JSON →
``-32700``; non-object request → ``-32600``.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, TextIO

from seahorse.facade.facade import MemoryFacade
from seahorse.facade.factory import build_facade
from seahorse.mcp.tools import TOOL_LIST, dispatch

PROFILE_URI = "io.seahorse.memory/v1"
"""The MCP profile URI. The regex assert at import time fails loud on a typo
(e.g. ``io.sehrose.memory``) — a malformed URI would silently break every
client, so we catch it at startup, not at first call."""

_PROTOCOL_VERSION = "2025-11-25"
_SERVER_NAME = "seahorse-memory"
_SERVER_VERSION = "0.1.0"

_PROFILE_RE = re.compile(r"^io\.seahorse\.memory/v[0-9]+$")
assert _PROFILE_RE.match(PROFILE_URI), f"profile URI malformed: {PROFILE_URI!r}"


def _error(request_id: Any, code: int, message: str, data: dict[str, Any] | None = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": err}


def _initialize_response(request_id: Any) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
        },
    }


def _tools_list_response(request_id: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOL_LIST}}


def _tools_call(facade: MemoryFacade, request: dict[str, Any], request_id: Any) -> dict:
    params = request.get("params") or {}
    if not isinstance(params, dict):
        return _error(
            request_id,
            -32602,
            "Invalid params",
            {"wire_shape_error": True, "detail": "params must be an object", "component": "#13"},
        )
    name = params.get("name")
    if not isinstance(name, str):
        return _error(
            request_id,
            -32602,
            "Invalid params",
            {"wire_shape_error": True, "detail": "tools/call requires 'name'", "component": "#13"},
        )
    arguments = params.get("arguments", {})
    if not isinstance(arguments, dict):
        return _error(
            request_id,
            -32602,
            "Invalid params",
            {
                "wire_shape_error": True,
                "detail": "'arguments' must be an object",
                "component": "#13",
            },
        )
    return dispatch(name, arguments, facade, request_id)


def handle_request(facade: MemoryFacade, request: Any) -> dict | None:
    """Handle one parsed JSON-RPC request → response dict (or None for notifications).

    Pure function: no I/O. ``serve`` drives the stdio loop and calls this per
    line, which makes the protocol logic unit-testable without spawning pipes.

    JSON-RPC 2.0: a Notification is a Request with the ``id`` member *absent*
    — an explicit ``id: null`` is a Request that gets a response with
    ``id: null``. We distinguish the two with ``"id" in request`` (not
    ``request.get("id") is None``, which conflates absent with explicit null).
    """
    if not isinstance(request, dict):
        return _error(None, -32600, "Invalid request", {"detail": "request must be an object"})
    if request.get("jsonrpc") != "2.0":
        return _error(
            request.get("id"),
            -32600,
            "Invalid request",
            {"detail": "jsonrpc must be \"2.0\""},
        )
    request_id = request.get("id")  # None for absent OR explicit null
    has_id = "id" in request  # distinguishes explicit id:null from absent
    method = request.get("method")

    if not isinstance(method, str):
        return _error(request_id, -32600, "Invalid request", {"detail": "missing 'method'"})

    # Notifications (id member ABSENT) get NO response — per JSON-RPC 2.0 / MCP.
    # ``initialize`` is a Request method (not a notification): an id-less
    # initialize is malformed in practice, but we still answer it with
    # ``id: null`` rather than silently dropping the handshake.
    if not has_id and method != "initialize":
        if method == "notifications/initialized":
            return None
        # Unknown notification: swallow (no response). Known notifications would
        # be handled here; unknown ones are ignored per JSON-RPC.
        return None

    if method == "initialize":
        return _initialize_response(request_id)
    if method == "tools/list":
        return _tools_list_response(request_id)
    if method == "tools/call":
        return _tools_call(facade, request, request_id)

    return _error(request_id, -32601, "Method not found", {"unknown_method": method})


def serve(
    facade: MemoryFacade, *, stdin: TextIO | None = None, stdout: TextIO | None = None
) -> None:
    """Run the stdio JSON-RPC 2.0 loop until EOF.

    Reads newline-delimited requests from ``stdin``, writes newline-delimited
    responses to ``stdout``. Each request is handled statelessly. ``stdin``
    and ``stdout`` default to ``sys.stdin`` / ``sys.stdout`` and are injectable
    for tests.
    """
    in_stream = stdin if stdin is not None else sys.stdin
    out_stream = stdout if stdout is not None else sys.stdout

    for line in in_stream:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            out_stream.write(json.dumps(_error(None, -32700, "Parse error")) + "\n")
            out_stream.flush()
            continue
        response = handle_request(facade, request)
        if response is not None:
            out_stream.write(json.dumps(response) + "\n")
            out_stream.flush()


def build_server(db_path: Path | str, *, clock: Any = None, config: Any = None) -> MemoryFacade:
    """Compose the real facade for production (Storage + engine + shaper + write-path).

    Returns the facade; the storage is retained by the engine's repository
    references (the connection manager lives as long as the repos do). For
    long-running processes call ``serve(build_server(db_path), ...)``.
    """
    facade, _storage = build_facade(db_path, clock=clock, config=config)
    return facade


__all__ = [
    "PROFILE_URI",
    "build_server",
    "handle_request",
    "serve",
]