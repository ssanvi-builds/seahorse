"""Packaged agent skills for one-command onboarding.

``seahorse setup`` installs Claude Code skills (``~/.claude/skills/<name>/
SKILL.md``) teaching the agent to drive Seahorse commands directly —
currently ``consolidate``, so an agent session can run the distillation
pass (and enrich its output via the seahorse-mcp tools) without any API
key: the agent's own LLM does the synthesis.

The same merge discipline as ``agent_instructions`` applies: each skill
file carries an HTML-comment marker so a Seahorse skill can be found,
updated and removed, while a foreign ``SKILL.md`` (user-written, no
marker) is never touched by install or uninstall.
"""

from __future__ import annotations

import os
from pathlib import Path

SKILL_MARKER = "<!-- seahorse-memory:skill -->"

SKILL_NAMES: tuple[str, ...] = ("consolidate",)

_TEMPLATES: dict[str, str] = {
    "consolidate": f"""\
---
name: consolidate
description: >-
  Distill recent Seahorse memory activity into consolidated knowledge notes.
  Use when the user asks to consolidate, or at a natural milestone (end of a
  feature, end of a session).
---

{SKILL_MARKER}
# Consolidate Seahorse memory

Run the Seahorse engine's consolidation pass, then enrich its output with the
seahorse-mcp tools. The agent's own LLM does the synthesis — no API key needed.

## Steps

1. Run `seahorse consolidate` (deterministic clustering). Never pass `--vault`
   unless the user asked for a specific vault — it resolves from the working
   directory.
2. Read the notes it reports. Enrich thin or contradictory notes via the
   seahorse-mcp tools: `recall_full` for context, `improve` to correct (the
   user is the authority over the vault). Use `--supersede` only when the
   user explicitly asks to replace older notes.
3. Report in 1-2 sentences: how many notes, what they cover, any conflicts
   left for the user to decide.
""",
}


def skills_dir() -> Path:
    """Root of Claude Code's personal skills (overridable for tests)."""
    env = os.environ.get("SEAHORSE_CLAUDE_SKILLS_DIR")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "skills"


def skill_path(name: str) -> Path:
    """Path of the SKILL.md for ``name``."""
    return skills_dir() / name / "SKILL.md"


def skill_template(name: str) -> str:
    """The exact file content setup installs for ``name``."""
    return _TEMPLATES[name]


def skill_state(name: str, *, path: Path | None = None) -> str:
    """``absent``, ``ours`` (marker present) or ``foreign``.

    An unreadable existing file counts as ``foreign`` — install must stay
    conservative and never clobber what it cannot read.
    """
    path = path or skill_path(name)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "foreign"
    return "ours" if SKILL_MARKER in text else "foreign"


def install_skill(name: str) -> tuple[bool, str]:
    """Idempotently ensure ``name`` matches the packaged template.

    Returns ``(installed, detail)``. A stale Seahorse skill is updated in
    place; a foreign SKILL.md is left untouched and reported as a failure
    so setup surfaces the conflict instead of silently skipping.
    """
    path = skill_path(name)
    state = skill_state(name)
    if state == "foreign":
        return False, f"{name}: foreign SKILL.md at {path} — left untouched"
    if state == "ours":
        current = path.read_text(encoding="utf-8")
        if current == skill_template(name):
            return True, f"{name}: already installed at {path}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(skill_template(name), encoding="utf-8")
    except OSError as exc:
        return False, f"{name}: cannot write {path}: {exc}"
    detail = "updated" if state == "ours" else "installed"
    return True, f"{name}: {detail} at {path}"


def remove_skill(name: str) -> tuple[bool, str]:
    """Remove the Seahorse skill for ``name`` (never a foreign one).

    Returns ``(removed, detail)``; ``(True, ...)`` when there is nothing
    to remove.
    """
    path = skill_path(name)
    state = skill_state(name)
    if state == "absent":
        return True, f"{name}: not installed — nothing to remove"
    if state == "foreign":
        return True, f"{name}: foreign SKILL.md at {path} — left untouched"
    try:
        path.unlink()
        path.parent.rmdir()
    except OSError as exc:
        return False, f"{name}: cannot remove {path}: {exc}"
    return True, f"{name}: removed {path}"


def install_skills() -> list[tuple[str, bool, str]]:
    """Install every packaged skill; returns ``(name, ok, detail)`` rows."""
    return [(name, ok, detail) for name in SKILL_NAMES for ok, detail in [install_skill(name)]]


def remove_skills() -> list[tuple[str, bool, str]]:
    """Remove every Seahorse skill; returns ``(name, ok, detail)`` rows."""
    return [(name, ok, detail) for name in SKILL_NAMES for ok, detail in [remove_skill(name)]]


__all__ = [
    "SKILL_MARKER",
    "SKILL_NAMES",
    "install_skill",
    "install_skills",
    "remove_skill",
    "remove_skills",
    "skill_path",
    "skill_state",
    "skill_template",
    "skills_dir",
]