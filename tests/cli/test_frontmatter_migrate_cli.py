"""End-to-end ``seahorse frontmatter migrate`` via the invoke harness.

The frontmatter vault migrator got its CLI surface late: the original
``migrate`` slot was taken by the SCHEMA DDL runner, so ``VaultMigrator`` sat
with no command. This file covers the new ``seahorse frontmatter migrate``
group command — plain, legacy, already-migrated, and incompatible notes,
dry-run vs apply, idempotency, resume, exit 97 (CLI error
``CLI_MIGRATION_DEFERRED``) when apply meets incompatible notes, and
migration-before-init.

The ``vault`` fixture is an init'd tmp vault (``seahorse.toml`` written, no db);
``invoke`` runs ``main(argv)`` with captured streams.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from seahorse.contracts.episode import Episode
from seahorse.frontmatter.adapter import serialize
from seahorse.frontmatter.discovery import discover_notes
from tests.cli.conftest import invoke


def _uuid7(suffix: str) -> str:
    return f"01234567-89ab-7def-8123-456789abcde{suffix}"


def _write_f31_note(vault: Path, name: str, *, ep_id: str) -> Path:
    """A note that is ALREADY valid in the canonical episode format (the
    already-migrated case on a later run)."""
    ep = Episode(
        id=ep_id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version="3.1",
        provenance={"agent_id": "seahorse/test", "extraction_mode": "skip"},
        body=f"# {name}\nbody.\n",
        valid_at=datetime(2026, 1, 1, tzinfo=UTC),
        cognitive_type="fact",
        source_type="agent",
        title=name,
        summary=f"summary {name}",
    )
    path = vault / f"{name}.md"
    serialize(ep, path, exclude_none=True)
    return path


def _write_legacy_note(vault: Path, name: str) -> Path:
    """Legacy Obsidian note — non-canonical frontmatter (the legacy case)."""
    path = vault / f"{name}.md"
    path.write_text(
        "---\ntags: [viaje, espana]\ncreated: 2026-01-01\n---\n# "
        + name.capitalize()
        + "\nbody legacy.\n",
        encoding="utf-8",
    )
    return path


def _write_plain_note(vault: Path, name: str) -> Path:
    """No frontmatter at all (the plain case)."""
    path = vault / f"{name}.md"
    path.write_text(f"# {name}\nbody plain.\n", encoding="utf-8")
    return path


def _write_broken_note(vault: Path, name: str) -> Path:
    """Malformed YAML frontmatter (the incompatible case — refused)."""
    path = vault / f"{name}.md"
    path.write_text("---\ntags: [unclosed\n---\n# broken\n", encoding="utf-8")
    return path


def _all_notes(vault: Path) -> list[Path]:
    return sorted(discover_notes(vault))


def _assert_stats(out: str, *, total, migrated, already_f31, errors, collisions):
    """Parse the human summary block ``name: N`` lines and assert each stat."""
    lines = out.splitlines()
    stats = {}
    for ln in lines:
        stripped = ln.strip()
        if ": " not in stripped:
            continue
        k, _, v = stripped.partition(": ")
        try:
            stats[k] = int(v)
        except ValueError:
            continue
    assert stats["total"] == total, f"total mismatch: {stats}"
    assert stats["migrated"] == migrated, f"migrated mismatch: {stats}"
    assert stats["already_f31"] == already_f31, f"already_f31 mismatch: {stats}"
    assert stats["errors"] == errors, f"errors mismatch: {stats}"
    assert stats["collisions"] == collisions, f"collisions mismatch: {stats}"


# --- dry-run (preview: exit 0, NO writes) -----------------------------------


def test_frontmatter_migrate_dry_run_no_write(tmp_path, vault):
    _write_plain_note(vault, "alpha")
    _write_legacy_note(vault, "beta")
    _write_f31_note(vault, "gamma", ep_id=_uuid7("03"))
    _write_broken_note(vault, "delta")

    pre = {p: p.read_bytes() for p in _all_notes(vault)}

    code, out, err = invoke(["--vault", str(vault), "frontmatter", "migrate", "--dry-run"])
    assert code == 0, err
    # beta's legacy `created:` collides with the canonical format's `created_at`
    # (COLLISION_MAP) — reported, never auto-resolved.
    _assert_stats(out, total=4, migrated=2, already_f31=1, errors=1, collisions=1)
    assert "manifest" in out

    # No file touched (all pre/post bytes identical).
    for p, before in pre.items():
        assert p.read_bytes() == before, f"{p.name} was written in dry-run"
    # No manifest persisted on dry-run (run() skips save when dry_run).
    assert not (vault / ".seahorse" / "migration_manifest.json").exists()


def test_frontmatter_migrate_dry_run_with_d_is_exit_0(tmp_path, vault):
    _write_broken_note(vault, "delta")
    code, out, err = invoke(["--vault", str(vault), "frontmatter", "migrate", "--dry-run"])
    assert code == 0, err  # preview: the incompatible case is informational, never a failure
    _assert_stats(out, total=1, migrated=0, already_f31=0, errors=1, collisions=0)


# --- apply -------------------------------------------------------------------


def test_frontmatter_migrate_apply_writes_f31(tmp_path, vault):
    a = _write_plain_note(vault, "alpha")
    b = _write_legacy_note(vault, "beta")
    code, out, err = invoke(["--vault", str(vault), "frontmatter", "migrate"])
    assert code == 0, err
    # beta's legacy `created:` collides with the canonical format's `created_at`
    # (reported, kept).
    _assert_stats(out, total=2, migrated=2, already_f31=0, errors=0, collisions=1)

    for p in (a, b):
        text = p.read_text(encoding="utf-8")
        assert "schema_version:" in text, f"{p.name} missing schema_version"
        assert "id:" in text, f"{p.name} missing id"
        assert "created_at:" in text, f"{p.name} missing created_at"
        assert "provenance:" in text, f"{p.name} missing provenance"

    assert (vault / ".seahorse" / "migration_manifest.json").exists()


def test_frontmatter_migrate_idempotent_second_run(tmp_path, vault):
    _write_plain_note(vault, "alpha")
    _write_legacy_note(vault, "beta")

    code, _, err = invoke(["--vault", str(vault), "frontmatter", "migrate"])
    assert code == 0, err
    after_first = {p: p.read_bytes() for p in _all_notes(vault)}

    code, out, err = invoke(["--vault", str(vault), "frontmatter", "migrate"])
    assert code == 0, err
    _assert_stats(out, total=2, migrated=0, already_f31=2, errors=0, collisions=0)
    for p, before in after_first.items():
        assert p.read_bytes() == before, f"{p.name} changed on idempotent re-run"


def test_frontmatter_migrate_resume_skips_unchanged(tmp_path, vault):
    _write_plain_note(vault, "alpha")
    _write_legacy_note(vault, "beta")
    code, _, err = invoke(["--vault", str(vault), "frontmatter", "migrate"])
    assert code == 0, err

    manifest_path = vault / ".seahorse" / "migration_manifest.json"

    # Modify ONLY beta's body (mtime + content change). Alpha is untouched.
    beta = vault / "beta.md"
    beta.write_text(beta.read_text(encoding="utf-8") + "extra.\n", encoding="utf-8")

    code, out, err = invoke(["--vault", str(vault), "frontmatter", "migrate", "--resume"])
    assert code == 0, err
    # Resume: alpha unchanged → skipped (mtime hint, no re-hash); beta re-hashed
    # and re-processed. The manifest is ACCUMULATIVE: alpha keeps its migrated
    # entry (skipped, untouched), beta's old legacy entry is replaced by an
    # already-migrated entry (it already carries canonical frontmatter) — so
    # migrated=1 (alpha), already-migrated=1 (beta), and the legacy `created:`
    # collision is no longer reported (the migrated case has none).
    _assert_stats(out, total=2, migrated=1, already_f31=1, errors=0, collisions=0)

    # The manifest now records beta's NEW content hash (it WAS re-processed);
    # a naive skip-everything would have left the stale post_hash.
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    beta_entry = next(n for n in manifest["notes"] if n["path"].endswith("beta.md"))
    expected = f"sha256:{__import__('hashlib').sha256(beta.read_bytes()).hexdigest()}"
    assert beta_entry["post_hash"] == expected


def test_frontmatter_migrate_resume_no_change_rehashes_nothing(tmp_path, vault):
    _write_plain_note(vault, "alpha")
    _write_legacy_note(vault, "beta")
    code, _, err = invoke(["--vault", str(vault), "frontmatter", "migrate"])
    assert code == 0, err

    manifest_path = vault / ".seahorse" / "migration_manifest.json"
    before = json.loads(manifest_path.read_text(encoding="utf-8"))

    code, out, err = invoke(["--vault", str(vault), "frontmatter", "migrate", "--resume"])
    assert code == 0, err
    # Nothing re-processed → the manifest's ACCUMULATIVE stats are unchanged
    # from the apply run (both notes still counted as migrated there).
    _assert_stats(out, total=2, migrated=2, already_f31=0, errors=0, collisions=1)

    after = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Both entries byte-identical (nothing re-processed: mtime hint skipped both).
    assert before["notes"] == after["notes"]


# --- incompatible case: exit 97 in apply, report on stdout first ------------


def test_frontmatter_migrate_deferred_exit_97(tmp_path, vault):
    _write_plain_note(vault, "alpha")
    _write_broken_note(vault, "delta")
    code, out, err = invoke(["--vault", str(vault), "--json", "frontmatter", "migrate"])
    assert code == 97, err
    obj = json.loads(out)
    assert obj["command"] == "frontmatter migrate"
    assert obj["errors"] == 1
    assert obj["deferred"][0]["path"].endswith("delta.md")
    assert obj["deferred"][0]["error"] is not None
    assert "CLI_MIGRATION_DEFERRED" in err


def test_frontmatter_migrate_deferred_human_shows_error(tmp_path, vault):
    _write_broken_note(vault, "delta")
    code, out, err = invoke(["--vault", str(vault), "frontmatter", "migrate"])
    assert code == 97, err
    assert "delta.md" in out  # the deferred note is named on stdout
    assert "CLI_MIGRATION_DEFERRED" in err


# --- JSON shape --------------------------------------------------------------


def test_frontmatter_migrate_json_payload(tmp_path, vault):
    _write_plain_note(vault, "alpha")
    _write_f31_note(vault, "gamma", ep_id=_uuid7("03"))
    argv = ["--vault", str(vault), "--json", "frontmatter", "migrate", "--dry-run"]
    code, out, err = invoke(argv)
    assert code == 0, err
    obj = json.loads(out)
    assert obj["command"] == "frontmatter migrate"
    assert obj["dry_run"] is True
    assert obj["resume"] is False
    assert obj["total_notes"] == 2
    assert obj["migrated"] == 1
    assert obj["already_f31"] == 1
    assert obj["errors"] == 0
    assert obj["manifest_path"].endswith(".seahorse/migration_manifest.json")
    assert len(obj["session_id"]) == 36  # UUID


# --- CLI-shape guards --------------------------------------------------------


def test_frontmatter_migrate_negative_batch_size_usage(tmp_path, vault):
    code, out, err = invoke(
        ["--vault", str(vault), "frontmatter", "migrate", "--batch-size", "-1"]
    )
    assert code == 2, err
    assert "CLI_USAGE" in err


# --- migration before init ---------------------------------------------------


def test_frontmatter_migrate_before_init(tmp_path):
    # A raw Obsidian vault with notes but NO .seahorse/seahorse.toml yet.
    bare = tmp_path / "bare"
    bare.mkdir()
    _write_plain_note(bare, "alpha")
    code, out, err = invoke(["--vault", str(bare), "frontmatter", "migrate", "--dry-run"])
    assert code == 0, err
    _assert_stats(out, total=1, migrated=1, already_f31=0, errors=0, collisions=0)
    # Apply also works pre-init (no config needed).
    code, out, err = invoke(["--vault", str(bare), "frontmatter", "migrate"])
    assert code == 0, err
    assert "schema_version:" in (bare / "alpha.md").read_text(encoding="utf-8")


# --- manifest path -----------------------------------------------------------


def test_frontmatter_migrate_manifest_path(tmp_path, vault):
    _write_plain_note(vault, "alpha")
    code, _, err = invoke(["--vault", str(vault), "frontmatter", "migrate"])
    assert code == 0, err
    manifest_path = vault / ".seahorse" / "migration_manifest.json"
    assert manifest_path.is_file()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["stats"]["migrated"] == 1
    assert data["vault_path"] == str(vault.resolve())
