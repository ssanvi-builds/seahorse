"""Root conftest — suite-level safety net.

``sandbox_user_surfaces`` (autouse) exists because of a real incident
(2026-09-11): the ``seahorse setup --uninstall`` tests isolated the hooks
settings file but let ``remove_mcp_registration`` / ``remove_agent_instructions``
/ ``remove_skills`` resolve their DEFAULT paths — a full suite run on a
machine with a real install wiped its ``~/.claude.json`` entry,
``~/.claude/CLAUDE.md`` block and ``~/.claude/skills`` entries. Invisible on
CI (no ``~/.claude`` there), destructive on a developer box.

The fixture redirects every user-surface env override (the ``SEAHORSE_*``
sandbox seams the production code already honors) to a per-test sandbox, so
the leak class is impossible rather than patched test-by-test. Tests that
assert the DEFAULT resolution delenv the override explicitly and keep
working.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def sandbox_user_surfaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never touch the developer's real user config (autouse, every test).

    Redirections (all honor a ``SEAHORSE_*`` env override in production):
    Claude Code (config json, CLAUDE.md, skills, settings.json), the other
    harnesses (codex config/AGENTS.md/hooks, cursor, vscode, gemini,
    antigravity), the credentials store, and XDG_CONFIG_HOME (global pointer).
    """
    home = tmp_path / "sandbox-user-home"
    monkeypatch.setenv("SEAHORSE_CLAUDE_JSON", str(home / "claude.json"))
    monkeypatch.setenv("SEAHORSE_CLAUDE_MD", str(home / "claude" / "CLAUDE.md"))
    monkeypatch.setenv(
        "SEAHORSE_CLAUDE_SKILLS_DIR", str(home / "claude" / "skills")
    )
    monkeypatch.setenv(
        "SEAHORSE_CLAUDE_SETTINGS", str(home / "claude" / "settings.json")
    )
    monkeypatch.setenv("SEAHORSE_CODEX_CONFIG", str(home / "codex" / "config.toml"))
    monkeypatch.setenv("SEAHORSE_CODEX_AGENTS_MD", str(home / "codex" / "AGENTS.md"))
    monkeypatch.setenv(
        "SEAHORSE_CODEX_HOOKS_JSON", str(home / "codex" / "hooks.json")
    )
    monkeypatch.setenv("SEAHORSE_CURSOR_MCP_JSON", str(home / "cursor" / "mcp.json"))
    monkeypatch.setenv(
        "SEAHORSE_VSCODE_MCP_JSON", str(home / "vscode" / "mcp.json")
    )
    monkeypatch.setenv(
        "SEAHORSE_ANTIGRAVITY_CONFIG", str(home / "gemini" / "mcp_config.json")
    )
    monkeypatch.setenv("SEAHORSE_ANTIGRAVITY_MD", str(home / "gemini" / "GEMINI.md"))
    monkeypatch.setenv(
        "SEAHORSE_GEMINI_SETTINGS", str(home / "gemini" / "settings.json")
    )
    monkeypatch.setenv("SEAHORSE_GEMINI_MD", str(home / "gemini" / "GEMINI.md"))
    monkeypatch.setenv(
        "SEAHORSE_CREDENTIALS", str(home / "seahorse" / "credentials.json")
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    return home