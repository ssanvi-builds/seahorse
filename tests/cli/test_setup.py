"""Tests for ``seahorse setup`` / ``seahorse setup --uninstall``.

``setup`` writes the ``[observe]`` section to ``seahorse.toml`` (with a
generated auth token) and MERGES the Claude Code hooks into
``~/.claude/settings.json`` — coexisting with claude-mem's hooks. ``--uninstall``
removes the observer hooks (identified by the ``seahorse observe event`` marker)
and the ``[observe]`` section, preserving other hooks.
"""

from __future__ import annotations

import json

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
        assert any(HOOK_MARKER in h["command"] for h in hooks[event])


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
    commands = [h["command"] for h in data["hooks"]["UserPromptSubmit"]]
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
        observer = [h for h in data["hooks"][event] if HOOK_MARKER in h["command"]]
        assert len(observer) == 1  # no duplicates


def test_remove_hooks_removes_observer_only(tmp_path) -> None:
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
    commands = [h["command"] for h in data["hooks"]["UserPromptSubmit"]]
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
    import io

    out = io.StringIO()
    run_setup(vault, settings_path=settings, fmt="human", out=out)
    assert "setup" in out.getvalue()
    cfg = load_config(vault)
    assert cfg.observe is not None
    with open(settings, encoding="utf-8") as fh:
        data = json.load(fh)
    assert "hooks" in data


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
