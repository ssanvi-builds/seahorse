"""Tests for ``seahorse.cli.mcp_register`` — safe ~/.claude.json writes.

Contract: atomic, idempotent, foreign keys preserved, corrupt file never
touched, one-time backup, and a symmetric removal.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from seahorse.cli.mcp_register import (
    MCP_SERVER_NAME,
    claude_json_path,
    is_mcp_registered,
    register_mcp,
    remove_mcp_registration,
)


@pytest.fixture()
def claude_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "claude.json"
    monkeypatch.setenv("SEAHORSE_CLAUDE_JSON", str(path))
    return path


class TestRegister:
    def test_fresh_file_creates_config_with_server(
        self, claude_json: Path
    ) -> None:
        ok, detail = register_mcp()
        assert ok
        data = json.loads(claude_json.read_text())
        assert data["mcpServers"][MCP_SERVER_NAME] == {
            "type": "stdio",
            "command": "seahorse-mcp",
            "args": [],
            "env": {},
        }
        assert MCP_SERVER_NAME in detail

    def test_idempotent_second_call_reports_already(
        self, claude_json: Path
    ) -> None:
        register_mcp()
        before = claude_json.read_text()
        ok, detail = register_mcp()
        assert ok and "already" in detail
        assert claude_json.read_text() == before

    def test_foreign_keys_and_fields_preserved(
        self, claude_json: Path
    ) -> None:
        claude_json.write_text(
            json.dumps(
                {
                    "theme": "dark",
                    "mcpServers": {
                        "other": {"type": "stdio", "command": "other-cmd"}
                    },
                }
            )
        )
        register_mcp()
        data = json.loads(claude_json.read_text())
        assert data["theme"] == "dark"
        assert data["mcpServers"]["other"] == {
            "type": "stdio",
            "command": "other-cmd",
        }
        assert MCP_SERVER_NAME in data["mcpServers"]

    def test_wrong_existing_entry_is_repaired(
        self, claude_json: Path
    ) -> None:
        claude_json.write_text(
            json.dumps({"mcpServers": {MCP_SERVER_NAME: {"command": "wrong"}}})
        )
        ok, _ = register_mcp()
        assert ok
        assert is_mcp_registered()

    def test_corrupt_file_is_never_touched(
        self, claude_json: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        claude_json.write_text("{not json")
        monkeypatch.setattr(
            "seahorse.cli.mcp_register.shutil.which", lambda _: None
        )  # no real `claude` subprocess in tests
        ok, detail = register_mcp()
        assert not ok
        assert "cannot parse" in detail
        assert claude_json.read_text() == "{not json"

    def test_one_time_backup_created(self, claude_json: Path) -> None:
        claude_json.write_text(json.dumps({"existing": True}))
        register_mcp()
        backup = claude_json.with_suffix(".json.seahorse-bak")
        assert backup.exists()
        assert json.loads(backup.read_text()) == {"existing": True}

    def test_backup_not_overwritten_on_second_repair(
        self, claude_json: Path
    ) -> None:
        register_mcp()
        backup = claude_json.with_suffix(".json.seahorse-bak")
        backup.write_text('{"original": true}')
        claude_json.write_text(json.dumps({"drifted": True}))
        register_mcp()
        assert json.loads(backup.read_text()) == {"original": True}

    def test_unwritable_file_reports_failure_not_crash(
        self, claude_json: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        claude_json.parent.mkdir(parents=True, exist_ok=True)
        claude_json.write_text("{}")
        claude_json.chmod(0o444)
        monkeypatch.setattr("os.replace", lambda *a, **kw: (_ for _ in ()).throw(OSError("ro")))
        monkeypatch.setattr(
            "seahorse.cli.mcp_register.shutil.which", lambda _: None
        )  # no real `claude` subprocess in tests
        ok, detail = register_mcp()
        assert not ok
        assert "cannot write" in detail
        claude_json.chmod(0o644)

    def test_no_temp_litter_on_success(self, claude_json: Path) -> None:
        register_mcp()
        assert [p.name for p in claude_json.parent.iterdir()] == ["claude.json"]


class TestRemove:
    def test_remove_preserves_rest(self, claude_json: Path) -> None:
        claude_json.write_text(
            json.dumps(
                {
                    "theme": "dark",
                    "mcpServers": {
                        MCP_SERVER_NAME: {"command": "seahorse-mcp"},
                        "other": {"command": "other"},
                    },
                }
            )
        )
        ok, _ = remove_mcp_registration()
        assert ok
        data = json.loads(claude_json.read_text())
        assert MCP_SERVER_NAME not in data["mcpServers"]
        assert data["mcpServers"]["other"] == {"command": "other"}
        assert data["theme"] == "dark"

    def test_remove_when_absent_is_ok(self, claude_json: Path) -> None:
        claude_json.write_text("{}")
        ok, _ = remove_mcp_registration()
        assert ok

    def test_remove_missing_file_is_ok(self, claude_json: Path) -> None:
        ok, _ = remove_mcp_registration()
        assert ok

    def test_remove_corrupt_never_touches(self, claude_json: Path) -> None:
        claude_json.write_text("garbage{")
        ok, detail = remove_mcp_registration()
        assert not ok
        assert "cannot parse" in detail
        assert claude_json.read_text() == "garbage{"


class TestPaths:
    def test_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_path = tmp_path / "custom.json"
        monkeypatch.setenv("SEAHORSE_CLAUDE_JSON", str(env_path))
        assert claude_json_path() == env_path

    def test_default_is_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SEAHORSE_CLAUDE_JSON", raising=False)
        assert claude_json_path() == Path.home() / ".claude.json"