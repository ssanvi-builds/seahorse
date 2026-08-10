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

# LLM factory default (2026-08-04 decision): a user with NOTHING starts on
# local Ollama — zero registration, zero key, data never leaves the machine.
# qwen3:1.7b is the medium-hardware floor for decent extraction; 0.6b is the
# low-end option offered by the wizard. Cloud providers are the quality lever
# when a free-tier key is present.
DEFAULT_LLM_PRIMARY = "ollama/qwen3:1.7b"
DEFAULT_LLM_TIMEOUT_S = 20.0  # extraction role timeout (f5-04 §4.4)

# Valid extraction modes the CLI accepts at the config level (the facade's
# _resolve_mode is the authority for the remember primitive; this only guards
# the config file).
_VALID_CONFIG_MODES = frozenset({"skip", "llm"})

# Observer defaults (obsiforge §4.3/§4.6). The observer is OPT-IN: a vault
# without an ``[observe]`` section has ``observe=None`` until ``seahorse setup``
# writes it. ``skip_tools`` / ``drop_tools`` mirror the threshold module.
DEFAULT_SKIP_TOOLS: tuple[str, ...] = ("WebSearch", "WebFetch")
DEFAULT_DROP_TOOLS: tuple[str, ...] = ("Read", "Bash")
DEFAULT_OBSERVE_SOCKET = "observer/observer.sock"

_VAULT_ENV = "SEAHORSE_VAULT"


@dataclass(frozen=True)
class LlmConfig:
    """The ``[llm]`` section — the extraction role route (f5-04 §2.5).

    ``primary`` / ``secondary`` / ``tertiary`` are LiteLLM ``provider/model``
    ids in fallback order; ``timeout_s`` is the per-call extraction timeout.
    ``None`` secondary/tertiary means the chain stops there. A vault with no
    ``[llm]`` section has ``SeahorseConfig.llm is None`` → the CLI wires no
    client and the write path keeps its honest llm→skip degrade.
    """

    primary: str
    secondary: str | None = None
    tertiary: str | None = None
    timeout_s: float = DEFAULT_LLM_TIMEOUT_S


@dataclass(frozen=True)
class ObserveConfig:
    """The ``[observe]`` section — the observer capture policy (obsiforge §4).

    ``enabled`` is True when the section is present (``seahorse setup`` writes
    it). ``extraction`` is the write-path mode (skip-first default, ADR-09).
    ``skip_tools`` / ``drop_tools`` are the threshold allowlists (§4.3).
    ``socket_path`` is relative to ``.seahorse/`` (the unix socket the hooks
    POST to, §4.4). ``token`` is the optional auth token (§15.2 redesign 10).
    """

    enabled: bool = True
    extraction: str = "skip"
    skip_tools: tuple[str, ...] = DEFAULT_SKIP_TOOLS
    drop_tools: tuple[str, ...] = DEFAULT_DROP_TOOLS
    socket_path: str = DEFAULT_OBSERVE_SOCKET
    token: str | None = None


# Procedural skill defaults (L2c §6.1). The section is OPT-IN: a vault without
# a ``[procedural]`` section has ``procedural=None`` and the CLI uses the
# module defaults (min_trust=medium, empty loadout).
DEFAULT_MIN_TRUST = "medium"


@dataclass(frozen=True)
class ProceduralSection:
    """The ``[procedural]`` section — skill defaults (L2c §6.1).

    ``min_trust`` is the R5 trust-gate default (low | medium | high) applied by
    ``seahorse skill show`` when the caller does not pass ``--min-trust``.
    ``loadout`` is the explicit per-agent skill loadout (Tencent pattern): the
    agent declares which skills it equips; empty means no loadout is pinned.
    """

    min_trust: str = DEFAULT_MIN_TRUST
    loadout: tuple[str, ...] = ()


@dataclass(frozen=True)
class SeahorseConfig:
    """Resolved Seahorse configuration for a vault.

    ``vault`` is the vault root; ``seahorse_dir`` is ``<vault>/.seahorse``;
    ``db_path`` is absolute. ``default_extraction_mode`` / ``top_k`` feed
    ``FacadeConfig`` via ``build_facade``; ``llm`` (optional ``[llm]`` section)
    feeds the write-path ``LiteLLMBackend`` route; ``observe`` (optional
    ``[observe]`` section) feeds the observer capture layer.
    """

    vault: Path
    seahorse_dir: Path
    db_path: Path
    default_extraction_mode: str = DEFAULT_EXTRACTION_MODE
    top_k: int = DEFAULT_TOP_K
    llm: LlmConfig | None = None
    observe: ObserveConfig | None = None
    procedural: ProceduralSection | None = None

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

    # Optional [llm] section (M4-C.3). Missing → llm=None (no client wired,
    # honest llm→skip degrade). Present → validate the role route.
    llm = _parse_llm_section(data.get("llm"))

    # Optional [observe] section (Sprint B). Missing → observe=None (the
    # observer is opt-in until `seahorse setup` writes it).
    observe = _parse_observe_section(data.get("observe"))

    # Optional [procedural] section (Sprint C). Missing → procedural=None (the
    # CLI uses the module defaults: min_trust=medium, empty loadout).
    procedural = _parse_procedural_section(data.get("procedural"))

    return SeahorseConfig(
        vault=vault,
        seahorse_dir=seahorse_dir,
        db_path=db_path,
        default_extraction_mode=mode,
        top_k=top_k,
        llm=llm,
        observe=observe,
        procedural=procedural,
    )


def _parse_llm_section(raw: object) -> LlmConfig | None:
    """Validate an optional ``[llm]`` section into a ``LlmConfig``.

    ``None`` input (no section) → ``None``. Any structurally wrong value is a
    ``CliConfigInvalid`` (Cat C, exit 83) — a config typo fails loud, not as a
    silent degrade at runtime.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CliConfigInvalid("llm must be a [llm] table")

    primary = raw.get("primary", DEFAULT_LLM_PRIMARY)
    if not isinstance(primary, str) or not primary:
        raise CliConfigInvalid("llm.primary must be a non-empty string")

    def _optional_model(key: str) -> str | None:
        v = raw.get(key)
        if v is None:
            return None
        if not isinstance(v, str) or not v:
            raise CliConfigInvalid(f"llm.{key} must be a non-empty string")
        return v

    secondary = _optional_model("secondary")
    tertiary = _optional_model("tertiary")

    timeout_s = raw.get("timeout_s", DEFAULT_LLM_TIMEOUT_S)
    if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool) or timeout_s <= 0:
        raise CliConfigInvalid("llm.timeout_s must be a positive number")

    return LlmConfig(
        primary=primary,
        secondary=secondary,
        tertiary=tertiary,
        timeout_s=float(timeout_s),
    )


def _parse_observe_section(raw: object) -> ObserveConfig | None:
    """Validate an optional ``[observe]`` section into an ``ObserveConfig``.

    ``None`` input (no section) → ``None`` (observer opt-in). Any structurally
    wrong value is a ``CliConfigInvalid`` (Cat C, exit 83) — a config typo
    fails loud, not as a silent degrade at runtime.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CliConfigInvalid("observe must be a [observe] table")

    extraction = raw.get("extraction", "skip")
    if not isinstance(extraction, str) or extraction not in _VALID_CONFIG_MODES:
        raise CliConfigInvalid(
            f"observe.extraction={extraction!r}; expected 'skip' or 'llm'"
        )

    def _tool_list(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
        v = raw.get(key, default)
        if not isinstance(v, (list, tuple)) or not all(isinstance(x, str) and x for x in v):
            raise CliConfigInvalid(f"observe.{key} must be a list of non-empty strings")
        return tuple(v)

    skip_tools = _tool_list("skip_tools", DEFAULT_SKIP_TOOLS)
    drop_tools = _tool_list("drop_tools", DEFAULT_DROP_TOOLS)

    socket_path = raw.get("socket_path", DEFAULT_OBSERVE_SOCKET)
    if not isinstance(socket_path, str) or not socket_path:
        raise CliConfigInvalid("observe.socket_path must be a non-empty string")

    token = raw.get("token")
    if token is not None and (not isinstance(token, str) or not token):
        raise CliConfigInvalid("observe.token must be a non-empty string or absent")

    return ObserveConfig(
        enabled=True,
        extraction=extraction,
        skip_tools=skip_tools,
        drop_tools=drop_tools,
        socket_path=socket_path,
        token=token,
    )


def _parse_procedural_section(raw: object) -> ProceduralSection | None:
    """Validate an optional ``[procedural]`` section into a ``ProceduralSection``.

    ``None`` input (no section) → ``None`` (the CLI uses the module defaults:
    min_trust=medium, empty loadout). Any structurally wrong value is a
    ``CliConfigInvalid`` (Cat C, exit 83) — a config typo fails loud, not as a
    silent degrade at runtime.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CliConfigInvalid("procedural must be a [procedural] table")

    min_trust = raw.get("min_trust", DEFAULT_MIN_TRUST)
    if not isinstance(min_trust, str) or min_trust not in ("low", "medium", "high"):
        raise CliConfigInvalid(
            f"procedural.min_trust={min_trust!r}; expected 'low' | 'medium' | 'high'"
        )

    loadout = raw.get("loadout", ())
    if not isinstance(loadout, (list, tuple)) or not all(
        isinstance(x, str) and x for x in loadout
    ):
        raise CliConfigInvalid("procedural.loadout must be a list of non-empty strings")

    return ProceduralSection(min_trust=min_trust, loadout=tuple(loadout))


def write_default_config(vault: Path) -> Path:
    """Write the minimal ``seahorse.toml`` into ``<vault>/.seahorse/``.

    Idempotent: overwrites an existing config. Returns the config path. The
    ``.seahorse`` directory is created if missing. Includes the ``[llm]``
    section with the local-first factory default (a user with nothing starts
    on Ollama qwen3:1.7b).
    """
    seahorse_dir = vault / SEAHORSE_DIR_NAME
    seahorse_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = config_path_for(vault)
    content = (
        "# Seahorse vault configuration.\n"
        "# Generated by `seahorse init`. Run `seahorse init --llm` to pick a "
        "provider.\n\n"
        "[seahorse]\n"
        f'db_path = "{DEFAULT_DB_FILENAME}"\n'
        f'default_extraction_mode = "{DEFAULT_EXTRACTION_MODE}"\n'
        f"top_k = {DEFAULT_TOP_K}\n\n"
        "[llm]\n"
        f'primary = "{DEFAULT_LLM_PRIMARY}"\n'
        f"timeout_s = {DEFAULT_LLM_TIMEOUT_S}\n"
    )
    cfg_path.write_text(content, encoding="utf-8")
    return cfg_path


def write_llm_config(vault: Path, llm: LlmConfig) -> Path:
    """Rewrite ``seahorse.toml`` with an updated ``[llm]`` section.

    Loads the existing ``[seahorse]`` values (so a personalized config is
    preserved) and re-serializes the file by hand (stdlib has no TOML writer —
    the file is small and generated). Returns the config path.
    """
    cfg = load_config(vault)
    cfg_path = config_path_for(vault)
    db_rel = cfg.db_path.relative_to(cfg.seahorse_dir)
    lines = [
        "# Seahorse vault configuration.\n",
        "# Generated by `seahorse init`. Run `seahorse init --llm` to change "
        "the provider.\n\n",
        "[seahorse]\n",
        f'db_path = "{db_rel}"\n',
        f'default_extraction_mode = "{cfg.default_extraction_mode}"\n',
        f"top_k = {cfg.top_k}\n\n",
        "[llm]\n",
        f'primary = "{llm.primary}"\n',
    ]
    if llm.secondary:
        lines.append(f'secondary = "{llm.secondary}"\n')
    if llm.tertiary:
        lines.append(f'tertiary = "{llm.tertiary}"\n')
    lines.append(f"timeout_s = {llm.timeout_s}\n")
    cfg_path.write_text("".join(lines), encoding="utf-8")
    return cfg_path


__all__ = [
    "SEAHORSE_DIR_NAME",
    "CONFIG_FILENAME",
    "DEFAULT_DB_FILENAME",
    "DEFAULT_EXTRACTION_MODE",
    "DEFAULT_TOP_K",
    "DEFAULT_LLM_PRIMARY",
    "DEFAULT_LLM_TIMEOUT_S",
    "DEFAULT_SKIP_TOOLS",
    "DEFAULT_DROP_TOOLS",
    "DEFAULT_OBSERVE_SOCKET",
    "SeahorseConfig",
    "LlmConfig",
    "ObserveConfig",
    "is_initialized",
    "resolve_vault",
    "config_path_for",
    "load_config",
    "write_default_config",
    "write_llm_config",
]