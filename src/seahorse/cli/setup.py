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

import json
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
)
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

_OBSERVE_SECTION_RE = re.compile(r"\n\[observe\].*?(?=\n\[|\Z)", re.DOTALL)


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
    path = Path(settings_path)
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    hooks = data.get("hooks", {})
    for event in list(hooks):
        kept = [
            h
            for h in hooks[event]
            if not any(HOOK_MARKER in c for c in _hook_commands(h))
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
) -> None:
    """Install the observer + materialization: config sections + Claude Code hooks.

    Writes the ``[observe]`` section (with a generated auth token) and the
    ``[materialize]`` section (defaults — the opt-in path for episode → .md
    materialization), then merges the observer hooks into the Claude Code
    settings. Both config writes are idempotent appends: a present section is
    preserved (the user's config wins).
    """
    from seahorse.cli.config import MaterializeConfig, write_materialize_config

    write_observe_config(vault)
    write_materialize_config(vault, MaterializeConfig())
    settings = Path(settings_path) if settings_path is not None else _default_settings_path()
    hook_command = f"{sys.executable} -m seahorse.cli.app observe event"
    merge_hooks(settings, hook_command=hook_command)
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
    """Remove the observer hooks + the ``[observe]`` config section."""
    settings = Path(settings_path) if settings_path is not None else _default_settings_path()
    remove_hooks(settings)
    _remove_observe_section(vault)
    if fmt == "human":
        out.write("seahorse setup: observer uninstalled (hooks + [observe] config removed)\n")


__all__ = [
    "HOOK_MARKER",
    "write_observe_config",
    "merge_hooks",
    "remove_hooks",
    "run_setup",
    "run_setup_uninstall",
]
