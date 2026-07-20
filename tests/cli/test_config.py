"""``seahorse.cli.config`` — vault discovery + seahorse.toml load/write."""

from __future__ import annotations

from pathlib import Path

import pytest

from seahorse.cli.config import (
    DEFAULT_DB_FILENAME,
    DEFAULT_EXTRACTION_MODE,
    DEFAULT_TOP_K,
    SEAHORSE_DIR_NAME,
    SeahorseConfig,
    config_path_for,
    is_initialized,
    load_config,
    resolve_vault,
    write_default_config,
)
from seahorse.cli.errors import CliConfigInvalid, CliVaultNotFound

# ---------------------------------------------------------------------------
# write_default_config + is_initialized.
# ---------------------------------------------------------------------------


def test_write_default_config_creates_dir_and_file(tmp_path):
    v = tmp_path / "vault"
    assert not v.exists()
    cfg = write_default_config(v)
    assert cfg.is_file()
    assert cfg == v / SEAHORSE_DIR_NAME / "seahorse.toml"
    assert is_initialized(v)


def test_write_default_config_is_idempotent(tmp_path):
    v = tmp_path / "vault"
    write_default_config(v)
    cfg2 = write_default_config(v)  # overwrite, no error
    assert cfg2.is_file()


def test_default_config_content_is_valid_toml(tmp_path):
    """The hand-written TOML round-trips through tomllib."""
    v = tmp_path / "vault"
    write_default_config(v)
    cfg = load_config(v)
    assert cfg.default_extraction_mode == DEFAULT_EXTRACTION_MODE
    assert cfg.top_k == DEFAULT_TOP_K
    assert cfg.db_path.name == DEFAULT_DB_FILENAME


def test_config_path_for(tmp_path):
    v = tmp_path / "vault"
    assert config_path_for(v) == v / SEAHORSE_DIR_NAME / "seahorse.toml"


# ---------------------------------------------------------------------------
# SeahorseConfig — frozen + with_overrides immutability.
# ---------------------------------------------------------------------------


def test_config_is_frozen(tmp_path):
    cfg = SeahorseConfig(
        vault=tmp_path, seahorse_dir=tmp_path / ".seahorse",
        db_path=tmp_path / "seahorse.db",
    )
    with pytest.raises((AttributeError, TypeError)):  # FrozenInstanceError subclasses these
        cfg.top_k = 5  # type: ignore[misc]


def test_with_overrides_returns_new_instance(tmp_path):
    cfg = SeahorseConfig(
        vault=tmp_path, seahorse_dir=tmp_path / ".seahorse",
        db_path=tmp_path / "seahorse.db", top_k=10,
    )
    cfg2 = cfg.with_overrides(extraction_mode="llm", top_k=5)
    assert cfg2.default_extraction_mode == "llm"
    assert cfg2.top_k == 5
    assert cfg.top_k == 10  # original unchanged
    assert cfg2 is not cfg


def test_with_overrides_none_keeps_values(tmp_path):
    cfg = SeahorseConfig(
        vault=tmp_path, seahorse_dir=tmp_path / ".seahorse",
        db_path=tmp_path / "seahorse.db", top_k=7, default_extraction_mode="llm",
    )
    cfg2 = cfg.with_overrides()
    assert cfg2.top_k == 7
    assert cfg2.default_extraction_mode == "llm"


# ---------------------------------------------------------------------------
# resolve_vault — discovery order.
# ---------------------------------------------------------------------------


def test_resolve_vault_explicit_flag(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    assert resolve_vault(v) == v.resolve()


def test_resolve_vault_explicit_missing_dir_raises(tmp_path):
    with pytest.raises(CliVaultNotFound):
        resolve_vault(tmp_path / "nope")


def test_resolve_vault_env(monkeypatch, tmp_path):
    v = tmp_path / "envvault"
    v.mkdir()
    monkeypatch.setenv("SEAHORSE_VAULT", str(v))
    assert resolve_vault(None) == v.resolve()


def test_resolve_vault_env_missing_dir_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("SEAHORSE_VAULT", str(tmp_path / "nope"))
    with pytest.raises(CliVaultNotFound):
        resolve_vault(None)


def test_resolve_vault_cwd_fallback(tmp_path, monkeypatch):
    """If --vault and env are absent, cwd with .seahorse/seahorse.toml wins."""
    monkeypatch.delenv("SEAHORSE_VAULT", raising=False)
    monkeypatch.chdir(tmp_path)
    write_default_config(tmp_path)  # cwd is now an init'd vault
    assert resolve_vault(None) == tmp_path.resolve()


def test_resolve_vault_nothing_resolves_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("SEAHORSE_VAULT", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(CliVaultNotFound):
        resolve_vault(None)


# ---------------------------------------------------------------------------
# load_config — validation of the [seahorse] section.
# ---------------------------------------------------------------------------


def _write_config(vault: Path, body: str) -> Path:
    d = vault / SEAHORSE_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    p = d / "seahorse.toml"
    p.write_text(body)
    return p


def test_load_config_missing_file_raises(tmp_path):
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)


def test_load_config_bad_toml_raises(tmp_path):
    _write_config(tmp_path, "not = valid = toml =")
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)


def test_load_config_missing_section_raises(tmp_path):
    _write_config(tmp_path, "other = 1\n")
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)


def test_load_config_bad_mode_raises(tmp_path):
    _write_config(tmp_path, '[seahorse]\ndefault_extraction_mode = "weird"\n')
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)


def test_load_config_bad_top_k_raises(tmp_path):
    _write_config(tmp_path, "[seahorse]\ntop_k = 0\n")
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)


def test_load_config_top_k_must_be_int_not_bool(tmp_path):
    _write_config(tmp_path, "[seahorse]\ntop_k = true\n")
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)


def test_load_config_bad_db_path_raises(tmp_path):
    _write_config(tmp_path, '[seahorse]\ndb_path = ""\n')
    with pytest.raises(CliConfigInvalid):
        load_config(tmp_path)


def test_load_config_explicit_path(tmp_path):
    """``explicit_config`` overrides the canonical config path."""
    v = tmp_path / "vault"
    v.mkdir()
    p = write_default_config(v)
    cfg = load_config(v, explicit_config=p)
    assert cfg.vault == v.resolve()