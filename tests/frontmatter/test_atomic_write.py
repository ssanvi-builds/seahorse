"""Atomic write — tmp + ``os.replace`` (f5-03 §4.2, safe-watcher)."""

from __future__ import annotations

import os
from pathlib import Path

from seahorse.frontmatter.adapter import _atomic_write, serialize
from tests.frontmatter.conftest import make_episode


def test_atomic_write_replaces_existing_file(vault: Path) -> None:
    p = vault / "note.md"
    p.write_text("old\n")
    _atomic_write(p, "new content\n")
    assert p.read_text() == "new content\n"


def test_atomic_write_creates_new_file(vault: Path) -> None:
    p = vault / "fresh.md"
    _atomic_write(p, "content\n")
    assert p.read_text() == "content\n"


def test_atomic_write_leaves_no_tmp_file_behind(vault: Path) -> None:
    p = vault / "note.md"
    serialize(make_episode(), p, exclude_none=True)
    tmps = [f for f in vault.iterdir() if f.name.startswith(f".{p.name}.") and f.suffix == ".tmp"]
    assert tmps == [], f"stale tmp left behind: {tmps}"


def test_atomic_write_uses_same_directory(vault: Path) -> None:
    # os.replace is only atomic on the same filesystem; the tmp must sit next
    # to the target. Verify by monkeypatching os.replace to capture the tmp path.
    p = vault / "note.md"
    captured: dict = {}
    real_replace = os.replace

    def spy_replace(src: str, dst: str) -> None:
        captured["src"] = Path(src)
        captured["dst"] = Path(dst)
        real_replace(src, dst)

    os.replace = spy_replace  # type: ignore[assignment]
    try:
        _atomic_write(p, "x\n")
    finally:
        os.replace = real_replace  # type: ignore[assignment]
    assert captured["src"].parent == p.parent
    assert captured["dst"] == p


def test_serialize_writes_atomically_no_partial_on_existing(vault: Path) -> None:
    # A pre-existing VALID file is fully replaced, never partially overwritten.
    # The baseline must parse (re-parse happens inside write_file); an invalid
    # baseline would raise FrontmatterInvalid, which is the correct behavior,
    # not a partial write.
    p = vault / "note.md"
    serialize(make_episode(title="Old Title"), p, exclude_none=True)
    serialize(make_episode(title="New Title"), p, exclude_none=True)
    text = p.read_text()
    assert text.startswith("---\n")
    assert "\n---\n" in text  # closing delimiter present (not partial)
    assert "title: New Title" in text
    # The file is re-parseable (the atomic write left a valid document).
    from seahorse.frontmatter.adapter import parse_file

    _cm, _body, ep = parse_file(p)
    assert ep.title == "New Title"