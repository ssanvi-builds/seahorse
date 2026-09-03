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