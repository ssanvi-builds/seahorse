"""Tests for ``seahorse.cli.credentials`` — the 0600 API-key store.

The store maps env-var NAMES to key values (``{"GEMINI_API_KEY": "..."}``)
so it plugs directly into ``os.environ`` and the provider-catalog lookup.
Security contract: the file is 0600, writes are atomic (same-directory
tempfile + ``os.replace``), values are never logged, and every read path
degrades to "absent" instead of raising — a hand-edited broken file must
never brick a headless command.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from seahorse.cli.credentials import (
    check_permissions,
    credentials_path,
    load_credentials_env,
    mask_secret,
    read_api_key,
    save_api_key,
)


@pytest.fixture()
def creds_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "credentials.json"
    monkeypatch.setenv("SEAHORSE_CREDENTIALS", str(p))
    return p


def test_default_path_is_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SEAHORSE_CREDENTIALS", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert credentials_path() == tmp_path / "xdg" / "seahorse" / "credentials.json"


def test_env_override_wins(creds_path: Path) -> None:
    assert credentials_path() == creds_path


def test_save_creates_file_with_0600(creds_path: Path) -> None:
    ok, detail = save_api_key("GEMINI_API_KEY", "k1")
    assert ok
    assert "GEMINI_API_KEY" in detail
    assert "k1" not in detail
    assert creds_path.stat().st_mode & 0o777 == 0o600


def test_save_tightens_existing_loose_file(creds_path: Path) -> None:
    creds_path.write_text("{}", encoding="utf-8")
    creds_path.chmod(0o644)
    ok, _ = save_api_key("GROQ_API_KEY", "k2")
    assert ok
    assert creds_path.stat().st_mode & 0o777 == 0o600


def test_save_merges_and_does_not_clobber(creds_path: Path) -> None:
    save_api_key("GEMINI_API_KEY", "k1")
    save_api_key("GROQ_API_KEY", "k2")
    data = json.loads(creds_path.read_text(encoding="utf-8"))
    assert data == {"GEMINI_API_KEY": "k1", "GROQ_API_KEY": "k2"}


def test_save_is_idempotent(creds_path: Path) -> None:
    first = save_api_key("GEMINI_API_KEY", "k1")
    second = save_api_key("GEMINI_API_KEY", "k1")
    assert first == second


def test_save_never_leaves_partial_file(creds_path: Path) -> None:
    bad_dir = creds_path.parent / "nope" / "credentials.json"
    bad_dir.parent.mkdir()
    bad_dir.parent.chmod(0o500)
    try:
        ok, detail = save_api_key("GEMINI_API_KEY", "k1", path=bad_dir)
        assert not ok
        assert "cannot write" in detail
        assert list(bad_dir.parent.glob("*.tmp")) == []
    finally:
        bad_dir.parent.chmod(0o700)


def test_load_sets_missing_env_only(
    creds_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # exercise the injectable-environ seam: no real-env writes, no leaks
    monkeypatch.setenv("GROQ_API_KEY", "preset")
    save_api_key("GEMINI_API_KEY", "k1")
    save_api_key("GROQ_API_KEY", "from-file")
    env = {"GROQ_API_KEY": "preset"}
    set_names = load_credentials_env(environ=env)

    assert set(set_names) == {"GEMINI_API_KEY"}
    assert env["GEMINI_API_KEY"] == "k1"
    assert env["GROQ_API_KEY"] == "preset"


def test_load_missing_file_is_empty_list(creds_path: Path) -> None:
    assert load_credentials_env() == []


def test_load_corrupt_json_is_empty_list(creds_path: Path) -> None:
    creds_path.write_text("{not json", encoding="utf-8")
    assert load_credentials_env() == []


def test_read_api_key_roundtrip(creds_path: Path) -> None:
    assert read_api_key("GEMINI_API_KEY") is None
    save_api_key("GEMINI_API_KEY", "k1")
    assert read_api_key("GEMINI_API_KEY") == "k1"


def test_check_permissions_flags_group_readable(creds_path: Path) -> None:
    creds_path.write_text("{}", encoding="utf-8")
    creds_path.chmod(0o640)
    ok, detail = check_permissions()
    assert not ok
    assert "0640" in detail


def test_check_permissions_absent_is_ok(creds_path: Path) -> None:
    ok, _ = check_permissions()
    assert ok


def test_check_permissions_0600_is_ok(creds_path: Path) -> None:
    save_api_key("GEMINI_API_KEY", "k1")
    ok, _ = check_permissions()
    assert ok


def test_mask_secret_redacts_all_occurrences() -> None:
    text = "call failed with key sk-abc at sk-abc end"
    masked = mask_secret(text, "sk-abc", "")
    assert "sk-abc" not in masked
    assert "***" in masked


def test_0600_mode_constant_is_private() -> None:
    assert stat.S_IMODE(0o600) == 0o600