"""Management commands for the CLI (#14) — init / status / uuid7 + reserved stubs.

These are the vault-lifecycle and operational commands (f5-14 §2.3). Five are
REAL in MVP-0 (their dependencies are built):

- ``init <vault>``   — write ``.seahorse/seahorse.toml`` (config.write_default_config).
- ``status``         — render resolved vault / db_path / config / init flag.
- ``uuid7``          — emit a fresh UUIDv7 (``seahorse.facade.new_uuid7`` — the
  facade re-exports it so #14 never reaches into #2 engine directly).
- ``migrate``        — apply SCHEMA migrations to the sidecar DB (commit 5; lives
  in ``cli/vault_ops.py`` so this module stays free of #6 SQL).
- ``inspect``        — read-only sidecar snapshot (commit 5; ``vault_ops``).
- ``index rebuild``  — regenerate the sidecar from the vault (commit 5;
  ``vault_ops``; ADR-10: reports conflicts, does not auto-pick).

The REMAINING commands are RESERVED STUBS (Cat C ``CLI_NOT_IN_MVP_0`` = 75):
their dependencies are not built in MVP-0 and the surface must fail-loud rather
than silently disappear (ADR-10 honesty):

- ``index verify``   — needs #3 + #7 (vec0 integrity).
- ``vigentes``       — MVP-1 full vigente set with freshness (decay-aware).
- ``activos-ahora``  — mediano decay-aware active set (needs ``expire``).
"""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

from seahorse.cli.config import SeahorseConfig, is_initialized, write_default_config
from seahorse.cli.errors import CliNotInMVP0
from seahorse.cli.output import OutputFormat, render_message
from seahorse.facade import new_uuid7

# Reserved management commands → Cat C CLI_NOT_IN_MVP_0 (75), with the reason
# each is deferred. Single source so app.py and tests agree on the surface.
# Commit 5 promoted migrate/inspect/index-rebuild to real commands (vault_ops),
# so the reserved surface is the remaining honest-stub set.
RESERVED_COMMANDS: dict[str, str] = {
    "index-verify": "index integrity check needs #3 + vec0 (#7)",
    "vigentes": "full vigente set with freshness is MVP-1",
    "activos-ahora": "decay-aware active set is mediano (needs expire)",
}


def run_init(
    vault: Path, *, fmt: OutputFormat = "human", out: TextIO
) -> None:
    """``seahorse init <vault>`` — bootstrap ``.seahorse/seahorse.toml``.

    Idempotent: overwrites an existing config. The vault directory is created
    if missing (bootstrap semantics — ``init`` is the command that brings a
    vault into being). ``.seahorse/`` is always created if missing.
    """
    vault = vault.expanduser().resolve()
    vault.mkdir(parents=True, exist_ok=True)
    cfg_path = write_default_config(vault)
    payload = {
        "command": "init",
        "vault": str(vault),
        "config_path": str(cfg_path),
        "status": "initialized",
    }
    render_message(
        payload,
        fmt=fmt,
        out=out,
        human_text=f"✓ Initialized Seahorse vault at {vault}\n  config: {cfg_path}\n",
    )


def run_status(
    config: SeahorseConfig, *, fmt: OutputFormat = "human", out: TextIO
) -> None:
    """``seahorse status`` — render the resolved vault / db / config snapshot."""
    payload = {
        "vault": str(config.vault),
        "seahorse_dir": str(config.seahorse_dir),
        "db_path": str(config.db_path),
        "db_exists": config.db_path.exists(),
        "initialized": is_initialized(config.vault),
        "default_extraction_mode": config.default_extraction_mode,
        "top_k": config.top_k,
    }
    human = (
        f"Seahorse vault: {config.vault}\n"
        f"  dir:     {config.seahorse_dir}\n"
        f"  db:      {config.db_path} ({'present' if payload['db_exists'] else 'absent'})\n"
        f"  mode:    {config.default_extraction_mode}\n"
        f"  top_k:   {config.top_k}\n"
    )
    render_message(payload, fmt=fmt, out=out, human_text=human)


def run_uuid7(*, fmt: OutputFormat = "human", out: TextIO) -> None:
    """``seahorse uuid7`` — emit a fresh UUIDv7 (RFC 9562)."""
    value = new_uuid7()
    payload = {"uuid": value, "version": 7}
    render_message(payload, fmt=fmt, out=out, human_text=f"{value}\n")


def run_reserved(command: str) -> None:
    """Refuse a reserved-in-MVP-0 management command (Cat C, exit 75).

    ``command`` is the canonical form (``index-rebuild`` not ``index rebuild``)
    so the reason lookup is deterministic.
    """
    reason = RESERVED_COMMANDS.get(command, "not implemented in MVP-0")
    # Display form: hyphens → spaces for the user-facing command name.
    display = command.replace("-", " ")
    raise CliNotInMVP0(display, reason=reason)


__all__ = [
    "RESERVED_COMMANDS",
    "run_init",
    "run_status",
    "run_uuid7",
    "run_reserved",
]