"""End-to-end ``seahorse migrate`` via the invoke harness (commit 5).

``migrate`` is the SCHEMA migrations runner (DDL 001-009 on the sidecar SQLite
DB), NOT the frontmatter vault migrator. It reuses the ``apply_migrations(up_to=)``
seam added in commit 4. Exit codes: success 0; ``--up-to -1`` → Cat C usage 2;
uninitialized vault → Cat C 82/83.
"""

from __future__ import annotations

import json

from seahorse.cli.exit_codes import CLI_CONFIG_INVALID, CLI_VAULT_NOT_FOUND, EXIT_USAGE
from tests.cli.conftest import invoke


def test_migrate_default_applies_all(tmp_path, vault):
    code, out, err = invoke(["--vault", str(vault), "migrate"])
    assert code == 0, err
    assert "applied" in out


def test_migrate_json_payload(tmp_path, vault):
    code, out, err = invoke(["--vault", str(vault), "--json", "migrate"])
    assert code == 0, err
    obj = json.loads(out)
    assert obj["command"] == "migrate"
    assert obj["applied"] == 9
    assert obj["schema_version"] == 9
    assert obj["latest_available"] == 9


def test_migrate_up_to_flag(tmp_path, vault):
    code, out, err = invoke(["--vault", str(vault), "--json", "migrate", "--up-to", "5"])
    assert code == 0, err
    obj = json.loads(out)
    assert obj["applied"] == 5
    assert obj["schema_version"] == 5
    assert obj["up_to"] == 5


def test_migrate_idempotent_second_run(tmp_path, vault):
    invoke(["--vault", str(vault), "migrate"])
    code, out, err = invoke(["--vault", str(vault), "--json", "migrate"])
    assert code == 0, err
    assert json.loads(out)["applied"] == 0


def test_migrate_negative_up_to_is_usage_error(tmp_path, vault):
    code, out, err = invoke(["--vault", str(vault), "migrate", "--up-to", "-1"])
    assert code == EXIT_USAGE, err
    assert "CLI_USAGE" in err


def test_migrate_nonexistent_vault_is_cat_c_82(tmp_path):
    code, out, err = invoke(["--vault", str(tmp_path / "nope"), "migrate"])
    assert code == CLI_VAULT_NOT_FOUND, err
    assert "CLI_VAULT_NOT_FOUND" in err


def test_migrate_uninitialized_vault_is_cat_c_83(tmp_path):
    # directory exists but no .seahorse/seahorse.toml -> CliConfigInvalid (83).
    bare = tmp_path / "bare"
    bare.mkdir()
    code, out, err = invoke(["--vault", str(bare), "migrate"])
    assert code == CLI_CONFIG_INVALID, err
    assert "CLI_CONFIG_INVALID" in err


def test_migrate_then_inspect_sees_schema_version(tmp_path, vault):
    invoke(["--vault", str(vault), "migrate"])
    code, out, err = invoke(["--vault", str(vault), "--json", "inspect"])
    assert code == 0, err
    assert json.loads(out)["schema_version"] == 9