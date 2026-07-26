"""End-to-end ``seahorse index rebuild`` via the invoke harness (commit 5).

Orchestrates ``frontmatter.rebuild.rebuild_from_vault`` over the vault's ``.md``
notes + reports ``RebuildReport`` / ``RebuildConflict``. ADR-10 honesty: conflicts
→ exit 75 (``CLI_REBUILD_CONFLICTS``); a parse failure → Cat A
``E_FRONTMATTER_INVALID`` (exit 90).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from seahorse.cli.exit_codes import CLI_NOT_IN_MVP_0
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
) -> Path:
    ep = Episode(
        id=ep_id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version="3.1",
        provenance={"agent_id": "seahorse/test", "extraction_mode": "skip"},
        body=f"# {name}\nbody.\n",
        valid_at=datetime(2026, 1, 1, tzinfo=UTC),
        cognitive_type="fact",
        source_type="agent",
        title=title if title is not None else name,
        summary=f"summary {name}",
    )
    path = vault / f"{name}.md"
    serialize(ep, path, exclude_none=True)
    return path


def test_index_rebuild_clean(tmp_path, vault):
    _write_note(vault, "madrid", ep_id=_uuid7("01"))
    _write_note(vault, "paris", ep_id=_uuid7("02"))
    code, out, err = invoke(["--vault", str(vault), "--json", "index", "rebuild"])
    assert code == 0, err
    obj = json.loads(out)
    assert obj["command"] == "index rebuild"
    assert obj["indexed"] == 2
    assert obj["skipped"] == 0
    assert obj["conflicts"] == []


def test_index_rebuild_human_clean(tmp_path, vault):
    _write_note(vault, "solo", ep_id=_uuid7("01"))
    code, out, err = invoke(["--vault", str(vault), "index", "rebuild"])
    assert code == 0, err
    assert "indexed" in out


def test_index_rebuild_empty_vault_clean(tmp_path, vault):
    code, out, err = invoke(["--vault", str(vault), "--json", "index", "rebuild"])
    assert code == 0, err
    obj = json.loads(out)
    assert obj["indexed"] == 0
    assert obj["skipped"] == 0


def test_index_rebuild_conflicts_exit_75(tmp_path, vault):
    _write_note(vault, "c1", ep_id=_uuid7("01"), title="same-subject")
    _write_note(vault, "c2", ep_id=_uuid7("02"), title="same-subject")
    code, out, err = invoke(["--vault", str(vault), "--json", "index", "rebuild"])
    assert code == 75, err
    # The report is on stdout (operator sees the conflict list)...
    obj = json.loads(out)
    assert obj["indexed"] == 0
    assert obj["skipped"] == 2
    # ...and the error envelope is on stderr with the CLI_REBUILD_CONFLICTS code.
    assert "CLI_REBUILD_CONFLICTS" in err


def test_index_rebuild_conflicts_exit_75_not_the_reserved_meaning(tmp_path, vault):
    # The int 75 is shared with CLI_NOT_IN_MVP_0, but the symbolic cli_code
    # disambiguates: rebuild conflicts is CLI_REBUILD_CONFLICTS, NOT the
    # "reserved in MVP-0" message of the stub commands.
    _write_note(vault, "c1", ep_id=_uuid7("01"), title="same-subject")
    _write_note(vault, "c2", ep_id=_uuid7("02"), title="same-subject")
    code, out, err = invoke(["--vault", str(vault), "index", "rebuild"])
    assert code == 75, err
    assert "CLI_REBUILD_CONFLICTS" in err
    assert "reserved in MVP-0" not in err


def test_index_rebuild_unparseable_note_is_cat_a_90(tmp_path, vault):
    _write_note(vault, "good", ep_id=_uuid7("01"))
    (vault / "raw.md").write_text("# no frontmatter\njust body.\n", encoding="utf-8")
    code, out, err = invoke(["--vault", str(vault), "--json", "index", "rebuild"])
    assert code == 90, err
    assert "E_FRONTMATTER_INVALID" in err
    assert "seahorse_code" in err


def test_index_rebuild_quiet_still_exits_75_on_conflicts(tmp_path, vault):
    _write_note(vault, "c1", ep_id=_uuid7("01"), title="same-subject")
    _write_note(vault, "c2", ep_id=_uuid7("02"), title="same-subject")
    code, out, err = invoke(["--vault", str(vault), "--quiet", "index", "rebuild"])
    assert code == 75, err
    # --quiet suppresses stdout (no report), but the error still hits stderr.
    assert out == ""
    assert "CLI_REBUILD_CONFLICTS" in err


def test_index_rebuild_is_no_longer_the_reserved_stub(tmp_path, vault):
    # Regression guard: `index rebuild` used to exit 75 with CLI_NOT_IN_MVP_0.
    # Now it runs for real; only `index verify` remains the reserved stub.
    _write_note(vault, "solo", ep_id=_uuid7("01"))
    code, out, err = invoke(["--vault", str(vault), "index", "rebuild"])
    assert code == 0, err
    assert "CLI_NOT_IN_MVP_0" not in err


def test_index_verify_still_reserved_exit_75(tmp_path, vault):
    # `index verify` remains an honest stub (needs #7 vec0) — ADR-10.
    code, out, err = invoke(["--vault", str(vault), "index", "verify"])
    assert code == CLI_NOT_IN_MVP_0, err
    assert "CLI_NOT_IN_MVP_0" in err
    assert "reserved in MVP-0" in err