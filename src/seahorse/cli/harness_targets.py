"""Harness targets: MCP stdio registration for local agent harnesses.

``seahorse setup --harness <id>`` registers the ``seahorse-mcp`` stdio
server into each supported harness's user-scope config. One contract,
six destinations — every write is:

- atomic (same-directory tempfile + ``os.replace``),
- preceded by a one-time backup (``<config>.seahorse-bak``),
- idempotent (a correct existing entry is left byte-for-byte alone),
- conservative about foreign state: a corrupt or unparseable file is
  reported (``ok=False``) and never touched, and foreign keys are
  preserved verbatim,
- sandbox-safe: a ``SEAHORSE_<HARNESS>`` env var redirects the config
  path (tests/sandboxes); CLI fallbacks that would bypass the redirect
  are disabled when it is set.

Claude Code keeps its own module (``mcp_register.py``, incl. the
``claude mcp add`` fallback); the generic JSON machinery it shares with
the other targets lives here. Codex is the one TOML target: the block
is appended between comment markers and re-parsed with ``tomllib``
after every write (rollback on failure) — stdlib ``tomllib`` is
read-only and adding a TOML writer would break the zero-extra-deps
posture (see ``config.py``).
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MCP_SERVER_NAME = "seahorse-mcp"
MCP_SERVER_COMMAND = "seahorse-mcp"
_BACKUP_SUFFIX = ".seahorse-bak"
_TOML_BEGIN = "# seahorse-mcp:begin"
_TOML_END = "# seahorse-mcp:end"


# ------------------------------------------------------------- shared helpers


def read_json_dict(path: Path) -> dict[str, object] | None:
    """Parsed top-level object, or None when absent/corrupt/unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def backup_once(path: Path) -> Path:
    """One-time backup (never overwritten); returns the backup path."""
    backup = path.with_suffix(path.suffix + _BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(path, backup)
    return backup


def atomic_write_json(path: Path, data: dict[str, object]) -> None:
    """Write JSON atomically: same-directory tempfile + ``os.replace``."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically: same-directory tempfile + ``os.replace``."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


# -------------------------------------------------------------- JSON targets


def _register_json_server(
    path: Path,
    name: str,
    entry: dict[str, object],
    *,
    servers_key: str,
) -> tuple[bool, str]:
    """Idempotent upsert of ``servers_key[name]`` in a JSON config."""
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
        servers = existing.get(servers_key)
        if isinstance(servers, dict) and servers.get(name) == entry:
            return True, f"already registered in {path}"
        data = existing
        backup_once(path)

    servers = data.get(servers_key)
    if not isinstance(servers, dict):
        servers = {}
    servers[name] = dict(entry)
    data[servers_key] = servers
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, data)
    except OSError as exc:
        return False, f"cannot write {path}: {exc}"
    return True, f"registered {name} in {path}"


def _remove_json_server(path: Path, name: str, *, servers_key: str) -> tuple[bool, str]:
    if not path.exists():
        return True, f"no {path.name} — nothing to remove"
    data = read_json_dict(path)
    if data is None:
        return False, f"cannot parse {path} — not touching it"
    servers = data.get(servers_key)
    if not isinstance(servers, dict) or name not in servers:
        return True, "not registered — nothing to remove"
    del servers[name]
    data[servers_key] = servers
    try:
        backup_once(path)
        atomic_write_json(path, data)
    except OSError as exc:
        return False, f"cannot write {path}: {exc}"
    return True, f"removed {name} from {path}"


def _is_json_registered(
    path: Path, name: str, entry: dict[str, object], *, servers_key: str
) -> bool:
    data = read_json_dict(path)
    if data is None:
        return False
    servers = data.get(servers_key)
    return isinstance(servers, dict) and servers.get(name) == entry


def _json_target(
    harness_id: str,
    env_var: str,
    default_path: Callable[[], Path],
    *,
    servers_key: str,
    entry: dict[str, object],
) -> HarnessTarget:
    def register(path: Path) -> tuple[bool, str]:
        return _register_json_server(path, MCP_SERVER_NAME, entry, servers_key=servers_key)

    def remove(path: Path) -> tuple[bool, str]:
        return _remove_json_server(path, MCP_SERVER_NAME, servers_key=servers_key)

    def is_registered(path: Path) -> bool:
        return _is_json_registered(path, MCP_SERVER_NAME, entry, servers_key=servers_key)

    return HarnessTarget(
        harness_id=harness_id,
        config_env_var=env_var,
        default_path=default_path,
        register=register,
        remove=remove,
        is_registered=is_registered,
    )


# ---------------------------------------------------------------- Codex (TOML)


def _codex_text(path: Path) -> str | None:
    """Raw config text, or None when the existing file does not parse."""
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
        tomllib.loads(text)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return text


def _codex_expected() -> dict[str, object]:
    return {"command": MCP_SERVER_COMMAND, "args": []}


def _codex_block() -> str:
    return (
        f"{_TOML_BEGIN}\n"
        f"[mcp_servers.{MCP_SERVER_NAME}]\n"
        f'command = "{MCP_SERVER_COMMAND}"\n'
        "args = []\n"
        f"{_TOML_END}\n"
    )


def _codex_register(path: Path) -> tuple[bool, str]:
    text = _codex_text(path)
    if text is None:
        return (
            False,
            f"cannot parse {path} — not touching it; fix or remove the "
            "file, then re-run `seahorse setup`",
        )
    servers = tomllib.loads(text).get("mcp_servers")
    name = MCP_SERVER_NAME
    if isinstance(servers, dict) and name in servers:
        existing = servers[name]
        if isinstance(existing, dict) and existing.get("command") == _codex_expected()["command"]:
            return True, f"already registered in {path}"
        return (
            False,
            f"existing [mcp_servers.{name}] in {path} with different settings "
            "— not touching it; remove or adjust that table, then re-run",
        )
    new_text = text if text.endswith("\n") or not text else text + "\n"
    new_text += _codex_block()
    try:
        tomllib.loads(new_text)
    except tomllib.TOMLDecodeError:
        return False, f"appending {name} would not re-parse — not touching {path}"
    try:
        if path.exists():
            backup_once(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, new_text)
    except OSError as exc:
        return False, f"cannot write {path}: {exc}"
    return True, f"registered {name} in {path}"


def _codex_remove(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return True, f"no {path.name} — nothing to remove"
    text = _codex_text(path)
    if text is None:
        return False, f"cannot parse {path} — not touching it"
    if _TOML_BEGIN not in text:
        servers = tomllib.loads(text).get("mcp_servers")
        if isinstance(servers, dict) and MCP_SERVER_NAME in servers:
            return (
                False,
                f"[mcp_servers.{MCP_SERVER_NAME}] in {path} was not written by "
                "`seahorse setup` — not touching it; remove that table manually",
            )
        return True, "not registered — nothing to remove"
    # Drop the marker-enclosed block: walk once, cutting begin..end spans.
    new_lines: list[str] = []
    inside = False
    for line in text.splitlines(keepends=True):
        if line.startswith(_TOML_BEGIN):
            inside = True
            continue
        if line.startswith(_TOML_END):
            inside = False
            continue
        if not inside:
            new_lines.append(line)
    new_text = "".join(new_lines)
    try:
        tomllib.loads(new_text)
    except tomllib.TOMLDecodeError:
        return False, f"removing {MCP_SERVER_NAME} would not re-parse — not touching {path}"
    try:
        backup_once(path)
        atomic_write_text(path, new_text)
    except OSError as exc:
        return False, f"cannot write {path}: {exc}"
    return True, f"removed {MCP_SERVER_NAME} from {path}"


def _codex_is_registered(path: Path) -> bool:
    text = _codex_text(path)
    if not text:
        return False
    servers = tomllib.loads(text).get("mcp_servers")
    if not isinstance(servers, dict) or MCP_SERVER_NAME not in servers:
        return False
    existing = servers[MCP_SERVER_NAME]
    return isinstance(existing, dict) and existing.get("command") == _codex_expected()["command"]


# ------------------------------------------------------------------- registry


def _env_path(env_var: str, default: Path) -> Path:
    env = os.environ.get(env_var)
    return Path(env) if env else default


def _claude_path() -> Path:
    return _env_path("SEAHORSE_CLAUDE_JSON", Path.home() / ".claude.json")


def _codex_path() -> Path:
    return _env_path("SEAHORSE_CODEX_CONFIG", Path.home() / ".codex" / "config.toml")


def _cursor_path() -> Path:
    return _env_path("SEAHORSE_CURSOR_MCP_JSON", Path.home() / ".cursor" / "mcp.json")


def _vscode_path() -> Path:
    return _env_path(
        "SEAHORSE_VSCODE_MCP_JSON", Path.home() / ".config" / "Code" / "User" / "mcp.json"
    )


def _antigravity_path() -> Path:
    return _env_path(
        "SEAHORSE_ANTIGRAVITY_CONFIG", Path.home() / ".gemini" / "config" / "mcp_config.json"
    )


def _gemini_path() -> Path:
    return _env_path("SEAHORSE_GEMINI_SETTINGS", Path.home() / ".gemini" / "settings.json")


def _claude_register(path: Path) -> tuple[bool, str]:
    from seahorse.cli.mcp_register import register_mcp

    return register_mcp(path)


def _claude_remove(path: Path) -> tuple[bool, str]:
    from seahorse.cli.mcp_register import remove_mcp_registration

    return remove_mcp_registration(path)


def _claude_is_registered(path: Path) -> bool:
    from seahorse.cli.mcp_register import is_mcp_registered

    return is_mcp_registered(path)


@dataclass(frozen=True)
class HarnessTarget:
    """One local harness destination for the ``seahorse-mcp`` registration."""

    harness_id: str
    config_env_var: str
    default_path: Callable[[], Path]
    register: Callable[[Path], tuple[bool, str]]
    remove: Callable[[Path], tuple[bool, str]]
    is_registered: Callable[[Path], bool]

    def config_path(self) -> Path:
        """Config path with the ``SEAHORSE_<HARNESS>`` override applied."""
        return _env_path(self.config_env_var, self.default_path())


# VS Code uses a "servers" top-level key and requires "type": "stdio";
# Cursor, Gemini CLI and Antigravity use "mcpServers" without a type field.
_VSCODE_ENTRY: dict[str, object] = {
    "type": "stdio",
    "command": MCP_SERVER_COMMAND,
    "args": [],
    "env": {},
}
_PLAIN_ENTRY: dict[str, object] = {"command": MCP_SERVER_COMMAND, "args": [], "env": {}}

HARNESS_TARGETS: dict[str, HarnessTarget] = {
    "claude-code": HarnessTarget(
        harness_id="claude-code",
        config_env_var="SEAHORSE_CLAUDE_JSON",
        default_path=_claude_path,
        register=_claude_register,
        remove=_claude_remove,
        is_registered=_claude_is_registered,
    ),
    "codex": HarnessTarget(
        harness_id="codex",
        config_env_var="SEAHORSE_CODEX_CONFIG",
        default_path=_codex_path,
        register=_codex_register,
        remove=_codex_remove,
        is_registered=_codex_is_registered,
    ),
    "cursor": _json_target(
        "cursor",
        "SEAHORSE_CURSOR_MCP_JSON",
        _cursor_path,
        servers_key="mcpServers",
        entry=dict(_PLAIN_ENTRY),
    ),
    "vscode": _json_target(
        "vscode",
        "SEAHORSE_VSCODE_MCP_JSON",
        _vscode_path,
        servers_key="servers",
        entry=dict(_VSCODE_ENTRY),
    ),
    "antigravity": _json_target(
        "antigravity",
        "SEAHORSE_ANTIGRAVITY_CONFIG",
        _antigravity_path,
        servers_key="mcpServers",
        entry=dict(_PLAIN_ENTRY),
    ),
    "gemini": _json_target(
        "gemini",
        "SEAHORSE_GEMINI_SETTINGS",
        _gemini_path,
        servers_key="mcpServers",
        entry=dict(_PLAIN_ENTRY),
    ),
}


def harness_ids() -> tuple[str, ...]:
    """Stable harness ids, in registration order (claude-code first)."""
    return tuple(HARNESS_TARGETS)


def resolve_target(harness_id: str) -> HarnessTarget:
    """Target for ``harness_id``; ValueError lists the valid ids."""
    target = HARNESS_TARGETS.get(harness_id)
    if target is None:
        raise ValueError(
            f"unknown harness {harness_id!r} — expected one of: "
            + ", ".join(harness_ids())
        )
    return target


__all__ = [
    "HARNESS_TARGETS",
    "MCP_SERVER_COMMAND",
    "MCP_SERVER_NAME",
    "HarnessTarget",
    "atomic_write_json",
    "atomic_write_text",
    "backup_once",
    "harness_ids",
    "read_json_dict",
    "resolve_target",
]