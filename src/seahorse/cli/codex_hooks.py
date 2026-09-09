"""Codex GA hooks (``~/.codex/hooks.json``) — capture + bootstrap for Codex.

Codex hooks (GA 2026-05) use the same stdin-JSON contract as Claude Code, so
the SAME capture command works for both — including the SessionStart bootstrap
injection (``observe event`` emits the ``hookSpecificOutput
additionalContext`` line on stdout). Codex payloads carry no agent field, so
the installed command passes ``--agent-id codex`` for provenance.

One caveat the installer cannot automate: non-managed Codex hooks are skipped
until the user approves them (hash-based trust review via ``/hooks``) — the
instructions block says so honestly.

Merge discipline identical to ``harness_targets.py``: marker-keyed entries
only, foreign hooks preserved, one-time backup, a file we cannot parse is
never touched.
"""

from __future__ import annotations

from pathlib import Path

from seahorse.cli.harness_targets import (
    _env_path,
    atomic_write_json,
    backup_once,
    read_json_dict,
)

CODEX_HOOKS_ENV = "SEAHORSE_CODEX_HOOKS_JSON"
# The capture command contains this literal in both harnesses — the uninstall
# matches on it exactly like Claude Code's HOOK_MARKER does.
CODEX_HOOK_MARKER = "observe event"
# Codex's own default hook timeout is 600s: a hung capture would stall the
# turn for ten minutes. 10s bounds the worst case (spool keeps the event).
CODEX_HOOK_TIMEOUT_S = 10

_CODEX_EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop")


def codex_hooks_path() -> Path:
    """Path of Codex's hooks file (overridable for tests/sandboxes)."""
    return _env_path(CODEX_HOOKS_ENV, Path.home() / ".codex" / "hooks.json")


def _entry_commands(entry: dict[str, object]) -> list[str]:
    """All commands in one hook entry (Codex: ``hooks: [{type, command}]``)."""
    handlers = entry.get("hooks")
    if not isinstance(handlers, list):
        return []
    return [
        str(h["command"])
        for h in handlers
        if isinstance(h, dict) and h.get("command")
    ]


def _has_marker(entries: list[dict[str, object]]) -> bool:
    return any(
        CODEX_HOOK_MARKER in c for e in entries for c in _entry_commands(e)
    )


def merge_codex_hooks(path: Path | str, *, hook_command: str) -> tuple[bool, str]:
    """Merge the 4 capture hooks into ``hooks.json`` (preserving foreign ones).

    Idempotent: an event whose entries already contain the marker is left
    alone. A file that cannot be parsed is never touched (loud WARN to the
    caller). Returns ``(installed, detail)``.
    """
    path = Path(path)
    data: dict[str, object]
    if not path.exists():
        data = {}
    else:
        existing = read_json_dict(path)
        if existing is None:
            return (
                False,
                f"cannot parse {path} — not touching it; fix or remove the "
                "file, then re-run `seahorse setup`",
            )
        data = existing
        backup_once(path)

    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    installed = False
    for event in _CODEX_EVENTS:
        entries = hooks.get(event)
        if not isinstance(entries, list):
            entries = []
        if _has_marker(entries):
            continue
        entries.append(
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_command,
                        "statusMessage": "seahorse memory capture",
                        "timeout": CODEX_HOOK_TIMEOUT_S,
                    }
                ]
            }
        )
        installed = True
        hooks[event] = entries
    data["hooks"] = hooks
    if not installed:
        return True, f"codex hooks already installed in {path}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, data)
    except OSError as exc:
        return False, f"cannot write {path}: {exc}"
    return (
        True,
        f"codex hooks written to {path} — approve them once via /hooks "
        "(Codex skips untrusted hooks)",
    )


def remove_codex_hooks(path: Path | str) -> tuple[bool, str]:
    """Remove the seahorse capture hooks (preserving foreign entries)."""
    path = Path(path)
    if not path.exists():
        return True, f"no codex hooks file at {path} — nothing to remove"
    data = read_json_dict(path)
    if data is None:
        return False, f"cannot parse {path} — not touching it"
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return True, f"no codex hooks in {path} — nothing to remove"
    removed = False
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        if not _has_marker(entries):
            continue
        kept = [e for e in entries if not _has_marker([e])]
        removed = True
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not removed:
        return True, f"no seahorse codex hooks in {path} — nothing to remove"
    backup_once(path)
    try:
        atomic_write_json(path, data)
    except OSError as exc:
        return False, f"cannot write {path}: {exc}"
    return True, f"codex hooks removed from {path}"


def codex_hooks_installed(path: Path | None = None) -> bool:
    """True when every event carries a marker entry in the hooks file."""
    path = path or codex_hooks_path()
    data = read_json_dict(path)
    if data is None:
        return False
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    return all(
        isinstance(hooks.get(event), list) and _has_marker(hooks[event])
        for event in _CODEX_EVENTS
    )


__all__ = [
    "CODEX_HOOKS_ENV",
    "CODEX_HOOK_MARKER",
    "codex_hooks_installed",
    "codex_hooks_path",
    "merge_codex_hooks",
    "remove_codex_hooks",
]