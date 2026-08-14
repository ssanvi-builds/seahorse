"""stdio JSON-RPC 2.0 server for the ``io.seahorse.memory/v1`` profile.

Stdlib-only framing (no ``mcp``/FastMCP SDK): newline-delimited JSON-RPC 2.0
over stdio. The MCP protocol is JSON-RPC 2.0 standard and SDK-independent; the
code repo is stdlib-only (``dependencies = []``) and ``main`` is clone-and-run,
so we hand-roll the framing with ``json`` + ``sys``. MCP spec pinned
``2025-11-25``.

Methods handled: ``initialize``, ``notifications/initialized``, ``tools/list``,
``tools/call``. Notifications (no ``id``) get NO response; EOF on stdin ends
the loop. Unknown methods → ``-32601``; malformed JSON → ``-32700``;
non-object request → ``-32600``.
"""

from __future__ import annotations

import json
import re
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any, Literal, TextIO, cast

from seahorse.facade.facade import MemoryFacade
from seahorse.facade.factory import build_facade
from seahorse.mcp.tools import TOOL_LIST, dispatch

PROFILE_URI = "io.seahorse.memory/v1"
"""The MCP profile URI. The regex assert at import time fails loud on a typo
(e.g. ``io.sehrose.memory``) — a malformed URI would silently break every
client, so we catch it at startup, not at first call."""

_PROTOCOL_VERSION = "2025-11-25"
_SERVER_NAME = "seahorse-memory"

# Single-source the server version from the installed package metadata so the
# version bump in pyproject.toml flows here without a hardcoded constant to keep
# in sync. Falls back to "0.0.0" when the package is not installed (a bare
# checkout run via PYTHONPATH, no metadata) — still a valid semver-ish string.
try:
    _SERVER_VERSION = _pkg_version("seahorse")
except PackageNotFoundError:  # not installed (bare checkout, no metadata)
    _SERVER_VERSION = "0.0.0"

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


def _safe_write(out_stream: TextIO, payload: str) -> bool:
    """Write ``payload`` + newline to ``out_stream``; return False if the client
    pipe is gone (``BrokenPipeError`` / ``ConnectionError``).

    A stdio server treats a closed client pipe as a normal disconnect, not a
    crash: the editor/MCP host/wrapper on the other end has dropped its read
    end (restart, crash, or a wrapper that closes the pipe). Without this guard
    the write/flush would raise, propagate out of ``serve``, and surface as a
    non-zero exit + traceback on the console-script path (or ``exit 1`` +
    stderr noise on the ``seahorse mcp`` subcommand path) — a restart-loop hazard
    for any launcher that retries on non-zero. Returning False lets ``serve``
    end the loop cleanly.
    """
    try:
        out_stream.write(payload)
        out_stream.flush()
    except (BrokenPipeError, ConnectionError):
        return False
    return True


def serve(
    facade: MemoryFacade, *, stdin: TextIO | None = None, stdout: TextIO | None = None
) -> None:
    """Run the stdio JSON-RPC 2.0 loop until EOF.

    Reads newline-delimited requests from ``stdin``, writes newline-delimited
    responses to ``stdout``. Each request is handled statelessly. ``stdin``
    and ``stdout`` default to ``sys.stdin`` / ``sys.stdout`` and are injectable
    for tests.

    A closed client pipe (``BrokenPipeError`` / ``ConnectionError`` on read or
    write) ends the loop cleanly — a stdio server exits 0 on disconnect, not
    non-zero with a traceback (see ``_safe_write``).
    """
    in_stream = stdin if stdin is not None else sys.stdin
    out_stream = stdout if stdout is not None else sys.stdout

    try:
        for line in in_stream:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                if not _safe_write(
                    out_stream, json.dumps(_error(None, -32700, "Parse error")) + "\n"
                ):
                    return
                continue
            response = handle_request(facade, request)
            if response is not None and not _safe_write(out_stream, json.dumps(response) + "\n"):
                return
    except (BrokenPipeError, ConnectionError):
        # Client closed the pipe mid-session — clean disconnect, not a crash.
        return


def build_server(db_path: Path | str, *, clock: Any = None, config: Any = None) -> MemoryFacade:
    """Compose the real facade for production (Storage + engine + shaper + write-path).

    Returns the facade; the storage is retained by the engine's repository
    references (the connection manager lives as long as the repos do). For
    long-running processes call ``serve(build_server(db_path), ...)``.
    """
    facade, _storage = build_facade(db_path, clock=clock, config=config)
    return facade


def main(
    argv: list[str] | None = None, *, stdin: TextIO | None = None, stdout: TextIO | None = None
) -> int:
    """Console-script + ``python -m seahorse.mcp`` entrypoint.

    Resolves the vault + ``db_path`` the SAME way the CLI does (via
    ``seahorse.cli.config``: ``--vault`` / ``SEAHORSE_VAULT`` / cwd discovery),
    builds the real facade with the vault's ``seahorse.toml`` honored
    (``default_extraction_mode`` + ``top_k`` — mirroring ``cli/app.py``'s
    ``CliContext.facade()`` so the console script and the ``seahorse mcp``
    subcommand are truly equivalent), and runs ``serve`` over stdio.

    Errors are translated through the shared ``seahorse.cli.exit_codes.translate``
    module, so a missing vault exits 82 (not a traceback). Argparse usage errors
    (``--help`` → 0, bad flag → 2) are caught and returned as ints so the
    ``-> int`` contract holds for in-process/library callers (argparse raises
    ``SystemExit``, which is a ``BaseException`` and so escapes the
    ``except Exception`` below).

    The ``seahorse.cli`` + ``seahorse.facade.types`` imports are deferred to
    INSIDE this function so a bare ``import seahorse.mcp`` never pulls the CLI
    (the ``seahorse.mcp`` package stays stdlib-only at import; only the launch
    path needs vault resolution). ``stdin``/``stdout`` are injectable for tests
    (default to ``sys.stdin`` / ``sys.stdout``). The SQLite ``Storage`` is
    closed in a ``finally`` around ``serve`` so connections release on a clean
    disconnect too (symmetric with the CLI subcommand's lifecycle).
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="seahorse-mcp",
        description="Run the Seahorse stdio MCP server (io.seahorse.memory/v1).",
    )
    parser.add_argument("--vault", help="Vault root dir (default: discover).")
    parser.add_argument("--config", help="Explicit seahorse.toml path.")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse raises SystemExit(0) on --help, SystemExit(2) on a bad flag;
        # honor the code without a traceback (parity with cli/app.py main()).
        return int(exc.code) if isinstance(exc.code, int) else 1

    from seahorse.cli.config import load_config, resolve_vault
    from seahorse.cli.exit_codes import translate
    from seahorse.facade.types import FacadeConfig

    try:
        vault = resolve_vault(Path(args.vault) if args.vault else None)
        cfg = load_config(vault, explicit_config=Path(args.config) if args.config else None)
        # Honor seahorse.toml exactly as `seahorse mcp` does (CliContext.facade):
        # config.load_config narrows mode to {skip, llm}; cast for mypy.
        mode = cast("Literal['skip', 'llm']", cfg.default_extraction_mode)
        facade, storage = build_facade(
            cfg.db_path,
            config=FacadeConfig(default_extraction_mode=mode, top_k=cfg.top_k),
        )
    except Exception as exc:  # noqa: BLE001 — translate is the fail-loud boundary (cli-owned)
        code, info = translate(exc)
        label = (
            info.get("seahorse_code")
            or info.get("exception_class")
            or info.get("cli_code")
            or "ERROR"
        )
        sys.stderr.write(f"seahorse-mcp: {label}: {info.get('detail', str(exc))}\n")
        return code

    try:
        serve(facade, stdin=stdin, stdout=stdout)
    finally:
        storage.close()
    return 0


__all__ = [
    "PROFILE_URI",
    "build_server",
    "handle_request",
    "main",
    "serve",
]