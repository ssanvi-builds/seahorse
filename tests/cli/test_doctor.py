"""Tests for `seahorse doctor` (M4-C.3, onboarding backlog).

Reports the extraction regime, the installed ``llm`` extra, missing API key
NAMES, a live provider probe (only when LiteLLM is installed), and the
extraction mode. A vault on pure ``skip`` (no ``[llm]``) is valid → WARN, not
FAIL; health is all-OK.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from seahorse.cli.config import load_config, write_default_config
from seahorse.cli.doctor import run_doctor


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
