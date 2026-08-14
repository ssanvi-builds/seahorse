"""End-to-end ``seahorse inspect`` via the invoke harness.

Read-only sidecar snapshot. Exit 0 always on success (empty or populated DB).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from seahorse.contracts.episode import Episode
from seahorse.frontmatter.adapter import serialize
from tests.cli.conftest import invoke


def _uuid7(suffix: str) -> str:
    return f"01234567-89ab-7def-8123-456789abcde{suffix}"


def _write_note(
    vault: Path,
    name: str,
    *,
    ep_id: str,
    title: str | None = None,
    invalid_at: datetime | None = None,
) -> Path:
    ep = Episode(
        id=ep_id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version="3.1",
        provenance={"agent_id": "seahorse/test", "extraction_mode": "skip"},
        body=f"# {name}\nbody.\n",
        valid_at=datetime(2026, 1, 1, tzinfo=UTC),
        invalid_at=invalid_at,
        cognitive_type="fact",
        source_type="agent",
        title=title if title is not None else name,
        summary=f"summary {name}",
    )
    path = vault / f"{name}.md"
    serialize(ep, path, exclude_none=True)
    return path


def test_inspect_no_db_json(tmp_path, vault):
    code, out, err = invoke(["--vault", str(vault), "--json", "inspect"])
    assert code == 0, err
    obj = json.loads(out)
    assert obj["command"] == "inspect"
    assert obj["db_exists"] is False
    assert obj["schema_version"] == 0
    assert obj["episodes"] == 0
    assert obj["episode_index"] == 0
    assert obj["vigentes"] == 0
    assert obj["activos_ahora"] == 0
    assert obj["last_mtime_ms"] is None


def test_inspect_no_db_human(tmp_path, vault):
    code, out, err = invoke(["--vault", str(vault), "inspect"])
    assert code == 0, err
    assert "schema_version" in out


def test_inspect_after_migrate(tmp_path, vault):
    invoke(["--vault", str(vault), "migrate"])
    code, out, err = invoke(["--vault", str(vault), "--json", "inspect"])
    assert code == 0, err
    obj = json.loads(out)
    assert obj["db_exists"] is True
    assert obj["schema_version"] == 10


def test_inspect_is_read_only_does_not_create_db(tmp_path, vault):
    invoke(["--vault", str(vault), "inspect"])
    # no db_path file should have been created by a read-only inspect.
    from seahorse.cli.config import load_config

    cfg = load_config(vault)
    assert not cfg.db_path.exists()


def test_inspect_nonexistent_vault_is_cat_c_82(tmp_path):
    code, out, err = invoke(["--vault", str(tmp_path / "nope"), "inspect"])
    assert code == 82, err
    assert "CLI_VAULT_NOT_FOUND" in err


def test_inspect_after_rebuild_distinguishes_vigentes_vs_activos_ahora(tmp_path, vault):
    """e2e: rebuild → inspect must surface the two bi-temporal axes separately.

    Closes the coverage gap where inspect e2e only asserted the zero state. A
    future-scheduled invalidation (invalid_at far ahead) is active-now but NOT
    current-state (current-state requires invalid_at IS NULL), so the two counts
    diverge: vigentes=1, activos_ahora=2. If run_inspect ever swapped or dropped
    a predicate, this e2e catches it (the unit-level test in
    test_sidecar_status.py covers the SQL; this covers the CLI wiring).
    """
    # alpha: current-state AND active-now (no invalidation).
    _write_note(vault, "alpha", ep_id=_uuid7("01"))
    # bravo: future-scheduled invalidation -> active-now but NOT current-state.
    _write_note(
        vault, "bravo", ep_id=_uuid7("02"), invalid_at=datetime(2099, 1, 1, tzinfo=UTC)
    )
    invoke(["--vault", str(vault), "index", "rebuild"])
    code, out, err = invoke(["--vault", str(vault), "--json", "inspect"])
    assert code == 0, err
    obj = json.loads(out)
    assert obj["db_exists"] is True
    assert obj["episode_index"] == 2
    assert obj["vigentes"] == 1, obj  # only alpha
    assert obj["activos_ahora"] == 2, obj  # alpha + bravo (invalid_at > now)