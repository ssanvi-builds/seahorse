"""Tests for the ``[http]`` section of ``seahorse.toml``.

Unlike ``[observe]`` (opt-in, absent → ``None``), the ``[http]`` section is
always parsed: a missing section yields the defaults because the opt-in
happens at the transport level (``--transport http``), not in the config.
The server config is additive to the existing ``[seahorse]`` / ``[llm]``
sections: ``host`` is a non-empty string, ``port`` an int in 1..65535,
``token`` optional (non-empty when present).
"""

from __future__ import annotations

import pytest

from seahorse.cli.config import HttpConfig, load_config, write_default_config
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


def test_missing_http_section_yields_defaults(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml())
    cfg = load_config(tmp_path)
    assert cfg.http == HttpConfig()  # host, port, token all defaulted


def test_http_section_defaults(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml() + "[http]\n")
    cfg = load_config(tmp_path)
    assert cfg.http.host == "127.0.0.1"
    assert cfg.http.port == 8767
    assert cfg.http.token is None


def test_http_section_full(tmp_path) -> None:
    _write_toml(
        tmp_path,
        _base_toml() + "[http]\n"
        + 'host = "0.0.0.0"\n'
        + "port = 9000\n"
        + 'token = "abc123"\n',
    )
    cfg = load_config(tmp_path)
    assert cfg.http.host == "0.0.0.0"
    assert cfg.http.port == 9000
    assert cfg.http.token == "abc123"


def test_http_non_int_port_rejected(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml() + '[http]\nport = "8767"\n')
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)


def test_http_bool_port_rejected(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml() + "[http]\nport = true\n")
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)


def test_http_port_out_of_range_rejected(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml() + "[http]\nport = 65536\n")
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)

    _write_toml(tmp_path, _base_toml() + "[http]\nport = 0\n")
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)


def test_http_empty_token_rejected(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml() + '[http]\ntoken = ""\n')
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)


def test_http_empty_host_rejected(tmp_path) -> None:
    _write_toml(tmp_path, _base_toml() + '[http]\nhost = ""\n')
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)


def test_write_default_config_has_no_http_section(tmp_path) -> None:
    write_default_config(tmp_path)
    cfg = load_config(tmp_path)
    assert cfg.http == HttpConfig()  # defaults, token absent: setup adds it