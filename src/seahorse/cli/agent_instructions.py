"""Agent instructions block for one-command onboarding.

``seahorse setup`` appends a delimited markdown block to Claude Code's
``~/.claude/CLAUDE.md`` teaching the agent how to use the seahorse-mcp
tools (recall before re-discovering, ``remember`` for durable facts,
``improve`` for corrections, the human as the authority over the vault).
The block is wrapped in HTML-comment markers so it can be found, updated
and removed without disturbing anything the user wrote around it — the
same merge discipline as the hooks in ``setup.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

BEGIN_MARKER = "<!-- seahorse-memory:begin -->"
END_MARKER = "<!-- seahorse-memory:end -->"

_INSTRUCTIONS = """\
# Persistent memory (Seahorse)

You have a persistent, bi-temporal memory via the `seahorse-mcp` MCP server.
The vault resolves automatically: the vault containing the current working
directory, else the user's default vault.

- **At the start of a task**: if prior context matters (past decisions,
  debugging history, user preferences), use `recall` with a focused query
  before asking the user or re-discovering from scratch.
- **When you learn something durable** — a decision with its rationale, a
  root cause that took real work to find, a preference, a project fact —
  save it with `remember` (concise body, meaningful `title`). Prefer
  `improve` (not a duplicate `remember`) when correcting an existing memory;
  the history is preserved.
- **Procedural knowledge** (repeatable workflows, "how we do X") goes in via
  `skill_add`; retrieve it with `skill_search`.
- **Session capture is automatic** (hooks + observer): you do not need to
  log what happened in the session. Distillation into consolidated knowledge
  notes is done by `seahorse consolidate` — run it when the user asks or at
  a natural milestone.
- The memory is the user's own Obsidian vault: the human reads and edits the
  same notes. If `recall` returns something that contradicts what the user
  just said, the user is right — correct the memory with `improve`.\
"""

_BLOCK = f"{BEGIN_MARKER}\n{_INSTRUCTIONS}\n{END_MARKER}"


def claude_md_path() -> Path:
    """Path of Claude Code's user memory file (overridable for tests)."""
    env = os.environ.get("SEAHORSE_CLAUDE_MD")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "CLAUDE.md"


def instructions_block() -> str:
    """The exact block setup installs (exposed for tests and doctor)."""
    return _BLOCK


def installed(path: Path | None = None) -> bool:
    """True when a current block is present between the markers."""
    return _read_block(path) is not None


def _read_block(path: Path | None) -> str | None:
    path = path or claude_md_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    start = text.find(BEGIN_MARKER)
    end = text.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        return None
    return text[start : end + len(END_MARKER)]


def install_agent_instructions(path: Path | None = None) -> tuple[bool, str]:
    """Idempotently ensure the block exists (updating a stale one in place).

    Returns ``(installed, detail)``. Anything the user wrote around the
    block is preserved byte-for-byte; a fresh file gets just the block.
    """
    path = path or claude_md_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_BLOCK + "\n", encoding="utf-8")
        return True, f"instructions written to {path}"
    except OSError as exc:
        return False, f"cannot read {path}: {exc}"

    existing = _read_block(path)
    if existing == _BLOCK:
        return True, f"already installed in {path}"
    if existing is not None:
        text = text.replace(existing, _BLOCK)
        detail = "updated"
    else:
        sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        text = text + sep + _BLOCK + "\n"
        detail = "appended"
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        return False, f"cannot write {path}: {exc}"
    return True, f"instructions {detail} in {path}"


def remove_agent_instructions(path: Path | None = None) -> tuple[bool, str]:
    """Remove the block (and any leftover blank gap), preserving the rest.

    Returns ``(removed, detail)``; ``(True, ...)`` when there is nothing to
    remove.
    """
    path = path or claude_md_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return True, "no user CLAUDE.md — nothing to remove"
    except OSError as exc:
        return False, f"cannot read {path}: {exc}"
    existing = _read_block(path)
    if existing is None:
        return True, "not installed — nothing to remove"
    cleaned = (
        text.replace("\n" + existing + "\n", "")
        .replace(existing + "\n", "")
        .replace("\n" + existing, "")
        .replace(existing, "")
    )
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")
    try:
        path.write_text(cleaned, encoding="utf-8")
    except OSError as exc:
        return False, f"cannot write {path}: {exc}"
    return True, f"instructions removed from {path}"


__all__ = [
    "BEGIN_MARKER",
    "END_MARKER",
    "claude_md_path",
    "install_agent_instructions",
    "installed",
    "instructions_block",
    "remove_agent_instructions",
]