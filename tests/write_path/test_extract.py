"""Tests for the write path's ``_deterministic_extract`` — the skip-path fallback.

``_deterministic_extract`` is the zero-LLM editorial fallback of the skip path:
it derives a subject deterministically (``title > first H1`` — no filename
fallback, ``RememberPayload`` carries no path) and raises ``SubjectDerivationError``
loud when neither is present (never silently produce a subject-less episode). It
returns an ``ExtractedCandidate`` carrying ONLY editorial fields; provenance is
built separately by ``run_skip_path`` (no field duplication). Engine-owned fields
(``created_at`` / ``invalid_at`` / ``expired_at`` / ``id``) are never set here.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from seahorse.disclosure.types import SUMMARY_MAX_CHARS
from seahorse.facade.types import RememberPayload
from seahorse.write_path.extract import (
    ExtractedCandidate,
    SubjectDerivationError,
    derive_summary,
    deterministic_extract,
)


def _payload(*, title: str | None = None, body: str = "body",
             valid_at: datetime | None = None,
             cognitive_type: str | None = None,
             schema_version: str = "1.1",
             tags: tuple[str, ...] = (),
             by: dict | None = None) -> RememberPayload:
    return RememberPayload(
        body=body, by=by or {"source_type": "agent"}, title=title,
        valid_at=valid_at, cognitive_type=cognitive_type,
        schema_version=schema_version, tags=tags,
    )


class TestExtractedCandidateShape:
    def test_is_frozen(self) -> None:
        c = ExtractedCandidate(
            subject="s", body="b", valid_at=None,
            cognitive_type=None, schema_version="1.1",
        )
        with pytest.raises(FrozenInstanceError):
            c.subject = "x"  # type: ignore[misc]

    def test_has_no_provenance_fields(self) -> None:
        # Provenance (extraction_mode/model_used/prompt_hash/confidence) is
        # built by run_skip_path, NOT here — no field duplication.
        c = ExtractedCandidate(
            subject="s", body="b", valid_at=None,
            cognitive_type=None, schema_version="1.1",
        )
        for forbidden in ("extraction_mode", "model_used", "prompt_hash", "confidence"):
            assert not hasattr(c, forbidden), forbidden

    def test_has_no_engine_owned_fields(self) -> None:
        c = ExtractedCandidate(
            subject="s", body="b", valid_at=None,
            cognitive_type=None, schema_version="1.1",
        )
        for forbidden in ("created_at", "invalid_at", "expired_at", "id", "supersedes"):
            assert not hasattr(c, forbidden), forbidden


class TestSubjectDerivation:
    def test_subject_from_title(self) -> None:
        c = deterministic_extract(_payload(title="My Title", body="some body"))
        assert c.subject == "my title"  # normalize_subject: NFC + casefold + strip

    def test_title_takes_priority_over_h1(self) -> None:
        c = deterministic_extract(_payload(title="Title", body="# H1\nbody"))
        assert c.subject == "title"

    def test_subject_from_first_h1_when_no_title(self) -> None:
        c = deterministic_extract(_payload(title=None, body="# A Heading\nmore"))
        assert c.subject == "a heading"

    def test_subject_normalized(self) -> None:
        c = deterministic_extract(_payload(title="  Multiple   Spaces  ", body="b"))
        assert c.subject == "multiple spaces"

    def test_whitespace_only_title_falls_through_to_h1(self) -> None:
        # raw_subject treats a whitespace-only title as absent (frontmatter/subject).
        c = deterministic_extract(_payload(title="   ", body="# Real H1"))
        assert c.subject == "real h1"

    def test_whitespace_only_title_no_h1_raises(self) -> None:
        # A whitespace-only title with no H1 must raise (pins fail-loud behavior).
        with pytest.raises(SubjectDerivationError):
            deterministic_extract(_payload(title="   ", body="just prose"))

    def test_bare_hash_then_prose_raises(self) -> None:
        # A first line of bare "# " (no heading content) followed by prose must
        # NOT capture the prose as the H1 — the H1 regex must not span newlines.
        with pytest.raises(SubjectDerivationError):
            deterministic_extract(_payload(title=None, body="# \nSome prose"))

    def test_bare_hash_no_space_then_prose_raises(self) -> None:
        with pytest.raises(SubjectDerivationError):
            deterministic_extract(_payload(title=None, body="#\nrest"))

    def test_double_hash_real_h1_after_empty_hash(self) -> None:
        # A bare '#' line followed by a real '# Title' line must yield the real
        # H1 content (not "# title" with a spurious leading '#').
        c = deterministic_extract(_payload(title=None, body="#\n# Real Title\nbody"))
        assert c.subject == "real title"

    def test_no_title_no_h1_raises(self) -> None:
        with pytest.raises(SubjectDerivationError):
            deterministic_extract(_payload(title=None, body="just prose, no heading"))

    def test_empty_body_raises(self) -> None:
        with pytest.raises(SubjectDerivationError):
            deterministic_extract(_payload(title=None, body=""))

    def test_subject_derivation_error_is_value_error(self) -> None:
        assert issubclass(SubjectDerivationError, ValueError)


class TestPassthroughFields:
    def test_body_passthrough(self) -> None:
        c = deterministic_extract(_payload(title="t", body="the body"))
        assert c.body == "the body"

    def test_valid_at_passthrough_unsanitized(self) -> None:
        # valid_at is passed through unsanitized; the engine enforces it.
        vt = datetime(2024, 1, 1, tzinfo=UTC)
        c = deterministic_extract(_payload(title="t", valid_at=vt))
        assert c.valid_at == vt

    def test_valid_at_none_passthrough(self) -> None:
        c = deterministic_extract(_payload(title="t", valid_at=None))
        assert c.valid_at is None

    def test_cognitive_type_passthrough(self) -> None:
        c = deterministic_extract(_payload(title="t", cognitive_type="semantic"))
        assert c.cognitive_type == "semantic"

    def test_cognitive_type_none_passthrough(self) -> None:
        c = deterministic_extract(_payload(title="t", cognitive_type=None))
        assert c.cognitive_type is None

    def test_schema_version_passthrough(self) -> None:
        c = deterministic_extract(_payload(title="t", schema_version="1.2"))
        assert c.schema_version == "1.2"


class TestDeriveSummary:
    """Deterministic zero-LLM summary fallback."""

    def test_first_sentence_of_body(self) -> None:
        assert derive_summary("This is the first sentence. And the second.") == (
            "This is the first sentence."
        )

    def test_skips_h1(self) -> None:
        # The H1 is the subject; the summary is the first sentence of the
        # CONTENT, never the tagged subject line.
        body = "# [session_tag:prompt_number]\n\nThis is the real content. More."
        assert derive_summary(body) == "This is the real content."

    def test_skips_leading_blank_lines(self) -> None:
        body = "\n\n# Title\n\nContent sentence. More."
        assert derive_summary(body) == "Content sentence."

    def test_truncates_to_summary_max_chars(self) -> None:
        long = "x" * (SUMMARY_MAX_CHARS + 50) + ". tail"
        out = derive_summary(long)
        assert out is not None
        assert len(out) == SUMMARY_MAX_CHARS

    def test_single_sentence_no_boundary_returns_whole(self) -> None:
        assert derive_summary("no punctuation here") == "no punctuation here"

    def test_empty_body_returns_none(self) -> None:
        assert derive_summary("") is None

    def test_h1_only_returns_none(self) -> None:
        assert derive_summary("# Title") is None

    def test_deterministic(self) -> None:
        body = "# T\n\nFirst sentence. Second."
        assert derive_summary(body) == derive_summary(body)

    def test_custom_max_chars(self) -> None:
        out = derive_summary("A very long first sentence that should be cut.", max_chars=10)
        assert out == "A very lon"

    def test_tags_passthrough(self) -> None:
        c = deterministic_extract(_payload(title="t", tags=("a", "b")))
        assert c.tags == ("a", "b")

    def test_tags_default_empty(self) -> None:
        c = deterministic_extract(_payload(title="t"))
        assert c.tags == ()