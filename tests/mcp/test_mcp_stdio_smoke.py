"""Real-stdio MCP smoke — the stdio half of the parity contract (ADR-013).

Spawns ``python -m seahorse.mcp --vault <tmp>`` as a real subprocess with
stdin/stdout pipes and drives the newline-delimited JSON-RPC 2.0 protocol.
The core session lives in ``contract_session.run_contract_session`` — ONE
behavior shared with the HTTP contract test. The stdio-only halves proven
here (divergent by transport design, never duplicated there):

- notification (no id) → NOTHING on stdout; proven by the NEXT reply arriving
  first (HTTP answers 202 + empty body instead).
- malformed JSON → -32700 with explicit ``id: null`` on stdout (HTTP answers
  400 without any id member).
- EOF on stdin → clean exit 0; missing vault → exit 82.

This catches what the in-process ``serve(io.StringIO)`` tests cannot: the
``main()`` launch path (argparse, vault resolution via ``seahorse.cli.config``,
``build_facade`` honoring ``seahorse.toml``, the Storage ``finally`` close),
the real process boundary, and real pipe I/O — including the
``serverInfo.version`` single-source from package metadata, asserted inside
the shared session.
"""

from __future__ import annotations

import contextlib
import json
import select
import subprocess
import sys
from pathlib import Path

import pytest

from seahorse.cli.config import write_default_config
from tests.mcp.contract_session import run_contract_session, tool_result

# Per-read deadline: a regression where the server returns None for an id'd
# request (a handler that forgets to emit a response) would otherwise block
# readline() forever — the finally's proc.wait() is unreachable while the
# try-body is stuck in readline, so the test would hang CI with no assertion.
_RECV_DEADLINE = 10.0


def _send(proc, req: dict) -> None:
    proc.stdin.write(json.dumps(req) + "\n")
    proc.stdin.flush()


def _send_raw(proc, line: str) -> None:
    proc.stdin.write(line + "\n")
    proc.stdin.flush()


def _recv(proc) -> dict:
    ready, _, _ = select.select([proc.stdout], [], [], _RECV_DEADLINE)
    if not ready:
        pytest.fail(
            f"no MCP response within {_RECV_DEADLINE}s "
            "(server likely returned None for an id'd request);\n"
            f"stderr:\n{proc.stderr.read()}"
        )
    line = proc.stdout.readline()
    assert line, "server closed stdout before a response arrived"
    return json.loads(line)


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    write_default_config(v)
    return v


def _spawn(vault: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "seahorse.mcp", "--vault", str(vault)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


def test_stdio_full_session(vault: Path) -> None:
    proc = _spawn(vault)
    try:
        # ①–⑨ the shared parity session (identical to the HTTP contract run).
        run_contract_session(lambda request: _send(proc, request), lambda: _recv(proc))

        # notification (no id) → NO response; the next request's reply arrives
        # first, proving the notification was silently consumed.
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(proc, {"jsonrpc": "2.0", "id": 10, "method": "tools/list"})
        nt_reply = _recv(proc)
        assert nt_reply["id"] == 10
        assert len(nt_reply["result"]["tools"]) == 15

        # malformed JSON → -32700; serve() serializes _error(None, ...) →
        # an explicit id:null on stdout (HTTP answers 400 WITHOUT any id).
        _send_raw(proc, "not json")
        parse = _recv(proc)
        assert parse["error"]["code"] == -32700
        assert parse["id"] is None

        # EOF → clean exit
        proc.stdin.close()
    finally:
        # Kill on ANY outcome (assertion failure, pytest.fail, or clean exit):
        # without this, a failure while stdin is still open leaves the server
        # blocked in its own readline, proc.wait() raises TimeoutExpired (which
        # masks the real AssertionError as __context__), and the
        # `python -m seahorse.mcp` process leaks as a zombie.
        with contextlib.suppress(Exception):
            proc.stdin.close()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    assert proc.returncode == 0, f"server exited {proc.returncode}:\n{proc.stderr.read()}"


def test_stdio_recall_pit_serves_pit_listing(vault: Path) -> None:
    # v1.3.0: recall with the loose pit_kind+pit_t pair resolves the PIT and the
    # PIT-capable listing serves it (success, not the old -32007 refusal).
    proc = _spawn(vault)
    try:
        _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        _recv(proc)
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "remember",
                    "arguments": {
                        "body": "PIT listing smoke",
                        "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"},
                    },
                },
            },
        )
        ep_id = tool_result(_recv(proc))["ep_id"]

        # known_at BEFORE the write → the row is not known yet (empty listing).
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "recall",
                    "arguments": {
                        "query": "anything",
                        "pit_kind": "known_at",
                        "pit_t": "2000-01-01T00:00:00Z",
                    },
                },
            },
        )
        assert tool_result(_recv(proc)) == []

        # known_at now (a far-future t includes everything ever created).
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "recall",
                    "arguments": {
                        "query": "anything",
                        "pit_kind": "known_at",
                        "pit_t": "2099-01-01T00:00:00Z",
                    },
                },
            },
        )
        rows = tool_result(_recv(proc))
        assert ep_id in [r["ep_id"] for r in rows]
        proc.stdin.close()
    finally:
        with contextlib.suppress(Exception):
            proc.stdin.close()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    assert proc.returncode == 0, f"server exited {proc.returncode}:\n{proc.stderr.read()}"


def test_stdio_missing_vault_exits_82(tmp_path: Path) -> None:
    proc = _spawn(tmp_path / "does-not-exist")
    proc.wait(timeout=15)
    assert proc.returncode == 82
    assert "CLI_VAULT_NOT_FOUND" in proc.stderr.read()