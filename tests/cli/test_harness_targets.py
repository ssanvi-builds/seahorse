"""Contract tests for the multi-harness MCP registration targets.

Every destination must honor the shared write guarantees: atomic write,
one-time backup, idempotency, foreign-state preservation, and the
sandbox env overrides. Claude Code's own module has its dedicated suite
(``test_mcp_register.py``); here it is exercised through the target
contract to prove delegation.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from seahorse.cli import harness_targets as ht

ENV_VARS = {
    "claude-code": "SEAHORSE_CLAUDE_JSON",
    "codex": "SEAHORSE_CODEX_CONFIG",
    "cursor": "SEAHORSE_CURSOR_MCP_JSON",
    "vscode": "SEAHORSE_VSCODE_MCP_JSON",
    "antigravity": "SEAHORSE_ANTIGRAVITY_CONFIG",
    "gemini": "SEAHORSE_GEMINI_SETTINGS",
}


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Redirect every harness config into a sandboxed temp dir."""
    paths = {
        harness_id: tmp_path / harness_id / "config"
        for harness_id in ENV_VARS
    }
    for harness_id, path in paths.items():
        monkeypatch.setenv(ENV_VARS[harness_id], str(path))
    return paths


def _foreign_json() -> dict[str, object]:
    return {
        "model": "foreign-model",
        "mcpServers": {"other-server": {"command": "other", "args": ["x"]}},
    }


# ------------------------------------------------------------------- registry


def test_harness_ids_are_stable_and_complete() -> None:
    assert ht.harness_ids() == (
        "claude-code",
        "codex",
        "cursor",
        "vscode",
        "antigravity",
        "gemini",
    )


def test_resolve_target_unknown_id_lists_valid_ids() -> None:
    with pytest.raises(ValueError, match="codex.*cursor"):
        ht.resolve_target("claude-desktop")


def test_config_path_uses_env_override(sandbox: dict[str, Path]) -> None:
    target = ht.resolve_target("codex")
    assert target.config_path() == sandbox["codex"]


def test_config_path_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)
    target = ht.resolve_target("cursor")
    assert target.config_path() == Path.home() / ".cursor" / "mcp.json"


@pytest.mark.parametrize(
    ("harness_id", "servers_key", "has_type"),
    [
        ("cursor", "mcpServers", False),
        ("vscode", "servers", True),
        ("antigravity", "mcpServers", False),
        ("gemini", "mcpServers", False),
    ],
)
def test_json_target_register_idempotent_preserving_foreign(
    sandbox: dict[str, Path], harness_id: str, servers_key: str, has_type: bool
) -> None:
    target = ht.resolve_target(harness_id)
    path = sandbox[harness_id]
    path.parent.mkdir(parents=True)
    foreign = _foreign_json()
    if servers_key != "mcpServers":
        foreign = {"model": "foreign-model", "servers": foreign.pop("mcpServers")}
    path.write_text(json.dumps(foreign), encoding="utf-8")

    ok, detail = target.register(path)
    assert ok, detail
    assert "registered" in detail

    data = json.loads(path.read_text(encoding="utf-8"))
    entry = data[servers_key][ht.MCP_SERVER_NAME]
    assert entry["command"] == ht.MCP_SERVER_COMMAND
    assert ("type" in entry) is has_type
    # Foreign state preserved verbatim.
    assert data["model"] == "foreign-model"
    other_key = "mcpServers" if "mcpServers" in data else "servers"
    assert "other-server" in data[other_key]

    first_bytes = path.read_bytes()
    ok2, detail2 = target.register(path)
    assert ok2 and "already" in detail2
    assert path.read_bytes() == first_bytes


@pytest.mark.parametrize(
    "harness_id", ["cursor", "vscode", "antigravity", "gemini"]
)
def test_json_target_corrupt_file_never_touched(
    sandbox: dict[str, Path], harness_id: str
) -> None:
    target = ht.resolve_target(harness_id)
    path = sandbox[harness_id]
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    ok, detail = target.register(path)
    assert not ok
    assert "not touching" in detail
    assert path.read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize(
    "harness_id", ["cursor", "vscode", "antigravity", "gemini"]
)
def test_json_target_remove_is_symmetric(
    sandbox: dict[str, Path], harness_id: str
) -> None:
    target = ht.resolve_target(harness_id)
    path = sandbox[harness_id]
    ok, _ = target.register(path)
    assert ok

    ok, detail = target.remove(path)
    assert ok and "removed" in detail
    data = json.loads(path.read_text(encoding="utf-8"))
    key = next(k for k in ("mcpServers", "servers") if k in data)
    assert ht.MCP_SERVER_NAME not in data[key]

    ok2, _ = target.remove(path)
    assert ok2  # nothing to remove is not an error


# ---------------------------------------------------------------- Codex (TOML)


def _foreign_toml() -> str:
    return (
        "# user config\n"
        'model = "o4-mini"\n'
        "\n"
        "[profiles.work]\n"
        'model = "gpt-5.3"\n'
    )


def test_codex_register_appends_marked_block_preserving_foreign(
    sandbox: dict[str, Path],
) -> None:
    target = ht.resolve_target("codex")
    path = sandbox["codex"]
    path.parent.mkdir(parents=True)
    path.write_text(_foreign_toml(), encoding="utf-8")

    ok, detail = target.register(path)
    assert ok and "registered" in detail

    text = path.read_text(encoding="utf-8")
    assert "# seahorse-mcp:begin" in text
    parsed = tomllib.loads(text)
    assert parsed["model"] == "o4-mini"
    assert parsed["profiles"]["work"]["model"] == "gpt-5.3"
    assert parsed["mcp_servers"][ht.MCP_SERVER_NAME]["command"] == ht.MCP_SERVER_COMMAND

    first_bytes = path.read_bytes()
    ok2, detail2 = target.register(path)
    assert ok2 and "already" in detail2
    assert path.read_bytes() == first_bytes


def test_codex_register_conflicting_table_never_touched(
    sandbox: dict[str, Path],
) -> None:
    target = ht.resolve_target("codex")
    path = sandbox["codex"]
    path.parent.mkdir(parents=True)
    original = _foreign_toml() + f"\n[mcp_servers.{ht.MCP_SERVER_NAME}]\n"
    original += 'command = "/custom/seahorse-mcp"\n'
    path.write_text(original, encoding="utf-8")

    ok, detail = target.register(path)
    assert not ok
    assert "different settings" in detail
    assert path.read_text(encoding="utf-8") == original


def test_codex_corrupt_toml_never_touched(sandbox: dict[str, Path]) -> None:
    target = ht.resolve_target("codex")
    path = sandbox["codex"]
    path.parent.mkdir(parents=True)
    path.write_text("model = [broken", encoding="utf-8")

    ok, detail = target.register(path)
    assert not ok and "not touching" in detail
    assert path.read_text(encoding="utf-8") == "model = [broken"


def test_codex_remove_restores_original_text(sandbox: dict[str, Path]) -> None:
    target = ht.resolve_target("codex")
    path = sandbox["codex"]
    path.parent.mkdir(parents=True)
    path.write_text(_foreign_toml(), encoding="utf-8")
    original = path.read_text(encoding="utf-8")

    ok, _ = target.register(path)
    assert ok
    ok, detail = target.remove(path)
    assert ok and "removed" in detail
    assert path.read_text(encoding="utf-8") == original


def test_codex_remove_foreign_table_never_touched(
    sandbox: dict[str, Path],
) -> None:
    target = ht.resolve_target("codex")
    path = sandbox["codex"]
    path.parent.mkdir(parents=True)
    original = _foreign_toml() + f"\n[mcp_servers.{ht.MCP_SERVER_NAME}]\n"
    original += 'command = "/custom/seahorse-mcp"\n'
    path.write_text(original, encoding="utf-8")

    ok, detail = target.remove(path)
    assert not ok
    assert "not written by" in detail
    assert path.read_text(encoding="utf-8") == original


# ---------------------------------------------------------- claude-code target


def test_claude_code_target_delegates_to_mcp_register(
    sandbox: dict[str, Path],
) -> None:
    target = ht.resolve_target("claude-code")
    path = sandbox["claude-code"]

    ok, detail = target.register(path)
    assert ok and "registered" in detail
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mcpServers"][ht.MCP_SERVER_NAME]["type"] == "stdio"

    assert target.is_registered(path) is True
    ok, _ = target.remove(path)
    assert ok
    assert target.is_registered(path) is False