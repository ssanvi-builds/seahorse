"""Vault → sidecar rebuild orchestrator e2e.

Guards the ruamel-touching half of the ``.md`` → SQLite index bridge:
``rebuild_from_vault`` scans a fixture vault, parses each ``.md`` via
``adapter.parse_file`` (ruamel), builds ruamel-free ``ParsedNote`` payloads, and
delegates to ``SidecarIndexRepository.rebuild_all``. The sidecar stays
ruamel-free (dependency injection); the orchestrator lives in the frontmatter module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from seahorse.contracts.episode import Episode
from seahorse.frontmatter.adapter import serialize
from seahorse.frontmatter.errors import FrontmatterInvalid
from seahorse.frontmatter.rebuild import iter_parsed_notes, rebuild_from_vault
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_sidecar import SqliteSidecarIndexRepository


@pytest.fixture()
def sidecar(tmp_path: Path):
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=4, extensions=("vec0",))
    mgr.open()
    apply_migrations(mgr.writer)
    repo = SqliteSidecarIndexRepository(mgr)
    yield repo
    mgr.close()


def _uuid7(suffix: str) -> str:
    # version nibble 7, variant 8 — a valid UUIDv7 shape (distinct per note).
    return f"01234567-89ab-7def-8123-456789abcde{suffix}"


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
    # `subject`/`fact_id` are NOT serialized (Field(exclude=True)); the orchestrator
    # re-derives subject = title>H1>stem and fact_id = SHA-256(subject). Two notes
    # sharing a title share a subject → share a fact_id.
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


def _index_rows(sidecar: SqliteSidecarIndexRepository) -> list[dict]:
    rows = sidecar._cm.writer.execute(  # noqa: SLF001
        "SELECT ep_id, fact_id, file_path, title, summary, skip_extraction "
        "FROM episode_index ORDER BY ep_id"
    ).fetchall()
    return [dict(r) for r in rows]


# --- happy path --------------------------------------------------------------


def test_rebuild_from_vault_populates_sidecar(tmp_path: Path, sidecar) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "sub").mkdir()
    _write_note(vault, "madrid", ep_id=_uuid7("01"))
    _write_note(vault / "sub", "paris", ep_id=_uuid7("02"))

    report = rebuild_from_vault(vault, sidecar)

    assert report.indexed == 2
    assert report.skipped == []
    rows = {r["ep_id"]: r for r in _index_rows(sidecar)}
    assert set(rows) == {_uuid7("01"), _uuid7("02")}
    # file_path is posix-relative to the vault root (works on macOS + Linux).
    assert rows[_uuid7("01")]["file_path"] == "madrid.md"
    assert rows[_uuid7("02")]["file_path"] == "sub/paris.md"
    assert rows[_uuid7("01")]["title"] == "madrid"
    assert rows[_uuid7("01")]["summary"] == "summary madrid"
    # fact_id derived from the stored subject: fact_id_of("madrid").
    from seahorse.frontmatter.subject import fact_id_of
    assert rows[_uuid7("01")]["fact_id"] == fact_id_of("madrid")
    assert rows[_uuid7("02")]["fact_id"] == fact_id_of("paris")
    # migrated notes default to extraction_mode=skip -> skip_extraction=1.
    assert rows[_uuid7("01")]["skip_extraction"] == 1
    # episode_paths mirrored.
    assert sidecar.get_path(_uuid7("01"))[0] == "madrid.md"
    assert sidecar.get_path(_uuid7("02"))[0] == "sub/paris.md"


def test_rebuild_from_vault_forwards_secondary_index_wipes(tmp_path: Path, sidecar) -> None:
    # The orchestrator forwards secondary_index_wipes to rebuild_all so
    # the CLI can clear vec0/FTS in the same atomic as the episode_index clear.
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_note(vault, "a", ep_id=_uuid7("01"))
    seen: list[str] = []

    def wipe(conn) -> None:
        seen.append("ran")

    report = rebuild_from_vault(vault, sidecar, secondary_index_wipes=[wipe])
    assert report.indexed == 1
    assert seen == ["ran"]


def test_rebuild_from_vault_clear_then_rebuild(tmp_path: Path, sidecar) -> None:
    # a second rebuild reflects vault edits/deletions (clear-then-rebuild).
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_note(vault, "a", ep_id=_uuid7("01"))
    rebuild_from_vault(vault, sidecar)
    # delete the note + add another; rebuild wipes the stale row.
    (vault / "a.md").unlink()
    _write_note(vault, "b", ep_id=_uuid7("02"))
    report = rebuild_from_vault(vault, sidecar)
    assert report.indexed == 1
    rows = {r["ep_id"] for r in _index_rows(sidecar)}
    assert rows == {_uuid7("02")}
    assert sidecar.get_path(_uuid7("01")) is None


def test_rebuild_from_vault_reports_duplicate_vigent_fact_id(
    tmp_path: Path, sidecar
) -> None:
    # two currently valid notes with the SAME title -> same derived subject -> same
    # fact_id -> conflict group. Both skipped + reported (no auto-pick).
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_note(vault, "c1", ep_id=_uuid7("01"), title="same-subject")
    _write_note(vault, "c2", ep_id=_uuid7("02"), title="same-subject")
    report = rebuild_from_vault(vault, sidecar)
    assert report.indexed == 0
    assert {c.ep_id for c in report.skipped} == {_uuid7("01"), _uuid7("02")}
    assert _index_rows(sidecar) == []


def test_rebuild_from_vault_vigent_and_invalidated_same_fact_id(
    tmp_path: Path, sidecar
) -> None:
    # a supersession pair sharing a title (same subject, same fact_id), one
    # invalidated, is NOT a conflict — the unique constraint only fires when
    # both are currently valid.
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_note(
        vault,
        "old",
        ep_id=_uuid7("01"),
        title="shared",
        invalid_at=datetime(2026, 1, 5, tzinfo=UTC),
    )
    _write_note(vault, "new", ep_id=_uuid7("02"), title="shared", supersedes=_uuid7("01"))
    report = rebuild_from_vault(vault, sidecar)
    assert report.indexed == 2
    assert report.skipped == []


def test_rebuild_from_vault_llm_extraction_mode_skips_extraction_zero(
    tmp_path: Path, sidecar
) -> None:
    # the provenance->skip_extraction chain through the orchestrator: a note
    # written with extraction_mode=llm lands with skip_extraction=0 (extract),
    # not the migrator default of 1 (skip).
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_note(vault, "llm1", ep_id=_uuid7("01"), extraction_mode="llm")
    report = rebuild_from_vault(vault, sidecar)
    assert report.indexed == 1
    rows = {r["ep_id"]: r["skip_extraction"] for r in _index_rows(sidecar)}
    assert rows == {_uuid7("01"): 0}


def test_rebuild_from_vault_no_title_no_h1_note_gets_null_fact_id(
    tmp_path: Path, sidecar
) -> None:
    # a note with NO title and NO H1 derives subject=None -> fact_id=None under
    # the engine-equivalent derivation (NO filename-stem fallback). It still
    # lands in the index (not indexed by fact_id). Two such notes coexist (no
    # phantom conflict from filename-stem collision). This pins the bridge
    # equality: the vault-rebuilt index matches what the engine would store.
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "plain1.md").write_text(
        "---\nid: " + _uuid7("01") + "\ncreated_at: 2026-01-01T00:00:00Z\n"
        "schema_version: '3.1'\nprovenance:\n  extraction_mode: skip\n"
        "valid_at: 2026-01-01T00:00:00Z\ncognitive_type: fact\nsource_type: agent\n"
        "---\nno heading here, just prose.\n",
        encoding="utf-8",
    )
    (vault / "sub").mkdir()
    (vault / "sub" / "plain1.md").write_text(
        "---\nid: " + _uuid7("02") + "\ncreated_at: 2026-01-01T00:00:00Z\n"
        "schema_version: '3.1'\nprovenance:\n  extraction_mode: skip\n"
        "valid_at: 2026-01-01T00:00:00Z\ncognitive_type: fact\nsource_type: agent\n"
        "---\nalso no heading.\n",
        encoding="utf-8",
    )
    report = rebuild_from_vault(vault, sidecar)
    assert report.indexed == 2  # both landed
    assert report.skipped == []  # no phantom conflict despite same filename stem
    rows = {r["ep_id"]: r["fact_id"] for r in _index_rows(sidecar)}
    assert rows[_uuid7("01")] is None  # fact_id NULL (engine-equivalent)
    assert rows[_uuid7("02")] is None


# --- fail-loud honesty: parse failure surfaces ----------------------------------


def test_rebuild_from_vault_raises_on_unparseable_note(tmp_path: Path, sidecar) -> None:
    # a non-migrated note (no frontmatter) raises FrontmatterInvalid — the
    # operator must run `seahorse frontmatter migrate` first. NOT silently
    # skipped (fail-loud).
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "raw.md").write_text("# no frontmatter here\njust body.\n", encoding="utf-8")
    with pytest.raises(FrontmatterInvalid):
        rebuild_from_vault(vault, sidecar)
    # nothing landed (the orchestrator raised before delegating to the sidecar).
    assert _index_rows(sidecar) == []


def test_iter_parsed_notes_is_lazy_stream(tmp_path: Path) -> None:
    # streaming: the generator does not parse all notes up front. Confirm it
    # yields ParsedNote objects with the right file metadata, one per .md.
    vault = tmp_path / "vault"
    vault.mkdir()
    p = _write_note(vault, "solo", ep_id=_uuid7("01"))
    notes = list(iter_parsed_notes(vault))
    assert len(notes) == 1
    assert notes[0].file_path == "solo.md"
    assert notes[0].size == p.stat().st_size
    assert notes[0].mtime_ms == p.stat().st_mtime_ns // 1_000_000


# --- P1b: human-edit divergence (proposal-only) --------------------------------


def test_parsed_note_carries_the_note_body_hash(tmp_path: Path, sidecar) -> None:
    # The orchestrator hashes the note body it parsed with the engine's
    # canonical hash (the same normalization the importer idempotency
    # contract consumes) so a human edit is detectable later.
    from seahorse.engine.canonical import canonical_body_hash

    vault = tmp_path / "vault"
    vault.mkdir()
    _write_note(vault, "a", ep_id=_uuid7("01"))
    notes = list(iter_parsed_notes(vault))
    assert notes[0].body_hash == canonical_body_hash("# a\nbody of a.\n")


def test_parsed_note_body_hash_defaults_to_none() -> None:
    # Additive field: existing builders (migrator, engine, tests) construct
    # ParsedNote without the hash and stay valid.
    from seahorse.contracts.persistence import ParsedNote

    ep = Episode(
        id=_uuid7("09"),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version="3.1",
        provenance={"agent_id": "seahorse/test"},
        source_type="agent",
    )
    note = ParsedNote(episode=ep, file_path="a.md", mtime_ms=0, size=0)
    assert note.body_hash is None


def test_rebuild_reports_divergence_when_note_was_human_edited(
    tmp_path: Path, sidecar
) -> None:
    from seahorse.contracts.persistence import RebuildDivergence

    vault = tmp_path / "vault"
    vault.mkdir()
    _write_note(vault, "a", ep_id=_uuid7("01"))
    edited = "# a\nbody of a.\nhuman corrected this line.\n"
    report = rebuild_from_vault(
        vault,
        sidecar,
        live_body=lambda ep_id: edited if ep_id == _uuid7("01") else None,
    )
    assert report.indexed == 1
    assert report.divergences == [
        RebuildDivergence(ep_id=_uuid7("01"), file_path="a.md")
    ]


def test_rebuild_no_divergence_when_bodies_hash_equal(tmp_path: Path, sidecar) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_note(vault, "a", ep_id=_uuid7("01"))
    report = rebuild_from_vault(
        vault, sidecar, live_body=lambda _ep_id: "# a\nbody of a.\n"
    )
    assert report.divergences == []


def test_rebuild_no_divergence_when_note_has_no_body_hash(
    tmp_path: Path, sidecar, monkeypatch
) -> None:
    # ``body_hash`` is additive: a ParsedNote built without it (older builders)
    # has nothing to compare against the live body — no divergence is invented
    # from the absent value.
    from dataclasses import replace

    import seahorse.frontmatter.rebuild as rebuild_mod

    vault = tmp_path / "vault"
    vault.mkdir()
    _write_note(vault, "a", ep_id=_uuid7("01"))
    real = rebuild_mod.iter_parsed_notes

    def hashless(vault_root: Path):
        for note in real(vault_root):
            yield replace(note, body_hash=None)

    monkeypatch.setattr(rebuild_mod, "iter_parsed_notes", hashless)
    report = rebuild_from_vault(
        vault, sidecar, live_body=lambda _ep_id: "some other body"
    )
    assert report.indexed == 1
    assert report.divergences == []


def test_rebuild_divergence_compares_canonical_bodies(
    tmp_path: Path, sidecar
) -> None:
    # A human reflow (trailing whitespace, extra trailing newlines) is NOT an
    # edit — the canonical hash normalizes it away, so no proposal.
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_note(vault, "a", ep_id=_uuid7("01"))
    reflowed = "# a  \nbody of a.  \n\n\n\n\n"
    report = rebuild_from_vault(vault, sidecar, live_body=lambda _ep_id: reflowed)
    assert report.divergences == []


def test_rebuild_no_divergence_when_live_episode_missing(
    tmp_path: Path, sidecar
) -> None:
    # ``live_body`` returns None (episode absent, invalidated, or body-less) —
    # no proposal is invented for a note with nothing to compare against.
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_note(vault, "a", ep_id=_uuid7("01"))
    report = rebuild_from_vault(vault, sidecar, live_body=lambda _ep_id: None)
    assert report.divergences == []


def test_rebuild_without_live_body_never_reports_divergence(
    tmp_path: Path, sidecar
) -> None:
    # Default (live_body=None): the orchestrator cannot compare, so the
    # report carries no divergences — existing callers see zero change.
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_note(vault, "a", ep_id=_uuid7("01"))
    report = rebuild_from_vault(vault, sidecar)
    assert report.divergences == []