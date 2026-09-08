"""Migration path 0.x → 1.0: contract tests for the frozen schema version.

Spec D2 (research sprint pre-1.0). The freeze decision (D1) writes
``SCHEMA_VERSION_MVP0 = "1.0.0"`` while the reader keeps accepting any semver
shape — accept-both, no rewrite. These tests pin that contract so the bump is
a one-line constant change plus this file:

- Group 1: the reader and the engine's skip-contract validator accept the
  existing 0.x park (a pre-1.0 vault keeps parsing under 1.0).
- Group 2: the migrator absorbs any 0.x (and, today, any semver shape) as
  CASE_C without rewriting; legacy notes get the frozen constant.
- Group 3: manifest/resume semantics survive the version bump.
- Group 4: constant hygiene — no ``"0.1.0"`` literal outside ``defaults.py``
  and the format doc agrees with the constant (anti-drift).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from seahorse.engine.engine import BiTemporalEngine
from seahorse.frontmatter.adapter import parse_file, serialize
from seahorse.frontmatter.defaults import SCHEMA_VERSION_MVP0
from seahorse.frontmatter.manifest import CASE_B, CASE_C, MigrationManifest
from seahorse.frontmatter.migrator import VaultMigrator
from seahorse.frontmatter.rebuild import rebuild_from_vault
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_sidecar import SqliteSidecarIndexRepository
from tests.frontmatter.conftest import CREATED, UUIDV7, make_episode

SESSION = "test-session-1"
NOW = datetime(2026, 7, 22, 10, 0, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[2]


def _uuid7(suffix: str) -> str:
    # version nibble 7, variant 8 — a valid UUIDv7 shape, distinct per note.
    return f"01234567-89ab-7def-8123-456789abcde{suffix}"


@pytest.fixture
def migrator(tmp_path: Path) -> VaultMigrator:
    return VaultMigrator(tmp_path, SESSION, now=NOW)


@pytest.fixture()
def sidecar(tmp_path: Path) -> SqliteSidecarIndexRepository:
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=4, extensions=("vec0",))
    mgr.open()
    apply_migrations(mgr.writer)
    repo = SqliteSidecarIndexRepository(mgr)
    yield repo
    mgr.close()


def _write_f31_note(
    vault: Path, name: str, *, schema_version: str, ep_id: str = UUIDV7
) -> Path:
    ep = make_episode(
        id=ep_id,
        valid_at=CREATED,
        schema_version=schema_version,
        title=name,
        body=f"# {name}\nNote about {name}.\n",
    )
    path = vault / f"{name}.md"
    serialize(ep, path, exclude_none=True, mvp="0")
    return path


# ----------------------------------------------------- Group 1: reader accepts 0.x


def test_reader_accepts_on_disk_0x(tmp_path: Path) -> None:
    note = _write_f31_note(tmp_path, "Madrid", schema_version="0.1.0")
    _cm, body, ep = parse_file(note)
    assert ep.schema_version == "0.1.0"
    assert "Madrid" in body


def test_engine_skip_contract_accepts_0x() -> None:
    eng = BiTemporalEngine.__new__(BiTemporalEngine)  # pure validator, no repo needed
    ep = make_episode(valid_at=CREATED, schema_version="0.1.0")
    assert eng.is_valid_skip_path(ep) is True


def test_mixed_vault_0x_and_1x_rebuilds(
    sidecar: SqliteSidecarIndexRepository, tmp_path: Path
) -> None:
    _write_f31_note(tmp_path, "Madrid", schema_version="0.1.0")
    _write_f31_note(tmp_path, "Lisbon", schema_version="1.0.0", ep_id=_uuid7("2"))
    report = rebuild_from_vault(tmp_path, sidecar)
    assert report.indexed == 2
    assert report.skipped == []


# --------------------------------------------------- Group 2: migrator absorbs 0.x


def test_legacy_note_migrated_writes_frozen_constant(
    migrator: VaultMigrator, tmp_path: Path
) -> None:
    note = tmp_path / "legacy.md"
    note.write_text("---\ntags: [geo]\n---\n# Madrid\nbody\n", encoding="utf-8")
    entry = migrator.migrate_note(note)
    assert entry.case == CASE_B
    _cm, _body, ep = parse_file(note)
    # Imported from defaults — the 1.0 bump is exercised by changing that one line.
    assert ep.schema_version == SCHEMA_VERSION_MVP0


def test_on_disk_0x_is_case_c_not_degraded(migrator: VaultMigrator, tmp_path: Path) -> None:
    note = _write_f31_note(tmp_path, "Madrid", schema_version="0.1.0")
    entry = migrator.migrate_note(note)
    assert entry.case == CASE_C
    assert entry.post_hash == entry.pre_hash  # no rewrite
    assert entry.migrated_at is None


@pytest.mark.parametrize("version", ["0.9.9", "2.0.0"])
def test_any_semver_shape_is_absorbed_as_case_c(
    migrator: VaultMigrator, tmp_path: Path, version: str
) -> None:
    # D1 decision point pinned as-is: classify checks semver SHAPE only, so an
    # unknown major is absorbed like any 0.x. Rejecting future majors is an
    # opt-in post-1.0 (contract stays additive).
    note = _write_f31_note(
        tmp_path, f"v{version.replace('.', '-')}", schema_version=version
    )
    entry = migrator.migrate_note(note)
    assert entry.case == CASE_C


# ------------------------------------- Group 3: idempotency + manifest across bump


def test_resume_skips_already_migrated_note(migrator: VaultMigrator, tmp_path: Path) -> None:
    note = tmp_path / "legacy.md"
    note.write_text("---\ntags: [geo]\n---\n# Madrid\nbody\n", encoding="utf-8")
    first = migrator.run()
    assert first.notes[str(note)].case == CASE_B
    mtime_after_first = note.stat().st_mtime

    second = migrator.run(resume=True)
    assert note.stat().st_mtime == mtime_after_first  # no rewrite on resume
    assert second.notes[str(note)].case == CASE_B


def test_manifest_from_0x_run_resumes_after_bump(migrator: VaultMigrator, tmp_path: Path) -> None:
    note = tmp_path / "legacy.md"
    note.write_text("---\ntags: [geo]\n---\n# Madrid\nbody\n", encoding="utf-8")
    manifest_path = tmp_path / ".seahorse" / "migration_manifest.json"
    migrator.run()

    # Simulate the manifest a 0.x-era run left on disk.
    on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
    on_disk["schema_version"] = "0.1.0"
    manifest_path.write_text(json.dumps(on_disk), encoding="utf-8")
    loaded = MigrationManifest.load(manifest_path)
    assert loaded.schema_version == "0.1.0"

    mtime_before = note.stat().st_mtime
    second = migrator.run(resume=True)
    assert note.stat().st_mtime == mtime_before
    assert second.notes[str(note)].case == CASE_B


# ------------------------------------------------------- Group 4: constant hygiene


def test_no_schema_version_literal_outside_defaults() -> None:
    # The single point of truth is defaults.py (which also legitimately holds
    # MANIFEST_VERSION/MIGRATOR_VERSION — different versions, excluded here).
    roots = [
        REPO_ROOT / "src" / "seahorse" / "frontmatter",
        REPO_ROOT / "src" / "seahorse" / "engine",
        REPO_ROOT / "src" / "seahorse" / "benchmark",
    ]
    offenders = []
    for root in roots:
        for py in sorted(root.rglob("*.py")):
            if py.name == "defaults.py":
                continue
            if '"0.1.0"' in py.read_text(encoding="utf-8"):
                offenders.append(str(py.relative_to(REPO_ROOT)))
    assert offenders == []


def test_format_doc_version_matches_constant() -> None:
    doc = (REPO_ROOT / "docs" / "f3.1-format.md").read_text(encoding="utf-8")
    assert f"The current version is `{SCHEMA_VERSION_MVP0}`" in doc
    assert f"Current version: `{SCHEMA_VERSION_MVP0}`." in doc