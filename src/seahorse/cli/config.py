"""Vault discovery + ``seahorse.toml`` config for the CLI.

Vault discovery in the current release:

1. ``--vault`` explicit flag (must be an existing directory).
2. ``SEAHORSE_VAULT`` environment variable (power-user override).
3. ``.seahorse/seahorse.toml`` in the current directory or any parent
   (git-style walk — a session in a subdirectory resolves the vault root).
4. The global pointer (``~/.config/seahorse/vault``, written by ``seahorse
   setup``): the vault a user registered as their default, resolved from any
   working directory without shell env changes.
5. ``CliVaultNotFound`` (exit 82) with an actionable hint.

``seahorse.toml`` is intentionally minimal in the current release (modes
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
import sys
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from seahorse.cli.errors import CliConfigInvalid, CliVaultNotFound

# Layout constants. The Seahorse dir lives inside the vault.
SEAHORSE_DIR_NAME = ".seahorse"
CONFIG_FILENAME = "seahorse.toml"
DEFAULT_DB_FILENAME = "seahorse.db"

# Default config values (austere in the current release). Modes are hardcoded
# constants — no extra config switches in the current release.
DEFAULT_EXTRACTION_MODE = "skip"
DEFAULT_TOP_K = 10

# LLM factory default (2026-08-04 decision): a user with NOTHING starts on
# local Ollama — zero registration, zero key, data never leaves the machine.
# qwen3:1.7b is the medium-hardware floor for decent extraction; 0.6b is the
# low-end option offered by the wizard. Cloud providers are the quality lever
# when a free-tier key is present.
DEFAULT_LLM_PRIMARY = "ollama/qwen3:1.7b"
DEFAULT_LLM_TIMEOUT_S = 20.0  # extraction role timeout

# Valid extraction modes the CLI accepts at the config level (the facade's
# _resolve_mode is the authority for the remember primitive; this only guards
# the config file).
_VALID_CONFIG_MODES = frozenset({"skip", "llm"})

# Observer defaults. The observer is OPT-IN: a vault without an ``[observe]``
# section has ``observe=None`` until ``seahorse setup`` writes it.
# ``skip_tools`` / ``drop_tools`` mirror the threshold module.
DEFAULT_SKIP_TOOLS: tuple[str, ...] = ("WebSearch", "WebFetch")
DEFAULT_DROP_TOOLS: tuple[str, ...] = ("Read", "Bash")
DEFAULT_OBSERVE_SOCKET = "observer/observer.sock"

# Materialization defaults. The section is OPT-IN: a vault without a
# ``[materialize]`` section has ``materialize=None`` (no .md materialization)
# until ``seahorse setup`` writes it. ``mode`` selects which episodes become
# F3.1 notes; ``dir`` is the vault-relative folder (visible to Obsidian).
DEFAULT_MATERIALIZE_MODE = "consolidated"
DEFAULT_MATERIALIZE_DIR = "Memory"
_VALID_MATERIALIZE_MODES = frozenset({"consolidated", "all", "off"})

_VAULT_ENV = "SEAHORSE_VAULT"
_APP_DIR_NAME = "seahorse"
POINTER_FILENAME = "vault"


def global_config_dir() -> Path:
    """The per-user Seahorse config dir (XDG on POSIX, Library on macOS)."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _APP_DIR_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / _APP_DIR_NAME


def global_pointer_path() -> Path:
    """The global vault pointer file (written by ``seahorse setup``)."""
    return global_config_dir() / POINTER_FILENAME


def read_global_pointer() -> Path | None:
    """The vault registered by ``seahorse setup``, or None.

    A stale pointer (deleted vault, or an existing dir without ``.seahorse/``)
    is treated as absent, never an error — the pointer is a convenience, not a
    state machine.
    """
    path = global_pointer_path()
    if not path.is_file():
        return None
    try:
        vault = Path(path.read_text(encoding="utf-8").strip()).expanduser()
    except OSError:
        return None
    if not vault.is_dir() or not is_initialized(vault):
        return None
    return vault.resolve()


def write_global_pointer(vault: Path) -> Path:
    """Register ``vault`` as the user's default; returns the pointer path."""
    path = global_pointer_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{vault.expanduser().resolve()}\n", encoding="utf-8")
    return path


@dataclass(frozen=True)
class LlmConfig:
    """The ``[llm]`` section — the extraction role route.

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
    """The ``[observe]`` section — the observer capture policy.

    ``enabled`` is True when the section is present (``seahorse setup`` writes
    it). ``extraction`` is the write-path mode (skip-first default).
    ``skip_tools`` / ``drop_tools`` are the threshold allowlists.
    ``socket_path`` is relative to ``.seahorse/`` (the unix socket the hooks
    POST to). ``token`` is the optional auth token.
    """

    enabled: bool = True
    extraction: str = "skip"
    skip_tools: tuple[str, ...] = DEFAULT_SKIP_TOOLS
    drop_tools: tuple[str, ...] = DEFAULT_DROP_TOOLS
    socket_path: str = DEFAULT_OBSERVE_SOCKET
    token: str | None = None


# Procedural skill defaults. The section is OPT-IN: a vault without a
# ``[procedural]`` section has ``procedural=None`` and the CLI uses the module
# defaults (min_trust=medium, empty loadout).
DEFAULT_MIN_TRUST = "medium"


@dataclass(frozen=True)
class ProceduralSection:
    """The ``[procedural]`` section — skill defaults.

    ``min_trust`` is the trust-gate default (low | medium | high) applied by
    ``seahorse skill show`` when the caller does not pass ``--min-trust``.
    ``loadout`` is the explicit per-agent skill loadout: the agent declares
    which skills it equips; empty means no loadout is pinned.
    """

    min_trust: str = DEFAULT_MIN_TRUST
    loadout: tuple[str, ...] = ()


@dataclass(frozen=True)
class DistillConfig:
    """The ``[distill]`` section — distillation synthesis + supersession policy.

    ``synthesis`` is the body provenance mode for ``seahorse consolidate``:
    ``"skip"`` (deterministic, the default) or ``"llm"`` (off-path LLM
    synthesis, opt-in — requires the ``[llm]`` section for a wired client).
    ``supersede`` (default False) enables F7+ supersession: an existing note is
    UPDATED via improve when the cluster gains new episodes (opt-in, ADR-10
    honesty — the human-edit guard is the vault mtime check).
    """

    synthesis: str = "skip"
    supersede: bool = False


@dataclass(frozen=True)
class MaterializeConfig:
    """The ``[materialize]`` section — episode → .md materialization policy.

    ``mode`` selects which episodes are materialized as F3.1 notes in the vault:
    ``"consolidated"`` (default) materializes consolidated knowledge notes
    (``extraction_mode=consolidated``) and project notes
    (``cognitive_type=project_doc``) — the distilled knowledge, not the session
    noise; ``"all"`` materializes every ACTIVE episode; ``"off"`` disables
    materialization. ``dir`` is the vault-relative folder the notes are written
    to (visible to Obsidian; the rebuild picks them up from any vault path).
    """

    mode: str = DEFAULT_MATERIALIZE_MODE
    dir: str = DEFAULT_MATERIALIZE_DIR


@dataclass(frozen=True)
class ConsolidateConfig:
    """The ``[consolidate]`` section — auto-consolidation policy.

    ``auto_on_stop`` (default False) opts into the consolidate-on-stop hook:
    at Claude Code's Stop event ``seahorse consolidate --auto`` distills the
    session's recurrent episodes into knowledge notes. The flag lives in the
    vault config (not the hook) so the hook is a no-op the moment it is
    turned off — the hook never blocks the session either way.
    """

    auto_on_stop: bool = False


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
    distill: DistillConfig | None = None
    materialize: MaterializeConfig | None = None
    consolidate: ConsolidateConfig | None = None

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
    """Resolve the vault directory per the current-release discovery order.

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
    for candidate in (cwd, *cwd.parents):
        if is_initialized(candidate):
            return candidate

    pointer = read_global_pointer()
    if pointer is not None:
        return pointer

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

    # Optional [llm] section. Missing → llm=None (no client wired, honest
    # llm→skip degrade). Present → validate the role route.
    llm = _parse_llm_section(data.get("llm"))

    # Optional [observe] section. Missing → observe=None (the observer is
    # opt-in until `seahorse setup` writes it).
    observe = _parse_observe_section(data.get("observe"))

    # Optional [procedural] section. Missing → procedural=None (the CLI uses
    # the module defaults: min_trust=medium, empty loadout).
    procedural = _parse_procedural_section(data.get("procedural"))

    # Optional [distill] section. Missing → distill=None (the LLM synthesis is
    # opt-in; the deterministic distillation is the default).
    distill = _parse_distill_section(data.get("distill"))

    # Optional [materialize] section. Missing → materialize=None (no .md
    # materialization until `seahorse setup` writes it).
    materialize = _parse_materialize_section(data.get("materialize"))

    # Optional [consolidate] section. Missing → consolidate=None
    # (auto-consolidation is opt-in: `seahorse setup --auto-consolidate`).
    consolidate = _parse_consolidate_section(data.get("consolidate"))

    return SeahorseConfig(
        vault=vault,
        seahorse_dir=seahorse_dir,
        db_path=db_path,
        default_extraction_mode=mode,
        top_k=top_k,
        llm=llm,
        observe=observe,
        procedural=procedural,
        distill=distill,
        materialize=materialize,
        consolidate=consolidate,
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


def _parse_distill_section(raw: object) -> DistillConfig | None:
    """Validate an optional ``[distill]`` section into a ``DistillConfig``.

    ``None`` input (no section) → ``None`` (the LLM synthesis is opt-in — the
    deterministic distillation is the default). Any structurally wrong value is
    a ``CliConfigInvalid`` (Cat C, exit 83) — a config typo fails loud, not as a
    silent degrade at runtime.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CliConfigInvalid("distill must be a [distill] table")

    synthesis = raw.get("synthesis", "skip")
    if not isinstance(synthesis, str) or synthesis not in _VALID_CONFIG_MODES:
        raise CliConfigInvalid(
            f"distill.synthesis={synthesis!r}; expected 'skip' or 'llm'"
        )

    supersede = raw.get("supersede", False)
    if not isinstance(supersede, bool):
        raise CliConfigInvalid("distill.supersede must be a boolean")

    return DistillConfig(synthesis=synthesis, supersede=supersede)


def _parse_materialize_section(raw: object) -> MaterializeConfig | None:
    """Validate an optional ``[materialize]`` section into a ``MaterializeConfig``.

    ``None`` input (no section) → ``None`` (materialization is opt-in until
    ``seahorse setup`` writes it). Any structurally wrong value is a
    ``CliConfigInvalid`` (Cat C, exit 83) — a config typo fails loud, not as a
    silent degrade at runtime.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CliConfigInvalid("materialize must be a [materialize] table")

    mode = raw.get("mode", DEFAULT_MATERIALIZE_MODE)
    if not isinstance(mode, str) or mode not in _VALID_MATERIALIZE_MODES:
        raise CliConfigInvalid(
            f"materialize.mode={mode!r}; expected 'consolidated' | 'all' | 'off'"
        )

    dir_name = raw.get("dir", DEFAULT_MATERIALIZE_DIR)
    if not isinstance(dir_name, str) or not dir_name:
        raise CliConfigInvalid("materialize.dir must be a non-empty string")

    return MaterializeConfig(mode=mode, dir=dir_name)


def _parse_consolidate_section(raw: object) -> ConsolidateConfig | None:
    """Validate an optional ``[consolidate]`` section into a ``ConsolidateConfig``.

    ``None`` input (no section) → ``None`` (auto-consolidation is opt-in).
    Any structurally wrong value is a ``CliConfigInvalid`` (Cat C, exit 83).
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CliConfigInvalid("consolidate must be a [consolidate] table")

    auto_on_stop = raw.get("auto_on_stop", False)
    if not isinstance(auto_on_stop, bool):
        raise CliConfigInvalid("consolidate.auto_on_stop must be a boolean")

    return ConsolidateConfig(auto_on_stop=auto_on_stop)


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


def write_materialize_config(vault: Path, materialize: MaterializeConfig) -> Path:
    """Write the ``[materialize]`` section to ``seahorse.toml`` (idempotent).

    A present section is preserved (the user's config wins); a missing one is
    appended with the given mode/dir. Appending (not re-serializing) preserves
    the ``[llm]`` / ``[observe]`` / ``[procedural]`` / ``[distill]`` sections
    already written by ``init`` / ``setup``. Returns the config path.
    """
    cfg_path = config_path_for(vault)
    content = cfg_path.read_text(encoding="utf-8")
    if "[materialize]" not in content:
        content += (
            "\n[materialize]\n"
            f'mode = "{materialize.mode}"\n'
            f'dir = "{materialize.dir}"\n'
        )
        cfg_path.write_text(content, encoding="utf-8")
    return cfg_path


def write_consolidate_config(vault: Path, consolidate: ConsolidateConfig) -> Path:
    """Write the ``[consolidate]`` section to ``seahorse.toml`` (idempotent).

    A present section is preserved (the user's config wins — flip the flag by
    editing the file); a missing one is appended. Returns the config path.
    """
    cfg_path = config_path_for(vault)
    content = cfg_path.read_text(encoding="utf-8")
    if "[consolidate]" not in content:
        content += (
            "\n[consolidate]\n"
            f"auto_on_stop = {'true' if consolidate.auto_on_stop else 'false'}\n"
        )
        cfg_path.write_text(content, encoding="utf-8")
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
    "DEFAULT_MATERIALIZE_MODE",
    "DEFAULT_MATERIALIZE_DIR",
    "SeahorseConfig",
    "LlmConfig",
    "ObserveConfig",
    "MaterializeConfig",
    "ConsolidateConfig",
    "is_initialized",
    "resolve_vault",
    "config_path_for",
    "load_config",
    "write_default_config",
    "write_llm_config",
    "write_materialize_config",
    "write_consolidate_config",
]