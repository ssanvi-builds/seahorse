"""Real-subprocess MCP smoke — the HTTP half of the parity contract (ADR-013).

Spawns ``python -m seahorse.mcp --vault <tmp> --transport http --port 0`` and
drives the shared 9-step session over a REAL socket — the in-process envelope
and contract tests cannot see the ``main()`` launch path (argparse
``--transport`` → ``serve_http``, the ``--port 0`` ephemeral bind + stderr
listen line, env token wiring, the SIGINT → clean-exit shutdown).

The core session lives in ``contract_session.run_contract_session`` — ONE
behavior shared with the stdio smoke and the in-process HTTP contract. The
divergent transport behaviors (202 notification, 400 malformed without
``id``) are proven in ``test_http_server.py`` — not duplicated here; one 202
assert rides the real socket below only as launch-path wiring.
"""

from __future__ import annotations

import contextlib
import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from seahorse.cli.config import write_default_config
from tests.mcp.contract_session import run_contract_session
from tests.mcp.test_http_server import _json, _post

# Startup deadline: a regression that never binds would otherwise block
# readline() on stderr forever; each HTTP call is self-bounded by the
# _post helper's own urlopen timeout.
_LISTEN_DEADLINE = 10.0

_SUBPROCESS_TOKEN = "test-token"  # passed explicitly — the wiring under test
_LISTEN_PREFIX = "seahorse-mcp: listening on http://"


def _wait_listen_line(proc) -> str:
    """Read stderr until the bind line; return the real base URL (no path)."""
    seen: list[str] = []
    deadline = time.monotonic() + _LISTEN_DEADLINE
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([proc.stderr], [], [], remaining)[0]:
            pytest.fail(
                f"no 'listening on' line within {_LISTEN_DEADLINE}s; stderr so far:\n"
                + "".join(seen)
            )
        line = proc.stderr.readline()
        assert line, f"server stderr closed before the listen line; got:\n{''.join(seen)}"
        seen.append(line)
        if line.startswith(_LISTEN_PREFIX):
            return line[len(_LISTEN_PREFIX) :].strip()


def _spawn(vault: Path, *, env: dict[str, str]):
    cmd = [sys.executable, "-m", "seahorse.mcp", "--vault", str(vault)]
    cmd += ["--transport", "http", "--port", "0"]
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )


def _shut_down(proc) -> None:
    """SIGINT (not SIGTERM): serve_http catches KeyboardInterrupt and unwinds
    through its ``finally: httpd.server_close()`` plus main()'s
    ``storage.close()``, so the process exits 0. SIGTERM bypasses that handler
    and would exit -15 with neither close running."""
    with contextlib.suppress(Exception):
        proc.stdin.close()
    # The kill may target an already-reaped pid (the body's own SIGINT, or the
    # exit-99 path) — ProcessLookupError is a no-op there, never an error.
    with contextlib.suppress(Exception):
        os.kill(proc.pid, signal.SIGINT)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    write_default_config(v)
    return v


def test_http_full_session(vault: Path) -> None:
    proc = _spawn(vault, env={**os.environ, "SEAHORSE_HTTP_TOKEN": _SUBPROCESS_TOKEN})
    try:
        # The listen line carries the REAL port after --port 0 and has no path
        # suffix — clients POST to /mcp.
        url = f"http://{_wait_listen_line(proc)}/mcp"

        # The shared 9-step parity session, byte-for-byte the stdio run.
        pending: list[dict] = []

        def send(request: dict) -> None:
            code, _headers, body = _post(url, body=_json(request), token=_SUBPROCESS_TOKEN)
            assert code == 200, f"transport rejected a request ({code}): {body!r}"
            pending.append(json.loads(body))

        ep_id, new_id = run_contract_session(send, lambda: pending.pop(0))
        assert ep_id
        assert new_id != ep_id

        # A notification rides 202 + empty body through the REAL pipeline too —
        # wiring check only; the shape itself is proven in test_http_server.py.
        code, _headers, empty = _post(
            url,
            body=_json({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            token=_SUBPROCESS_TOKEN,
        )
        assert code == 202
        assert empty == b""

        # Ctrl-C → serve_http unwinds → clean exit (asserted after the finally).
        os.kill(proc.pid, signal.SIGINT)
    finally:
        # Kill on ANY outcome (assertion failure, pytest.fail, or clean exit):
        # a failure mid-session must not leak the spawned server process.
        _shut_down(proc)
    assert proc.returncode == 0, f"server exited {proc.returncode}:\n{proc.stderr.read()}"


def test_http_missing_token_exits_99(vault: Path) -> None:
    # A REAL vault on purpose: vault resolution precedes the token check, so a
    # missing vault would exit 82 first and mask the 99 this asserts.
    env = dict(os.environ)
    env.pop("SEAHORSE_HTTP_TOKEN", None)  # a stray developer env must not leak in
    proc = _spawn(vault, env=env)
    try:
        proc.wait(timeout=15)
    finally:
        with contextlib.suppress(Exception):
            proc.kill()
            proc.wait()
    assert proc.returncode == 99
    assert "SEAHORSE_HTTP_TOKEN" in proc.stderr.read()