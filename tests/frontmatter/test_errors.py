"""``frontmatter.errors`` — the loud-rejection surface (ADR-10)."""

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