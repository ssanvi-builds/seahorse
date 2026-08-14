"""Seahorse CLI surface — Typer-based, sibling of the MCP (JSON-RPC) server.

A thin translation layer over the ``MemoryFacade``: the 6 memory-native
primitives (``remember`` / ``recall`` / ``recall-timeline`` / ``recall-full`` /
``improve`` / ``forget``) plus management commands (``init`` / ``status`` /
``uuid7`` / ``index rebuild``). It re-exports the canonical episode types
verbatim — no transformation, no domain logic (delegation purity).

Submodules:
- ``primitives``  — logic of the 6 memory-native commands (parser-agnostic).
- ``management``  — logic of init/status/uuid7/index rebuild (+ reserved stubs).
- ``app``         — the Typer application + ``main()`` entrypoint.
- ``config``      — vault discovery + ``seahorse.toml``.
- ``output``      — human / json / jsonl renderers.
- ``errors``      — ``CliError`` (Cat C, CLI-owned).
- ``exit_codes``  — exception → exit-code translation (mirrors the MCP catalog).
"""

from __future__ import annotations

from seahorse.cli.errors import (
    CliConfigInvalid,
    CliError,
    CliNotInMVP0,
    CliUsageError,
    CliVaultNotFound,
)
from seahorse.cli.exit_codes import (
    CAT_A,
    CAT_B,
    CLI_CONFIG_INVALID,
    CLI_NOT_IN_MVP_0,
    CLI_VAULT_NOT_FOUND,
    EXIT_GENERAL,
    EXIT_SUCCESS,
    EXIT_USAGE,
    translate,
)

__all__ = [
    "CliError",
    "CliNotInMVP0",
    "CliVaultNotFound",
    "CliConfigInvalid",
    "CliUsageError",
    "CAT_A",
    "CAT_B",
    "CLI_NOT_IN_MVP_0",
    "CLI_VAULT_NOT_FOUND",
    "CLI_CONFIG_INVALID",
    "EXIT_SUCCESS",
    "EXIT_GENERAL",
    "EXIT_USAGE",
    "translate",
]