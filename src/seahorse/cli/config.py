"""Vault discovery + ``seahorse.toml`` config for the CLI (#14).

MVP-0 vault discovery (f5-14 §6.2; the upward climb is deferred to MVP-1):

1. ``--vault`` explicit flag (must be an existing directory).
2. ``SEAHORSE_VAULT`` environment variable.
3. ``./.seahorse/seahorse.toml`` in the current working directory.
4. ``CliVaultNotFound`` (exit 82) with a hint to ``seahorse init <vault>``.

``seahorse.toml`` is intentionally minimal in MVP-0 (f5-14 §Pins / §6.2: modes
hardcoded as constants). Only three keys are read:

.. code-block:: toml

   [seahorse]
   db_path = "seahorse.db"        # relative to .seahorse/
   default_extraction_mode = "skip"
   top_k = 10

Parsing uses the stdlib ``tomllib`` (Python 3.11+). Writing the default config
on ``init`` is done by hand (the file is tiny and stdlib has no TOML writer —
pulling in ``tomli_w`` would break the zero-extra-deps posture of this module).

The config is a frozen dataclass; mutation creates a new copy (immutability,
per the project coding style).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from seahorse.cli.errors import CliConfigInvalid, CliVaultNotFound

# Layout constants (f5-14 §6.2). The Seahorse dir lives inside the vault.
SEAHORSE_DIR_NAME = ".seahorse"
CONFIG_FILENAME = "seahorse.toml"
DEFAULT_DB_FILENAME = "seahorse.db"

# Default config values (MVP-0 austero, ADR-10). Modes are hardcoded constants
# per f5-14 §Pins — no rung/phase/rich/default_format switches in MVP-0.
DEFAULT_EXTRACTION_MODE = "skip"
DEFAULT_TOP_K = 10

# Valid extraction modes the CLI accepts at the config level (the facade's
# _resolve_mode is the authority for the remember primitive; this only guards
# the config file).
_VALID_CONFIG_MODES = frozenset({"skip", "llm"})

_VAULT_ENV = "SEAHORSE_VAULT"


@dataclass(frozen=True)
class SeahorseConfig:
    """Resolved Seahorse configuration for a vault.

    ``vault`` is the vault root; ``seahorse_dir`` is ``<vault>/.seahorse``;
    ``db_path`` is absolute. ``default_extraction_mode`` / ``top_k`` feed
    ``FacadeConfig`` via ``build_facade``.
    """

    vault: Path
    seahorse_dir: Path
    db_path: Path
    default_extraction_mode: str = DEFAULT_EXTRACTION_MODE
    top_k: int = DEFAULT_TOP_K

    def with_overrides(
        self, *, extraction_mode: str | None = None, top_k: int | None = None
    ) -> SeahorseConfig:
        """Return a copy with optional overrides (immutability)."""
        return replace(
            self,
            default_extraction_mode=extraction_mode or self.default_extraction_mode,
            top_k=top_k if top_k is not None else self.top_k,
        )


def is_initialized(vault: Path) -> bool:
    """True iff ``<vault>/.seahorse/seahorse.toml`` exists."""
    return (vault / SEAHORSE_DIR_NAME / CONFIG_FILENAME).is_file()


def resolve_vault(explicit: Path | None) -> Path:
    """Resolve the vault directory per the MVP-0 discovery order.

    Raises ``CliVaultNotFound`` (exit 82) if nothing resolves.
    """
    if explicit is not None:
        vault = explicit.expanduser().resolve()
        if not vault.is_dir():
            raise CliVaultNotFound(
                hint=f"--vault {explicit} is not an existing directory"
            )
        return vault

    env = os.environ.get(_VAULT_ENV)
    if env:
        vault = Path(env).expanduser().resolve()
        if not vault.is_dir():
            raise CliVaultNotFound(hint=f"${_VAULT_ENV}={env} is not an existing directory")
        return vault

    cwd = Path.cwd().resolve()
    if is_initialized(cwd):
        return cwd

    raise CliVaultNotFound()


def config_path_for(vault: Path) -> Path:
    """The canonical config path: ``<vault>/.seahorse/seahorse.toml``."""
    return vault / SEAHORSE_DIR_NAME / CONFIG_FILENAME


def load_config(
    vault: Path, *, explicit_config: Path | None = None
) -> SeahorseConfig:
    """Load ``SeahorseConfig`` for ``vault``.

    If ``explicit_config`` is given it is used instead of the canonical path
    (still must live inside a ``.seahorse``-resolved layout). A missing config
    file is tolerated only when ``.seahorse/`` does not exist yet — but callers
    reach here after ``resolve_vault`` (which requires the file for cwd
    discovery), so a missing file means a corrupted vault → ``CliConfigInvalid``.
    """
    seahorse_dir = vault / SEAHORSE_DIR_NAME
    cfg_path = explicit_config if explicit_config is not None else config_path_for(vault)

    if not cfg_path.is_file():
        raise CliConfigInvalid(f"config file not found: {cfg_path}")

    try:
        with cfg_path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise CliConfigInvalid(f"parse error: {exc}") from exc
    except OSError as exc:
        raise CliConfigInvalid(f"read error: {exc}") from exc

    section = data.get("seahorse")
    if not isinstance(section, dict):
        raise CliConfigInvalid("missing [seahorse] section")

    db_rel = section.get("db_path", DEFAULT_DB_FILENAME)
    if not isinstance(db_rel, str) or not db_rel:
        raise CliConfigInvalid("db_path must be a non-empty string")

    mode = section.get("default_extraction_mode", DEFAULT_EXTRACTION_MODE)
    if not isinstance(mode, str) or mode not in _VALID_CONFIG_MODES:
        raise CliConfigInvalid(
            f"default_extraction_mode={mode!r}; expected 'skip' or 'llm'"
        )

    top_k = section.get("top_k", DEFAULT_TOP_K)
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0:
        raise CliConfigInvalid("top_k must be a positive integer")

    db_path = (seahorse_dir / db_rel).resolve()
    return SeahorseConfig(
        vault=vault,
        seahorse_dir=seahorse_dir,
        db_path=db_path,
        default_extraction_mode=mode,
        top_k=top_k,
    )


def write_default_config(vault: Path) -> Path:
    """Write the minimal ``seahorse.toml`` into ``<vault>/.seahorse/``.

    Idempotent: overwrites an existing config. Returns the config path. The
    ``.seahorse`` directory is created if missing.
    """
    seahorse_dir = vault / SEAHORSE_DIR_NAME
    seahorse_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = config_path_for(vault)
    content = (
        "# Seahorse vault configuration (MVP-0).\n"
        "# Generated by `seahorse init`. Modes are hardcoded constants in MVP-0.\n\n"
        "[seahorse]\n"
        f'db_path = "{DEFAULT_DB_FILENAME}"\n'
        f'default_extraction_mode = "{DEFAULT_EXTRACTION_MODE}"\n'
        f"top_k = {DEFAULT_TOP_K}\n"
    )
    cfg_path.write_text(content, encoding="utf-8")
    return cfg_path


__all__ = [
    "SEAHORSE_DIR_NAME",
    "CONFIG_FILENAME",
    "DEFAULT_DB_FILENAME",
    "DEFAULT_EXTRACTION_MODE",
    "DEFAULT_TOP_K",
    "SeahorseConfig",
    "is_initialized",
    "resolve_vault",
    "config_path_for",
    "load_config",
    "write_default_config",
]