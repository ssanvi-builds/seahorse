"""Tests for ``seahorse.cli.codex_hooks`` — the Codex GA hooks installer.

Merge discipline identical to ``harness_targets.py``: marker-keyed entries
only, foreign hooks preserved, one-time backup, corrupt file never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from seahorse.cli.codex_hooks import (
    CODEX_HOOK_MARKER,
    codex_hooks_installed,
    codex_hooks_path,
    merge_codex_hooks,
    remove_codex_hooks,
)

COMMAND = "/opt/py -m seahorse.cli.app observe event --agent-id codex"
EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop")


@pytest.fixture
def hooks_path(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("SEAHORSE_CODEX_HOOKS_JSON", str(tmp_path / "hooks.json"))
    return tmp_path / "hooks.json"


def test_codex_hooks_path_env_override(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / "custom" / "hooks.json"
    monkeypatch.setenv("SEAHORSE_CODEX_HOOKS_JSON", str(env_path))
    assert codex_hooks_path() == env_path


def test_merge_fresh_install_writes_all_four_events(hooks_path) -> None:
    installed, detail = merge_codex_hooks(hooks_path, hook_command=COMMAND)
    assert installed
    assert "approve them once via /hooks" in detail
    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    for event in EVENTS:
        entries = data["hooks"][event]
        assert len(entries) == 1
        handler = entries[0]["hooks"][0]
        assert handler["command"] == COMMAND
        assert handler["type"] == "command"
        assert handler["timeout"] == 10
        assert handler["statusMessage"].startswith("seahorse")


def test_merge_is_idempotent_byte_identical(hooks_path) -> None:
    merge_codex_hooks(hooks_path, hook_command=COMMAND)
    first = hooks_path.read_text(encoding="utf-8")
    installed, detail = merge_codex_hooks(hooks_path, hook_command=COMMAND)
    assert installed  # end-state OK — same convention as harness_targets
    assert "already installed" in detail
    assert hooks_path.read_text(encoding="utf-8") == first


def test_merge_preserves_foreign_hooks(hooks_path) -> None:
    foreign = {
        "hooks": {
            "SessionStart": [
                {"matcher": "startup", "hooks": [{"type": "command", "command": "my-own-hook"}]}
            ]
        }
    }
    hooks_path.write_text(json.dumps(foreign), encoding="utf-8")
    merge_codex_hooks(hooks_path, hook_command=COMMAND)
    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    session_entries = data["hooks"]["SessionStart"]
    assert len(session_entries) == 2
    assert session_entries[0]["hooks"][0]["command"] == "my-own-hook"


def test_merge_corrupt_file_never_touched(hooks_path) -> None:
    hooks_path.write_text("{not json", encoding="utf-8")
    before = hooks_path.read_text(encoding="utf-8")
    installed, detail = merge_codex_hooks(hooks_path, hook_command=COMMAND)
    assert not installed
    assert "not touching it" in detail
    assert hooks_path.read_text(encoding="utf-8") == before


def test_merge_makes_one_time_backup(hooks_path) -> None:
    foreign = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}}
    hooks_path.write_text(json.dumps(foreign), encoding="utf-8")
    merge_codex_hooks(hooks_path, hook_command=COMMAND)
    backup = hooks_path.with_suffix(".json.seahorse-bak")
    assert backup.exists()
    assert json.loads(backup.read_text(encoding="utf-8")) == foreign
    # Second install (nothing to do, but no overwrite either).
    merge_codex_hooks(hooks_path, hook_command=COMMAND)


def test_remove_symmetric_to_merge(hooks_path) -> None:
    foreign = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}}
    hooks_path.write_text(json.dumps(foreign), encoding="utf-8")
    merge_codex_hooks(hooks_path, hook_command=COMMAND)
    removed, detail = remove_codex_hooks(hooks_path)
    assert removed
    assert "removed" in detail
    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert data["hooks"] == foreign["hooks"]


def test_remove_idempotent_when_absent(hooks_path) -> None:
    removed, detail = remove_codex_hooks(hooks_path)
    assert removed
    assert "nothing to remove" in detail
    merge_codex_hooks(hooks_path, hook_command=COMMAND)
    removed, _ = remove_codex_hooks(hooks_path)
    assert removed
    removed, _ = remove_codex_hooks(hooks_path)
    assert removed
    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert CODEX_HOOK_MARKER not in json.dumps(data)


def test_remove_corrupt_file_never_touched(hooks_path) -> None:
    hooks_path.write_text("{nope", encoding="utf-8")
    removed, detail = remove_codex_hooks(hooks_path)
    assert not removed
    assert "not touching it" in detail


def test_installed_truth_table(hooks_path) -> None:
    assert not codex_hooks_installed(hooks_path)
    hooks_path.write_text("{}", encoding="utf-8")
    assert not codex_hooks_installed(hooks_path)
    merge_codex_hooks(hooks_path, hook_command=COMMAND)
    assert codex_hooks_installed(hooks_path)
    remove_codex_hooks(hooks_path)
    assert not codex_hooks_installed(hooks_path)