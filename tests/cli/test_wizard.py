"""Tests for the interactive LLM provider wizard.

The wizard is a no-TUI flow (typer.prompt/confirm). The tests drive it with a
fake prompt/confirm (mimicking typer's ``type=int`` coercion) and assert the
``[llm]`` section that gets written. A user with NOTHING lands on local Ollama
qwen3:1.7b; a free-tier key preselects the cloud provider.
"""

from __future__ import annotations

from seahorse.cli.config import load_config, write_default_config
from seahorse.cli.wizard import run_llm_wizard


def _run(tmp_path, monkeypatch, prompts, confirms, env=None, ollama=False) -> None:
    """Drive the wizard with canned answers and return nothing (asserts via load)."""
    write_default_config(tmp_path)
    if env:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    answers = iter(prompts)
    monkeypatch.setattr("seahorse.cli.wizard._ollama_running", lambda: ollama)

    def fake_prompt(message, **kw):
        value = next(answers)
        return int(value) if kw.get("type") is int else value

    monkeypatch.setattr("seahorse.cli.wizard.typer.prompt", fake_prompt)
    conf = iter(confirms)
    monkeypatch.setattr("seahorse.cli.wizard.typer.confirm", lambda msg, **kw: next(conf))
    monkeypatch.setattr("seahorse.cli.wizard.typer.echo", lambda *a, **k: None)
    run_llm_wizard(tmp_path)


class TestWizard:
    def test_user_with_nothing_picks_local_ollama(self, tmp_path, monkeypatch) -> None:
        _run(tmp_path, monkeypatch, prompts=["1", "1.7b", ""], confirms=[False])
        cfg = load_config(tmp_path)
        assert cfg.llm.primary == "ollama/qwen3:1.7b"
        assert cfg.llm.secondary is None

    def test_low_end_hardware_0_6b(self, tmp_path, monkeypatch) -> None:
        _run(tmp_path, monkeypatch, prompts=["1", "0.6b", ""], confirms=[False])
        assert load_config(tmp_path).llm.primary == "ollama/qwen3:0.6b"

    def test_gemini_key_picks_cloud_primary(self, tmp_path, monkeypatch) -> None:
        _run(
            tmp_path,
            monkeypatch,
            prompts=["2", "gemini-2.5-flash", ""],
            confirms=[False],
            env={"GEMINI_API_KEY": "test-key"},
        )
        cfg = load_config(tmp_path)
        assert cfg.llm.primary == "gemini/gemini-2.5-flash"

    def test_fallback_model_written(self, tmp_path, monkeypatch) -> None:
        _run(
            tmp_path,
            monkeypatch,
            prompts=["1", "1.7b", "ollama/qwen3:0.6b"],
            confirms=[False],
        )
        cfg = load_config(tmp_path)
        assert cfg.llm.primary == "ollama/qwen3:1.7b"
        assert cfg.llm.secondary == "ollama/qwen3:0.6b"

    def test_running_ollama_counts_as_available(self, tmp_path, monkeypatch) -> None:
        # With Ollama running and no keys, the default is still Ollama (1).
        _run(tmp_path, monkeypatch, prompts=["1", "1.7b", ""], confirms=[False], ollama=True)
        assert load_config(tmp_path).llm.primary == "ollama/qwen3:1.7b"
