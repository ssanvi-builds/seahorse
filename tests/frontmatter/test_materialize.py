"""Materializer tests — episodes → F3.1 .md notes in the vault.

Covers the write side of the vault contract: naming/slugs, the id-based
human-edit guard (C3), collision handling (C3/C5), the consolidated effective
episode (C2), the invalidation merge (C1), path registration, and the
best-effort contract (M9).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from seahorse.contracts.episode import Episode
from seahorse.frontmatter.adapter import parse_file
from seahorse.frontmatter.materialize import (
    MODE_ALL,
    MODE_CONSOLIDATED,
    MODE_OFF,
    Materializer,
    MaterializeReport,
    slugify,
)
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_sidecar import SqliteSidecarIndexRepository

_NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


@pytest.fixture()
def sidecar(tmp_path: Path):
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=4, extensions=("vec0",))
    mgr.open()
    apply_migrations(mgr.writer)
    repo = SqliteSidecarIndexRepository(mgr)
    yield repo
    mgr.close()


def _episode(
    ep_id: str = "e1",
    *,
    subject: str = "my-subject",
    title: str = "My Subject",
    body: str = "# My Subject\n\nSome body",
    cognitive_type: str = "episodic",
    extraction_mode: str = "skip",
    invalid_at: datetime | None = None,
) -> Episode:
    return Episode(
        id=ep_id,
        created_at=_NOW,
        schema_version="1.1",
        provenance={"source_type": "agent", "extraction_mode": extraction_mode},
        body=body,
        title=title,
        subject=subject,
        valid_at=_NOW,
        invalid_at=invalid_at,
        cognitive_type=cognitive_type,
        source_type="agent",
    )


# ---------------------------------------------------------------------------
# slugify.
# ---------------------------------------------------------------------------


def test_slugify_lowercases_and_collapses() -> None:
    assert slugify("My Subject") == "my-subject"
    assert slugify("  Spaces  Around  ") == "spaces-around"
    assert slugify("Café & Code") == "caf-code"
    assert slugify("a--b---c") == "a-b-c"
    assert slugify("---leading---trailing---") == "leading-trailing"
    assert slugify("!!!") == ""


# ---------------------------------------------------------------------------
# materialize — write + idempotency.
# ---------------------------------------------------------------------------


def test_materialize_writes_f31_note(tmp_path, sidecar) -> None:
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_ALL)
    r = m.materialize(_episode())
    assert r.status == "written"
    assert r.path == "Memory/my-subject.md"
    note = tmp_path / "Memory" / "my-subject.md"
    assert note.exists()
    _cm, _body, parsed = parse_file(note)
    assert parsed.id == "e1"
    assert parsed.title == "My Subject"  # subject is derived from title by the rebuild


def test_materialize_stamps_on_disk_format_version(tmp_path, sidecar) -> None:
    """The note's frontmatter carries the ON-DISK format version ('0.1.0'),
    not the episode-contract semver from the DB episode ('1.1') — L7 found the
    write path stamping '1.1', disagreeing with the migrator's marker."""
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_ALL)
    m.materialize(_episode())  # _episode() uses the contract version 1.1
    _cm, _body, parsed = parse_file(tmp_path / "Memory" / "my-subject.md")
    assert parsed.schema_version == "0.1.0"


def test_materialize_is_idempotent(tmp_path, sidecar) -> None:
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_ALL)
    m.materialize(_episode())
    r = m.materialize(_episode())
    assert r.status == "skipped"
    assert r.reason == "already_materialized"


def test_materialize_round_trip_preserves_body(tmp_path, sidecar) -> None:
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_ALL)
    m.materialize(_episode(body="# My Subject\n\nOriginal body."))
    _cm, body, parsed = parse_file(tmp_path / "Memory" / "my-subject.md")
    assert body == "# My Subject\n\nOriginal body."
    assert parsed.id == "e1"


# ---------------------------------------------------------------------------
# Collision handling (C3/C5).
# ---------------------------------------------------------------------------


def test_materialize_foreign_note_gets_id8_suffix(tmp_path, sidecar) -> None:
    """A same-slug note that is NOT ours is never overwritten (id guard)."""
    target = tmp_path / "Memory" / "my-subject.md"
    target.parent.mkdir(parents=True)
    target.write_text("# My Subject\n\nHuman note.", encoding="utf-8")
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_ALL)
    r = m.materialize(_episode())
    assert r.status == "written"
    assert r.path == f"Memory/my-subject-{_episode().id[:8]}.md"
    # The human note is untouched.
    assert target.read_text(encoding="utf-8") == "# My Subject\n\nHuman note."


def test_materialize_slug_and_id8_taken_reports_collision(tmp_path, sidecar) -> None:
    """Both the slug and the id8-suffixed name taken → collision, never overwrite."""
    ep = _episode()
    for name in ("my-subject.md", f"my-subject-{ep.id[:8]}.md"):
        p = tmp_path / "Memory" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# Foreign\n\nNot ours.", encoding="utf-8")
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_ALL)
    r = m.materialize(ep)
    assert r.status == "collision"
    assert r.reason == "slug_and_id8_taken"


def test_materialize_unparseable_note_is_foreign(tmp_path, sidecar) -> None:
    """A broken note (unparseable frontmatter) is foreign — never overwritten."""
    target = tmp_path / "Memory" / "my-subject.md"
    target.parent.mkdir(parents=True)
    target.write_text("not: [valid: yaml\n---\nbody", encoding="utf-8")
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_ALL)
    r = m.materialize(_episode())
    assert r.status == "written"
    assert r.path != "Memory/my-subject.md"  # suffixed, not overwritten


# ---------------------------------------------------------------------------
# Mode filter.
# ---------------------------------------------------------------------------


def test_materialize_off_skips(tmp_path, sidecar) -> None:
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_OFF)
    r = m.materialize(_episode())
    assert r.status == "skipped"
    assert r.reason == "mode_off"


def test_materialize_consolidated_filters_episodic(tmp_path, sidecar) -> None:
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_CONSOLIDATED)
    r = m.materialize(_episode(cognitive_type="episodic"))
    assert r.status == "skipped"
    assert r.reason == "mode_filter"


def test_materialize_consolidated_includes_project_doc(tmp_path, sidecar) -> None:
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_CONSOLIDATED)
    r = m.materialize(_episode(cognitive_type="project_doc"))
    assert r.status == "written"


def test_materialize_consolidated_includes_consolidated(tmp_path, sidecar) -> None:
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_CONSOLIDATED)
    r = m.materialize(_episode(extraction_mode="consolidated", cognitive_type="semantic"))
    assert r.status == "written"


def test_materialize_no_subject_skips(tmp_path, sidecar) -> None:
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_ALL)
    r = m.materialize(_episode(subject=None, title=None, body="no heading"))
    assert r.status == "skipped"
    assert r.reason == "no_subject"


# ---------------------------------------------------------------------------
# Effective episode (C2 — consolidated title = stable subject).
# ---------------------------------------------------------------------------


def test_materialize_consolidated_writes_stable_title(tmp_path, sidecar) -> None:
    """A consolidated episode's .md title is the stable subject (C2)."""
    ep = _episode(
        subject="stable-key",
        title="My Subject [session_tag:3]",
        body="# stable-key\n\nDistilled knowledge.",
        extraction_mode="consolidated",
        cognitive_type="semantic",
    )
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_CONSOLIDATED)
    r = m.materialize(ep)
    assert r.status == "written"
    _cm, _body, parsed = parse_file(tmp_path / "Memory" / "stable-key.md")
    assert parsed.title == "stable-key"  # NOT the suffixed engine title


def test_materialize_non_consolidated_keeps_engine_title(tmp_path, sidecar) -> None:
    ep = _episode(title="My Subject", subject="my-subject")
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_ALL)
    m.materialize(ep)
    _cm, _body, parsed = parse_file(tmp_path / "Memory" / "my-subject.md")
    assert parsed.title == "My Subject"


# ---------------------------------------------------------------------------
# invalidate (C1 — merge, body preserved).
# ---------------------------------------------------------------------------


def test_invalidate_merges_invalid_at_preserving_body(tmp_path, sidecar) -> None:
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_ALL)
    m.materialize(_episode(body="# My Subject\n\nOriginal body."))
    # A human edit lands in the body.
    note = tmp_path / "Memory" / "my-subject.md"
    note.write_text(
        note.read_text(encoding="utf-8").replace("Original body.", "Human edit."),
        encoding="utf-8",
    )
    r = m.invalidate(_episode(invalid_at=_NOW))
    assert r is not None
    assert r.status == "invalidated"
    text = note.read_text(encoding="utf-8")
    assert "invalid_at" in text
    assert "Human edit." in text  # the human edit survives the merge


def test_invalidate_never_materialized_is_noop(tmp_path, sidecar) -> None:
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_ALL)
    assert m.invalidate(_episode(ep_id="never-materialized")) is None


def test_invalidate_consolidated_keeps_stable_title(tmp_path, sidecar) -> None:
    """The invalidation merge never clobbers the stable title (C2)."""
    ep = _episode(
        subject="stable-key",
        title="My Subject [session_tag:3]",
        body="# stable-key\n\nDistilled.",
        extraction_mode="consolidated",
        cognitive_type="semantic",
    )
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_CONSOLIDATED)
    m.materialize(ep)
    r = m.invalidate(ep.model_copy(update={"invalid_at": _NOW}))
    assert r is not None and r.status == "invalidated"
    _cm, _body, parsed = parse_file(tmp_path / "Memory" / "stable-key.md")
    assert parsed.title == "stable-key"


def test_invalidate_unparseable_note_reports_error(tmp_path, sidecar) -> None:
    """A note whose frontmatter broke is reported, never raised (M9)."""
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_ALL)
    m.materialize(_episode())
    note = tmp_path / "Memory" / "my-subject.md"
    note.write_text("not: [valid: yaml\n---\nbody", encoding="utf-8")
    r = m.invalidate(_episode(invalid_at=_NOW))
    assert r is not None
    assert r.status == "error"
    assert "invalidate_parse" in r.reason


def test_invalidate_oserror_reports_error(tmp_path, sidecar, monkeypatch) -> None:
    """A failed invalidation write is reported, never raised (M9)."""
    import seahorse.frontmatter.materialize as mat_mod

    def _boom(*_a, **_k) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(mat_mod, "write_file", _boom)
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_ALL)
    m.materialize(_episode())
    r = m.invalidate(_episode(invalid_at=_NOW))
    assert r is not None
    assert r.status == "error"
    assert r.reason == "disk full"


# ---------------------------------------------------------------------------
# Path registration.
# ---------------------------------------------------------------------------


def test_materialize_registers_path(tmp_path, sidecar) -> None:
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_ALL)
    m.materialize(_episode())
    row = sidecar.get_path("e1")
    assert row is not None
    assert row[0] == "Memory/my-subject.md"
    assert row[2] > 0  # size


# ---------------------------------------------------------------------------
# Best-effort (M9).
# ---------------------------------------------------------------------------


def test_materialize_oserror_reports_error_not_raise(tmp_path, sidecar) -> None:
    """A failed write is reported in the result, never raised (M9)."""
    # ``Memory`` is a FILE, so mkdir(parents=True) raises FileExistsError.
    blocker = tmp_path / "Memory"
    blocker.write_text("not a dir", encoding="utf-8")
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_ALL)
    r = m.materialize(_episode())
    assert r.status == "error"
    assert r.reason  # the OSError message


# ---------------------------------------------------------------------------
# Batch.
# ---------------------------------------------------------------------------


def test_materialize_episodes_batch_report(tmp_path, sidecar) -> None:
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_ALL)
    report = m.materialize_episodes(
        [_episode("e1", subject="one"), _episode("e2", subject="two")]
    )
    assert isinstance(report, MaterializeReport)
    assert report.written == 2
    assert report.skipped == 0
    assert [i.status for i in report.items] == ["written", "written"]


def test_materialize_episodes_deterministic_order(tmp_path, sidecar) -> None:
    m = Materializer(tmp_path, dir="Memory", sidecar=sidecar, mode=MODE_ALL)
    report = m.materialize_episodes(
        [_episode("e2", subject="two"), _episode("e1", subject="one")]
    )
    assert [i.ep_id for i in report.items] == ["e2", "e1"]  # input order preserved
