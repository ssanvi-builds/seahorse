"""Tests for #5 ``_deterministic_extract`` — the skip-path fallback (f5-05 sec 3.2).

``_deterministic_extract`` is the zero-LLM editorial fallback of the skip path:
it derives a subject deterministically (``title > first H1`` — no filename
fallback, ``RememberPayload`` carries no path) and raises ``SubjectDerivationError``
loud when neither is present (ADR-10: never silently produce a subject-less
episode). It returns an ``ExtractedCandidate`` carrying ONLY editorial fields;
provenance is built separately by ``run_skip_path`` (no field duplication).
Engine-owned fields (``created_at`` / ``invalid_at`` / ``expired_at`` / ``id``)
are never set here.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from seahorse.facade.types import RememberPayload
from seahorse.write_path.extract import (
    ExtractedCandidate,
    SubjectDerivationError,
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
        # built by run_skip_path, NOT here — no field duplication (f5-05 sec 3.2).
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
        # A whitespace-only title with no H1 must raise (pins ADR-10 loud failure).
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
        # I2: valid_at is passed through unsanitized; the engine enforces it.
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

    def test_tags_passthrough(self) -> None:
        c = deterministic_extract(_payload(title="t", tags=("a", "b")))
        assert c.tags == ("a", "b")

    def test_tags_default_empty(self) -> None:
        c = deterministic_extract(_payload(title="t"))
        assert c.tags == ()