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
