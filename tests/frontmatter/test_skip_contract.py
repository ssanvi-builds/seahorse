"""Resume skip predicate contract tests.

The hash is truth; the mtime is only a cheap hint. ``should_skip`` returns True
iff the content is unchanged since the manifest was written.
"""

from __future__ import annotations

from seahorse.frontmatter.manifest import ManifestEntry, should_skip

ENTRY = ManifestEntry(
    path="note.md",
    case="A",
    pre_hash="sha256:aaaa",
    post_hash="sha256:bbbb",
    mtime_pre=1000.0,
    mtime_post=1001.0,
    migrated_at="2026-07-22T10:00:00Z",
)


class TestShouldSkip:
    def test_same_hash_skips(self) -> None:
        assert should_skip(ENTRY, "sha256:bbbb", current_mtime=1001.0) is True

    def test_same_hash_skips_even_if_mtime_changed(self) -> None:
        # mtime manipulation (Obsidian Property UI) does not force a re-run.
        assert should_skip(ENTRY, "sha256:bbbb", current_mtime=9999.0) is True

    def test_different_hash_does_not_skip(self) -> None:
        assert should_skip(ENTRY, "sha256:cccc", current_mtime=1001.0) is False

    def test_different_hash_does_not_skip_even_if_mtime_unchanged(self) -> None:
        assert should_skip(ENTRY, "sha256:cccc", current_mtime=1001.0) is False

    def test_case_c_entry_skips_when_unchanged(self) -> None:
        # Idempotent case C: post_hash == pre_hash; unchanged content skips.
        c_entry = ManifestEntry(
            path="c.md",
            case="C",
            pre_hash="sha256:cccc",
            post_hash="sha256:cccc",
            mtime_pre=1000.0,
            mtime_post=-1,
            migrated_at=None,
        )
        assert should_skip(c_entry, "sha256:cccc", current_mtime=1000.0) is True