"""``frontmatter.errors`` — the loud-rejection surface."""

from __future__ import annotations

from pathlib import Path

from seahorse.frontmatter.errors import (
    FrontmatterInvalid,
    MigrationError,
    SubjectEmpty,
    XReservedCollision,
)


def test_frontmatter_invalid_carries_path_and_cause() -> None:
    cause = ValueError("naive datetime rejected")
    err = FrontmatterInvalid(Path("/v/note.md"), cause)
    assert err.path == Path("/v/note.md")
    assert err.cause is cause
    assert err.code == "E_FRONTMATTER_INVALID"
    assert "/v/note.md" in str(err)
    assert "naive datetime rejected" in str(err)


def test_frontmatter_invalid_message_is_actionable() -> None:
    # `index rebuild` on a legacy Obsidian note used to surface only the raw
    # pydantic validation errors — no hint for a user who does not know the
    # canonical format. The message must say the note is not valid and what to
    # do about it.
    cause = ValueError("id Field required")
    err = FrontmatterInvalid(Path("/v/legacy.md"), cause)
    msg = str(err)
    assert "not valid frontmatter" in msg
    assert "correct" in msg
    assert "id" in msg  # names a required field so the user knows what to fix


def test_parse_file_wraps_yaml_syntax_error_with_path(tmp_path: Path) -> None:
    """L7: a YAML syntax error in one note surfaced a raw ruamel ParserError
    that named no file. parse_file must wrap it in FrontmatterInvalid with the
    source path so `index rebuild` points at the offending note."""
    import pytest

    from seahorse.frontmatter.adapter import parse_file

    note = tmp_path / "broken.md"
    note.write_text("---\ntags: [geo, \n---\n# broken\n", encoding="utf-8")
    with pytest.raises(FrontmatterInvalid) as excinfo:
        parse_file(note)
    assert excinfo.value.path == note
    assert "broken.md" in str(excinfo.value)


def test_migration_error_is_base_class() -> None:
    assert issubclass(XReservedCollision, MigrationError)
    assert issubclass(SubjectEmpty, MigrationError)


def test_x_reserved_collision_carries_key() -> None:
    err = XReservedCollision(Path("/v/n.md"), "x-valid-at")
    assert err.code == "E_X_RESERVED_COLLISION"
    assert err.key == "x-valid-at"
    assert "x-valid-at" in str(err)


def test_subject_empty_carries_path() -> None:
    err = SubjectEmpty(Path("/v/.md"))
    assert err.code == "E_SUBJECT_EMPTY"
    assert err.path == Path("/v/.md")