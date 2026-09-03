"""Tests for one-command onboarding (``run_full_setup``) and its wiring.

Sandbox rules: every global surface is redirected (pointer, Obsidian
registry, ~/.claude.json, ~/.claude/CLAUDE.md, settings.json) and the
observer spawn is stubbed — no real processes, no real network.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from seahorse.cli.config import load_config, write_default_config
from seahorse.cli.onboarding import repair_steps_for, run_full_setup
from seahorse.cli.setup import (
    CONSOLIDATE_HOOK_MARKER,
    ensure_vault,
    merge_consolidate_hook,
    remove_consolidate_hook,
)


def _cfg(tmp_path: Path) -> Path:
    write_default_config(tmp_path)
    return tmp_path


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    """Redirect every global surface to the sandbox; return their paths."""
    xdg = tmp_path / "xdg"
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    settings = tmp_path / "settings.json"
    claude_json = tmp_path / "claude.json"
    claude_md = home / ".claude" / "CLAUDE.md"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SEAHORSE_CLAUDE_JSON", str(claude_json))
    monkeypatch.setenv("SEAHORSE_CLAUDE_MD", str(claude_md))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.setattr(
        "seahorse.cli.config.global_pointer_path",
        lambda: xdg / "seahorse" / "vault",
    )
    monkeypatch.setattr(
        "seahorse.cli.setup._obsidian_registry_path",
        lambda: xdg / "obsidian" / "obsidian.json",
    )
    return {
        "settings": settings,
        "claude_json": claude_json,
        "claude_md": claude_md,
        "pointer": xdg / "seahorse" / "vault",
    }


@pytest.fixture()
def no_observer(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Stub the observer start/stop: record calls, never spawn."""
    calls: list[dict] = []

    def fake_start(cfg, *, fmt, out):
        calls.append({"op": "start", "vault": str(cfg.vault)})
        out.write('{"started": true, "pid": 4242}\n')

    def fake_stop(cfg, *, fmt, out):
        calls.append({"op": "stop", "vault": str(cfg.vault)})
        out.write('{"stopped": true}\n')

    monkeypatch.setattr("seahorse.observe.cli.run_observe_start", fake_start)
    monkeypatch.setattr("seahorse.observe.cli.run_observe_stop", fake_stop)
    return calls


@pytest.fixture()
def llm_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The provider bootstrap is replaced (no network, no TTY reads)."""
    from seahorse.cli.provider_bootstrap import ProviderDecision

    monkeypatch.setattr(
        "seahorse.cli.provider_bootstrap.bootstrap_llm_provider",
        lambda vault, *, out, self_test=None: ProviderDecision(
            primary="ollama/qwen3:0.6b", detail="ok"
        ),
    )


def _run(vault: Path, paths: dict[str, Path] | None = None, **kwargs):
    out = io.StringIO()
    checks = run_full_setup(
        vault,
        settings_path=str(paths["settings"]) if paths else None,
        fmt="human",
        out=out,
        **kwargs,
    )
    return checks, out.getvalue()


def _status(checks: list[dict], name: str) -> dict:
    return next(c for c in checks if c["check"] == name)


class TestRunFullSetup:
    def test_fresh_user_sandbox_everything_configured(
        self, tmp_path, monkeypatch, no_observer, llm_skipped
    ) -> None:
        paths = _isolate(monkeypatch, tmp_path)
        vault = _cfg(tmp_path / "vault")
        checks, human = _run(vault, paths)

        by_name = {c["check"]: c["status"] for c in checks}
        assert by_name["vault"] == "OK"
        assert by_name["db"] == "OK"  # eager DB
        assert by_name["capture"] == "OK"
        assert by_name["observer"] == "OK"
        assert by_name["mcp"] == "OK"
        assert by_name["agent_instructions"] == "OK"
        assert by_name["llm"] == "OK"
        assert by_name["embeddings"] == "SKIP"  # opt-in download
        assert "seahorse setup complete" in human

        cfg = load_config(vault)
        assert cfg.observe is not None and cfg.materialize is not None
        assert (vault / ".seahorse" / "seahorse.db").exists()
        assert paths["pointer"].is_file()
        servers = json.loads(paths["claude_json"].read_text())["mcpServers"]
        assert servers["seahorse-mcp"]["command"] == "seahorse-mcp"
        md = paths["claude_md"].read_text()
        assert "seahorse-memory:begin" in md and "seahorse-memory:end" in md
        hooks = json.loads(paths["settings"].read_text())["hooks"]
        assert "SessionStart" in hooks and "Stop" in hooks
        assert len(no_observer) == 1 and no_observer[0]["op"] == "start"

    def test_idempotent_rerun_stays_green(
        self, tmp_path, monkeypatch, no_observer, llm_skipped
    ) -> None:
        paths = _isolate(monkeypatch, tmp_path)
        vault = _cfg(tmp_path / "vault")
        _run(vault, paths)
        checks, _ = _run(vault, paths)
        assert all(c["status"] != "WARN" for c in checks)

    def test_no_mcp_and_no_instructions_flags_skip_steps(
        self, tmp_path, monkeypatch, no_observer, llm_skipped
    ) -> None:
        paths = _isolate(monkeypatch, tmp_path)
        vault = _cfg(tmp_path / "vault")
        checks, _ = _run(vault, paths, no_mcp=True, no_agent_instructions=True, skip_llm=True)
        by_name = {c["check"]: c["status"] for c in checks}
        assert by_name["mcp"] == "SKIP"
        assert by_name["agent_instructions"] == "SKIP"
        assert by_name["llm"] == "SKIP"
        assert not paths["claude_json"].exists()
        assert not paths["claude_md"].exists()

    def test_auto_consolidate_opt_in_writes_config_and_hook(
        self, tmp_path, monkeypatch, no_observer, llm_skipped
    ) -> None:
        paths = _isolate(monkeypatch, tmp_path)
        vault = _cfg(tmp_path / "vault")
        checks, _ = _run(vault, paths, auto_consolidate=True)
        by_name = {c["check"]: c["status"] for c in checks}
        assert by_name["consolidate"] == "OK"
        assert load_config(vault).consolidate.auto_on_stop is True
        hooks = json.loads(paths["settings"].read_text())["hooks"]
        stop_commands = [
            c["command"]
            for h in hooks["Stop"]
            for c in h.get("hooks", [])
        ]
        assert any("consolidate --auto" in c for c in stop_commands)

    def test_without_opt_in_no_consolidate_step(
        self, tmp_path, monkeypatch, no_observer, llm_skipped
    ) -> None:
        paths = _isolate(monkeypatch, tmp_path)
        vault = _cfg(tmp_path / "vault")
        checks, _ = _run(vault, paths)
        assert "consolidate" not in {c["check"] for c in checks}
        assert load_config(vault).consolidate is None

    def test_step_failure_is_warn_not_crash(
        self, tmp_path, monkeypatch, no_observer, llm_skipped
    ) -> None:
        paths = _isolate(monkeypatch, tmp_path)
        vault = _cfg(tmp_path / "vault")

        def boom():
            raise RuntimeError("disk exploded")

        monkeypatch.setattr(
            "seahorse.cli.onboarding._register_mcp", boom
        )
        checks, human = _run(vault, paths)
        mcp = _status(checks, "mcp")
        assert mcp["status"] == "WARN" and "disk exploded" in mcp["detail"]
        assert "some steps need attention" in human

    def test_json_output_shape(
        self, tmp_path, monkeypatch, no_observer, llm_skipped
    ) -> None:
        _isolate(monkeypatch, tmp_path)
        vault = _cfg(tmp_path / "vault")
        out = io.StringIO()
        run_full_setup(vault, fmt="json", out=out)
        payload = json.loads(out.getvalue())
        assert payload["command"] == "setup"
        assert {"check", "status", "detail"} <= set(payload["checks"][0])


class TestEnsureVaultNonTty:
    def test_non_tty_no_resolution_bootstraps_portable_default(
        self, tmp_path, monkeypatch
    ) -> None:
        _isolate(monkeypatch, tmp_path)
        empty_cwd = tmp_path / "elsewhere"
        empty_cwd.mkdir()
        monkeypatch.chdir(empty_cwd)  # no .seahorse up the tree, no env var
        monkeypatch.delenv("SEAHORSE_VAULT", raising=False)
        monkeypatch.setattr("sys.stdin", type("_S", (), {"isatty": lambda self: False})())
        vault = ensure_vault(None)
        assert vault == (tmp_path / "home" / "seahorse-mem").resolve()
        assert load_config(vault)  # initialized (no CliConfigInvalid)


class TestConsolidateHook:
    def test_merge_and_remove_consolidate_hook(self, tmp_path) -> None:
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"hooks": {}}))
        merge_consolidate_hook(
            settings, hook_command="python -m seahorse.cli.app consolidate --auto"
        )
        data = json.loads(settings.read_text())
        assert CONSOLIDATE_HOOK_MARKER in json.dumps(data)
        merge_consolidate_hook(
            settings, hook_command="python -m seahorse.cli.app consolidate --auto"
        )
        data = json.loads(settings.read_text())
        assert json.dumps(data).count("consolidate --auto") == 1
        remove_consolidate_hook(settings)
        assert "consolidate --auto" not in settings.read_text()

    def test_remove_consolidate_hook_keeps_observer_hook(self, tmp_path) -> None:
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
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
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "python -m seahorse.cli.app consolidate --auto",
                                    }
                                ],
                            },
                        ]
                    }
                }
            )
        )
        remove_consolidate_hook(settings)
        data = json.loads(settings.read_text())
        commands = [c["command"] for h in data["hooks"]["Stop"] for c in h["hooks"]]
        assert len(commands) == 1 and "observe event" in commands[0]


class TestRepairSteps:
    def test_known_checks_map_to_steps(self, tmp_path, monkeypatch) -> None:
        _isolate(monkeypatch, tmp_path)
        steps = repair_steps_for(
            ["claude_hooks", "mcp_registered", "agent_instructions", "nonsense"],
            vault=tmp_path / "vault",
            settings_path=str(tmp_path / "settings.json"),
        )
        assert [s.check for s in steps] == [
            "claude_hooks",
            "mcp_registered",
            "agent_instructions",
        ]

    def test_repair_steps_execute(self, tmp_path, monkeypatch, no_observer) -> None:
        paths = _isolate(monkeypatch, tmp_path)
        vault = _cfg(tmp_path / "vault")
        steps = repair_steps_for(
            ["claude_hooks", "mcp_registered", "agent_instructions"],
            vault=vault,
            settings_path=str(paths["settings"]),
        )
        for step in steps:
            step.run()
        assert paths["claude_json"].exists()
        assert "seahorse-memory:begin" in paths["claude_md"].read_text()
        assert "hooks" in json.loads(paths["settings"].read_text())