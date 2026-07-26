"""``seahorse.cli.vault_ops`` — real migrate / inspect / index rebuild (commit 5).

Unit-level: calls ``run_migrate`` / ``run_inspect`` / ``run_index_rebuild``
directly with a resolved ``SeahorseConfig`` and a ``StringIO`` sink, asserting
the rendered payload + the ADR-10 honesty contract (conflicts → exit 75, parse
failure → ``FrontmatterInvalid`` → Cat A). The invoke-harness end-to-end tests
(exit codes, ``--json``, stderr) live in ``test_migrate_cli.py`` /
``test_inspect_cli.py`` / ``test_index_rebuild_cli.py``.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from seahorse.cli.config import load_config, write_default_config
from seahorse.cli.errors import CliRebuildConflicts, CliUsageError
from seahorse.cli.vault_ops import run_index_rebuild, run_inspect, run_migrate
from seahorse.contracts.episode import Episode
from seahorse.frontmatter.adapter import serialize
from seahorse.frontmatter.errors import FrontmatterInvalid


def _uuid7(suffix: str) -> str:
    return f"01234567-89ab-7def-8123-456789abcde{suffix}"


def _config(tmp_path: Path) -> tuple[Path, object]:
    v = tmp_path / "vault"
    write_default_config(v)
    return v, load_config(v)


def _out() -> io.StringIO:
    return io.StringIO()


def _write_note(
    vault: Path,
    name: str,
    *,
    ep_id: str,
    title: str | None = None,
    invalid_at: datetime | None = None,
    supersedes: str | None = None,
    extraction_mode: str = "skip",
) -> Path:
    ep = Episode(
        id=ep_id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version="3.1",
        provenance={"agent_id": "seahorse/test", "extraction_mode": extraction_mode},
        body=f"# {name}\nbody of {name}.\n",
        valid_at=datetime(2026, 1, 1, tzinfo=UTC),
        invalid_at=invalid_at,
        supersedes=supersedes,
        cognitive_type="fact",
        source_type="agent",
        title=title if title is not None else name,
        summary=f"summary {name}",
    )
    path = vault / f"{name}.md"
    serialize(ep, path, exclude_none=True)
    return path


# ---------------------------------------------------------------------------
# migrate — schema migrations runner (apply_migrations with up_to seam).
# ---------------------------------------------------------------------------


def test_migrate_fresh_db_applies_all_migrations(tmp_path):
    _v, cfg = _config(tmp_path)
    o = _out()
    run_migrate(cfg, up_to=None, fmt="json", out=o)
    obj = json.loads(o.getvalue())
    assert obj["command"] == "migrate"
    assert obj["applied"] == 9  # fresh DB -> all 9 migrations
    assert obj["schema_version"] == 9
    assert obj["up_to"] is None
    assert obj["latest_available"] == 9
    assert cfg.db_path.exists()


def test_migrate_is_idempotent_second_run_applies_zero(tmp_path):
    _v, cfg = _config(tmp_path)
    run_migrate(cfg, up_to=None, fmt="human", out=_out())
    o = _out()
    run_migrate(cfg, up_to=None, fmt="json", out=o)
    obj = json.loads(o.getvalue())
    assert obj["applied"] == 0
    assert obj["schema_version"] == 9


def test_migrate_up_to_caps_applied_migrations(tmp_path):
    _v, cfg = _config(tmp_path)
    o = _out()
    run_migrate(cfg, up_to=5, fmt="json", out=o)
    obj = json.loads(o.getvalue())
    assert obj["applied"] == 5
    assert obj["schema_version"] == 5
    assert obj["up_to"] == 5
    assert obj["latest_available"] == 9


def test_migrate_up_to_zero_applies_nothing(tmp_path):
    _v, cfg = _config(tmp_path)
    o = _out()
    run_migrate(cfg, up_to=0, fmt="json", out=o)
    obj = json.loads(o.getvalue())
    assert obj["applied"] == 0
    assert obj["schema_version"] == 0


def test_migrate_up_to_beyond_latest_applies_all_available(tmp_path):
    # up_to is a CAP, not a requirement: 15 > 9 applies all 9 (no error, honest
    # reporting via latest_available so the operator sees the ceiling).
    _v, cfg = _config(tmp_path)
    o = _out()
    run_migrate(cfg, up_to=15, fmt="json", out=o)
    obj = json.loads(o.getvalue())
    assert obj["applied"] == 9
    assert obj["schema_version"] == 9
    assert obj["latest_available"] == 9


def test_migrate_negative_up_to_raises_usage(tmp_path):
    _v, cfg = _config(tmp_path)
    with pytest.raises(CliUsageError):
        run_migrate(cfg, up_to=-1, fmt="human", out=_out())


def test_migrate_human_output(tmp_path):
    _v, cfg = _config(tmp_path)
    o = _out()
    run_migrate(cfg, up_to=None, fmt="human", out=o)
    text = o.getvalue()
    assert "applied" in text
    assert "schema_version" in text


# ---------------------------------------------------------------------------
# inspect — read-only sidecar snapshot.
# ---------------------------------------------------------------------------


def test_inspect_no_db_reports_zeros_and_db_absent(tmp_path):
    _v, cfg = _config(tmp_path)
    o = _out()
    run_inspect(cfg, now=datetime(2026, 7, 1, tzinfo=UTC), fmt="json", out=o)
    obj = json.loads(o.getvalue())
    assert obj["command"] == "inspect"
    assert obj["db_exists"] is False
    assert obj["schema_version"] == 0
    assert obj["episodes"] == 0
    assert obj["episode_index"] == 0
    assert obj["vigentes"] == 0
    assert obj["activos_ahora"] == 0
    assert obj["last_mtime_ms"] is None
    # read-only: no DB file created.
    assert not cfg.db_path.exists()


def test_inspect_after_migrate_reports_schema_version_no_rows(tmp_path):
    _v, cfg = _config(tmp_path)
    run_migrate(cfg, up_to=None, fmt="human", out=_out())
    o = _out()
    run_inspect(cfg, now=datetime(2026, 7, 1, tzinfo=UTC), fmt="json", out=o)
    obj = json.loads(o.getvalue())
    assert obj["db_exists"] is True
    assert obj["schema_version"] == 9
    assert obj["episodes"] == 0
    assert obj["episode_index"] == 0


def test_inspect_human_output(tmp_path):
    _v, cfg = _config(tmp_path)
    run_migrate(cfg, up_to=None, fmt="human", out=_out())
    o = _out()
    run_inspect(cfg, now=datetime(2026, 7, 1, tzinfo=UTC), fmt="human", out=o)
    text = o.getvalue()
    assert "schema_version" in text
    assert "vigentes" in text


# ---------------------------------------------------------------------------
# index rebuild — rebuild_from_vault orchestration + ADR-10 honesty.
# ---------------------------------------------------------------------------


def test_index_rebuild_populates_sidecar_from_vault(tmp_path):
    v, cfg = _config(tmp_path)
    _write_note(v, "madrid", ep_id=_uuid7("01"))
    _write_note(v, "paris", ep_id=_uuid7("02"))
    o = _out()
    run_index_rebuild(cfg, fmt="json", out=o)
    obj = json.loads(o.getvalue())
    assert obj["command"] == "index rebuild"
    assert obj["indexed"] == 2
    assert obj["skipped"] == 0
    assert obj["conflicts"] == []


def test_index_rebuild_creates_db_if_absent(tmp_path):
    # Storage applies migrations on construct — rebuild bootstraps the DB.
    v, cfg = _config(tmp_path)
    assert not cfg.db_path.exists()
    _write_note(v, "solo", ep_id=_uuid7("01"))
    run_index_rebuild(cfg, fmt="json", out=_out())
    assert cfg.db_path.exists()


def test_index_rebuild_empty_vault_is_clean_zero(tmp_path):
    v, cfg = _config(tmp_path)
    # no .md notes at all
    o = _out()
    run_index_rebuild(cfg, fmt="json", out=o)
    obj = json.loads(o.getvalue())
    assert obj["indexed"] == 0
    assert obj["skipped"] == 0


def test_index_rebuild_conflicts_raise_cli_rebuild_conflicts_exit_75(tmp_path):
    # two vigent notes with the same title -> duplicate vigent fact_id -> the
    # whole group is skipped + reported (ADR-10: NO auto-pick). Exit 75.
    v, cfg = _config(tmp_path)
    _write_note(v, "c1", ep_id=_uuid7("01"), title="same-subject")
    _write_note(v, "c2", ep_id=_uuid7("02"), title="same-subject")
    o = _out()
    with pytest.raises(CliRebuildConflicts) as exc_info:
        run_index_rebuild(cfg, fmt="json", out=o)
    assert exc_info.value.exit_code == 75
    # the report is rendered to stdout BEFORE the error is raised, so the
    # operator sees the conflict list.
    obj = json.loads(o.getvalue())
    assert obj["indexed"] == 0
    assert obj["skipped"] == 2
    reasons = {c["reason"] for c in obj["conflicts"]}
    assert reasons == {"duplicate-vigent-fact_id"}
    assert exc_info.value.name == "CLI_REBUILD_CONFLICTS"


def test_index_rebuild_unparseable_note_raises_frontmatter_invalid(tmp_path):
    # a non-migrated note (no frontmatter) -> FrontmatterInvalid (Cat A exit 90),
    # NOT a silent skip (ADR-10). Storage is closed on the way out (finally).
    v, cfg = _config(tmp_path)
    _write_note(v, "good", ep_id=_uuid7("01"))
    (v / "raw.md").write_text("# no frontmatter here\njust body.\n", encoding="utf-8")
    with pytest.raises(FrontmatterInvalid):
        run_index_rebuild(cfg, fmt="json", out=_out())


def test_index_rebuild_human_output_clean(tmp_path):
    v, cfg = _config(tmp_path)
    _write_note(v, "solo", ep_id=_uuid7("01"))
    o = _out()
    run_index_rebuild(cfg, fmt="human", out=o)
    text = o.getvalue()
    assert "indexed" in text
    assert "1" in text


def test_index_rebuild_human_output_lists_conflicts(tmp_path):
    v, cfg = _config(tmp_path)
    _write_note(v, "c1", ep_id=_uuid7("01"), title="same-subject")
    _write_note(v, "c2", ep_id=_uuid7("02"), title="same-subject")
    o = _out()
    with pytest.raises(CliRebuildConflicts):
        run_index_rebuild(cfg, fmt="human", out=o)
    text = o.getvalue()
    assert "conflicts" in text
    assert "same-subject" in text or "c1" in text


# ---------------------------------------------------------------------------
# ruamel-confinement invariant — importing the CLI must NOT load ruamel.
# ---------------------------------------------------------------------------


def test_cli_app_import_does_not_load_ruamel():
    """Importing ``seahorse.cli.app`` must NOT pull ruamel into ``sys.modules``.

    Regression guard for the ruamel-confinement invariant: ``frontmatter.rebuild``
    transitively imports ruamel (via ``frontmatter.adapter``). If ``vault_ops``
    imported ``rebuild_from_vault`` at module top, every CLI command (init/status/
    recall/remember/migrate/inspect) would eagerly load ruamel. The fix keeps the
    import lazy (inside ``run_index_rebuild``); this guard fails loud if it creeps
    back to module top. Run in a fresh subprocess so the assertion sees a clean
    ``sys.modules`` (the test process itself has ruamel loaded by other tests).
    """
    import subprocess
    import sys

    script = (
        "import seahorse.cli.app, sys; "
        "leaked = sorted(k for k in sys.modules if k == 'ruamel' or k.startswith('ruamel.')); "
        "assert not leaked, f'ruamel leaked by cli.app import: {leaked}'; "
        "assert 'seahorse.frontmatter.adapter' not in sys.modules, 'frontmatter.adapter leaked'; "
        "assert 'seahorse.frontmatter.rebuild' not in sys.modules, 'frontmatter.rebuild leaked'; "
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, (
        f"ruamel-confinement guard failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert result.stdout.strip() == "ok"