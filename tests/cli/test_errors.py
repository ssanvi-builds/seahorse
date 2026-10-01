"""``seahorse.cli.errors`` — CliError hierarchy + exit codes."""

from __future__ import annotations

from pathlib import Path

import pytest

from seahorse.cli.errors import (
    CliCloudflaredMissing,
    CliConfigInvalid,
    CliError,
    CliNotInMVP0,
    CliRemoteStartFailed,
    CliUsageError,
    CliVaultNotFound,
)
from seahorse.cli.exit_codes import (
    CLI_CLOUDFLARED_MISSING,
    CLI_CONFIG_INVALID,
    CLI_NOT_IN_MVP_0,
    CLI_REMOTE_START_FAILED,
    CLI_VAULT_NOT_FOUND,
    EXIT_USAGE,
)


def test_base_carries_exit_code_name_detail():
    err = CliError(exit_code=99, name="CLI_X", detail="boom")
    assert err.exit_code == 99
    assert err.name == "CLI_X"
    assert err.detail == "boom"
    assert str(err) == "CLI_X: boom"


def test_base_info_payload():
    err = CliError(exit_code=99, name="CLI_X", detail="boom")
    info = err.info()
    assert info == {"cli_code": "CLI_X", "detail": "boom", "component": "#14", "exit_code": 99}


def test_not_in_mvp0_carries_command():
    err = CliNotInMVP0("expire", reason="mediano")
    assert err.exit_code == CLI_NOT_IN_MVP_0
    assert err.command == "expire"
    assert err.name == "CLI_NOT_IN_MVP_0"
    assert "expire" in err.detail and "mediano" in err.detail


def test_vault_not_found_default_hint():
    err = CliVaultNotFound()
    assert err.exit_code == CLI_VAULT_NOT_FOUND
    assert "seahorse setup" in err.detail


def test_vault_not_found_custom_hint():
    err = CliVaultNotFound(hint="custom hint")
    assert "custom hint" in err.detail


def test_config_invalid_wraps_detail():
    err = CliConfigInvalid("parse error: bad")
    assert err.exit_code == CLI_CONFIG_INVALID
    assert "seahorse.toml invalid" in err.detail
    assert "parse error: bad" in err.detail


def test_usage_error_is_exit_2():
    err = CliUsageError("--body too long")
    assert err.exit_code == EXIT_USAGE
    assert err.name == "CLI_USAGE"


def test_cloudflared_missing_carries_install_line():
    err = CliCloudflaredMissing("brew install cloudflared")
    assert err.exit_code == CLI_CLOUDFLARED_MISSING
    assert err.name == "CLI_CLOUDFLARED_MISSING"
    assert "brew install cloudflared" in err.detail


def test_remote_start_failed_names_child_and_log(tmp_path):
    log = tmp_path / "server.log"
    err = CliRemoteStartFailed("server", log)
    assert err.exit_code == CLI_REMOTE_START_FAILED
    assert err.name == "CLI_REMOTE_START_FAILED"
    assert "server" in err.detail and str(log) in err.detail


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (CliNotInMVP0("x", reason="y"), CLI_NOT_IN_MVP_0),
        (CliVaultNotFound(), CLI_VAULT_NOT_FOUND),
        (CliConfigInvalid("z"), CLI_CONFIG_INVALID),
        (CliUsageError("w"), EXIT_USAGE),
        (CliCloudflaredMissing("install me"), CLI_CLOUDFLARED_MISSING),
        (CliRemoteStartFailed("tunnel", Path("/tmp/t.log")), CLI_REMOTE_START_FAILED),
    ],
)
def test_all_subclasses_are_cli_error(exc, code):
    assert isinstance(exc, CliError)
    assert exc.exit_code == code