"""Migrator case A/B/C/D classification + per-note migration (f5-03 §3.1/§3.4)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from seahorse.frontmatter.adapter import parse_file
from seahorse.frontmatter.manifest import CASE_A, CASE_B, CASE_C, CASE_D
from seahorse.frontmatter.migrator import VaultMigrator

SESSION = "test-session-1"
NOW = datetime(2026, 7, 22, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def migrator(tmp_path: Path) -> VaultMigrator:
    return VaultMigrator(tmp_path, SESSION, now=NOW)


# --------------------------------------------------------------------- classify


class TestClassifyCaseA:
    def test_no_frontmatter_is_case_a(self, migrator: VaultMigrator, tmp_path: Path) -> None:
        note = tmp_path / "plain.md"
        note.write_text("# Madrid\nSergio lives in Madrid.\n", encoding="utf-8")
        case, cm, body = migrator.classify(note)
        assert case == CASE_A
        assert len(cm) == 0
        assert body == "# Madrid\nSergio lives in Madrid.\n"


class TestClassifyCaseB:
    def test_legacy_obsidian_frontmatter_is_case_b(
        self, migrator: VaultMigrator, tmp_path: Path
    ) -> None:
        note = tmp_path / "legacy.md"
        note.write_text(
            "---\ntags: [geo, person]\ncreated: 2024-01-01\n---\n# Madrid\nbody\n",
            encoding="utf-8",
        )
        case, cm, body = migrator.classify(note)
        assert case == CASE_B
        assert "tags" in cm
        assert "created" in cm
        assert body == "# Madrid\nbody\n"


class TestClassifyCaseC:
    def test_valid_f31_frontmatter_is_case_c(
        self, migrator: VaultMigrator, tmp_path: Path
    ) -> None:
        from tests.frontmatter.conftest import CREATED, make_episode

        note = tmp_path / "f31.md"
        ep = make_episode(valid_at=CREATED)
        from seahorse.frontmatter.adapter import serialize

        serialize(ep, note, exclude_none=True, mvp="0")
        case, _cm, _body = migrator.classify(note)
        assert case == CASE_C


class TestClassifyCaseD:
    def test_broken_yaml_syntax_is_case_d(
        self, migrator: VaultMigrator, tmp_path: Path
    ) -> None:
        note = tmp_path / "broken.md"
        # Malformed YAML: unclosed bracket.
        note.write_text("---\ntags: [geo, \n---\n# Title\n", encoding="utf-8")
        case, _cm, _body = migrator.classify(note)
        assert case == CASE_D

    def test_x_reserved_key_is_case_d(
        self, migrator: VaultMigrator, tmp_path: Path
    ) -> None:
        note = tmp_path / "xres.md"
        note.write_text(
            "---\nx-valid-at: 2024-01-01\ntags: [geo]\n---\n# Title\n",
            encoding="utf-8",
        )
        case, _cm, _body = migrator.classify(note)
        assert case == CASE_D

    def test_f31_marker_but_invalid_is_case_d(
        self, migrator: VaultMigrator, tmp_path: Path
    ) -> None:
        note = tmp_path / "badf31.md"
        # schema_version present (F3.1 marker) but created_at is naive -> invalid.
        note.write_text(
            "---\n"
            "schema_version: 0.1.0\n"
            "id: 01234567-89ab-7def-8123-456789abcdef\n"
            "created_at: 2024-01-01T00:00:00\n"  # naive, no Z
            "provenance:\n  agent_id: x\n"
            "valid_at: 2024-01-01T00:00:00Z\n"
            "---\n# Title\n",
            encoding="utf-8",
        )
        case, _cm, _body = migrator.classify(note)
        assert case == CASE_D

    def test_f31_marker_without_valid_at_is_case_d(
        self, migrator: VaultMigrator, tmp_path: Path
    ) -> None:
        # schema_version present + parse_file OK, but valid_at absent -> the F3.1
        # marker is present yet incomplete -> case D (not silently treated as C).
        note = tmp_path / "novalidat.md"
        note.write_text(
            "---\n"
            "schema_version: 0.1.0\n"
            "id: 01234567-89ab-7def-8123-456789abcdef\n"
            "created_at: 2024-01-01T00:00:00Z\n"
            "provenance:\n  agent_id: x\n"
            "---\n# Title\n",
            encoding="utf-8",
        )
        case, _cm, _body = migrator.classify(note)
        assert case == CASE_D


class TestMigrateNoteCaseA:
    def test_writes_f31_frontmatter_preserving_body(
        self, migrator: VaultMigrator, tmp_path: Path
    ) -> None:
        note = tmp_path / "plain.md"
        original = "# Madrid\nSergio lives in Madrid.\n"
        note.write_text(original, encoding="utf-8")
        entry = migrator.migrate_note(note)
        assert entry.case == CASE_A
        assert entry.post_hash != entry.pre_hash
        # Re-parse: valid F3.1 episode with the body intact.
        _cm, body, ep = parse_file(note)
        assert body == original
        assert ep.schema_version == "0.1.0"
        assert ep.valid_at is not None

    def test_dry_run_does_not_write(
        self, migrator: VaultMigrator, tmp_path: Path
    ) -> None:
        note = tmp_path / "plain.md"
        original = "# Madrid\nbody\n"
        note.write_text(original, encoding="utf-8")
        before = note.read_text(encoding="utf-8")
        entry = migrator.migrate_note(note, dry_run=True)
        assert entry.case == CASE_A
        assert entry.post_hash == entry.pre_hash  # no write
        assert entry.migrated_at is None
        assert note.read_text(encoding="utf-8") == before  # untouched


class TestMigrateNoteCaseB:
    def test_preserves_legacy_keys_and_adds_f31(
        self, migrator: VaultMigrator, tmp_path: Path
    ) -> None:
        note = tmp_path / "legacy.md"
        note.write_text(
            "---\ntags: [geo, person]\ncreated: 2024-01-01\n---\n# Madrid\nbody\n",
            encoding="utf-8",
        )
        entry = migrator.migrate_note(note)
        assert entry.case == CASE_B
        assert "created vs created_at" in entry.collisions
        # Legacy key preserved + F3.1 added.
        cm, body, ep = parse_file(note)
        assert "tags" in cm  # legacy preserved
        assert "created" in cm  # legacy preserved
        assert ep.schema_version == "0.1.0"  # F3.1 added
        assert body == "# Madrid\nbody\n"


class TestMigrateNoteCaseC:
    def test_idempotent_no_write(
        self, migrator: VaultMigrator, tmp_path: Path
    ) -> None:
        from seahorse.frontmatter.adapter import serialize
        from tests.frontmatter.conftest import CREATED, make_episode

        note = tmp_path / "f31.md"
        ep = make_episode(valid_at=CREATED)
        serialize(ep, note, exclude_none=True, mvp="0")
        before = note.read_text(encoding="utf-8")
        entry = migrator.migrate_note(note)
        assert entry.case == CASE_C
        assert entry.post_hash == entry.pre_hash
        assert entry.mtime_post == -1
        assert entry.migrated_at is None
        assert note.read_text(encoding="utf-8") == before  # untouched

    def test_consolidated_episode_is_case_c_idempotent(
        self, migrator: VaultMigrator, tmp_path: Path
    ) -> None:
        # A batch-distilled note (``extraction_mode=consolidated``, obsiforge
        # §5.2) is already valid F3.1 -> case C, untouched on re-run. The schema
        # is freeform, so des-reserving consolidated must not disturb the
        # migrator (it round-trips without a migration).
        from seahorse.frontmatter.adapter import serialize
        from tests.frontmatter.conftest import CREATED, make_episode

        note = tmp_path / "consolidated.md"
        ep = make_episode(
            valid_at=CREATED,
            cognitive_type="semantic",
            provenance={
                "agent_id": "seahorse/distill",
                "session_id": "consolidator-1",
                "source_type": "system",
                "extraction_mode": "consolidated",
            },
        )
        serialize(ep, note, exclude_none=True, mvp="0")
        before = note.read_text(encoding="utf-8")
        entry = migrator.migrate_note(note)
        assert entry.case == CASE_C
        assert entry.post_hash == entry.pre_hash
        assert entry.mtime_post == -1
        assert note.read_text(encoding="utf-8") == before  # untouched


class TestMigrateNoteCaseD:
    def test_does_not_overwrite_and_logs_error(
        self, migrator: VaultMigrator, tmp_path: Path
    ) -> None:
        note = tmp_path / "xres.md"
        original = "---\nx-valid-at: 2024-01-01\ntags: [geo]\n---\n# Title\n"
        note.write_text(original, encoding="utf-8")
        entry = migrator.migrate_note(note)
        assert entry.case == CASE_D
        assert entry.post_hash == entry.pre_hash
        assert entry.error is not None
        assert "X_RESERVED" in entry.error or "FRONTMATTER" in entry.error
        assert note.read_text(encoding="utf-8") == original  # untouched

    def test_syntax_d_logs_frontmatter_invalid_reason(
        self, migrator: VaultMigrator, tmp_path: Path
    ) -> None:
        note = tmp_path / "broken.md"
        original = "---\ntags: [geo, \n---\n# Title\n"
        note.write_text(original, encoding="utf-8")
        entry = migrator.migrate_note(note)
        assert entry.case == CASE_D
        assert entry.error is not None
        assert "FRONTMATTER_INVALID" in entry.error
        assert note.read_text(encoding="utf-8") == original  # untouched

    def test_incomplete_f31_logs_incomplete_reason(
        self, migrator: VaultMigrator, tmp_path: Path
    ) -> None:
        # schema_version present, parses OK, but no valid_at -> case D "incomplete".
        note = tmp_path / "novalidat.md"
        original = (
            "---\n"
            "schema_version: 0.1.0\n"
            "id: 01234567-89ab-7def-8123-456789abcdef\n"
            "created_at: 2024-01-01T00:00:00Z\n"
            "provenance:\n  agent_id: x\n"
            "---\n# Title\n"
        )
        note.write_text(original, encoding="utf-8")
        entry = migrator.migrate_note(note)
        assert entry.case == CASE_D
        assert entry.error is not None
        assert "incomplete" in entry.error.lower()
        assert note.read_text(encoding="utf-8") == original  # untouched

    def test_subject_empty_degenerate_filename_is_case_d(
        self, migrator: VaultMigrator, tmp_path: Path
    ) -> None:
        # A file whose stem normalizes to empty (whitespace-only) and whose body
        # has no title/H1 derives an empty subject -> case D (refused).
        note = tmp_path / " .md"
        note.write_text("just a body with no heading and no frontmatter\n", encoding="utf-8")
        entry = migrator.migrate_note(note)
        assert entry.case == CASE_D
        assert entry.error is not None
        assert "SUBJECT_EMPTY" in entry.error
        unchanged = note.read_text(encoding="utf-8")
        assert unchanged == "just a body with no heading and no frontmatter\n"


# ------------------------------------------------------------------------ run


class TestRun:
    def test_run_classifies_mixed_vault_and_writes_manifest(
        self, tmp_path: Path
    ) -> None:
        from seahorse.frontmatter.adapter import serialize
        from tests.frontmatter.conftest import CREATED, make_episode

        # case A
        (tmp_path / "a.md").write_text("# A\nbody a\n", encoding="utf-8")
        # case B
        (tmp_path / "b.md").write_text(
            "---\ntags: [x]\ncreated: 2024-01-01\n---\n# B\nbody b\n", encoding="utf-8"
        )
        # case C
        c = tmp_path / "c.md"
        serialize(make_episode(valid_at=CREATED), c, exclude_none=True, mvp="0")
        # case D
        (tmp_path / "d.md").write_text(
            "---\nx-valid-at: 2024-01-01\n---\n# D\nbody d\n", encoding="utf-8"
        )

        migrator = VaultMigrator(tmp_path, SESSION, now=NOW)
        manifest = migrator.run()
        assert manifest.stats.total_notes == 4
        assert manifest.stats.migrated == 2  # A + B
        assert manifest.stats.already_f31 == 1
        assert manifest.stats.errors == 1
        assert manifest.stats.collisions >= 1  # B's created collision
        # Manifest file written.
        manifest_path = tmp_path / ".seahorse" / "migration_manifest.json"
        assert manifest_path.exists()

    def test_run_excludes_seahorse_sidecar_dir(self, tmp_path: Path) -> None:
        (tmp_path / "note.md").write_text("# N\nbody\n", encoding="utf-8")
        sidecar = tmp_path / ".seahorse"
        sidecar.mkdir()
        (sidecar / "seahorse.db").write_text("not a note", encoding="utf-8")
        (sidecar / "index.md").write_text("# sidecar index\n", encoding="utf-8")
        migrator = VaultMigrator(tmp_path, SESSION, now=NOW)
        manifest = migrator.run()
        assert manifest.stats.total_notes == 1  # only note.md, sidecar excluded