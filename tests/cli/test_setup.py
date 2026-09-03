"""Tests for ``seahorse setup`` / ``seahorse setup --uninstall``.

``setup`` writes the ``[observe]`` section to ``seahorse.toml`` (with a
generated auth token) and MERGES the Claude Code hooks into
``~/.claude/settings.json`` — coexisting with claude-mem's hooks. ``--uninstall``
removes the observer hooks (identified by the ``seahorse observe event`` marker)
and the ``[observe]`` section, preserving other hooks.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from seahorse.cli.config import load_config, write_default_config
from seahorse.cli.setup import (
    HOOK_MARKER,
    discover_obsidian_vaults,
    ensure_vault,
    merge_hooks,
    remove_hooks,
    run_setup,
    run_setup_uninstall,
    write_observe_config,
)


def _cfg(tmp_path):
    (tmp_path / ".seahorse").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".seahorse" / "seahorse.toml").write_text(
        "[seahorse]\n"
        'db_path = "seahorse.db"\n'
        'default_extraction_mode = "skip"\n'
        "top_k = 10\n",
        encoding="utf-8",
    )
    return tmp_path


def _isolate_global_config(monkeypatch, tmp_path) -> Path:
    """Redirect the global config (pointer + Obsidian registry) to tmp.

    Monkeypatches the production path helpers instead of XDG_CONFIG_HOME
    alone: on macOS both root at ~/Library and IGNORE XDG, so XDG-only
    isolation leaked into the runner's real home and cross-contaminated
    tests (macOS CI failures)."""
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setattr(
        "seahorse.cli.config.global_pointer_path",
        lambda: xdg / "seahorse" / "vault",
    )
    monkeypatch.setattr(
        "seahorse.cli.setup._obsidian_registry_path",
        lambda: xdg / "obsidian" / "obsidian.json",
    )
    return xdg


def _settings_path(tmp_path) -> str:
    return str(tmp_path / "settings.json")


def _write_settings(path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def _commands(entry: dict) -> list[str]:
    """All commands in a hook entry, from either the flat (legacy, invalid)
    or the nested (Claude Code) shape. Test-side mirror of the production
    helper — kept independent so the tests do not trust the implementation.
    """
    commands = [entry["command"]] if entry.get("command") else []
    commands.extend(
        h["command"] for h in entry.get("hooks", []) if h.get("command")
    )
    return commands


# ---------------------------------------------------------------------------
# write_observe_config
# ---------------------------------------------------------------------------


def test_write_observe_config_adds_section(tmp_path) -> None:
    vault = _cfg(tmp_path)
    write_observe_config(vault)
    cfg = load_config(vault)
    assert cfg.observe is not None
    assert cfg.observe.enabled is True
    assert cfg.observe.extraction == "skip"
    assert cfg.observe.token is not None  # auth token generated


def test_write_observe_config_preserves_existing_section(tmp_path) -> None:
    vault = _cfg(tmp_path)
    (vault / ".seahorse" / "seahorse.toml").write_text(
        "[seahorse]\n"
        'db_path = "seahorse.db"\n'
        'default_extraction_mode = "skip"\n'
        "top_k = 10\n"
        "[observe]\n"
        'extraction = "llm"\n'
        'token = "keep-me"\n',
        encoding="utf-8",
    )
    write_observe_config(vault)
    cfg = load_config(vault)
    assert cfg.observe is not None
    assert cfg.observe.extraction == "llm"  # preserved
    assert cfg.observe.token == "keep-me"  # preserved


# ---------------------------------------------------------------------------
# merge_hooks / remove_hooks
# ---------------------------------------------------------------------------


def test_merge_hooks_adds_observer_hooks(tmp_path) -> None:
    path = _settings_path(tmp_path)
    _write_settings(path, {})
    merge_hooks(path, hook_command="python -m seahorse.cli.app observe event")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    hooks = data["hooks"]
    assert "SessionStart" in hooks
    assert "UserPromptSubmit" in hooks
    assert "PostToolUse" in hooks
    assert "Stop" in hooks
    for event in ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"):
        for entry in hooks[event]:
            # The Claude Code shape: matcher + hooks array of command objects.
            assert entry["hooks"][0]["type"] == "command"
            assert HOOK_MARKER in entry["hooks"][0]["command"]


def test_merge_hooks_shape_is_claude_code_valid(tmp_path) -> None:
    """The written entry must validate against Claude Code's schema:
    `matcher` (string) + `hooks` (array of {type, command}). A flat
    `command` key at the entry level is silently ignored by Claude Code.
    """
    path = _settings_path(tmp_path)
    _write_settings(path, {})
    merge_hooks(path, hook_command="python -m seahorse.cli.app observe event")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    for event in ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"):
        for entry in data["hooks"][event]:
            assert isinstance(entry["hooks"], list)
            assert entry.get("command") is None


def test_merge_hooks_creates_missing_parent_dir(tmp_path) -> None:
    """A fresh user has no ~/.claude/ — merge_hooks must create it, not crash."""
    path = tmp_path / "does-not-exist" / "settings.json"
    merge_hooks(path, hook_command="python -m seahorse.cli.app observe event")
    assert path.is_file()
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    assert "hooks" in data


def test_merge_hooks_coexists_with_existing_hooks(tmp_path) -> None:
    """Coexistence with claude-mem: the observer hooks are ADDED, not replacing."""
    path = _settings_path(tmp_path)
    _write_settings(
        path,
        {
            "hooks": {
                "UserPromptSubmit": [
                    {"matcher": "*", "command": "claude-mem capture"}
                ]
            }
        },
    )
    merge_hooks(path, hook_command="python -m seahorse.cli.app observe event")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    commands = [c for h in data["hooks"]["UserPromptSubmit"] for c in _commands(h)]
    assert "claude-mem capture" in commands  # preserved
    assert any(HOOK_MARKER in c for c in commands)  # observer added


def test_merge_hooks_is_idempotent(tmp_path) -> None:
    path = _settings_path(tmp_path)
    _write_settings(path, {})
    merge_hooks(path, hook_command="python -m seahorse.cli.app observe event")
    merge_hooks(path, hook_command="python -m seahorse.cli.app observe event")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    for event in ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"):
        observer = [
            h
            for h in data["hooks"][event]
            if any(HOOK_MARKER in c for c in _commands(h))
        ]
        assert len(observer) == 1  # no duplicates


def test_merge_hooks_idempotent_with_preexisting_wellformed_hook(tmp_path) -> None:
    """Regression: a well-formed (nested) observer hook written by a newer
    version must be detected by an older-format run and NOT duplicated —
    the marker check must look inside `hooks`, not just at the entry level.
    """
    path = _settings_path(tmp_path)
    nested = {
        "matcher": "*",
        "hooks": [{"type": "command", "command": "py -m seahorse.cli.app observe event"}],
    }
    _write_settings(path, {"hooks": {"UserPromptSubmit": [nested]}})
    merge_hooks(path, hook_command="python -m seahorse.cli.app observe event")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    entries = data["hooks"]["UserPromptSubmit"]
    assert len(entries) == 1  # the pre-existing hook, not duplicated
    assert HOOK_MARKER in entries[0]["hooks"][0]["command"]


def test_remove_hooks_removes_observer_only(tmp_path) -> None:
    """Legacy flat entries (written by v0.16.0's buggy merge) are still
    removed — the uninstall must clean up the malformed artifacts too.
    """
    path = _settings_path(tmp_path)
    _write_settings(
        path,
        {
            "hooks": {
                "UserPromptSubmit": [
                    {"matcher": "*", "command": "claude-mem capture"},
                    {"matcher": "*", "command": "python -m seahorse.cli.app observe event"},
                ]
            }
        },
    )
    remove_hooks(path)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    commands = [c for h in data["hooks"]["UserPromptSubmit"] for c in _commands(h)]
    assert "claude-mem capture" in commands  # preserved
    assert not any(HOOK_MARKER in c for c in commands)  # observer removed


def test_remove_hooks_removes_nested_observer_hooks(tmp_path) -> None:
    """Regression: well-formed (nested) observer hooks were invisible to the
    uninstall because the marker check only looked at the entry level.
    """
    path = _settings_path(tmp_path)
    _write_settings(
        path,
        {
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python -m seahorse.cli.app observe event",
                            }
                        ],
                    },
                    {
                        "matcher": "*",
                        "hooks": [{"type": "command", "command": "claude-mem capture"}],
                    },
                ]
            }
        },
    )
    remove_hooks(path)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    commands = [c for h in data["hooks"]["UserPromptSubmit"] for c in _commands(h)]
    assert "claude-mem capture" in commands  # preserved
    assert not any(HOOK_MARKER in c for c in commands)  # observer removed


def test_remove_hooks_missing_file_is_noop(tmp_path) -> None:
    path = _settings_path(tmp_path)
    remove_hooks(path)  # no crash


# ---------------------------------------------------------------------------
# run_setup / run_setup_uninstall
# ---------------------------------------------------------------------------


def test_run_setup_writes_config_and_hooks(tmp_path, monkeypatch) -> None:
    vault = _cfg(tmp_path)
    settings = _settings_path(tmp_path)
    monkeypatch.setenv("SEAHORSE_CLAUDE_SETTINGS", settings)
    _isolate_global_config(monkeypatch, tmp_path)
    import io

    out = io.StringIO()
    run_setup(vault, settings_path=settings, fmt="human", out=out)
    assert "setup" in out.getvalue()
    cfg = load_config(vault)
    assert cfg.observe is not None
    assert cfg.materialize is not None  # setup is the [materialize] opt-in path
    with open(settings, encoding="utf-8") as fh:
        data = json.load(fh)
    assert "hooks" in data


def test_run_setup_registers_global_pointer(tmp_path, monkeypatch) -> None:
    """``setup`` registers the vault as the user's default (no env var needed)."""
    vault = _cfg(tmp_path)
    settings = _settings_path(tmp_path)
    monkeypatch.setenv("SEAHORSE_CLAUDE_SETTINGS", settings)
    _isolate_global_config(monkeypatch, tmp_path)
    import io

    run_setup(vault, settings_path=settings, fmt="human", out=io.StringIO())
    pointer = tmp_path / "xdg" / "seahorse" / "vault"
    assert pointer.is_file()
    assert Path(pointer.read_text().strip()) == vault.resolve()


# ---------------------------------------------------------------------------
# ensure_vault — setup's resolve-or-create entry (kills the exit-82 wall)
# ---------------------------------------------------------------------------


def test_ensure_vault_explicit_missing_dir_is_created(tmp_path, monkeypatch) -> None:
    """``setup --vault <new path>`` bootstraps instead of exit 82."""
    _isolate_global_config(monkeypatch, tmp_path)
    vault = ensure_vault(tmp_path / "fresh")
    assert (vault / ".seahorse" / "seahorse.toml").is_file()
    assert vault == (tmp_path / "fresh").resolve()


def test_ensure_vault_resolution_wins_without_explicit(tmp_path, monkeypatch) -> None:
    """cwd (or parents) / pointer resolve as before — no wizard, no prompt."""
    _isolate_global_config(monkeypatch, tmp_path)
    monkeypatch.delenv("SEAHORSE_VAULT", raising=False)
    write_default_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert ensure_vault(None) == tmp_path.resolve()


def test_ensure_vault_wizard_picks_discovered_vault(tmp_path, monkeypatch) -> None:
    """Interactive: the discovered vaults are offered; the pick is honored."""
    _isolate_global_config(monkeypatch, tmp_path)
    monkeypatch.delenv("SEAHORSE_VAULT", raising=False)
    vault_a = tmp_path / "vault-a"
    vault_a.mkdir()
    monkeypatch.setattr(
        "seahorse.cli.setup.discover_obsidian_vaults", lambda: [vault_a]
    )
    monkeypatch.setattr("sys.stdin", type("_TTY", (), {"isatty": lambda _s: True})())
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")
    os.chdir(tmp_path)
    picked = ensure_vault(None)
    assert picked == vault_a
    assert (vault_a / ".seahorse" / "seahorse.toml").is_file()  # bootstrapped


def test_ensure_vault_wizard_create_default(tmp_path, monkeypatch) -> None:
    """The last option creates a fresh vault at the portable ~/seahorse-mem."""
    _isolate_global_config(monkeypatch, tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("SEAHORSE_VAULT", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("seahorse.cli.setup.discover_obsidian_vaults", lambda: [])
    monkeypatch.setattr("sys.stdin", type("_TTY", (), {"isatty": lambda _s: True})())
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")
    picked = ensure_vault(None)
    assert picked == (tmp_path / "home" / "seahorse-mem").resolve()
    assert (picked / ".seahorse" / "seahorse.toml").is_file()


def test_ensure_vault_non_tty_bootstraps_portable_default(tmp_path, monkeypatch) -> None:
    """No resolution + no TTY: the portable ~/seahorse-mem is bootstrapped.

    An agent running one-command onboarding without a TTY must never hit the
    cold-start exit 82.
    """
    _isolate_global_config(monkeypatch, tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("SEAHORSE_VAULT", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", type("_NO_TTY", (), {"isatty": lambda _s: False})())
    picked = ensure_vault(None)
    assert picked == (tmp_path / "home" / "seahorse-mem").resolve()
    assert (picked / ".seahorse" / "seahorse.toml").is_file()


def test_discover_obsidian_vaults_parses_registry(tmp_path, monkeypatch) -> None:
    """obsidian.json vaults are parsed; non-existent dirs are filtered."""
    _isolate_global_config(monkeypatch, tmp_path)
    reg = tmp_path / "xdg" / "obsidian" / "obsidian.json"
    reg.parent.mkdir(parents=True)
    real = tmp_path / "real-vault"
    real.mkdir()
    reg.write_text(json.dumps({
        "vaults": {
            "abc123": {"path": str(real), "ts": 1},
            "dead": {"path": str(tmp_path / "gone"), "ts": 2},
        }
    }))
    assert discover_obsidian_vaults() == [real.resolve()]


def test_discover_obsidian_vaults_missing_registry_is_empty(tmp_path, monkeypatch) -> None:
    _isolate_global_config(monkeypatch, tmp_path)
    assert discover_obsidian_vaults() == []


def test_run_setup_uninstall_removes_hooks(tmp_path, monkeypatch) -> None:
    vault = _cfg(tmp_path)
    settings = _settings_path(tmp_path)
    _write_settings(
        settings,
        {
            "hooks": {
                "UserPromptSubmit": [
                    {"matcher": "*", "command": "python -m seahorse.cli.app observe event"}
                ]
            }
        },
    )
    import io

    out = io.StringIO()
    run_setup_uninstall(vault, settings_path=settings, fmt="human", out=out)
    with open(settings, encoding="utf-8") as fh:
        data = json.load(fh)
    # The only hook was the observer's — the empty event key is removed.
    assert "UserPromptSubmit" not in data["hooks"]


def test_run_setup_uninstall_removes_ours_skills_keeps_foreign(
    tmp_path, monkeypatch
) -> None:
    vault = _cfg(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setenv(
        "SEAHORSE_CLAUDE_SKILLS_DIR", str(home / ".claude" / "skills")
    )
    from seahorse.cli.skill_install import install_skill, skill_state

    install_skill("consolidate")
    foreign_dir = home / ".claude" / "skills" / "my-own"
    foreign_dir.mkdir(parents=True)
    (foreign_dir / "SKILL.md").write_text("# mine\n", encoding="utf-8")
    import io

    out = io.StringIO()
    run_setup_uninstall(vault, settings_path=tmp_path / "settings.json", fmt="human", out=out)
    assert skill_state("consolidate") == "absent"
    assert (foreign_dir / "SKILL.md").read_text(encoding="utf-8") == "# mine\n"
    assert "skill: consolidate: removed" in out.getvalue()


def test_run_setup_uninstall_reports_each_surface_once(tmp_path) -> None:
    """Regression: the MCP/instructions block used to run twice."""
    vault = _cfg(tmp_path)
    import io

    out = io.StringIO()
    run_setup_uninstall(vault, settings_path=tmp_path / "settings.json", fmt="human", out=out)
    text = out.getvalue()
    assert text.count("seahorse setup: uninstalled") == 1
    assert text.count("mcp:") == 1
    assert text.count("agent instructions:") == 1
