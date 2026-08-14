"""Tests for the ``[observe]`` section of ``seahorse.toml``.

The observer config is additive to the existing ``[seahorse]`` / ``[llm]``
sections. A missing ``[observe]`` section → ``observe=None`` (the observer is
opt-in — the vault must run ``seahorse setup`` to enable it). A present section
is validated: ``extraction`` ∈ {skip, llm}, ``skip_tools`` / ``drop_tools`` are
string lists, ``socket_path`` is a non-empty string, ``token`` is optional.
"""

from __future__ import annotations

import pytest

from seahorse.cli.config import (
    DEFAULT_DROP_TOOLS,
    DEFAULT_SKIP_TOOLS,
    load_config,
    write_default_config,
)
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


def test_missing_observe_section_is_none(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml())
    cfg = load_config(tmp_path)
    assert cfg.observe is None


def test_observe_section_defaults(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml() + "[observe]\n")
    cfg = load_config(tmp_path)
    assert cfg.observe is not None
    assert cfg.observe.enabled is True
    assert cfg.observe.extraction == "skip"
    assert cfg.observe.skip_tools == DEFAULT_SKIP_TOOLS
    assert cfg.observe.drop_tools == DEFAULT_DROP_TOOLS
    assert cfg.observe.token is None


def test_observe_section_full(tmp_path) -> None:
    _write_toml(
        tmp_path,
        _base_toml()
        + "[observe]\n"
        + 'extraction = "llm"\n'
        + 'skip_tools = ["WebSearch"]\n'
        + 'drop_tools = ["Read"]\n'
        + 'socket_path = "observer/custom.sock"\n'
        + 'token = "abc123"\n',
    )
    cfg = load_config(tmp_path)
    assert cfg.observe is not None
    assert cfg.observe.extraction == "llm"
    assert cfg.observe.skip_tools == ("WebSearch",)
    assert cfg.observe.drop_tools == ("Read",)
    assert cfg.observe.socket_path == "observer/custom.sock"
    assert cfg.observe.token == "abc123"


def test_observe_invalid_extraction_rejected(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml() + '[observe]\nextraction = "bogus"\n')
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)


def test_observe_invalid_skip_tools_rejected(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml() + '[observe]\nskip_tools = "WebSearch"\n')
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)


def test_observe_socket_path_resolves_under_seahorse_dir(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml() + "[observe]\n")
    cfg = load_config(tmp_path)
    assert cfg.observe is not None
    # The socket path is relative to .seahorse/ and resolves inside it.
    assert cfg.observe.socket_path.startswith("observer/")
    assert cfg.observe.socket_path.endswith(".sock")


def test_write_default_config_has_no_observe_section(tmp_path) -> None:
    write_default_config(tmp_path)
    cfg = load_config(tmp_path)
    assert cfg.observe is None  # opt-in: setup adds it
