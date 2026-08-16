"""Entrypoint tests for the runnable stdio MCP server.

Two surfaces must launch the SAME server from an install:

- ``seahorse-mcp`` console script + ``python -m seahorse.mcp`` →
  ``seahorse.mcp.profile:main``, which resolves the vault/db the SAME way the
  CLI does (``seahorse.cli.config``: ``--vault`` / ``SEAHORSE_VAULT`` / cwd) and
  runs ``serve(build_server(db_path))``.
- ``seahorse mcp`` CLI subcommand → delegates to ``serve`` over the CliContext
  facade (reuses the storage lifecycle closed by ``main()``'s finally).

Invariants guarded here:
- ``main`` exists and is callable with injectable stdio (no real pipes needed).
- vault/db resolution reuses the CLI extension point (a missing vault → exit
  82, not a traceback; ``SEAHORSE_VAULT`` is honored).
- ``serverInfo.version`` is single-sourced from ``importlib.metadata`` so the
  version bump flows without touching ``profile.py``.
- ``import seahorse.mcp`` stays stdlib-only: it must NOT load Typer nor pull
  ``seahorse.cli`` (the cli import is deferred to inside ``main()``).
"""

from __future__ import annotations

import io
import json
import subprocess
import sys

from seahorse.cli.config import write_default_config

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _line(request: dict) -> str:
    return json.dumps(request) + "\n"


def _responses(buf) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# main() exists + serves over injected stdio
# ---------------------------------------------------------------------------


def test_main_is_callable() -> None:
    from seahorse.mcp.profile import main

    assert callable(main)


def test_main_serves_initialize_and_tools_list(tmp_path) -> None:
    from seahorse.mcp.profile import main

    vault = tmp_path / "vault"
    write_default_config(vault)

    stdin = io.StringIO(
        _line({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        + _line({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    )
    stdout = io.StringIO()
    code = main(["--vault", str(vault)], stdin=stdin, stdout=stdout)

    assert code == 0
    resps = _responses(stdout)
    assert len(resps) == 2
    assert resps[0]["result"]["protocolVersion"] == "2025-11-25"
    assert resps[0]["result"]["serverInfo"]["name"] == "seahorse-memory"
    names = {t["name"] for t in resps[1]["result"]["tools"]}
    assert len(names) == 14


def test_main_creates_db_and_runs_remember_recall(tmp_path) -> None:
    """First use through ``main`` auto-migrates the DB (Storage construction)."""
    from seahorse.mcp.profile import main

    vault = tmp_path / "vault"
    write_default_config(vault)

    remember_args = {
        "body": "Sergio lives in Madrid",
        "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"},
    }
    stdin = io.StringIO(
        _line({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "remember", "arguments": remember_args}})
        + _line({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "recall", "arguments": {"query": "madrid"}}})
    )
    stdout = io.StringIO()
    code = main(["--vault", str(vault)], stdin=stdin, stdout=stdout)

    assert code == 0
    resps = _responses(stdout)
    wr = json.loads(resps[0]["result"]["content"][0]["text"])
    assert wr["status"] == "ACTIVE"
    rows = json.loads(resps[1]["result"]["content"][0]["text"])
    assert wr["ep_id"] in [r["ep_id"] for r in rows]


# ---------------------------------------------------------------------------
# vault/db resolution reuses the CLI extension point
# ---------------------------------------------------------------------------


def test_main_vault_not_found_exits_82(tmp_path, capsys) -> None:
    from seahorse.mcp.profile import main

    code = main(["--vault", str(tmp_path / "does-not-exist")])

    assert code == 82
    err = capsys.readouterr().err
    assert "CLI_VAULT_NOT_FOUND" in err


def test_main_honors_seahorse_vault_env(tmp_path, monkeypatch) -> None:
    from seahorse.mcp.profile import main

    vault = tmp_path / "vault"
    write_default_config(vault)
    monkeypatch.setenv("SEAHORSE_VAULT", str(vault))

    stdin = io.StringIO(_line({"jsonrpc": "2.0", "id": 1, "method": "initialize"}))
    stdout = io.StringIO()
    code = main([], stdin=stdin, stdout=stdout)  # no --vault → env must resolve

    assert code == 0
    resps = _responses(stdout)
    assert resps[0]["result"]["serverInfo"]["name"] == "seahorse-memory"


# ---------------------------------------------------------------------------
# serverInfo.version single-sourced from package metadata
# ---------------------------------------------------------------------------


def test_server_version_sourced_from_metadata() -> None:
    import importlib.metadata

    from seahorse.mcp.profile import _SERVER_VERSION

    try:
        expected = importlib.metadata.version("seahorse")
    except importlib.metadata.PackageNotFoundError:
        expected = "0.0.0"
    assert expected == _SERVER_VERSION


# ---------------------------------------------------------------------------
# import-laziness: `import seahorse.mcp` must NOT load Typer nor seahorse.cli
# ---------------------------------------------------------------------------


def test_import_seahorse_mcp_does_not_load_typer_nor_cli() -> None:
    """``seahorse.mcp`` stays stdlib-only at import — Typer + ``seahorse.cli``
    are deferred to ``main()`` (the launch path), never pulled on a bare import.

    Run in a fresh subprocess so the assertion sees a clean ``sys.modules``
    (this process has typer/cli loaded by other tests).
    """
    script = (
        "import seahorse.mcp, sys; "
        "assert 'typer' not in sys.modules, 'typer leaked by seahorse.mcp import'; "
        "assert not any(k == 'seahorse.cli' or k.startswith('seahorse.cli.') "
        "for k in sys.modules), 'seahorse.cli leaked by seahorse.mcp import'; "
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, (
        f"import-laziness guard failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert result.stdout.strip() == "ok"


# ---------------------------------------------------------------------------
# `seahorse mcp` subcommand is wired
# ---------------------------------------------------------------------------


def test_seahorse_mcp_subcommand_is_listed() -> None:
    from tests.cli.conftest import invoke

    code, out, _ = invoke(["--help"])
    assert code == 0
    assert "mcp" in out


# ---------------------------------------------------------------------------
# review fixes (pre-commit review)
# ---------------------------------------------------------------------------


class _BrokenStdout(io.StringIO):
    """A stdout that simulates a client which dropped its read end: every
    write raises BrokenPipeError. A stdio server must treat this as a clean
    disconnect (exit 0), not a crash (traceback + exit 1)."""

    def write(self, _payload: str) -> int:  # noqa: D401, ARG002
        raise BrokenPipeError()

    def flush(self) -> None:  # noqa: ARG002
        pass


def test_main_survives_broken_client_pipe(tmp_path) -> None:
    """A closed client pipe ends ``serve`` cleanly — exit 0, no traceback."""
    from seahorse.mcp.profile import main

    vault = tmp_path / "vault"
    write_default_config(vault)
    stdin = io.StringIO(_line({"jsonrpc": "2.0", "id": 1, "method": "initialize"}))
    stdout = _BrokenStdout()
    code = main(["--vault", str(vault)], stdin=stdin, stdout=stdout)

    assert code == 0  # clean disconnect, not a crash


def test_main_honors_vault_top_k(tmp_path) -> None:
    """The console script honors ``seahorse.toml`` ``top_k`` (parity with the
    ``seahorse mcp`` subcommand). With 4 current-state facts and ``top_k = 2``,
    recall
    returns at most 2; without the fix (default ``top_k = 10``) it would be 4.
    """
    from seahorse.mcp.profile import main

    vault = tmp_path / "vault"
    seahorse_dir = vault / ".seahorse"
    seahorse_dir.mkdir(parents=True)
    (seahorse_dir / "seahorse.toml").write_text(
        "[seahorse]\n"
        'db_path = "seahorse.db"\n'
        'default_extraction_mode = "skip"\n'
        "top_k = 2\n",
        encoding="utf-8",
    )

    lines: list[str] = []
    for i in range(4):
        lines.append(
            _line(
                {
                    "jsonrpc": "2.0",
                    "id": i + 1,
                    "method": "tools/call",
                    "params": {
                        "name": "remember",
                        "arguments": {
                            "body": f"fact number {i}",
                            "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"},
                        },
                    },
                }
            )
        )
    lines.append(
        _line(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "recall", "arguments": {"query": "x"}},
            }
        )
    )
    stdin = io.StringIO("".join(lines))
    stdout = io.StringIO()
    code = main(["--vault", str(vault)], stdin=stdin, stdout=stdout)

    assert code == 0
    resps = _responses(stdout)
    rows = json.loads(resps[-1]["result"]["content"][0]["text"])
    assert len(rows) <= 2


def test_main_help_returns_zero(capsys) -> None:
    """argparse --help raises SystemExit(0); main honors it as a returned int."""
    from seahorse.mcp.profile import main

    capsys.readouterr()  # discard argparse's help text
    assert main(["--help"]) == 0


def test_main_bad_flag_returns_two(capsys) -> None:
    """argparse bad flag raises SystemExit(2); main returns 2 (usage), not a raise."""
    from seahorse.mcp.profile import main

    capsys.readouterr()
    assert main(["--bogus"]) == 2