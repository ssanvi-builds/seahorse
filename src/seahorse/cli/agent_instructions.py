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

# Legacy (pre-0.22) installs wrote the instructions WITHOUT the HTML-comment
# markers, so _read_block cannot see them — an updater that only knows the
# marked block would append a duplicate. Every legacy block starts with this
# exact H1.
_LEGACY_HEADING = "# Persistent memory (Seahorse)"

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
  **Call shape for `remember`/`improve`/`forget`**: the `by` parameter is a
  provenance OBJECT with the required keys `agent_id`, `session_id` and
  `source_type` (one of `agent`/`human`/`importer`/`system`) — never a
  string:
  `{"by": {"agent_id": "claude-code", "session_id": "<current session>", "source_type": "agent"}}`.
  Tags are not supported in this release — do not send them.
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


def _extract_block(text: str) -> str | None:
    """The marked block present in ``text``, or None."""
    start = text.find(BEGIN_MARKER)
    end = text.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        return None
    return text[start : end + len(END_MARKER)]


def _read_block(path: Path | None) -> str | None:
    path = path or claude_md_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return _extract_block(text)


def _strip_stale_blocks(text: str) -> str:
    """Remove every marked block that is not the current one.

    ``_extract_block`` only sees the first marked block; when several exist
    (a legacy migration landing next to a stale block), the non-current ones
    are stripped, together with any blank-line gaps they leave behind.
    """
    while True:
        stale = None
        idx = 0
        while True:
            start = text.find(BEGIN_MARKER, idx)
            if start == -1:
                break
            end = text.find(END_MARKER, start)
            if end == -1:
                break
            end += len(END_MARKER)
            block = text[start:end]
            if block != _BLOCK:
                stale = block
                break
            idx = end
        if stale is None:
            return text
        text = text.replace(stale + "\n", "").replace(stale, "")
        while "\n\n\n" in text:
            text = text.replace("\n\n\n", "\n\n")


def _legacy_span(lines: list[str]) -> tuple[int, str, int] | None:
    """Locate a legacy markerless instructions section.

    Returns ``(start_line, section_text, end_line_exclusive)`` for the first
    legacy H1 found OUTSIDE a marked block, spanning to the next marker, the
    next top-level heading, or EOF. Lines inside a marked block are ignored
    (the same H1 opens the current block too).
    """
    inside = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == BEGIN_MARKER:
            inside = True
        elif stripped == END_MARKER:
            inside = False
        elif not inside and stripped == _LEGACY_HEADING:
            for j in range(i + 1, len(lines)):
                s = lines[j].strip()
                if s == BEGIN_MARKER or (s.startswith("# ") and s != _LEGACY_HEADING):
                    return i, "".join(lines[i:j]), j
            return i, "".join(lines[i:]), len(lines)
    return None


def install_agent_instructions(path: Path | None = None) -> tuple[bool, str]:
    """Idempotently ensure the block exists (updating a stale one in place).

    A legacy markerless block (pre-0.22 installs) is replaced, not appended
    after — the result is always exactly one current block. Returns
    ``(installed, detail)``. Anything the user wrote around the block is
    preserved byte-for-byte; a fresh file gets just the block.
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

    # Migrate any legacy unmarked section first, so a stale-or-current marked
    # block plus an orphan legacy one never both survive.
    legacy_detail = ""
    lines = text.splitlines(keepends=True)
    span = _legacy_span(lines)
    if span is not None:
        start, _, end = span
        while end > start and not lines[end - 1].strip():
            end -= 1  # trailing blanks of the legacy section
        lines[start:end] = [_BLOCK + "\n"]
        if start + 1 < len(lines) and lines[start + 1].strip():
            lines.insert(start + 1, "\n")  # blank line before what follows
        text = "".join(lines)
        # A stale MARKED block from a later install must not survive next to
        # the migrated one.
        text = _strip_stale_blocks(text)
        legacy_detail = "legacy unmarked block replaced, "

    existing = _extract_block(text)
    if existing == _BLOCK:
        detail = legacy_detail + f"already installed in {path}"
        if not legacy_detail:
            return True, f"instructions already installed in {path}"
    elif existing is not None:
        text = text.replace(existing, _BLOCK)
        detail = legacy_detail + "updated"
    else:
        sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        text = text + sep + _BLOCK + "\n"
        detail = legacy_detail + "appended"
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        return False, f"cannot write {path}: {exc}"
    return True, f"instructions {detail}"


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