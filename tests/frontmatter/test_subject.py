"""``frontmatter.subject`` — syntactic subject derivation (f5-03 §5.6)."""

from __future__ import annotations

from pathlib import Path

from seahorse.frontmatter.subject import (
    derive_subject,
    fact_id_of,
    normalize_subject,
    raw_subject,
)


class TestRawSubject:
    def test_title_takes_precedence_over_h1(self) -> None:
        assert raw_subject("The Title", "# Other\n") == "The Title"

    def test_empty_title_falls_back_to_h1(self) -> None:
        assert raw_subject("   ", "# First Heading\nbody") == "First Heading"

    def test_none_title_falls_back_to_h1(self) -> None:
        assert raw_subject(None, "# First Heading\nbody") == "First Heading"

    def test_returns_none_when_no_title_and_no_h1(self) -> None:
        assert raw_subject(None, "plain text\nno heading") is None

    def test_h2_is_not_an_h1(self) -> None:
        assert raw_subject(None, "## Sub\nbody") is None

    def test_h1_regex_edge_cases_return_none(self) -> None:
        # The H1 regex '^#\s+(.+?)\s*$' requires '#' + at least one whitespace +
        # at least one content char. These all fail it → None (filename-stem
        # fallback in derive_subject). Pinned so future loosening/tightening is
        # caught.
        assert raw_subject(None, "# \n") is None  # '#' + space, no content
        assert raw_subject(None, "#Title\n") is None  # '#' with no space
        assert raw_subject(None, " # Title\n") is None  # not line-start
        assert raw_subject(None, "#\n") is None  # bare '#'


class TestDeriveSubject:
    def test_title_precedence(self) -> None:
        assert derive_subject("The Title", "# Other\n", Path("note.md")) == "the title"

    def test_h1_fallback(self) -> None:
        assert derive_subject(None, "# First Heading\n", Path("note.md")) == "first heading"

    def test_filename_stem_fallback(self) -> None:
        # The F3.3 addition over the engine view: no title/H1 → filename stem.
        assert derive_subject(None, "plain text no heading", Path("My Note.md")) == "my note"

    def test_nfc_normalization(self) -> None:
        pre = derive_subject(None, "# café\n", Path("note.md"))
        decomp = derive_subject(None, "# café\n", Path("note.md"))  # café decomposed
        assert pre == decomp == "café"

    def test_casefold_and_whitespace_collapse(self) -> None:
        s = derive_subject(None, "#   Hello    World  \n", Path("n.md"))
        assert s == "hello world"

    def test_degenerate_stem_returns_empty(self) -> None:
        # A path with an empty stem (the migrator's case D trigger): the
        # subject is empty, so fact_id collapses to the constant sha256("").
        assert derive_subject(None, "no heading", Path("")) == ""

    def test_dotfile_stem_uses_the_dotfile_name(self) -> None:
        # Python's Path treats ".md" as a dotfile (stem == ".md"), not an empty
        # stem — so it yields a non-empty subject, not case D. Documented so the
        # migrator's degenerate-detection (commit 3) keys on stem emptiness.
        assert derive_subject(None, "no heading", Path(".md")) == ".md"


class TestFactIdOf:
    def test_is_sha256_truncated_to_32_hex(self) -> None:
        fid = fact_id_of("madrid")
        assert len(fid) == 32
        assert all(c in "0123456789abcdef" for c in fid)

    def test_deterministic(self) -> None:
        assert fact_id_of("madrid") == fact_id_of("madrid")

    def test_different_subjects_different_fact_id(self) -> None:
        assert fact_id_of("madrid") != fact_id_of("barcelona")

    def test_empty_subject_is_constant(self) -> None:
        # The collision risk the migrator guards against (f5-03 §5.6).
        assert fact_id_of("") == fact_id_of("")
        assert len(fact_id_of("")) == 32


def test_normalize_subject_is_pure_string_operation() -> None:
    # Independent of any file; the engine and frontmatter share this primitive.
    assert normalize_subject("  Café   Bar ") == "café bar"