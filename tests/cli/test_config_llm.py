"""Tests for the ``[llm]`` config section — round-trip + tolerance.

The factory default is local-first (Ollama qwen3:1.7b, 2026-08-04 decision);
a vault without ``[llm]`` still loads (``llm=None`` → the honest llm→skip
degrade); a malformed section is a loud ``CliConfigInvalid`` (exit 83).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from seahorse.cli.config import (
    DEFAULT_LLM_PRIMARY,
    LlmConfig,
    load_config,
    write_default_config,
    write_llm_config,
)
from seahorse.cli.errors import CliConfigInvalid


def _write(vault: Path, body: str) -> Path:
    cfg = vault / ".seahorse" / "seahorse.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(body, encoding="utf-8")
    return cfg


class TestWriteDefaultConfig:
    def test_default_llm_is_local_first(self, tmp_path) -> None:
        write_default_config(tmp_path)
        cfg = load_config(tmp_path)
        assert cfg.llm is not None
        assert cfg.llm.primary == DEFAULT_LLM_PRIMARY  # ollama/qwen3:1.7b
        assert cfg.llm.secondary is None
        assert cfg.llm.timeout_s == 20.0

    def test_default_round_trips_via_tomllib(self, tmp_path) -> None:
        p = write_default_config(tmp_path)
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        assert data["llm"]["primary"] == DEFAULT_LLM_PRIMARY


class TestLoadConfigLlm:
    def test_missing_llm_section_tolerated(self, tmp_path) -> None:
        _write(tmp_path, '[seahorse]\ndb_path = "x.db"\n')
        cfg = load_config(tmp_path)
        assert cfg.llm is None

    def test_full_route_parsed(self, tmp_path) -> None:
        _write(
            tmp_path,
            "[seahorse]\n"
            '[llm]\nprimary = "gemini/gemini-2.5-flash"\n'
            'secondary = "groq/llama-3.3-70b-versatile"\n'
            'tertiary = "ollama/qwen3:1.7b"\n'
            "timeout_s = 15.0\n",
        )
        cfg = load_config(tmp_path)
        assert cfg.llm == LlmConfig(
            primary="gemini/gemini-2.5-flash",
            secondary="groq/llama-3.3-70b-versatile",
            tertiary="ollama/qwen3:1.7b",
            timeout_s=15.0,
        )

    def test_empty_primary_rejected(self, tmp_path) -> None:
        _write(tmp_path, '[seahorse]\n[llm]\nprimary = ""\n')
        with pytest.raises(CliConfigInvalid, match="llm.primary"):
            load_config(tmp_path)

    def test_negative_timeout_rejected(self, tmp_path) -> None:
        _write(tmp_path, '[seahorse]\n[llm]\nprimary = "ollama/x"\ntimeout_s = -1\n')
        with pytest.raises(CliConfigInvalid, match="timeout_s"):
            load_config(tmp_path)

    def test_llm_not_a_table_rejected(self, tmp_path) -> None:
        _write(tmp_path, 'llm = 5\n[seahorse]\ndb_path = "x.db"\n')
        with pytest.raises(CliConfigInvalid, match=r"\[llm\]"):
            load_config(tmp_path)


class TestWriteLlmConfig:
    def test_preserves_seahorse_section(self, tmp_path) -> None:
        write_default_config(tmp_path)
        write_llm_config(
            tmp_path,
            LlmConfig(primary="gemini/gemini-2.5-flash", secondary="ollama/qwen3:1.7b"),
        )
        cfg = load_config(tmp_path)
        assert cfg.default_extraction_mode == "skip"  # preserved
        assert cfg.top_k == 10  # preserved
        assert cfg.llm.primary == "gemini/gemini-2.5-flash"
        assert cfg.llm.secondary == "ollama/qwen3:1.7b"
        assert cfg.llm.tertiary is None

    def test_overwrites_previous_llm(self, tmp_path) -> None:
        write_default_config(tmp_path)
        write_llm_config(tmp_path, LlmConfig(primary="ollama/qwen3:0.6b"))
        write_llm_config(tmp_path, LlmConfig(primary="ollama/qwen3:1.7b"))
        cfg = load_config(tmp_path)
        assert cfg.llm.primary == "ollama/qwen3:1.7b"
