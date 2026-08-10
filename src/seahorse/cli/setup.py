"""``seahorse setup`` / ``seahorse setup --uninstall`` (Sprint B, obsiforge §4.7).

``setup`` installs the observer:
1. Writes the ``[observe]`` section to ``seahorse.toml`` (with a generated auth
   token, §15.2 redesign 10).
2. MERGES the Claude Code hooks into ``~/.claude/settings.json`` — coexisting
   with claude-mem's hooks (obsiforge §15.3-4: coexistence = migration, not
   convivencia). The observer hooks are identified by the ``seahorse observe
   event`` marker so ``--uninstall`` can remove exactly them.

``--uninstall`` removes the observer hooks and the ``[observe]`` section,
preserving other hooks and config.

References:
- obsiforge-evolution-architecture.md §4.7 (instala-y-funciona)
- obsiforge-evolution-architecture.md §15.2 redesign 10 (auth token)
- obsiforge-evolution-architecture.md §15.3-4 (coexistence = migration)
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

# Hook event → matcher (obsiforge §4.2).
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
    appended with the defaults + a generated auth token (§15.2 redesign 10).
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


def merge_hooks(settings_path: Path | str, *, hook_command: str) -> None:
    """Merge the observer hooks into settings.json (coexisting with others).

    Idempotent: a hook whose command already contains the marker is not
    duplicated. Other hooks (e.g. claude-mem) are preserved.
    """
    path = Path(settings_path)
    data: dict = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    hooks = data.setdefault("hooks", {})
    for event, matcher in _OBSERVER_HOOKS.items():
        entries = hooks.setdefault(event, [])
        if not any(HOOK_MARKER in h.get("command", "") for h in entries):
            entries.append({"matcher": matcher, "command": hook_command})
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
        hooks[event] = [
            h for h in hooks[event] if HOOK_MARKER not in h.get("command", "")
        ]
        if not hooks[event]:
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
    """Install the observer: ``[observe]`` config + Claude Code hooks."""
    write_observe_config(vault)
    settings = Path(settings_path) if settings_path is not None else _default_settings_path()
    hook_command = f"{sys.executable} -m seahorse.cli.app observe event"
    merge_hooks(settings, hook_command=hook_command)
    if fmt == "human":
        out.write(
            "seahorse setup: observer installed "
            f"(hooks merged into {settings}, [observe] config written)\n"
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
