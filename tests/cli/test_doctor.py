"""Tests for `seahorse doctor`.

Reports the extraction regime, the installed ``llm`` extra, missing API key
NAMES, a live provider probe (only when LiteLLM is installed), and the
extraction mode. A vault on pure ``skip`` (no ``[llm]``) is valid → WARN, not
FAIL; health is all-OK.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
from pathlib import Path

import pytest

from seahorse.cli.config import load_config, write_default_config
from seahorse.cli.doctor import _context_probe, run_doctor
from seahorse.cli.setup import write_observe_config


def _write(vault: Path, body: str) -> Path:
    cfg = vault / ".seahorse" / "seahorse.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(body, encoding="utf-8")
    return cfg


def _doctor(config, monkeypatch, litellm: bool = False) -> dict:
    monkeypatch.setattr("seahorse.cli.doctor._litellm_installed", lambda: litellm)
    out = io.StringIO()
    run_doctor(config, fmt="json", out=out)
    return json.loads(out.getvalue())


class TestDoctor:
    def test_no_llm_config_warns_and_marks_unhealthy(self, tmp_path, monkeypatch) -> None:
        _write(tmp_path, '[seahorse]\ndb_path = "x.db"\n')
        payload = _doctor(load_config(tmp_path), monkeypatch)
        assert any(
            c["check"] == "llm_config" and c["status"] == "WARN"
            for c in payload["checks"]
        )
        assert payload["healthy"] is False

    def test_local_ollama_route_ok_no_key_needed(self, tmp_path, monkeypatch) -> None:
        write_default_config(tmp_path)  # [llm] ollama/qwen3:1.7b
        payload = _doctor(load_config(tmp_path), monkeypatch)
        assert any(
            c["check"] == "llm_config" and c["status"] == "OK"
            for c in payload["checks"]
        )
        # Ollama has no key env → api_keys reports present.
        assert any(
            c["check"] == "api_keys" and c["status"] == "OK"
            for c in payload["checks"]
        )

    def test_missing_cloud_key_warns_with_env_name(self, tmp_path, monkeypatch) -> None:
        _write(
            tmp_path,
            '[seahorse]\n[llm]\nprimary = "gemini/gemini-2.5-flash"\n',
        )
        payload = _doctor(load_config(tmp_path), monkeypatch)
        api = next(c for c in payload["checks"] if c["check"] == "api_keys")
        assert api["status"] == "WARN"
        assert "GEMINI_API_KEY" in api["detail"]  # the NAME, not a value

    def test_provider_probe_skipped_without_litellm(self, tmp_path, monkeypatch) -> None:
        write_default_config(tmp_path)
        payload = _doctor(load_config(tmp_path), monkeypatch, litellm=False)
        assert not any(c["check"] == "provider" for c in payload["checks"])

    def test_litellm_missing_warns(self, tmp_path, monkeypatch) -> None:
        write_default_config(tmp_path)
        payload = _doctor(load_config(tmp_path), monkeypatch)
        assert any(
            c["check"] == "litellm" and c["status"] == "WARN"
            for c in payload["checks"]
        )

    def test_unknown_model_reported_by_id(self, tmp_path, monkeypatch) -> None:
        _write(tmp_path, '[seahorse]\n[llm]\nprimary = "nosuch/model-x"\n')
        payload = _doctor(load_config(tmp_path), monkeypatch)
        api = next(c for c in payload["checks"] if c["check"] == "api_keys")
        assert api["status"] == "WARN"
        assert "nosuch/model-x" in api["detail"]


class TestPrereqChecks:
    def test_python_check_reports_version_ok(self, tmp_path, monkeypatch) -> None:
        write_default_config(tmp_path)
        payload = _doctor(load_config(tmp_path), monkeypatch)
        py = next(c for c in payload["checks"] if c["check"] == "python")
        assert py["status"] == "OK"
        assert ">=3.11 required" in py["detail"]

    def test_uv_present_ok(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "seahorse.cli.doctor.shutil.which", lambda _name: "/usr/local/bin/uv"
        )
        write_default_config(tmp_path)
        payload = _doctor(load_config(tmp_path), monkeypatch)
        uv = next(c for c in payload["checks"] if c["check"] == "uv")
        assert uv["status"] == "OK"
        assert uv["detail"] == "present"

    def test_uv_absent_warns_and_unhealthy(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("seahorse.cli.doctor.shutil.which", lambda _name: None)
        write_default_config(tmp_path)
        payload = _doctor(load_config(tmp_path), monkeypatch)
        uv = next(c for c in payload["checks"] if c["check"] == "uv")
        assert uv["status"] == "WARN"
        assert "docs.astral.sh/uv" in uv["detail"]
        assert payload["healthy"] is False

    def test_obsidian_optional_ok(self, tmp_path, monkeypatch) -> None:
        write_default_config(tmp_path)
        payload = _doctor(load_config(tmp_path), monkeypatch)
        obs = next(c for c in payload["checks"] if c["check"] == "obsidian")
        assert obs["status"] == "OK"
        assert "optional" in obs["detail"]

    def test_sqlite_vec_supported_ok(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "seahorse.cli.doctor._sqlite_load_extension_supported", lambda: True
        )
        write_default_config(tmp_path)
        payload = _doctor(load_config(tmp_path), monkeypatch)
        sv = next(c for c in payload["checks"] if c["check"] == "sqlite_vec")
        assert sv["status"] == "OK"

    def test_sqlite_vec_unsupported_fails(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "seahorse.cli.doctor._sqlite_load_extension_supported", lambda: False
        )
        write_default_config(tmp_path)
        payload = _doctor(load_config(tmp_path), monkeypatch)
        sv = next(c for c in payload["checks"] if c["check"] == "sqlite_vec")
        assert sv["status"] == "FAIL"
        assert "load_extension" in sv["detail"]
        assert payload["healthy"] is False


class TestProviderSelfTest:
    def test_llm_error_reported_as_fail_not_crash(self, monkeypatch) -> None:
        from seahorse.cli.config import LlmConfig
        from seahorse.cli.doctor import _provider_self_test
        from seahorse.llm import LiteLLMBackend, LLMError

        def fake_extract(self, content, schema_hint, **kw):
            raise LLMError("boom")

        monkeypatch.setattr(LiteLLMBackend, "extract", fake_extract)
        ok, detail = _provider_self_test(LlmConfig(primary="ollama/qwen3:1.7b"))
        assert ok is False
        assert "boom" in detail

    def test_self_test_schema_tolerates_extra_fields(self) -> None:
        """Small local models emit ``valid_at`` from the extraction pattern; the
        probe schema must accept it (extra=allow) while still requiring the
        core ``subject`` field."""
        from seahorse.cli.doctor import _SelfTestSchema

        ok = _SelfTestSchema.model_validate({"subject": "Seahorse", "valid_at": ""})
        assert ok.subject == "Seahorse"

    def test_self_test_schema_requires_subject(self) -> None:
        from pydantic import ValidationError

        from seahorse.cli.doctor import _SelfTestSchema

        try:
            _SelfTestSchema.model_validate({"valid_at": ""})
        except ValidationError:
            return
        raise AssertionError("subject is required — probe must not pass without it")


# ---------------------------------------------------------------------------
# Capture end-to-end checks (hooks / observer / context)
# ---------------------------------------------------------------------------


def _settings(tmp_path: Path, events: list[str]) -> Path:
    """A settings.json whose observer hooks cover only ``events``."""
    path = tmp_path / "settings.json"
    hooks = {
        event: [{"matcher": "*", "hooks": [{"type": "command",
                                            "command": "py -m seahorse.cli.app observe event"}]}]
        for event in events
    }
    path.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    return path


class TestCaptureChecks:
    def test_hooks_installed_ok(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SEAHORSE_CLAUDE_SETTINGS", str(_settings(
            tmp_path, ["SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"]
        )))
        write_default_config(tmp_path)
        payload = _doctor(load_config(tmp_path), monkeypatch)
        hooks = next(c for c in payload["checks"] if c["check"] == "claude_hooks")
        assert hooks["status"] == "OK"
        assert "4 events" in hooks["detail"]

    def test_hooks_missing_file_warns(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(
            "SEAHORSE_CLAUDE_SETTINGS", str(tmp_path / "nope" / "settings.json")
        )
        write_default_config(tmp_path)
        payload = _doctor(load_config(tmp_path), monkeypatch)
        hooks = next(c for c in payload["checks"] if c["check"] == "claude_hooks")
        assert hooks["status"] == "WARN"
        assert "setup" in hooks["detail"]
        assert payload["healthy"] is False

    def test_hooks_partial_warns_naming_missing_events(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SEAHORSE_CLAUDE_SETTINGS", str(
            _settings(tmp_path, ["SessionStart"])
        ))
        write_default_config(tmp_path)
        payload = _doctor(load_config(tmp_path), monkeypatch)
        hooks = next(c for c in payload["checks"] if c["check"] == "claude_hooks")
        assert hooks["status"] == "WARN"
        assert "UserPromptSubmit" in hooks["detail"]

    def test_observer_socket_present_ok(self, tmp_path, monkeypatch) -> None:
        write_default_config(tmp_path)
        write_observe_config(tmp_path)
        cfg = load_config(tmp_path)
        assert cfg.observe is not None
        sock = cfg.seahorse_dir / cfg.observe.socket_path
        sock.parent.mkdir(parents=True, exist_ok=True)
        sock.touch()
        # Liveness is pid-based (L10): the socket alone is not proof the
        # observer runs — the pid file must point at a live process.
        pid = sock.parent / "observer.pid"
        pid.write_text(str(os.getpid()), encoding="utf-8")
        payload = _doctor(cfg, monkeypatch)
        obs = next(c for c in payload["checks"] if c["check"] == "observer")
        assert obs["status"] == "OK"
        assert str(os.getpid()) in obs["detail"]

    def test_observer_stale_socket_warns(self, tmp_path, monkeypatch) -> None:
        """L10 state 5: socket file left behind by a dead observer must be
        flagged, not reported as running."""
        write_default_config(tmp_path)
        write_observe_config(tmp_path)
        cfg = load_config(tmp_path)
        sock = cfg.seahorse_dir / cfg.observe.socket_path  # type: ignore[union-attr]
        sock.parent.mkdir(parents=True, exist_ok=True)
        sock.touch()
        payload = _doctor(cfg, monkeypatch)
        obs = next(c for c in payload["checks"] if c["check"] == "observer")
        assert obs["status"] == "WARN"
        assert "stale" in obs["detail"]
        assert payload["healthy"] is False

    def test_observer_dead_pid_warns_stale(self, tmp_path, monkeypatch) -> None:
        """A pid file pointing at a dead process is stale even with a socket."""
        write_default_config(tmp_path)
        write_observe_config(tmp_path)
        cfg = load_config(tmp_path)
        sock = cfg.seahorse_dir / cfg.observe.socket_path  # type: ignore[union-attr]
        sock.parent.mkdir(parents=True, exist_ok=True)
        sock.touch()
        pid = sock.parent / "observer.pid"
        pid.write_text("999999999", encoding="utf-8")
        payload = _doctor(cfg, monkeypatch)
        obs = next(c for c in payload["checks"] if c["check"] == "observer")
        assert obs["status"] == "WARN"
        assert "stale" in obs["detail"]


class TestDbCheck:
    """L10 states 1/6/6b: the db check must probe integrity, not just existence."""

    def test_db_missing_warns(self, tmp_path, monkeypatch) -> None:
        _write(tmp_path, '[seahorse]\ndb_path = "nope.db"\n')
        payload = _doctor(load_config(tmp_path), monkeypatch)
        db = next(c for c in payload["checks"] if c["check"] == "db")
        assert db["status"] == "WARN"

    def test_db_valid_ok(self, tmp_path, monkeypatch) -> None:
        write_default_config(tmp_path)
        cfg = load_config(tmp_path)
        con = sqlite3.connect(cfg.db_path)
        con.execute("CREATE TABLE t (x)")
        con.commit()
        con.close()
        payload = _doctor(cfg, monkeypatch)
        db = next(c for c in payload["checks"] if c["check"] == "db")
        assert db["status"] == "OK"

    def test_db_garbage_fails(self, tmp_path, monkeypatch) -> None:
        """L10 state 6b: random bytes in the db file → FAIL, never 'OK'."""
        write_default_config(tmp_path)
        cfg = load_config(tmp_path)
        cfg.db_path.write_bytes(os.urandom(65536))
        payload = _doctor(cfg, monkeypatch)
        db = next(c for c in payload["checks"] if c["check"] == "db")
        assert db["status"] == "FAIL"
        assert payload["healthy"] is False

    def test_db_unwritable_fails(self, tmp_path, monkeypatch) -> None:
        """L10 state 1: read-only vault → the db check names the fix."""
        write_default_config(tmp_path)
        cfg = load_config(tmp_path)
        cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.db_path.touch()
        cfg.db_path.chmod(0o444)
        try:
            payload = _doctor(cfg, monkeypatch)
        finally:
            cfg.db_path.chmod(0o644)
        db = next(c for c in payload["checks"] if c["check"] == "db")
        assert db["status"] == "FAIL"
        assert "writable" in db["detail"]

    def test_observer_socket_absent_warns_autostart(self, tmp_path, monkeypatch) -> None:
        write_default_config(tmp_path)
        write_observe_config(tmp_path)
        payload = _doctor(load_config(tmp_path), monkeypatch)
        obs = next(c for c in payload["checks"] if c["check"] == "observer")
        assert obs["status"] == "WARN"
        assert "auto-starts" in obs["detail"]

    def test_observer_unconfigured_warns_setup(self, tmp_path, monkeypatch) -> None:
        _write(tmp_path, '[seahorse]\ndb_path = "x.db"\n')
        payload = _doctor(load_config(tmp_path), monkeypatch)
        obs = next(c for c in payload["checks"] if c["check"] == "observer")
        assert obs["status"] == "WARN"
        assert "setup" in obs["detail"]

    def test_context_probe_ok(self, tmp_path, monkeypatch) -> None:
        write_default_config(tmp_path)
        monkeypatch.setattr(
            "seahorse.cli.doctor._context_probe", lambda _cfg: (True, "ok (128 chars)")
        )
        payload = _doctor(load_config(tmp_path), monkeypatch)
        ctx = next(c for c in payload["checks"] if c["check"] == "context")
        assert ctx["status"] == "OK"

    def test_context_probe_fail_warns(self, tmp_path, monkeypatch) -> None:
        write_default_config(tmp_path)
        monkeypatch.setattr(
            "seahorse.cli.doctor._context_probe", lambda _cfg: (False, "exit 83")
        )
        payload = _doctor(load_config(tmp_path), monkeypatch)
        ctx = next(c for c in payload["checks"] if c["check"] == "context")
        assert ctx["status"] == "WARN"
        assert "exit 83" in ctx["detail"]

    def test_context_probe_live_renders_nonempty(self, tmp_path, monkeypatch) -> None:
        """The real probe (subprocess) renders context against a real vault."""
        write_default_config(tmp_path)
        cfg = load_config(tmp_path)
        ok, detail = _context_probe(cfg)
        assert ok is True
        assert "ok" in detail


class TestOnboardingChecks:
    """The agent-surface checks: MCP, instructions, auto-consolidate + --fix."""

    @pytest.fixture(autouse=True)
    def _isolate_globals(self, monkeypatch, tmp_path):
        xdg = tmp_path / "xdg"
        home = tmp_path / "home"
        home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("SEAHORSE_CLAUDE_JSON", str(tmp_path / "claude.json"))
        # Redirect the other harnesses too — the real home dir must never leak in.
        monkeypatch.setenv("SEAHORSE_CODEX_CONFIG", str(tmp_path / "codex" / "config.toml"))
        monkeypatch.setenv("SEAHORSE_CODEX_HOOKS_JSON", str(tmp_path / "codex" / "hooks.json"))
        monkeypatch.setenv("SEAHORSE_CURSOR_MCP_JSON", str(tmp_path / "cursor" / "mcp.json"))
        monkeypatch.setenv("SEAHORSE_VSCODE_MCP_JSON", str(tmp_path / "vscode" / "mcp.json"))
        monkeypatch.setenv(
            "SEAHORSE_ANTIGRAVITY_CONFIG", str(tmp_path / "antigravity" / "mcp_config.json")
        )
        monkeypatch.setenv("SEAHORSE_GEMINI_SETTINGS", str(tmp_path / "gemini" / "settings.json"))
        monkeypatch.setenv("SEAHORSE_CLAUDE_MD", str(home / ".claude" / "CLAUDE.md"))
        monkeypatch.setenv(
            "SEAHORSE_CODEX_AGENTS_MD", str(tmp_path / "codex" / "AGENTS.md")
        )
        monkeypatch.setenv("SEAHORSE_GEMINI_MD", str(home / ".gemini" / "GEMINI.md"))
        monkeypatch.setenv(
            "SEAHORSE_ANTIGRAVITY_MD", str(home / ".gemini" / "GEMINI.md")
        )
        monkeypatch.setenv(
            "SEAHORSE_CLAUDE_SETTINGS", str(tmp_path / "settings.json")
        )
        monkeypatch.setenv(
            "SEAHORSE_CLAUDE_SKILLS_DIR", str(home / ".claude" / "skills")
        )
        monkeypatch.setenv("SEAHORSE_CREDENTIALS", str(tmp_path / "credentials.json"))

    def _config(self, tmp_path):
        write_default_config(tmp_path)
        return load_config(tmp_path)

    def _doctor(self, tmp_path, *, fix: bool = False):
        config = self._config(tmp_path)
        out = io.StringIO()
        run_doctor(config, fmt="json", out=out, fix=fix)
        return json.loads(out.getvalue())["checks"]

    def test_mcp_unregistered_warns(self, tmp_path, monkeypatch) -> None:
        config = self._config(tmp_path)
        monkeypatch.setattr("seahorse.cli.doctor._context_probe", lambda _c: (True, "ok"))
        payload = _doctor(config, monkeypatch)
        mcp = next(c for c in payload["checks"] if c["check"] == "mcp_registered")
        assert mcp["status"] == "WARN"
        assert "seahorse setup" in mcp["detail"]

    def test_agent_instructions_missing_warns(self, tmp_path, monkeypatch) -> None:
        config = self._config(tmp_path)
        monkeypatch.setattr("seahorse.cli.doctor._context_probe", lambda _c: (True, "ok"))
        payload = _doctor(config, monkeypatch)
        ai = next(c for c in payload["checks"] if c["check"] == "agent_instructions")
        assert ai["status"] == "WARN"

    def test_consolidate_off_is_ok_not_warn(self, tmp_path, monkeypatch) -> None:
        """Auto-consolidation is opt-in: off is a valid, healthy state."""
        config = self._config(tmp_path)
        monkeypatch.setattr("seahorse.cli.doctor._context_probe", lambda _c: (True, "ok"))
        payload = _doctor(config, monkeypatch)
        con = next(c for c in payload["checks"] if c["check"] == "consolidate")
        assert con["status"] == "OK"
        assert "opt-in" in con["detail"]

    def test_consolidate_on_reports_enabled(self, tmp_path, monkeypatch) -> None:
        config = self._config(tmp_path)
        monkeypatch.setattr("seahorse.cli.doctor._context_probe", lambda _c: (True, "ok"))
        from seahorse.cli.config import ConsolidateConfig, write_consolidate_config

        write_consolidate_config(tmp_path, ConsolidateConfig(auto_on_stop=True))
        config = load_config(tmp_path)
        payload = _doctor(config, monkeypatch)
        con = next(c for c in payload["checks"] if c["check"] == "consolidate")
        assert con["status"] == "OK" and "true" in con["detail"]

    def test_fix_repairs_unhealthy_surface(self, tmp_path, monkeypatch) -> None:
        """--fix registers MCP + installs instructions; the fix lines are OK."""
        config = self._config(tmp_path)
        monkeypatch.setattr("seahorse.cli.doctor._context_probe", lambda _c: (True, "ok"))
        out = io.StringIO()
        run_doctor(config, fmt="json", out=out, fix=True)
        payload = json.loads(out.getvalue())
        fix_rows = {
            c["check"]: c["status"]
            for c in payload["checks"]
            if c["check"].startswith("fix:")
        }
        assert fix_rows.get("fix:mcp_registered") == "OK"
        assert fix_rows.get("fix:agent_instructions") == "OK"
        # The repairs actually landed on disk.
        assert Path(os.environ["SEAHORSE_CLAUDE_JSON"]).exists()

    def test_fix_is_reported_not_raised_on_failure(self, tmp_path, monkeypatch) -> None:
        """A repair that raises becomes a FAIL line — doctor never crashes."""
        config = self._config(tmp_path)
        monkeypatch.setattr("seahorse.cli.doctor._context_probe", lambda _c: (True, "ok"))
        monkeypatch.setenv("SEAHORSE_CLAUDE_JSON", "/proc/nope/impossible.json")
        monkeypatch.setattr(
            "seahorse.cli.mcp_register.shutil.which", lambda _: None
        )  # no real `claude` subprocess in tests
        out = io.StringIO()
        run_doctor(config, fmt="json", out=out, fix=True)
        payload = json.loads(out.getvalue())
        fix_rows = [c for c in payload["checks"] if c["check"] == "fix:mcp_registered"]
        assert len(fix_rows) == 1
        assert fix_rows[0]["status"] == "FAIL"

    # -- per-harness MCP checks ---------------------------------------------

    def test_per_harness_mcp_registered_ok(self, tmp_path, monkeypatch) -> None:
        config = self._config(tmp_path)
        monkeypatch.setattr("seahorse.cli.doctor._context_probe", lambda _c: (True, "ok"))
        from seahorse.cli.harness_targets import resolve_target

        target = resolve_target("codex")
        codex_config = Path(os.environ["SEAHORSE_CODEX_CONFIG"])
        codex_config.parent.mkdir(parents=True, exist_ok=True)
        ok, _ = target.register(codex_config)
        assert ok
        payload = _doctor(config, monkeypatch)
        by_name = {c["check"]: c for c in payload["checks"]}
        assert by_name["mcp_registered:codex"]["status"] == "OK"
        assert "registered" in by_name["mcp_registered:codex"]["detail"]

    def test_per_harness_mcp_installed_but_unregistered_warns(
        self, tmp_path, monkeypatch
    ) -> None:
        config = self._config(tmp_path)
        monkeypatch.setattr("seahorse.cli.doctor._context_probe", lambda _c: (True, "ok"))
        codex_config = Path(os.environ["SEAHORSE_CODEX_CONFIG"])
        codex_config.parent.mkdir(parents=True, exist_ok=True)
        codex_config.write_text('model = "o4-mini"\n', encoding="utf-8")
        payload = _doctor(config, monkeypatch)
        check = next(c for c in payload["checks"] if c["check"] == "mcp_registered:codex")
        assert check["status"] == "WARN"
        assert "--harness codex" in check["detail"]

    def test_per_harness_absent_config_is_skipped_ok(self, tmp_path, monkeypatch) -> None:
        config = self._config(tmp_path)
        monkeypatch.setattr("seahorse.cli.doctor._context_probe", lambda _c: (True, "ok"))
        payload = _doctor(config, monkeypatch)
        for hid in ("cursor", "vscode", "antigravity", "gemini"):
            check = next(
                c for c in payload["checks"] if c["check"] == f"mcp_registered:{hid}"
            )
            assert check["status"] == "OK"
            assert "skipped" in check["detail"]

    def test_fix_repairs_per_harness_mcp(self, tmp_path, monkeypatch) -> None:
        config = self._config(tmp_path)
        monkeypatch.setattr("seahorse.cli.doctor._context_probe", lambda _c: (True, "ok"))
        codex_config = Path(os.environ["SEAHORSE_CODEX_CONFIG"])
        codex_config.parent.mkdir(parents=True, exist_ok=True)
        codex_config.write_text('model = "o4-mini"\n', encoding="utf-8")
        out = io.StringIO()
        run_doctor(config, fmt="json", out=out, fix=True)
        payload = json.loads(out.getvalue())
        fix_rows = [c for c in payload["checks"] if c["check"] == "fix:mcp_registered:codex"]
        assert len(fix_rows) == 1 and fix_rows[0]["status"] == "OK"
        from seahorse.cli.harness_targets import resolve_target

        assert resolve_target("codex").is_registered(codex_config)

    # -- skills_installed ---------------------------------------------------

    def test_per_harness_instructions_ok_when_block_installed(
        self, tmp_path, monkeypatch
    ) -> None:
        gemini_md = tmp_path / "home" / ".gemini" / "GEMINI.md"
        gemini_md.parent.mkdir(parents=True, exist_ok=True)
        gemini_md.write_text(
            "user rules\n\n"
            "<!-- seahorse-memory:begin -->\nblock\n<!-- seahorse-memory:end -->\n"
        )
        checks = self._doctor(tmp_path)
        by_name = {c["check"]: (c["status"], c["detail"]) for c in checks}
        assert by_name["agent_instructions:gemini"][0] == "OK"
        assert "installed" in by_name["agent_instructions:gemini"][1]

    def test_per_harness_instructions_installed_but_missing_block_warns(
        self, tmp_path, monkeypatch
    ) -> None:
        codex_md = tmp_path / "codex" / "AGENTS.md"
        codex_md.parent.mkdir(parents=True, exist_ok=True)
        codex_md.write_text("# my agent rules\n")
        checks = self._doctor(tmp_path)
        by_name = {c["check"]: (c["status"], c["detail"]) for c in checks}
        assert by_name["agent_instructions:codex"][0] == "WARN"
        assert "--harness codex" in by_name["agent_instructions:codex"][1]

    def test_per_harness_instructions_absent_file_is_skipped_ok(
        self, tmp_path, monkeypatch
    ) -> None:
        checks = self._doctor(tmp_path)
        by_name = {c["check"]: c["status"] for c in checks}
        for hid in ("codex", "antigravity", "gemini"):
            assert by_name[f"agent_instructions:{hid}"] == "OK"

    def test_fix_repairs_per_harness_instructions(self, tmp_path, monkeypatch) -> None:
        codex_md = tmp_path / "codex" / "AGENTS.md"
        codex_md.parent.mkdir(parents=True, exist_ok=True)
        codex_md.write_text("# my agent rules\n")
        checks = self._doctor(tmp_path, fix=True)
        by_name = {c["check"]: c["status"] for c in checks}
        assert by_name["fix:agent_instructions:codex"] == "OK"
        assert "seahorse-memory:begin" in codex_md.read_text()

    # -- codex_hooks --------------------------------------------------------

    def test_codex_hooks_absent_home_is_skipped_ok(self, tmp_path) -> None:
        checks = self._doctor(tmp_path)
        check = next(c for c in checks if c["check"] == "codex_hooks")
        assert check["status"] == "OK"
        assert "skipped" in check["detail"]

    def test_codex_home_without_hooks_file_warns(self, tmp_path) -> None:
        Path(tmp_path / "codex").mkdir()
        checks = self._doctor(tmp_path)
        check = next(c for c in checks if c["check"] == "codex_hooks")
        assert check["status"] == "WARN"
        assert "--harness codex" in check["detail"]

    def test_codex_hooks_corrupt_file_warns_never_repaired(self, tmp_path) -> None:
        hooks = Path(os.environ["SEAHORSE_CODEX_HOOKS_JSON"])
        hooks.parent.mkdir(parents=True)
        hooks.write_text("{nope", encoding="utf-8")
        checks = self._doctor(tmp_path, fix=True)
        check = next(c for c in checks if c["check"] == "codex_hooks")
        assert check["status"] == "WARN"
        assert "cannot parse" in check["detail"]
        assert hooks.read_text(encoding="utf-8") == "{nope"
        # The repair attempt runs (the check warned) but reports FAIL loudly.
        fix_row = next(c for c in checks if c["check"] == "fix:codex_hooks")
        assert fix_row["status"] == "FAIL"

    def test_codex_hooks_installed_ok(self, tmp_path) -> None:
        from seahorse.cli.codex_hooks import merge_codex_hooks

        hooks = Path(os.environ["SEAHORSE_CODEX_HOOKS_JSON"])
        hooks.parent.mkdir(parents=True)
        installed, _ = merge_codex_hooks(hooks, hook_command="py -m seahorse observe event")
        assert installed
        checks = self._doctor(tmp_path)
        check = next(c for c in checks if c["check"] == "codex_hooks")
        assert check["status"] == "OK"
        assert "installed" in check["detail"]

    def test_codex_hooks_file_without_marker_warns_and_fix_repairs(self, tmp_path) -> None:
        from seahorse.cli.codex_hooks import codex_hooks_installed

        hooks = Path(os.environ["SEAHORSE_CODEX_HOOKS_JSON"])
        hooks.parent.mkdir(parents=True)
        hooks.write_text(json.dumps({"hooks": {"Stop": []}}), encoding="utf-8")
        checks = self._doctor(tmp_path, fix=True)
        check = next(c for c in checks if c["check"] == "codex_hooks")
        assert check["status"] == "WARN"
        assert "missing" in check["detail"]
        fix_row = next(c for c in checks if c["check"] == "fix:codex_hooks")
        assert fix_row["status"] == "OK"
        assert codex_hooks_installed(hooks)

    # -- capture_health -----------------------------------------------------

    def _write_recent_episode(self, cfg) -> None:
        from datetime import UTC, datetime

        from seahorse.contracts.episode import Episode
        from seahorse.persistence.storage import Storage

        now = datetime.now(UTC)
        ep = Episode(
            id="cap-1",
            created_at=now,
            schema_version="1.1",
            provenance={"source_type": "agent"},
            body="# t\n\nb",
            subject="t",
            fact_id="f-cap-1",
            valid_at=now,
            cognitive_type="episodic",
            source_type="agent",
        )
        storage = Storage(cfg.db_path)
        try:
            storage.episodes.append(ep)
        finally:
            storage.close()

    def test_capture_health_no_db_is_ok(self, tmp_path) -> None:
        checks = self._doctor(tmp_path)
        check = next(c for c in checks if c["check"] == "capture_health")
        assert check["status"] == "OK"
        assert "no db yet" in check["detail"]

    def test_capture_health_recent_episode_ok(self, tmp_path) -> None:
        cfg = self._config(tmp_path)
        self._write_recent_episode(cfg)
        checks = self._doctor(tmp_path)
        check = next(c for c in checks if c["check"] == "capture_health")
        assert check["status"] == "OK"
        assert "1 episode" in check["detail"]

    def test_capture_health_zero_with_no_hooks_is_capture_on_intent(self, tmp_path) -> None:
        cfg = self._config(tmp_path)
        cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
        import sqlite3

        sqlite3.connect(cfg.db_path).close()  # db exists, empty
        checks = self._doctor(tmp_path)
        check = next(c for c in checks if c["check"] == "capture_health")
        assert check["status"] == "OK"
        assert "capture-on-intent" in check["detail"]

    def test_capture_health_zero_with_codex_hooks_warns_trust(self, tmp_path) -> None:
        from seahorse.cli.codex_hooks import merge_codex_hooks

        cfg = self._config(tmp_path)
        cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
        import sqlite3

        sqlite3.connect(cfg.db_path).close()  # db exists, empty
        hooks = Path(os.environ["SEAHORSE_CODEX_HOOKS_JSON"])
        installed, _ = merge_codex_hooks(hooks, hook_command="py -m seahorse observe event")
        assert installed
        checks = self._doctor(tmp_path)
        check = next(c for c in checks if c["check"] == "capture_health")
        assert check["status"] == "WARN"
        assert "/hooks" in check["detail"]

    def test_skills_absent_warns(self, tmp_path, monkeypatch) -> None:
        config = self._config(tmp_path)
        monkeypatch.setattr("seahorse.cli.doctor._context_probe", lambda _c: (True, "ok"))
        payload = _doctor(config, monkeypatch)
        skills = next(c for c in payload["checks"] if c["check"] == "skills_installed")
        assert skills["status"] == "WARN" and "seahorse setup" in skills["detail"]

    def test_skills_installed_ok(self, tmp_path, monkeypatch) -> None:
        config = self._config(tmp_path)
        monkeypatch.setattr("seahorse.cli.doctor._context_probe", lambda _c: (True, "ok"))
        from seahorse.cli.skill_install import SKILL_NAMES, install_skill

        for name in SKILL_NAMES:
            install_skill(name)
        payload = _doctor(config, monkeypatch)
        skills = next(c for c in payload["checks"] if c["check"] == "skills_installed")
        assert skills["status"] == "OK"
        assert all(name in skills["detail"] for name in SKILL_NAMES)

    def test_partial_skills_install_warns(self, tmp_path, monkeypatch) -> None:
        # One skill ours, one missing: the check must name both states so
        # `seahorse setup` (the fix) is the obvious next step.
        config = self._config(tmp_path)
        monkeypatch.setattr("seahorse.cli.doctor._context_probe", lambda _c: (True, "ok"))
        from seahorse.cli.skill_install import install_skill

        install_skill("consolidate")
        payload = _doctor(config, monkeypatch)
        skills = next(c for c in payload["checks"] if c["check"] == "skills_installed")
        assert skills["status"] == "WARN"
        assert "consolidate: installed" in skills["detail"]
        assert "session-note: missing" in skills["detail"]

    def test_foreign_skill_warns_and_never_repaired(self, tmp_path, monkeypatch) -> None:
        from pathlib import Path as _P

        config = self._config(tmp_path)
        monkeypatch.setattr("seahorse.cli.doctor._context_probe", lambda _c: (True, "ok"))
        skill_dir = _P(os.environ["SEAHORSE_CLAUDE_SKILLS_DIR"]) / "consolidate"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# user's own\n", encoding="utf-8")
        out = io.StringIO()
        run_doctor(config, fmt="json", out=out, fix=True)
        payload = json.loads(out.getvalue())
        skills = next(c for c in payload["checks"] if c["check"] == "skills_installed")
        assert skills["status"] == "WARN" and "not repaired" in skills["detail"]
        # --fix must NOT clobber the foreign file — the attempt reports FAIL.
        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# user's own\n"
        fix_row = next(c for c in payload["checks"] if c["check"] == "fix:skills_installed")
        assert fix_row["status"] == "FAIL" and "left untouched" in fix_row["detail"]

    # -- credentials --------------------------------------------------------

    def test_credentials_absent_is_ok(self, tmp_path, monkeypatch) -> None:
        config = self._config(tmp_path)
        monkeypatch.setattr("seahorse.cli.doctor._context_probe", lambda _c: (True, "ok"))
        payload = _doctor(config, monkeypatch)
        cred = next(c for c in payload["checks"] if c["check"] == "credentials")
        assert cred["status"] == "OK"

    def test_credentials_loose_permissions_warn_and_fix(self, tmp_path, monkeypatch) -> None:
        cred_path = Path(os.environ["SEAHORSE_CREDENTIALS"])
        cred_path.write_text("{}", encoding="utf-8")
        cred_path.chmod(0o644)
        config = self._config(tmp_path)
        monkeypatch.setattr("seahorse.cli.doctor._context_probe", lambda _c: (True, "ok"))
        out = io.StringIO()
        run_doctor(config, fmt="json", out=out, fix=True)
        payload = json.loads(out.getvalue())
        cred = next(c for c in payload["checks"] if c["check"] == "credentials")
        assert cred["status"] == "WARN" and "0600" in cred["detail"]
        fix_row = next(c for c in payload["checks"] if c["check"] == "fix:credentials")
        assert fix_row["status"] == "OK"
        assert cred_path.stat().st_mode & 0o777 == 0o600

    def test_stored_credentials_key_counts_as_present(
        self, tmp_path, monkeypatch, request
    ) -> None:
        """A key pasted during setup satisfies the api_keys check (name only)."""
        import os

        from seahorse.cli.config import LlmConfig, write_default_config, write_llm_config
        from seahorse.cli.credentials import save_api_key

        write_default_config(tmp_path)
        write_llm_config(
            tmp_path,
            LlmConfig(
                primary="gemini/gemini-2.5-flash",
                secondary=None,
                tertiary=None,
                timeout_s=5.0,
            ),
        )
        save_api_key("GEMINI_API_KEY", "stored-key")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        # run_doctor's load_credentials_env writes the REAL environ (delenv on
        # an absent var records no undo) — pop it explicitly after the test
        request.addfinalizer(lambda: os.environ.pop("GEMINI_API_KEY", None))
        config = load_config(tmp_path)
        monkeypatch.setattr("seahorse.cli.doctor._context_probe", lambda _c: (True, "ok"))
        monkeypatch.setattr("seahorse.cli.doctor._provider_self_test", lambda _l: (True, "ok"))
        payload = _doctor(config, monkeypatch, litellm=True)
        keys = next(c for c in payload["checks"] if c["check"] == "api_keys")
        assert keys["status"] == "OK"
        assert os.environ.get("GEMINI_API_KEY") == "stored-key"
