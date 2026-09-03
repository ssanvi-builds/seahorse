"""Tests for ``seahorse.cli.provider_bootstrap`` — LLM provider bootstrap.

The hard rule under test: ``[llm]`` is only written after a self-test that
passes, a failing primary falls through to the next candidate, and big
downloads never happen without consent.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from seahorse.cli.config import (
    DEFAULT_LLM_PRIMARY,
    LlmConfig,
    load_config,
    write_default_config,
)
from seahorse.cli.provider_bootstrap import (
    _CLOUD_CANDIDATES,
    ProviderDecision,
    bootstrap_llm_provider,
    candidate_primaries,
    ollama_status,
    provider_self_test,
)


def _ollama(models: list[str]):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {"models": [{"name": m} for m in models]}
            ).encode("utf-8")

    def _open(url, timeout):
        return _Resp()

    return _open


class TestOllamaStatus:
    def test_unreachable_is_false_empty_never_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(url, timeout):
            raise OSError("refused")

        monkeypatch.setattr("seahorse.cli.provider_bootstrap.urllib.request.urlopen", boom)
        assert ollama_status() == (False, [])

    def test_reachable_lists_models(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "seahorse.cli.provider_bootstrap.urllib.request.urlopen",
            _ollama(["llama3:8b", "qwen3:0.6b"]),
        )
        assert ollama_status() == (True, ["llama3:8b", "qwen3:0.6b"])


class TestCandidateOrder:
    def test_ollama_models_qwen3_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "seahorse.cli.provider_bootstrap.urllib.request.urlopen",
            _ollama(["llama3:8b", "qwen3:1.7b", "qwen3:0.6b"]),
        )
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert candidate_primaries() == [
            "ollama/qwen3:0.6b",
            "ollama/qwen3:1.7b",
            "ollama/llama3:8b",
        ]

    def test_env_keys_after_ollama(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "seahorse.cli.provider_bootstrap.urllib.request.urlopen",
            _ollama(["qwen3:0.6b"]),
        )
        monkeypatch.setenv("GROQ_API_KEY", "k")
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        candidates = candidate_primaries()
        assert candidates[0] == "ollama/qwen3:0.6b"
        assert candidates[1] == "gemini/gemini-2.5-flash"  # catalog order wins
        assert candidates[2] == "groq/llama-3.3-70b-versatile"

    def test_no_ollama_no_keys_is_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "seahorse.cli.provider_bootstrap.urllib.request.urlopen",
            lambda url, timeout: (_ for _ in ()).throw(OSError()),
        )
        for env in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY"):
            monkeypatch.delenv(env, raising=False)
        assert candidate_primaries() == []


def _unreachable(url: str, timeout: float):
    raise OSError("refused")


def _paste_answers(monkeypatch: pytest.MonkeyPatch, provider: str, model: str, key: str):
    """Script typer.prompt for the paste flow: provider choice, model, key."""
    calls: list[tuple[str, dict]] = []

    def fake_prompt(text: str = "", **kwargs: object):
        calls.append((text, kwargs))
        lowered = text.lower()
        if "provider" in lowered:
            return provider
        if "model" in lowered:
            return model
        return key

    monkeypatch.setattr("typer.prompt", fake_prompt)
    return calls


class TestRemediationMenu:
    def _menu_env(self, tmp_path, monkeypatch, reachable, models):
        write_default_config(tmp_path)
        monkeypatch.setattr(
            "seahorse.cli.provider_bootstrap.urllib.request.urlopen",
            _ollama(models) if reachable else _unreachable,
        )
        monkeypatch.setattr("sys.stdin", type("_S", (), {"isatty": lambda self: True})())
        # setenv (not delenv): the flow may write the REAL environ in-process,
        # and setenv's undo restores pre-test absence — delenv on an absent
        # var records no undo, which would leak the key between tests
        monkeypatch.setenv("GEMINI_API_KEY", "pre-test-sentinel")
        monkeypatch.setenv("GROQ_API_KEY", "pre-test-sentinel")
        monkeypatch.setenv(
            "SEAHORSE_CREDENTIALS", str(tmp_path / "credentials.json")
        )

    def test_menu_offers_pull_paste_and_skip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from seahorse.cli import provider_bootstrap as pb

        self._menu_env(tmp_path, monkeypatch, reachable=True, models=[])
        monkeypatch.setattr("typer.prompt", lambda text="", **kw: kw.get("default", ""))
        out = io.StringIO()
        decision = pb._offer_remediation(tmp_path, out, probe=lambda p: (False, "down"))
        text = out.getvalue()
        assert "1)" in text and "qwen3:0.6b" in text
        assert "2)" in text and "API key" in text
        assert "3)" in text and "Skip" in text
        assert decision is None  # prompt answered "" → default skip

    def test_menu_default_is_skip_no_pull_no_llm(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from seahorse.cli import provider_bootstrap as pb

        self._menu_env(tmp_path, monkeypatch, reachable=True, models=[])
        answers: list[str] = []

        def fake_prompt(text: str = "", **kwargs: object):
            answers.append(text)
            return kwargs.get("default", "")

        monkeypatch.setattr("typer.prompt", fake_prompt)
        pulls: list[list[str]] = []
        monkeypatch.setattr(
            "seahorse.cli.provider_bootstrap.subprocess.run",
            lambda cmd, **kw: pulls.append(cmd),
        )
        out = io.StringIO()
        decision = pb._offer_remediation(tmp_path, out, probe=lambda p: (False, "down"))
        assert decision is None
        assert pulls == []
        assert load_config(tmp_path).llm.primary == DEFAULT_LLM_PRIMARY

    def test_menu_pull_option_writes_llm_on_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from seahorse.cli import provider_bootstrap as pb

        self._menu_env(tmp_path, monkeypatch, reachable=True, models=[])
        monkeypatch.setattr("typer.prompt", lambda text="", **kw: "1")
        monkeypatch.setattr(
            "seahorse.cli.provider_bootstrap.subprocess.run", lambda cmd, **kw: None
        )
        out = io.StringIO()
        decision = pb._offer_remediation(
            tmp_path, out, probe=lambda p: (p == "ollama/qwen3:0.6b", "ok")
        )
        assert decision is not None
        assert decision.primary == "ollama/qwen3:0.6b"
        assert load_config(tmp_path).llm.primary == "ollama/qwen3:0.6b"

    def test_remediation_used_via_bootstrap_on_tty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """bootstrap_llm_provider installs the TTY remediation automatically."""

        self._menu_env(tmp_path, monkeypatch, reachable=False, models=[])
        monkeypatch.setattr("typer.prompt", lambda text="", **kw: "3")
        out = io.StringIO()
        decision = bootstrap_llm_provider(tmp_path, out=out, self_test=lambda p: (False, "down"))
        assert decision.primary is None
        assert "API key" in out.getvalue()


class TestPasteKeyFlow:
    def _flow_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        write_default_config(tmp_path)
        creds = tmp_path / "credentials.json"
        monkeypatch.setenv("SEAHORSE_CREDENTIALS", str(creds))
        # the paste flow writes the REAL environ in-process — setenv records
        # pre-test absence so its undo removes the key again (delenv on an
        # absent var records no undo and would leak between tests)
        monkeypatch.setenv("GEMINI_API_KEY", "pre-test-sentinel")
        return creds

    def test_paste_flow_writes_credentials_then_env_then_self_test(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from seahorse.cli import provider_bootstrap as pb

        creds = self._flow_env(tmp_path, monkeypatch)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        probes: list[tuple[str, dict]] = []

        def probe(primary: str) -> tuple[bool, str]:
            import os

            probes.append((primary, {"env": os.environ.get("GEMINI_API_KEY")}))
            return True, "ok"

        _paste_answers(monkeypatch, "1", "", "sk-test-123")
        out = io.StringIO()
        decision = pb._paste_key_flow(tmp_path, out, probe=probe)
        assert decision is not None
        assert decision.primary == "gemini/gemini-2.5-flash"
        # order: credentials on disk + env set BEFORE the probe ran
        assert probes[0][0] == "gemini/gemini-2.5-flash"
        assert probes[0][1]["env"] == "sk-test-123"
        data = json.loads(creds.read_text(encoding="utf-8"))
        assert data["GEMINI_API_KEY"] == "sk-test-123"
        assert creds.stat().st_mode & 0o777 == 0o600
        assert load_config(tmp_path).llm is not None
        assert load_config(tmp_path).llm.primary == "gemini/gemini-2.5-flash"

    def test_paste_flow_default_model_from_catalog(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from seahorse.cli import provider_bootstrap as pb

        self._flow_env(tmp_path, monkeypatch)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        seen: list[str] = []

        def probe(primary: str) -> tuple[bool, str]:
            seen.append(primary)
            return True, "ok"

        _paste_answers(monkeypatch, "1", "", "k")
        pb._paste_key_flow(tmp_path, io.StringIO(), probe=probe)
        assert seen == ["gemini/gemini-2.5-flash"]

    def test_paste_flow_failing_self_test_writes_no_llm_and_masks_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from seahorse.cli import provider_bootstrap as pb

        creds = self._flow_env(tmp_path, monkeypatch)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        _paste_answers(monkeypatch, "1", "", "sk-secret-9")
        out = io.StringIO()
        decision = pb._paste_key_flow(tmp_path, out, probe=lambda p: (False, "401 at sk-secret-9"))
        assert decision is None
        text = out.getvalue()
        assert "sk-secret-9" not in text  # masked
        assert "nothing written" in text
        assert load_config(tmp_path).llm.primary == DEFAULT_LLM_PRIMARY
        # the key is KEPT in the store (a 401 is often transient)
        assert json.loads(creds.read_text(encoding="utf-8"))["GEMINI_API_KEY"] == "sk-secret-9"

    def test_paste_flow_empty_key_is_skip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from seahorse.cli import provider_bootstrap as pb

        creds = self._flow_env(tmp_path, monkeypatch)
        _paste_answers(monkeypatch, "1", "", "")
        decision = pb._paste_key_flow(tmp_path, io.StringIO(), probe=lambda p: (True, "ok"))
        assert decision is None
        assert not creds.exists()
        assert load_config(tmp_path).llm.primary == DEFAULT_LLM_PRIMARY

    def test_stored_credentials_key_becomes_candidate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
    ) -> None:
        """load_credentials_env at bootstrap entry feeds the candidate list."""
        import os

        from seahorse.cli.credentials import save_api_key

        write_default_config(tmp_path)
        monkeypatch.setenv("SEAHORSE_CREDENTIALS", str(tmp_path / "credentials.json"))
        monkeypatch.setattr(
            "seahorse.cli.provider_bootstrap.urllib.request.urlopen", _unreachable
        )
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        # bootstrap's load_credentials_env writes the REAL environ (delenv on
        # an absent var records no undo) — pop it explicitly after the test
        request.addfinalizer(lambda: os.environ.pop("GEMINI_API_KEY", None))
        save_api_key("GEMINI_API_KEY", "stored-key")
        monkeypatch.setattr("sys.stdin", type("_S", (), {"isatty": lambda self: False})())
        out = io.StringIO()
        tested: list[str] = []

        decision = bootstrap_llm_provider(
            tmp_path, out=out, self_test=lambda p: (tested.append(p), (True, "ok"))[1]
        )
        assert decision.primary == "gemini/gemini-2.5-flash"
        assert "gemini/gemini-2.5-flash" in tested


class TestCatalogSingleSource:
    def test_bootstrap_candidates_derive_from_llm_catalog(self) -> None:
        from seahorse.llm.providers import CLOUD_PROVIDER_MODELS

        derived = tuple((env, f"{name}/{model}") for name, env, model in CLOUD_PROVIDER_MODELS)
        assert derived == _CLOUD_CANDIDATES

    def test_wizard_catalog_matches_llm_catalog(self) -> None:
        from seahorse.cli.wizard import _PROVIDERS
        from seahorse.llm.providers import CLOUD_PROVIDER_MODELS

        for name, env, model in CLOUD_PROVIDER_MODELS:
            meta = _PROVIDERS[name]
            assert meta.key == env
            assert meta.model == model


class TestBootstrapLlmProvider:
    def test_writes_llm_only_after_passing_self_test(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_default_config(tmp_path)
        monkeypatch.setattr(
            "seahorse.cli.provider_bootstrap.urllib.request.urlopen",
            _ollama(["qwen3:1.7b"]),
        )
        tested: list[str] = []
        out = io.StringIO()
        decision = bootstrap_llm_provider(
            tmp_path, out=out, self_test=lambda p: (tested.append(p), (True, "ok"))[1]
        )
        assert decision.primary == "ollama/qwen3:1.7b"
        assert tested == ["ollama/qwen3:1.7b"]
        cfg = load_config(tmp_path)
        assert cfg.llm is not None and cfg.llm.primary == "ollama/qwen3:1.7b"

    def test_failing_primary_falls_to_next_candidate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_default_config(tmp_path)
        monkeypatch.setattr(
            "seahorse.cli.provider_bootstrap.urllib.request.urlopen",
            _ollama(["qwen3:1.7b"]),
        )
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.delenv("GROQ_API_KEY", raising=False)

        def probe(primary: str) -> tuple[bool, str]:
            return (primary.startswith("gemini"), "ok" if primary.startswith("gemini") else "down")

        out = io.StringIO()
        decision = bootstrap_llm_provider(tmp_path, out=out, self_test=probe)
        assert decision.primary == "gemini/gemini-2.5-flash"
        assert "candidate ollama/qwen3:1.7b failed" in out.getvalue()
        assert load_config(tmp_path).llm.primary == "gemini/gemini-2.5-flash"

    def test_all_candidates_fail_leaves_no_llm_section(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_default_config(tmp_path)
        monkeypatch.setattr(
            "seahorse.cli.provider_bootstrap.urllib.request.urlopen",
            _ollama(["qwen3:1.7b"]),
        )
        for env in ("GEMINI_API_KEY", "GROQ_API_KEY"):
            monkeypatch.delenv(env, raising=False)
        out = io.StringIO()
        decision = bootstrap_llm_provider(
            tmp_path, out=out, self_test=lambda p: (False, "down")
        )
        assert decision == ProviderDecision(
            primary=None, detail="no provider passed the self-test — extraction skip"
        )
        # The bootstrap never writes on failure — the factory default that
        # `init` laid down is left untouched (runtime degrades to skip).
        assert load_config(tmp_path).llm is not None
        assert load_config(tmp_path).llm.primary == DEFAULT_LLM_PRIMARY
        assert "extraction stays skip" in out.getvalue()

    def test_ollama_empty_non_tty_skips_without_pull(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty Ollama on non-TTY never triggers a download."""
        write_default_config(tmp_path)
        monkeypatch.setattr(
            "seahorse.cli.provider_bootstrap.urllib.request.urlopen",
            _ollama([]),
        )
        monkeypatch.setattr("sys.stdin", type("_FakeStdin", (), {"isatty": lambda self: False})())
        pulls: list[list[str]] = []
        monkeypatch.setattr(
            "seahorse.cli.provider_bootstrap.subprocess.run",
            lambda cmd, **kw: pulls.append(cmd),
        )
        out = io.StringIO()
        decision = bootstrap_llm_provider(tmp_path, out=out)
        assert decision.primary is None
        assert pulls == []  # no download without consent
        assert "ollama pull" in out.getvalue()
        assert load_config(tmp_path).llm.primary == DEFAULT_LLM_PRIMARY

    def test_self_test_reports_missing_extra_as_fail_not_crash(self) -> None:
        llm = LlmConfig(
            primary="ollama/qwen3:1.7b",
            secondary=None,
            tertiary=None,
            timeout_s=5.0,
        )
        ok, detail = provider_self_test(llm)
        # No litellm route can succeed in a unit test without a backend; the
        # contract under test is the (bool, str) shape and honest failure.
        assert isinstance(ok, bool)
        assert isinstance(detail, str) and detail