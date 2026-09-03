"""``seahorse setup`` / ``seahorse setup --uninstall``.

``setup`` installs the observer:
1. Writes the ``[observe]`` section to ``seahorse.toml`` (with a generated auth
   token).
2. MERGES the Claude Code hooks into ``~/.claude/settings.json`` — coexisting
   with claude-mem's hooks. The observer hooks are identified by the ``seahorse
   observe event`` marker so ``--uninstall`` can remove exactly them.

``--uninstall`` removes the observer hooks and the ``[observe]`` section,
preserving other hooks and config.
"""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import TextIO

from seahorse.cli.config import (
    DEFAULT_DROP_TOOLS,
    DEFAULT_OBSERVE_SOCKET,
    DEFAULT_SKIP_TOOLS,
    config_path_for,
    is_initialized,
    load_config,
    resolve_vault,
    write_default_config,
    write_global_pointer,
)
from seahorse.cli.errors import CliConfigInvalid, CliVaultNotFound
from seahorse.cli.output import OutputFormat

# The marker that identifies the observer hooks in settings.json (the uninstall
# removes exactly the hooks whose command contains it). It is a substring of
# the hook command ``{python} -m seahorse.cli.app observe event``.
HOOK_MARKER = "observe event"

# Hook event → matcher.
_OBSERVER_HOOKS: dict[str, str] = {
    "SessionStart": "startup",
    "UserPromptSubmit": "*",
    "PostToolUse": "*",
    "Stop": "*",
}

# The opt-in consolidate-on-stop hook is identified by its own marker so the
# uninstall removes exactly it (the observer Stop hook is a different entry).
CONSOLIDATE_HOOK_MARKER = "consolidate --auto"

_OBSERVE_SECTION_RE = re.compile(r"\n\[observe\].*?(?=\n\[|\Z)", re.DOTALL)

# Obsidian's vault registry (obsidian.json) lives next to Seahorse's global
# pointer, under the same config-home convention.
_OBSIDIAN_APP_DIR = "obsidian"
_OBSIDIAN_REGISTRY_NAME = "obsidian.json"
# Portable per-user default vault (the global pointer stores the literal
# ``~/seahorse-mem``; ``expanduser`` resolves it per user at read time).
_DEFAULT_VAULT_DIRNAME = "seahorse-mem"


def _obsidian_registry_path() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / _OBSIDIAN_APP_DIR / _OBSIDIAN_REGISTRY_NAME


def discover_obsidian_vaults() -> list[Path]:
    """Existing Obsidian vaults from Obsidian's own registry (obsidian.json).

    Tolerates a missing or corrupt registry (empty list — discovery is best
    effort, never a failure). Vaults whose directory no longer exists are
    filtered out.
    """
    try:
        data = json.loads(_obsidian_registry_path().read_text(encoding="utf-8"))
        entries = list(data.get("vaults", {}).values())
    except (OSError, ValueError):
        return []
    vaults: list[Path] = []
    for entry in entries:
        raw = entry.get("path") if isinstance(entry, dict) else None
        if not raw:
            continue
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            vaults.append(candidate.resolve())
    return vaults


def _bootstrap_vault(vault: Path) -> Path:
    """Create the vault directory + minimal config if missing (idempotent)."""
    vault.mkdir(parents=True, exist_ok=True)
    if not is_initialized(vault):
        write_default_config(vault)
    return vault


def _pick_vault_interactively() -> Path:
    """Ask the user which vault Seahorse should use (TTY only).

    Offers Obsidian's registered vaults plus a last option that creates a
    fresh vault at ``~/Seahorse``. The pick is bootstrapped, so the chosen
    directory always ends up initialized.
    """
    discovered = discover_obsidian_vaults()
    options = [*discovered, Path.home() / _DEFAULT_VAULT_DIRNAME]
    print("No Seahorse vault found. Pick the vault to use with Seahorse:")
    for index, option in enumerate(options, start=1):
        note = "" if option in discovered else " (created if missing)"
        print(f"  {index}. {option}{note}")
    choice = input(f"Vault [1-{len(options)}]: ").strip()
    try:
        picked = options[int(choice) - 1]
    except (ValueError, IndexError) as exc:
        raise CliVaultNotFound(hint=f"invalid choice {choice!r}") from exc
    return _bootstrap_vault(picked)


def ensure_vault(explicit: Path | None) -> Path:
    """Resolve the vault for ``setup``, bootstrapping instead of failing.

    An explicit ``--vault`` is created and initialized if missing. Otherwise
    the normal resolution order applies (env / cwd walk / global pointer);
    only when nothing resolves does setup interact: on a TTY it offers a
    numbered pick, without a TTY it bootstraps the portable per-user default
    (``~/seahorse-mem``) — an agent running one-command onboarding without a
    TTY must never hit the cold-start exit 82.
    """
    if explicit is not None:
        return _bootstrap_vault(explicit.expanduser().resolve())
    try:
        return resolve_vault(None)
    except CliVaultNotFound:
        if not sys.stdin.isatty():
            return _bootstrap_vault(Path.home() / _DEFAULT_VAULT_DIRNAME)
        return _pick_vault_interactively()


def _default_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


# ---------------------------------------------------------------------------
# [observe] config
# ---------------------------------------------------------------------------


def write_observe_config(vault: Path) -> Path:
    """Write the ``[observe]`` section to ``seahorse.toml`` (idempotent).

    A present section is preserved (the user's config wins); a missing one is
    appended with the defaults + a generated auth token.
    """
    cfg_path = config_path_for(vault)
    content = cfg_path.read_text(encoding="utf-8")
    if "[observe]" not in content:
        token = secrets.token_hex(16)
        content += (
            "\n[observe]\n"
            "enabled = true\n"
            'extraction = "skip"\n'
            f"skip_tools = {list(DEFAULT_SKIP_TOOLS)!r}\n"
            f"drop_tools = {list(DEFAULT_DROP_TOOLS)!r}\n"
            f'socket_path = "{DEFAULT_OBSERVE_SOCKET}"\n'
            f'token = "{token}"\n'
        )
        cfg_path.write_text(content, encoding="utf-8")
    return cfg_path


def _remove_observe_section(vault: Path) -> None:
    cfg_path = config_path_for(vault)
    content = cfg_path.read_text(encoding="utf-8")
    content = _OBSERVE_SECTION_RE.sub("", content)
    cfg_path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# settings.json hooks
# ---------------------------------------------------------------------------


def _hook_commands(entry: dict) -> list[str]:
    """All commands in a settings.json hook entry.

    Claude Code's shape nests the command inside ``hooks: [{type, command}]``;
    a flat ``command`` at the entry level (written by ≤0.16.0) is ignored by
    Claude Code but still tracked here so the uninstall cleans it up too.
    """
    commands = [entry["command"]] if entry.get("command") else []
    commands.extend(
        h["command"] for h in entry.get("hooks", []) if h.get("command")
    )
    return commands


def merge_hooks(settings_path: Path | str, *, hook_command: str) -> None:
    """Merge the observer hooks into settings.json (coexisting with others).

    Idempotent: an entry whose command already contains the marker is not
    duplicated. Other hooks (e.g. claude-mem) are preserved.
    """
    path = Path(settings_path)
    data: dict = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    hooks = data.setdefault("hooks", {})
    for event, matcher in _OBSERVER_HOOKS.items():
        entries = hooks.setdefault(event, [])
        already_installed = any(
            HOOK_MARKER in c for h in entries for c in _hook_commands(h)
        )
        if not already_installed:
            entries.append(
                {
                    "matcher": matcher,
                    "hooks": [{"type": "command", "command": hook_command}],
                }
            )
    # A fresh user may not have ~/.claude/ yet (no Claude Code installed) — the
    # hooks are written ready for when it is. The observer is a Claude Code
    # capture adapter; the rest of Seahorse is agent-agnostic.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def remove_hooks(settings_path: Path | str) -> None:
    """Remove the observer hooks from settings.json (preserving others)."""
    _remove_hooks_matching(settings_path, marker=HOOK_MARKER)


def merge_consolidate_hook(settings_path: Path | str, *, hook_command: str) -> None:
    """Merge the consolidate-on-stop hook (Stop event) into settings.json.

    Opt-in: only ``seahorse setup --auto-consolidate`` calls this. Idempotent
    by its own marker; the flag lives in the vault config, so the hook stays
    a no-op while ``[consolidate] auto_on_stop = false``.
    """
    path = Path(settings_path)
    data: dict = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    stop_entries = data.setdefault("hooks", {}).setdefault("Stop", [])
    already_installed = any(
        CONSOLIDATE_HOOK_MARKER in c
        for h in stop_entries
        for c in _hook_commands(h)
    )
    if not already_installed:
        stop_entries.append(
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": hook_command}],
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def remove_consolidate_hook(settings_path: Path | str) -> None:
    """Remove the consolidate-on-stop hook (preserving the observer hooks)."""
    _remove_hooks_matching(settings_path, marker=CONSOLIDATE_HOOK_MARKER)


def _remove_hooks_matching(settings_path: Path | str, *, marker: str) -> None:
    """Remove every hook entry whose command contains ``marker``."""
    path = Path(settings_path)
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    hooks = data.get("hooks", {})
    for event in list(hooks):
        kept = [
            h
            for h in hooks[event]
            if not any(marker in c for c in _hook_commands(h))
        ]
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def run_setup(
    vault: Path,
    *,
    settings_path: Path | str | None = None,
    fmt: OutputFormat = "human",
    out: TextIO,
    auto_consolidate: bool = False,
) -> None:
    """Install the observer + materialization: config sections + Claude Code hooks.

    Writes the ``[observe]`` section (with a generated auth token) and the
    ``[materialize]`` section (defaults — the opt-in path for episode → .md
    materialization), then merges the observer hooks into the Claude Code
    settings. Both config writes are idempotent appends: a present section is
    preserved (the user's config wins). ``auto_consolidate`` additionally
    writes the ``[consolidate]`` section and merges the consolidate-on-stop
    hook.
    """
    from seahorse.cli.config import (
        ConsolidateConfig,
        MaterializeConfig,
        write_consolidate_config,
        write_materialize_config,
    )

    write_observe_config(vault)
    write_materialize_config(vault, MaterializeConfig())
    if auto_consolidate:
        write_consolidate_config(vault, ConsolidateConfig(auto_on_stop=True))
    write_global_pointer(vault)
    settings = Path(settings_path) if settings_path is not None else _default_settings_path()
    hook_command = f"{sys.executable} -m seahorse.cli.app observe event"
    merge_hooks(settings, hook_command=hook_command)
    if auto_consolidate:
        consolidate_command = (
            f"{sys.executable} -m seahorse.cli.app consolidate --auto"
        )
        merge_consolidate_hook(settings, hook_command=consolidate_command)
    if fmt == "human":
        out.write(
            "seahorse setup: observer installed "
            f"(hooks merged into {settings}, [observe] + [materialize] config written)\n"
        )


def run_setup_uninstall(
    vault: Path,
    *,
    settings_path: Path | str | None = None,
    fmt: OutputFormat = "human",
    out: TextIO,
) -> None:
    """Symmetric uninstall: hooks, config section, MCP, instructions, observer.

    Removes the observer + consolidate hooks, the ``[observe]`` section, the
    MCP registration and the agent instructions block, and stops the
    observer. The ``[materialize]`` section and the global pointer are kept —
    they only affect the vault's own layout, nothing global.
    """
    from seahorse.cli.agent_instructions import remove_agent_instructions
    from seahorse.cli.mcp_register import remove_mcp_registration
    from seahorse.observe.cli import run_observe_stop

    settings = Path(settings_path) if settings_path is not None else _default_settings_path()
    remove_hooks(settings)
    remove_consolidate_hook(settings)
    _remove_observe_section(vault)
    observer_detail = "not configured"
    try:
        cfg = load_config(vault)
        buf = io.StringIO()
        run_observe_stop(cfg, fmt="json", out=buf)
        observer_detail = buf.getvalue().strip()
    except CliConfigInvalid:
        pass  # config already gone — nothing to stop
    mcp_ok, mcp_detail = remove_mcp_registration()
    ai_ok, ai_detail = remove_agent_instructions()
    if fmt == "human":
        out.write("seahorse setup: uninstalled (hooks + [observe] removed)\n")
        out.write(f"  observer: {observer_detail}\n")
        out.write(f"  mcp: {mcp_detail}\n" if mcp_ok else f"  mcp: WARN {mcp_detail}\n")
        out.write(
            f"  agent instructions: {ai_detail}\n"
            if ai_ok
            else f"  agent instructions: WARN {ai_detail}\n"
        )
    mcp_ok, mcp_detail = remove_mcp_registration()
    ai_ok, ai_detail = remove_agent_instructions()
    if fmt == "human":
        out.write("seahorse setup: uninstalled (hooks + [observe] removed)\n")
        out.write(f"  mcp: {mcp_detail}\n" if mcp_ok else f"  mcp: WARN {mcp_detail}\n")
        out.write(
            f"  agent instructions: {ai_detail}\n"
            if ai_ok
            else f"  agent instructions: WARN {ai_detail}\n"
        )


__all__ = [
    "CONSOLIDATE_HOOK_MARKER",
    "HOOK_MARKER",
    "discover_obsidian_vaults",
    "ensure_vault",
    "write_observe_config",
    "merge_hooks",
    "merge_consolidate_hook",
    "remove_hooks",
    "remove_consolidate_hook",
    "run_setup",
    "run_setup_uninstall",
]
