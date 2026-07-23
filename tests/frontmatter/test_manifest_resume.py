"""Manifest serialization + resume integration tests (f5-03 §3.5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from seahorse.frontmatter.manifest import (
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    ManifestEntry,
    MigrationManifest,
    MigrationStats,
    sha256_of,
)
from seahorse.frontmatter.migrator import VaultMigrator


def _entry(path: str, case: str, *, collisions: list[str] | None = None,
           error: str | None = None) -> ManifestEntry:
    return ManifestEntry(
        path=path,
        case=case,
        pre_hash="sha256:pre",
        post_hash="sha256:post" if case in (CASE_A, CASE_B) else "sha256:pre",
        mtime_pre=1000.0,
        mtime_post=1001.0 if case in (CASE_A, CASE_B) else -1,
        migrated_at="2026-07-22T10:00:00Z" if case in (CASE_A, CASE_B) else None,
        collisions=collisions or [],
        error=error,
    )


class TestManifestStats:
    def test_add_counts_by_case(self) -> None:
        m = MigrationManifest()
        m.add(_entry("a.md", CASE_A))
        m.add(_entry("b.md", CASE_B))
        m.add(_entry("c.md", CASE_C))
        m.add(_entry("d.md", CASE_D, error="boom"))
        assert m.stats == MigrationStats(
            total_notes=0, migrated=2, already_f31=1, errors=1, collisions=0
        )

    def test_add_with_collision_counts_collisions(self) -> None:
        m = MigrationManifest()
        m.add(_entry("b.md", CASE_B, collisions=["created vs created_at"]))
        assert m.stats.collisions == 1

    def test_replace_entry_recomputes_stats(self) -> None:
        m = MigrationManifest()
        m.add(_entry("a.md", CASE_A))
        # Re-add the same path as case C (idempotent re-run): stats adjust.
        m.add(_entry("a.md", CASE_C))
        assert m.stats.migrated == 0
        assert m.stats.already_f31 == 1


class TestManifestRoundTrip:
    def test_save_load_round_trips_entries_and_stats(self, tmp_path: Path) -> None:
        m = MigrationManifest(vault_path=str(tmp_path), session_id="sess-1")
        m.add(_entry("a.md", CASE_A))
        m.add(_entry("b.md", CASE_B, collisions=["created vs created_at"]))
        m.stats.total_notes = 2
        path = tmp_path / "manifest.json"
        m.save(path)
        loaded = MigrationManifest.load(path)
        assert loaded.session_id == "sess-1"
        assert loaded.stats.migrated == 2
        assert loaded.stats.collisions == 1
        assert set(loaded.notes.keys()) == {"a.md", "b.md"}
        assert loaded.notes["b.md"].collisions == ["created vs created_at"]

    def test_to_json_is_valid_sorted_payload(self) -> None:
        m = MigrationManifest(session_id="s")
        m.add(_entry("z.md", CASE_A))
        m.add(_entry("a.md", CASE_B))
        payload = json.loads(m.to_json())
        paths = [n["path"] for n in payload["notes"]]
        assert paths == ["a.md", "z.md"]  # sorted by path


class TestSha256Of:
    def test_format_is_prefixed_hex(self, tmp_path: Path) -> None:
        f = tmp_path / "x.md"
        f.write_text("hello\n", encoding="utf-8")
        h = sha256_of(f)
        assert h.startswith("sha256:")
        assert len(h) == len("sha256:") + 64

    def test_same_content_same_hash(self, tmp_path: Path) -> None:
        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        a.write_text("same\n", encoding="utf-8")
        b.write_text("same\n", encoding="utf-8")
        assert sha256_of(a) == sha256_of(b)


class TestRunResume:
    """Resume skips notes whose content matches the manifest's post_hash."""

    def test_resume_skips_unchanged_notes(self, tmp_path: Path) -> None:
        # Build a manifest claiming both notes were already migrated (post_hash).
        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        a.write_text("# A\nbody a\n", encoding="utf-8")
        b.write_text("# B\nbody b\n", encoding="utf-8")
        migrator = VaultMigrator(tmp_path, "sess-1", now=datetime(2026, 7, 22, tzinfo=UTC))
        # First run migrates for real.
        manifest = migrator.run()
        a_post = manifest.notes[str(a)].post_hash
        b_post = manifest.notes[str(b)].post_hash

        # Second run with resume=True: both should skip (case C, unchanged).
        manifest2 = migrator.run(resume=True)
        # Skipped notes keep their prior entry (case A from first run, since the
        # content matches post_hash and should_skip returned True — they are not
        # re-added, so stats reflect the original classification).
        assert manifest2.notes[str(a)].post_hash == a_post
        assert manifest2.notes[str(b)].post_hash == b_post

    def test_resume_reprocesses_modified_note(self, tmp_path: Path) -> None:
        # A note whose content changed since the manifest was written is
        # re-processed (mtime hint misses -> hash differs -> not skipped).
        a = tmp_path / "a.md"
        a.write_text("# A\nbody a\n", encoding="utf-8")
        migrator = VaultMigrator(tmp_path, "sess-1", now=datetime(2026, 7, 22, tzinfo=UTC))
        first = migrator.run()
        first_post = first.notes[str(a)].post_hash
        # Modify the note's content (and mtime) after the first run.
        a.write_text("# A\nbody a CHANGED\n", encoding="utf-8")
        import os

        os.utime(a, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
        second = migrator.run(resume=True)
        # Re-processed: post_hash differs from the first run's.
        assert second.notes[str(a)].post_hash != first_post


class TestBatchCheckpoint:
    """The manifest is checkpointed every ``batch_size`` notes (resumability)."""

    def test_batch_size_one_checkpoints_after_each_note(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("a.md", "b.md", "c.md"):
            (tmp_path / name).write_text(f"# {name[0]}\nbody\n", encoding="utf-8")
        saves: list[Path] = []
        monkeypatch.setattr(
            MigrationManifest, "save", lambda self, p: saves.append(p)
        )
        migrator = VaultMigrator(tmp_path, "sess-1", now=datetime(2026, 7, 22, tzinfo=UTC))
        manifest_path = tmp_path / "manifest.json"
        migrator.run(batch_size=1, manifest_path=manifest_path)
        # 3 checkpoints (one per processed note) + 1 final save.
        assert len(saves) == 4
        assert all(p == manifest_path for p in saves)

    def test_batch_size_zero_skips_intermediate_checkpoints(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("a.md", "b.md", "c.md"):
            (tmp_path / name).write_text(f"# {name[0]}\nbody\n", encoding="utf-8")
        saves: list[Path] = []
        monkeypatch.setattr(
            MigrationManifest, "save", lambda self, p: saves.append(p)
        )
        migrator = VaultMigrator(tmp_path, "sess-1", now=datetime(2026, 7, 22, tzinfo=UTC))
        migrator.run(batch_size=0, manifest_path=tmp_path / "manifest.json")
        # Only the final save.
        assert len(saves) == 1


class TestRunResilience:
    """An I/O failure on one note is recorded as case D and does not abort the run."""

    def test_io_failure_recorded_as_case_d_run_continues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import seahorse.frontmatter.migrator as migmod

        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        a.write_text("# A\nbody a\n", encoding="utf-8")
        b.write_text("# B\nbody b\n", encoding="utf-8")
        real_write = migmod.write_file

        def flaky_write(path: Path, *args: object, **kwargs: object) -> None:
            if path.name == "a.md":
                raise OSError("disk full")
            return real_write(path, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(migmod, "write_file", flaky_write)
        migrator = VaultMigrator(tmp_path, "sess-1", now=datetime(2026, 7, 22, tzinfo=UTC))
        manifest = migrator.run()
        assert manifest.notes[str(a)].case == CASE_D
        assert manifest.notes[str(a)].error is not None
        assert "E_IO_FAILED" in manifest.notes[str(a)].error
        # The run continued and processed the other note.
        assert manifest.notes[str(b)].case in (CASE_A, CASE_B)