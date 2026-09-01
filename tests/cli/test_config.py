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
    global_pointer_path,
    is_initialized,
    load_config,
    read_global_pointer,
    resolve_vault,
    write_default_config,
    write_global_pointer,
)
from seahorse.cli.errors import CliConfigInvalid, CliVaultNotFound


def _isolate_pointer(monkeypatch, tmp_path) -> Path:
    """Redirect the global pointer to a tmp dir (tests never touch the host's)."""
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return xdg

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
    _isolate_pointer(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    write_default_config(tmp_path)  # cwd is now an init'd vault
    assert resolve_vault(None) == tmp_path.resolve()


def test_resolve_vault_nothing_resolves_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("SEAHORSE_VAULT", raising=False)
    _isolate_pointer(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(CliVaultNotFound):
        resolve_vault(None)


# ---------------------------------------------------------------------------
# global vault pointer (~/.config/seahorse/vault) + parent-directory walk.
# ---------------------------------------------------------------------------


def test_global_pointer_roundtrip(monkeypatch, tmp_path):
    _isolate_pointer(monkeypatch, tmp_path)
    v = tmp_path / "vault"
    v.mkdir()
    write_default_config(v)
    path = write_global_pointer(v)
    assert path == global_pointer_path()
    assert read_global_pointer() == v.resolve()


def test_global_pointer_missing_is_none(monkeypatch, tmp_path):
    _isolate_pointer(monkeypatch, tmp_path)
    assert read_global_pointer() is None


def test_global_pointer_stale_vault_is_none(monkeypatch, tmp_path):
    """A pointer to a deleted directory is treated as absent, not an error."""
    _isolate_pointer(monkeypatch, tmp_path)
    write_global_pointer(tmp_path / "gone")
    assert read_global_pointer() is None


def test_global_pointer_uninitialized_vault_is_none(monkeypatch, tmp_path):
    """A pointer to an existing dir without .seahorse/ does not resolve."""
    _isolate_pointer(monkeypatch, tmp_path)
    v = tmp_path / "plain"
    v.mkdir()
    write_global_pointer(v)
    assert read_global_pointer() is None


def test_resolve_vault_parent_walk(tmp_path, monkeypatch):
    """A session in a subdirectory of an init'd vault resolves the vault root."""
    monkeypatch.delenv("SEAHORSE_VAULT", raising=False)
    _isolate_pointer(monkeypatch, tmp_path)
    v = tmp_path / "vault"
    write_default_config(v)
    subdir = v / "notes" / "deep"
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)
    assert resolve_vault(None) == v.resolve()


def test_resolve_vault_pointer_fallback(tmp_path, monkeypatch):
    """No cwd vault: the global pointer resolves the registered vault."""
    monkeypatch.delenv("SEAHORSE_VAULT", raising=False)
    _isolate_pointer(monkeypatch, tmp_path)
    v = tmp_path / "vault"
    write_default_config(v)
    write_global_pointer(v)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert resolve_vault(None) == v.resolve()


def test_resolve_vault_cwd_beats_pointer(tmp_path, monkeypatch):
    _isolate_pointer(monkeypatch, tmp_path)
    pointer_vault = tmp_path / "pointer-vault"
    write_default_config(pointer_vault)
    write_global_pointer(pointer_vault)
    monkeypatch.chdir(tmp_path)
    write_default_config(tmp_path)  # cwd vault
    assert resolve_vault(None) == tmp_path.resolve()


def test_resolve_vault_env_beats_pointer(tmp_path, monkeypatch):
    _isolate_pointer(monkeypatch, tmp_path)
    pointer_vault = tmp_path / "pointer-vault"
    write_default_config(pointer_vault)
    write_global_pointer(pointer_vault)
    env_vault = tmp_path / "env-vault"
    env_vault.mkdir()
    monkeypatch.setenv("SEAHORSE_VAULT", str(env_vault))
    monkeypatch.chdir(tmp_path)
    assert resolve_vault(None) == env_vault.resolve()


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


# ---------------------------------------------------------------------------
# [materialize] section.
# ---------------------------------------------------------------------------


def test_materialize_section_absent_is_none(tmp_path):
    """A vault without ``[materialize]`` has ``materialize=None`` (opt-in)."""
    v = tmp_path / "vault"
    v.mkdir()
    write_default_config(v)
    cfg = load_config(v)
    assert cfg.materialize is None


def test_materialize_section_defaults(tmp_path):
    """An empty ``[materialize]`` table resolves to the module defaults."""
    v = tmp_path / "vault"
    v.mkdir()
    write_default_config(v)
    cfg_path = config_path_for(v)
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8") + "\n[materialize]\n",
        encoding="utf-8",
    )
    cfg = load_config(v)
    assert cfg.materialize is not None
    assert cfg.materialize.mode == "consolidated"
    assert cfg.materialize.dir == "Memory"


def test_materialize_section_explicit(tmp_path):
    """``mode`` / ``dir`` are parsed from the section."""
    v = tmp_path / "vault"
    v.mkdir()
    write_default_config(v)
    cfg_path = config_path_for(v)
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8")
        + '\n[materialize]\nmode = "all"\ndir = "Notes"\n',
        encoding="utf-8",
    )
    cfg = load_config(v)
    assert cfg.materialize is not None
    assert cfg.materialize.mode == "all"
    assert cfg.materialize.dir == "Notes"


def test_materialize_section_bad_mode_raises(tmp_path):
    """A structurally wrong ``mode`` is a ``CliConfigInvalid`` (exit 83)."""
    v = tmp_path / "vault"
    v.mkdir()
    write_default_config(v)
    cfg_path = config_path_for(v)
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8")
        + '\n[materialize]\nmode = "bogus"\n',
        encoding="utf-8",
    )
    with pytest.raises(CliConfigInvalid, match="materialize.mode"):
        load_config(v)


def test_materialize_section_bad_dir_raises(tmp_path):
    """A structurally wrong ``dir`` is a ``CliConfigInvalid`` (exit 83)."""
    v = tmp_path / "vault"
    v.mkdir()
    write_default_config(v)
    cfg_path = config_path_for(v)
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8") + '\n[materialize]\ndir = ""\n',
        encoding="utf-8",
    )
    with pytest.raises(CliConfigInvalid, match="materialize.dir"):
        load_config(v)


def test_materialize_section_not_a_table_raises(tmp_path):
    """A non-table ``[materialize]`` value is a ``CliConfigInvalid``."""
    v = tmp_path / "vault"
    v.mkdir()
    # ``materialize = 42`` must sit at the ROOT (a bare key after a table header
    # would belong to that table in TOML).
    cfg_path = config_path_for(v)
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(
        'materialize = 42\n\n[seahorse]\n'
        'db_path = "seahorse.db"\n',
        encoding="utf-8",
    )
    with pytest.raises(CliConfigInvalid, match="materialize must be a"):
        load_config(v)


def test_write_materialize_config_appends_and_preserves(tmp_path):
    """``write_materialize_config`` appends idempotently, preserving other sections."""
    v = tmp_path / "vault"
    v.mkdir()
    write_default_config(v)
    from seahorse.cli.config import MaterializeConfig, write_materialize_config

    write_materialize_config(v, MaterializeConfig(mode="all", dir="Notes"))
    cfg = load_config(v)
    assert cfg.materialize is not None
    assert cfg.materialize.mode == "all"
    assert cfg.materialize.dir == "Notes"
    # The [llm] section from init survives the append.
    assert cfg.llm is not None

    # Idempotent: a second write preserves the user's config.
    write_materialize_config(v, MaterializeConfig(mode="off", dir="X"))
    cfg2 = load_config(v)
    assert cfg2.materialize.mode == "all"
    assert cfg2.materialize.dir == "Notes"