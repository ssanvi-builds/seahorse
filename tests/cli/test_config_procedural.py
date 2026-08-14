"""Tests for the ``[procedural]`` section of ``seahorse.toml``.

The procedural config is additive to the existing ``[seahorse]`` / ``[llm]`` /
``[observe]`` sections. A missing ``[procedural]`` section → ``procedural=None``
(the CLI uses the module defaults: min_trust=medium, empty loadout). A present
section is validated: ``min_trust`` ∈ {low, medium, high}, ``loadout`` is a
string list.
"""

from __future__ import annotations

import pytest

from seahorse.cli.config import DEFAULT_MIN_TRUST, load_config
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


def test_missing_procedural_section_is_none(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml())
    cfg = load_config(tmp_path)
    assert cfg.procedural is None


def test_procedural_section_defaults(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml() + "[procedural]\n")
    cfg = load_config(tmp_path)
    assert cfg.procedural is not None
    assert cfg.procedural.min_trust == DEFAULT_MIN_TRUST
    assert cfg.procedural.loadout == ()


def test_procedural_section_full(tmp_path) -> None:
    _write_toml(
        tmp_path,
        _base_toml()
        + "[procedural]\n"
        + 'min_trust = "high"\n'
        + 'loadout = ["skill-a", "skill-b"]\n',
    )
    cfg = load_config(tmp_path)
    assert cfg.procedural is not None
    assert cfg.procedural.min_trust == "high"
    assert cfg.procedural.loadout == ("skill-a", "skill-b")


def test_procedural_invalid_min_trust_rejected(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml() + '[procedural]\nmin_trust = "bogus"\n')
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)


def test_procedural_invalid_loadout_rejected(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml() + '[procedural]\nloadout = "not-a-list"\n')
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)
