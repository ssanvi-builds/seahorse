"""Hook-invoked command paths must resolve the vault WITHOUT the global pointer.

The observe ``event`` and ``consolidate --auto`` commands are launched by agent
hooks (Claude Code / Codex) from whatever directory the session uses — which
may be a project that is not a vault at all. The global pointer's target moves
across ``seahorse setup`` runs and machines, so the pointer fallback here is a
cross-vault leak: a session in a non-vault directory silently captures into
whatever vault was registered last (the 2026-10-07 wrong-vault incident). Hook
paths resolve explicit → env → cwd-walk only, and resolution failure is a
SILENT no-op (the hook contract: never abort the agent session).
"""

from __future__ import annotations

from pathlib import Path

from tests.cli.conftest import invoke

POINTER_MODULE = "seahorse.cli.config"


def _pointer_to(tmp_path, monkeypatch, *, initialized: bool) -> Path:
    """Point the global pointer at ``tmp_path/pointer-vault`` (isolated)."""
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setattr(
        f"{POINTER_MODULE}.global_pointer_path",
        lambda: xdg / "seahorse" / "vault",
    )
    target = tmp_path / "pointer-vault"
    if initialized:
        from seahorse.cli.config import write_default_config, write_global_pointer

        write_default_config(target)
        write_global_pointer(target)
    return target


def _non_vault_cwd(tmp_path, monkeypatch) -> Path:
    cwd = tmp_path / "project"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    return cwd


def test_observe_event_without_vault_is_silent_noop(tmp_path, monkeypatch) -> None:
    """No vault anywhere: the hook path exits 0 with no output (never exit 82)."""
    monkeypatch.delenv("SEAHORSE_VAULT", raising=False)
    _pointer_to(tmp_path, monkeypatch, initialized=False)
    _non_vault_cwd(tmp_path, monkeypatch)
    code, out, err = invoke(["observe", "event"])
    assert code == 0
    assert out == ""
    assert err == ""


def test_observe_event_never_reads_the_global_pointer(tmp_path, monkeypatch) -> None:
    """A pointer exists; the hook path must not even consult it (leak guard)."""
    monkeypatch.delenv("SEAHORSE_VAULT", raising=False)
    _pointer_to(tmp_path, monkeypatch, initialized=True)
    _non_vault_cwd(tmp_path, monkeypatch)

    def _boom() -> None:
        raise AssertionError("hook paths must not read the global pointer")

    monkeypatch.setattr(f"{POINTER_MODULE}.read_global_pointer", _boom)
    code, out, err = invoke(["observe", "event"])
    assert code == 0
    assert out == ""
    assert err == ""


def test_consolidate_auto_without_vault_is_silent_noop(tmp_path, monkeypatch) -> None:
    """``consolidate --auto`` with no vault: exit 0, an honest no-op line."""
    monkeypatch.delenv("SEAHORSE_VAULT", raising=False)
    _pointer_to(tmp_path, monkeypatch, initialized=False)
    _non_vault_cwd(tmp_path, monkeypatch)
    code, out, err = invoke(["consolidate", "--auto"])
    assert code == 0
    assert "no-op" in out


def test_consolidate_auto_never_reads_the_global_pointer(tmp_path, monkeypatch) -> None:
    """Same leak guard as the observe-event path, for the stop-hook entry."""
    monkeypatch.delenv("SEAHORSE_VAULT", raising=False)
    _pointer_to(tmp_path, monkeypatch, initialized=True)
    _non_vault_cwd(tmp_path, monkeypatch)

    def _boom() -> None:
        raise AssertionError("hook paths must not read the global pointer")

    monkeypatch.setattr(f"{POINTER_MODULE}.read_global_pointer", _boom)
    code, out, err = invoke(["consolidate", "--auto"])
    assert code == 0