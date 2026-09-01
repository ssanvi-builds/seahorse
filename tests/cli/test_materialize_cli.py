"""Tests for ``seahorse materialize`` — the .md backfill command.

The command reads the currently-valid episodes (the ``get_vigente`` predicate —
PENDING episodes are not materialized until vigente) and materializes each as
an F3.1 note in the configured ``[materialize] dir``. Idempotent: an
already-materialized note is skipped (frontmatter-id guard, C3). Best-effort
per note (M9): a failed write is reported, never fatal.
"""

from __future__ import annotations

import io

import pytest

from seahorse.cli.config import (
    MaterializeConfig,
    load_config,
    write_default_config,
    write_materialize_config,
)
from seahorse.cli.errors import CliMaterializeNotConfigured
from seahorse.cli.primitives import run_materialize
from seahorse.facade.factory import build_facade
from seahorse.facade.types import RememberPayload


def _out() -> io.StringIO:
    return io.StringIO()


def _config(vault, *, mode: str = "consolidated", dir: str = "Memory"):
    """An init'd vault with a ``[materialize]`` section."""
    write_default_config(vault)
    write_materialize_config(vault, MaterializeConfig(mode=mode, dir=dir))
    return load_config(vault)


def _seed_episodes(vault, *, project: bool = True, episodic: bool = True) -> None:
    """Write episodes to the DB WITHOUT materializing (pre-materialization state).

    The facade is built without ``vault_root``/``materialize`` so the write-path
    hooks are inert — the backfill is what materializes them.
    """
    facade, storage = build_facade(vault / ".seahorse" / "seahorse.db")
    try:
        if project:
            facade.remember(
                RememberPayload(
                    body="# Project Status\n\nWhere we are on seahorse",
                    by={"source_type": "agent", "agent_id": "a1", "session_id": "s1"},
                    cognitive_type="project_doc",
                )
            )
        if episodic:
            facade.remember(
                RememberPayload(
                    body="# Session Noise\n\nA passing thought",
                    by={"source_type": "agent", "agent_id": "a1", "session_id": "s1"},
                    cognitive_type="episodic",
                )
            )
    finally:
        storage.close()


# ---------------------------------------------------------------------------
# Not configured.
# ---------------------------------------------------------------------------


def test_materialize_not_configured_raises(tmp_path) -> None:
    """A vault without ``[materialize]`` fails loud (exit 98, Cat C)."""
    v = tmp_path / "vault"
    write_default_config(v)
    cfg = load_config(v)
    with pytest.raises(CliMaterializeNotConfigured):
        run_materialize(cfg, fmt="human", out=_out())


# ---------------------------------------------------------------------------
# Backfill behavior.
# ---------------------------------------------------------------------------


def test_materialize_writes_project_doc_notes(tmp_path) -> None:
    """consolidated (default) materializes project_doc notes."""
    v = tmp_path / "vault"
    cfg = _config(v)
    _seed_episodes(v, episodic=False)
    out = _out()
    run_materialize(cfg, fmt="human", out=out)
    assert "materialized:" in out.getvalue()
    assert (v / "Memory" / "project-status.md").exists()


def test_materialize_consolidated_filters_episodic(tmp_path) -> None:
    """consolidated mode does NOT materialize session-noise episodes."""
    v = tmp_path / "vault"
    cfg = _config(v)
    _seed_episodes(v)
    out = _out()
    run_materialize(cfg, fmt="human", out=out)
    assert (v / "Memory" / "project-status.md").exists()
    assert not (v / "Memory" / "session-noise.md").exists()


def test_materialize_mode_all_materializes_everything(tmp_path) -> None:
    """``--mode all`` materializes every currently-valid episode."""
    v = tmp_path / "vault"
    cfg = _config(v)
    _seed_episodes(v)
    out = _out()
    run_materialize(cfg, fmt="human", out=out, mode="all")
    assert (v / "Memory" / "project-status.md").exists()
    assert (v / "Memory" / "session-noise.md").exists()


def test_materialize_cognitive_type_filter(tmp_path) -> None:
    """``--cognitive-type`` filters the backfill set."""
    v = tmp_path / "vault"
    cfg = _config(v)
    _seed_episodes(v)
    out = _out()
    run_materialize(cfg, fmt="human", out=out, cognitive_type="project_doc")
    assert (v / "Memory" / "project-status.md").exists()
    assert not (v / "Memory" / "session-noise.md").exists()


def test_materialize_is_idempotent(tmp_path) -> None:
    """A second run skips already-materialized notes (frontmatter-id guard)."""
    v = tmp_path / "vault"
    cfg = _config(v)
    _seed_episodes(v, episodic=False)
    run_materialize(cfg, fmt="human", out=_out())
    out = _out()
    run_materialize(cfg, fmt="human", out=out)
    assert "skipped" in out.getvalue()
    assert "already_materialized" in out.getvalue()


def test_materialize_no_episodes(tmp_path) -> None:
    """An empty vault reports no currently-valid episodes."""
    v = tmp_path / "vault"
    cfg = _config(v)
    out = _out()
    run_materialize(cfg, fmt="human", out=out)
    assert "no currently-valid episodes" in out.getvalue()


def test_materialize_json_output(tmp_path) -> None:
    """``--format json`` emits a structured payload."""
    v = tmp_path / "vault"
    cfg = _config(v)
    _seed_episodes(v, episodic=False)
    out = _out()
    run_materialize(cfg, fmt="json", out=out)
    import json

    payload = json.loads(out.getvalue())
    assert payload["written"] == 1
    assert payload["items"][0]["status"] == "written"
    assert payload["items"][0]["path"] == "Memory/project-status.md"


def test_materialize_custom_dir(tmp_path) -> None:
    """The ``[materialize] dir`` controls the target folder."""
    v = tmp_path / "vault"
    cfg = _config(v, dir="Notes")
    _seed_episodes(v, episodic=False)
    run_materialize(cfg, fmt="human", out=_out())
    assert (v / "Notes" / "project-status.md").exists()
    assert not (v / "Memory" / "project-status.md").exists()


# ---------------------------------------------------------------------------
# CLI surface (Typer parser + exit codes).
# ---------------------------------------------------------------------------


def test_materialize_command_not_configured_exits_98(vault) -> None:
    """``seahorse materialize`` without ``[materialize]`` exits 98."""
    from tests.cli.conftest import invoke

    code, out, err = invoke(["--vault", str(vault), "materialize"])
    assert code == 98
    assert "CLI_MATERIALIZE_NOT_CONFIGURED" in err


def test_materialize_command_accepts_flags(vault) -> None:
    """``--mode`` / ``--cognitive-type`` are accepted by the Typer parser."""
    from seahorse.cli.config import write_materialize_config

    write_materialize_config(vault, MaterializeConfig())
    from tests.cli.conftest import invoke

    code, out, err = invoke(
        [
            "--vault",
            str(vault),
            "materialize",
            "--mode",
            "all",
            "--cognitive-type",
            "project_doc",
        ]
    )
    assert code == 0
    assert "no currently-valid episodes" in out
    assert err == ""
