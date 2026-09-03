"""MCP registration for one-command onboarding.

``seahorse setup`` writes the ``seahorse-mcp`` stdio server into Claude
Code's ``~/.claude.json`` (user scope) so every session gets the memory
tools without per-project configuration. The write is:

- atomic (tempfile in the same directory + ``os.replace``),
- preceded by a one-time backup (``~/.claude.json.seahorse-bak``),
- idempotent (a correct existing entry is left byte-for-byte alone),
- conservative about foreign state: a corrupt or unreadable file is
  reported as a WARN and never touched, and any key the user already has
  in ``mcpServers`` is preserved verbatim.

A ``claude mcp add`` subprocess is the fallback when the direct write is
impossible (e.g. exotic permissions); the direct write is preferred
because it works even when the ``claude`` binary is not on PATH.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

MCP_SERVER_NAME = "seahorse-mcp"
MCP_SERVER_COMMAND = "seahorse-mcp"
_BACKUP_SUFFIX = ".seahorse-bak"

_MCP_ENTRY: dict[str, object] = {
    "type": "stdio",
    "command": MCP_SERVER_COMMAND,
    "args": [],
    "env": {},
}


def claude_json_path() -> Path:
    """Path of Claude Code's user config (overridable for tests/sandboxes)."""
    env = os.environ.get("SEAHORSE_CLAUDE_JSON")
    if env:
        return Path(env)
    return Path.home() / ".claude.json"


def _read_json(path: Path) -> dict[str, object] | None:
    """Parsed top-level object, or None when absent/corrupt/unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_mcp_registered(path: Path | None = None) -> bool:
    """True when ``mcpServers`` already holds a correct ``seahorse-mcp`` entry."""
    path = path or claude_json_path()
    data = _read_json(path)
    if data is None:
        return False
    servers = data.get("mcpServers")
    return isinstance(servers, dict) and servers.get(MCP_SERVER_NAME) == _MCP_ENTRY


def _atomic_write(path: Path, data: dict[str, object]) -> None:
    """Write JSON atomically: same-directory tempfile + ``os.replace``."""
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def register_mcp(path: Path | None = None) -> tuple[bool, str]:
    """Register the seahorse-mcp server in Claude Code's user config.

    Returns ``(registered, detail)``. Idempotent: an existing correct entry
    is a no-op. Foreign keys in ``mcpServers`` are preserved. A corrupt or
    unwritable file is never touched — the report says so and the caller
    surfaces the exact fix.
    """
    path = path or claude_json_path()
    if is_mcp_registered(path):
        return True, f"already registered in {path}"

    if not path.exists():
        data: dict[str, object] = {}
    else:
        existing = _read_json(path)
        if existing is None:
            ok, detail = _fallback_via_claude_cli()
            if ok:
                return True, detail
            return (
                False,
                f"cannot parse {path} — not touching it; fix or remove the "
                "file, then re-run `seahorse setup` (or register manually "
                "with `claude mcp add`)",
            )
        data = existing
        backup = path.with_suffix(path.suffix + _BACKUP_SUFFIX)
        if not backup.exists():
            shutil.copy2(path, backup)

    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers[MCP_SERVER_NAME] = dict(_MCP_ENTRY)
    data["mcpServers"] = servers

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, data)
    except OSError as exc:
        ok, detail = _fallback_via_claude_cli()
        if ok:
            return True, detail
        return False, f"cannot write {path}: {exc} — register manually with `claude mcp add`"
    return True, f"registered {MCP_SERVER_NAME} in {path}"


def remove_mcp_registration(path: Path | None = None) -> tuple[bool, str]:
    """Remove the seahorse-mcp entry, preserving everything else.

    Returns ``(removed, detail)``; ``(True, ...)`` when there is nothing to
    remove. A corrupt file is reported, never touched.
    """
    path = path or claude_json_path()
    if not path.exists():
        return True, "no Claude config — nothing to remove"
    data = _read_json(path)
    if data is None:
        return False, f"cannot parse {path} — not touching it"
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or MCP_SERVER_NAME not in servers:
        return True, "not registered — nothing to remove"
    del servers[MCP_SERVER_NAME]
    data["mcpServers"] = servers
    try:
        backup = path.with_suffix(path.suffix + _BACKUP_SUFFIX)
        if not backup.exists():
            shutil.copy2(path, backup)
        _atomic_write(path, data)
    except OSError as exc:
        return False, f"cannot write {path}: {exc}"
    return True, f"removed {MCP_SERVER_NAME} from {path}"


def _fallback_via_claude_cli() -> tuple[bool, str]:
    """Last resort: `claude mcp add` (user scope) when direct write failed."""
    claude = shutil.which("claude")
    if claude is None:
        return False, "claude binary not found"
    try:
        res = subprocess.run(  # noqa: S603
            [
                claude,
                "mcp",
                "add",
                "--scope",
                "user",
                MCP_SERVER_NAME,
                "--",
                MCP_SERVER_COMMAND,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"`claude mcp add` failed: {exc}"
    if res.returncode != 0:
        return False, f"`claude mcp add` failed: {res.stderr.strip()}"
    return True, f"registered {MCP_SERVER_NAME} via `claude mcp add` (user scope)"


__all__ = [
    "MCP_SERVER_COMMAND",
    "MCP_SERVER_NAME",
    "claude_json_path",
    "is_mcp_registered",
    "register_mcp",
    "remove_mcp_registration",
]