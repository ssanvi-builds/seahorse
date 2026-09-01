"""Tests for ``seahorse setup`` / ``seahorse setup --uninstall``.

``setup`` writes the ``[observe]`` section to ``seahorse.toml`` (with a
generated auth token) and MERGES the Claude Code hooks into
``~/.claude/settings.json`` — coexisting with claude-mem's hooks. ``--uninstall``
removes the observer hooks (identified by the ``seahorse observe event`` marker)
and the ``[observe]`` section, preserving other hooks.
"""

from __future__ import annotations

import json
from pathlib import Path

from seahorse.cli.config import load_config
from seahorse.cli.setup import (
    HOOK_MARKER,
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
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
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
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    import io

    run_setup(vault, settings_path=settings, fmt="human", out=io.StringIO())
    pointer = tmp_path / "xdg" / "seahorse" / "vault"
    assert pointer.is_file()
    assert Path(pointer.read_text().strip()) == vault.resolve()


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
