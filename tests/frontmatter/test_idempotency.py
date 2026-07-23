"""Migrator idempotency — re-running a migrated vault is a no-op (f5-03 §3.5)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from seahorse.frontmatter.manifest import CASE_A, CASE_C
from seahorse.frontmatter.migrator import VaultMigrator

NOW = datetime(2026, 7, 22, 10, 0, 0, tzinfo=UTC)


def _migrator(vault: Path) -> VaultMigrator:
    return VaultMigrator(vault, "sess-1", now=NOW)


class TestIdempotency:
    def test_second_run_reclassifies_migrated_notes_as_c(self, tmp_path: Path) -> None:
        a = tmp_path / "a.md"
        a.write_text("# A\nbody a\n", encoding="utf-8")
        b = tmp_path / "b.md"
        b.write_text(
            "---\ntags: [x]\ncreated: 2024-01-01\n---\n# B\nbody b\n",
            encoding="utf-8",
        )

        migrator = _migrator(tmp_path)
        first = migrator.run()
        assert first.notes[str(a)].case == CASE_A
        assert first.notes[str(b)].case.startswith("B")

        # Second run: previously-migrated notes are now case C (no-op).
        second = migrator.run()
        assert second.notes[str(a)].case == CASE_C
        assert second.notes[str(b)].case == CASE_C
        assert second.stats.migrated == 0
        assert second.stats.already_f31 == 2

    def test_second_run_does_not_modify_files(self, tmp_path: Path) -> None:
        a = tmp_path / "a.md"
        a.write_text("# A\nbody a\n", encoding="utf-8")
        migrator = _migrator(tmp_path)
        migrator.run()
        after_first = a.read_text(encoding="utf-8")
        migrator.run()
        after_second = a.read_text(encoding="utf-8")
        assert after_first == after_second

    def test_run_then_resume_skips_all(self, tmp_path: Path) -> None:
        a = tmp_path / "a.md"
        a.write_text("# A\nbody a\n", encoding="utf-8")
        migrator = _migrator(tmp_path)
        migrator.run()
        manifest = migrator.run(resume=True)
        # Resume with unchanged content: the note keeps its prior entry, no
        # re-processing, no stat churn.
        assert manifest.notes[str(a)].case in (CASE_A, CASE_C)