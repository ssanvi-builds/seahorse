"""Management commands for the CLI — init / status / uuid7 + reserved stubs.

These are the vault-lifecycle and operational commands. Five are REAL in the
current release (their dependencies are built):

- ``init <vault>``   — write ``.seahorse/seahorse.toml`` (config.write_default_config).
- ``status``         — render resolved vault / db_path / config / init flag.
- ``uuid7``          — emit a fresh UUIDv7 (``seahorse.facade.new_uuid7`` — the
  facade re-exports it so the CLI never reaches into the engine directly).
- ``migrate``        — apply SCHEMA migrations to the sidecar DB (lives in
  ``cli/vault_ops.py`` so this module stays free of persistence SQL).
- ``inspect``        — read-only sidecar snapshot (``vault_ops``).
- ``index rebuild``  — regenerate the sidecar from the vault (``vault_ops``;
  fail-loud: reports conflicts, does not auto-pick).

The REMAINING commands are RESERVED STUBS (Cat C ``CLI_NOT_IN_MVP_0`` = 75):
their dependencies are not built in the current release and the surface must
fail-loud rather than silently disappear:

- ``index verify``   — needs the frontmatter migrator + the embedder (vec0 integrity).
- ``vigentes``       — full current-state set with freshness (decay-aware) in a later release.
- ``activos-ahora``  — decay-aware active set, a medium-term goal (needs ``expire``).
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
RESERVED_COMMANDS: dict[str, str] = {
    "index-verify": "index integrity check needs the frontmatter migrator + the embedder",
    "vigentes": "full current-state set with freshness is a later release",
    "activos-ahora": "decay-aware active set is a medium-term goal (needs expire)",
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


def _llm_regime(config: SeahorseConfig) -> str:
    """Human-readable LLM regime from the ``[llm]`` config.

    ``skip (no [llm] config; run `seahorse init --llm`)`` when no provider is
    configured; otherwise ``llm:<provider>:<model>`` from the effective primary
    (the extraction route's first backend).
    """
    if config.llm is None:
        return "skip (no [llm] config; run `seahorse init --llm`)"
    provider = config.llm.primary.split("/", 1)[0]
    return f"llm:{provider}:{config.llm.primary}"


def run_status(
    config: SeahorseConfig, *, fmt: OutputFormat = "human", out: TextIO
) -> None:
    """``seahorse status`` — render the resolved vault / db / config snapshot."""
    from seahorse.embeddings.fastembed_backend import retrieval_status

    retrieval = retrieval_status()
    llm_regime = _llm_regime(config)
    payload = {
        "vault": str(config.vault),
        "seahorse_dir": str(config.seahorse_dir),
        "db_path": str(config.db_path),
        "db_exists": config.db_path.exists(),
        "initialized": is_initialized(config.vault),
        "default_extraction_mode": config.default_extraction_mode,
        "top_k": config.top_k,
        "retrieval": retrieval,
        "llm": llm_regime,
    }
    human = (
        f"Seahorse vault: {config.vault}\n"
        f"  dir:     {config.seahorse_dir}\n"
        f"  db:      {config.db_path} ({'present' if payload['db_exists'] else 'absent'})\n"
        f"  mode:    {config.default_extraction_mode}\n"
        f"  top_k:   {config.top_k}\n"
        f"  retrieval: {retrieval}\n"
        f"  llm:     {llm_regime}\n"
    )
    render_message(payload, fmt=fmt, out=out, human_text=human)


def run_uuid7(*, fmt: OutputFormat = "human", out: TextIO) -> None:
    """``seahorse uuid7`` — emit a fresh UUIDv7 (RFC 9562)."""
    value = new_uuid7()
    payload = {"uuid": value, "version": 7}
    render_message(payload, fmt=fmt, out=out, human_text=f"{value}\n")


def run_reserved(command: str) -> None:
    """Refuse a management command reserved in the current release (Cat C, exit 75).

    ``command`` is the canonical form (``index-rebuild`` not ``index rebuild``)
    so the reason lookup is deterministic.
    """
    reason = RESERVED_COMMANDS.get(command, "not implemented in the current release")
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