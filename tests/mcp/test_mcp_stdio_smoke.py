"""Real-stdio MCP smoke — the systematic functional review for the agent
surface, committed as regression tests.

Spawns ``python -m seahorse.mcp --vault <tmp>`` as a real subprocess with
stdin/stdout pipes and drives the newline-delimited JSON-RPC 2.0 protocol:
initialize → tools/list (7) → remember → recall → improve → forget →
build_pit → notification (no reply) → deferred tool (-32601) → malformed
(-32700) → EOF (clean exit 0).

This catches what the in-process ``serve(io.StringIO)`` tests cannot: the
``main()`` launch path (argparse, vault resolution via ``seahorse.cli.config``,
``build_facade`` honoring ``seahorse.toml``, the Storage ``finally`` close),
the real process boundary, and real pipe I/O (incl. the ``serverInfo.version``
single-source from package metadata).
"""

from __future__ import annotations

import contextlib
import importlib.metadata
import json
import select
import subprocess
import sys
from pathlib import Path

import pytest

from seahorse.cli.config import write_default_config

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


def _content(resp: dict):
    return json.loads(resp["result"]["content"][0]["text"])


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
        # initialize
        _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        init = _recv(proc)
        assert init["result"]["protocolVersion"] == "2025-11-25"
        assert init["result"]["serverInfo"]["name"] == "seahorse-memory"
        # version is single-sourced from package metadata
        try:
            expected_version = importlib.metadata.version("seahorse-memory")
        except importlib.metadata.PackageNotFoundError:
            expected_version = "0.0.0"
        assert init["result"]["serverInfo"]["version"] == expected_version

        # tools/list → exactly 14
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        listing = _recv(proc)
        names = {t["name"] for t in listing["result"]["tools"]}
        assert len(names) == 14

        # remember → ep_id
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "remember",
                    "arguments": {
                        "body": "Sergio lives in Madrid",
                        "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"},
                    },
                },
            },
        )
        wr = _content(_recv(proc))
        assert wr["status"] == "ACTIVE"
        ep_id = wr["ep_id"]

        # recall → shows it
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "recall", "arguments": {"query": "madrid"}},
            },
        )
        rows = _content(_recv(proc))
        assert ep_id in [r["ep_id"] for r in rows]

        # improve → new episode superseding the old
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "improve",
                    "arguments": {
                        "ep_id": ep_id,
                        "new_body": "Sergio lives in Barcelona",
                        "by": {"agent_id": "s", "session_id": "s2", "source_type": "human"},
                        "reason": "correction",
                    },
                },
            },
        )
        new_ep = _content(_recv(proc))
        assert new_ep["id"] != ep_id
        new_id = new_ep["id"]

        # forget → invalidated
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "forget",
                    "arguments": {
                        "ep_id": new_id,
                        "reason": "wrong",
                        "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"},
                    },
                },
            },
        )
        forgotten = _content(_recv(proc))
        assert forgotten["invalid_at"] is not None

        # build_pit all-None → null
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "build_pit", "arguments": {}},
            },
        )
        assert _content(_recv(proc)) is None

        # notification (no id) → NO response; the next request's reply arrives
        # first, proving the notification was silently consumed.
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(proc, {"jsonrpc": "2.0", "id": 8, "method": "tools/list"})
        nt_reply = _recv(proc)
        assert nt_reply["id"] == 8
        assert len(nt_reply["result"]["tools"]) == 14

        # unknown tool → -32601 (expire is still outside the MCP surface)
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "expire", "arguments": {}},
            },
        )
        assert _recv(proc)["error"]["code"] == -32601

        # malformed JSON → -32700
        _send_raw(proc, "not json")
        assert _recv(proc)["error"]["code"] == -32700

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


def test_stdio_missing_vault_exits_82(tmp_path: Path) -> None:
    proc = _spawn(tmp_path / "does-not-exist")
    proc.wait(timeout=15)
    assert proc.returncode == 82
    assert "CLI_VAULT_NOT_FOUND" in proc.stderr.read()
