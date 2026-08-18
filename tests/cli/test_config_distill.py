"""Tests for the ``[distill]`` section of ``seahorse.toml``.

The distill config is additive to the existing ``[seahorse]`` / ``[llm]`` /
``[observe]`` sections. A missing ``[distill]`` section → ``distill=None`` (the
LLM synthesis is opt-in — the deterministic distillation is the default). A
present section is validated: ``synthesis`` ∈ {skip, llm}.
"""

from __future__ import annotations

import pytest

from seahorse.cli.config import load_config, write_default_config
from seahorse.cli.errors import CliConfigInvalid


def _write_toml(vault, content: str) -> None:
    (vault / ".seahorse").mkdir(parents=True, exist_ok=True)
    (vault / ".seahorse" / "seahorse.toml").write_text(content, encoding="utf-8")


def _base_toml() -> str:
    return (
        "[seahorse]\n"
        'db_path = "seahorse.db"\n'
        'default_extraction_mode = "skip"\n'
        "top_k = 10\n"
    )


def test_missing_distill_section_is_none(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml())
    cfg = load_config(tmp_path)
    assert cfg.distill is None


def test_distill_section_defaults(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml() + "[distill]\n")
    cfg = load_config(tmp_path)
    assert cfg.distill is not None
    assert cfg.distill.synthesis == "skip"
    assert cfg.distill.supersede is False  # opt-in (ADR-10 honesty)


def test_distill_supersede_true(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml() + "[distill]\nsupersede = true\n")
    cfg = load_config(tmp_path)
    assert cfg.distill is not None
    assert cfg.distill.supersede is True


def test_distill_invalid_supersede_rejected(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml() + '[distill]\nsupersede = "yes"\n')
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)


def test_distill_section_full(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml() + '[distill]\nsynthesis = "llm"\n')
    cfg = load_config(tmp_path)
    assert cfg.distill is not None
    assert cfg.distill.synthesis == "llm"


def test_distill_invalid_synthesis_rejected(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml() + '[distill]\nsynthesis = "bogus"\n')
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)


def test_distill_not_a_table_rejected(tmp_path) -> None:
    # A top-level scalar (before the [seahorse] table) is not a [distill] table.
    _write_toml(tmp_path, 'distill = "bogus"\n' + _base_toml())
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)


def test_write_default_config_has_no_distill_section(tmp_path) -> None:
    write_default_config(tmp_path)
    cfg = load_config(tmp_path)
    assert cfg.distill is None  # opt-in: the user enables synthesis explicitly
