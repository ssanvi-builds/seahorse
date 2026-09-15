"""Round-trip + parse/serialize tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from seahorse.contracts.episode import Episode
from seahorse.frontmatter.adapter import hydrate, parse_file, serialize, write_file
from seahorse.frontmatter.errors import FrontmatterInvalid
from tests.frontmatter.conftest import make_episode


def _write_round_trip(vault: Path, ep: Episode, *, exclude_none: bool = True) -> str:
    p = vault / "note.md"
    serialize(ep, p, exclude_none=exclude_none)
    return p.read_text()


class TestParseSerialize:
    def test_serialize_then_parse_recovers_fields(self, vault: Path) -> None:
        ep = make_episode()
        _write_round_trip(vault, ep)
        p = vault / "note.md"
        _cm, body, ep2 = parse_file(p)
        assert ep2.id == ep.id
        assert ep2.created_at == ep.created_at
        assert ep2.title == ep.title
        assert ep2.tags == ep.tags
        assert ep2.provenance == ep.provenance
        # body is NOT in the model after parse_file (lazy hydration):
        assert ep2.body is None
        assert body == ep.body  # byte-a-byte body recovered separately

    def test_hydrate_attaches_body(self, vault: Path) -> None:
        ep = make_episode(body="# Madrid\nSergio lives in Madrid.\n")
        p = vault / "note.md"
        serialize(ep, p, exclude_none=True)
        ep2 = hydrate(p)
        assert ep2.body == ep.body
        assert ep2.title == ep.title

    def test_body_is_byte_a_byte_including_embedded_hr(self, vault: Path) -> None:
        # Edge case: a body containing '---' (markdown hr) survives
        # because the split only breaks on the first two '---' lines.
        body = "# Title\n\n---\n\nA horizontal rule above.\n"
        ep = make_episode(body=body)
        p = vault / "note.md"
        serialize(ep, p, exclude_none=True)
        _, recovered, _ = parse_file(p)
        assert recovered == body

    def test_body_with_leading_blank_line_preserved(self, vault: Path) -> None:
        # The join strips exactly ONE leading '\n' (the separator). A body that
        # starts with a blank line keeps the second '\n'.
        body = "\n# Madrid\n"
        ep = make_episode(body=body)
        p = vault / "note.md"
        serialize(ep, p, exclude_none=True)
        _, recovered, _ = parse_file(p)
        assert recovered == body


class TestIdempotency:
    # The byte-identical guarantee must hold across the body-separator edge
    # cases, not just the trivial default body. Each body exercises the custom
    # '\n---\n' closing delimiter differently (the reason the override exists).
    @pytest.mark.parametrize(
        "body",
        [
            "# Madrid\nSergio lives in Madrid.\n",  # no leading newline
            "\n# Madrid\n",  # body's own leading blank line
            "# Title\n\n---\n\nA horizontal rule above.\n",  # embedded '---'
            "",  # empty body
        ],
        ids=["plain", "leading-blank", "embedded-hr", "empty"],
    )
    def test_write_parse_write_is_byte_identical(self, vault: Path, body: str) -> None:
        ep = make_episode(body=body)
        p = vault / "note.md"
        serialize(ep, p, exclude_none=True)
        first = p.read_text()
        ep2 = hydrate(p)
        serialize(ep2, p, exclude_none=True)
        assert p.read_text() == first

    def test_repeated_writes_stable(self, vault: Path) -> None:
        ep = make_episode()
        p = vault / "note.md"
        serialize(ep, p, exclude_none=True)
        for _ in range(3):
            serialize(hydrate(p), p, exclude_none=True)
        assert p.read_text() == _write_round_trip(vault, ep)


class TestWriteFileBaselineContract:
    def test_explicit_baseline_cm_avoids_reparse_and_preserves_x_keys(
        self, vault: Path
    ) -> None:
        # The migrator fast-path: parse once, hand the CommentedMap to write_file
        # so it does NOT re-parse (and so x-* keys + formatting survive). Also
        # verifies the caller's baseline_cm is not mutated in place (deepcopy).
        ep = make_episode()
        p = vault / "note.md"
        serialize(ep, p, exclude_none=True)
        # inject an x-* key by hand
        text = p.read_text().replace(
            "---\n", "---\nx-seahorse-author-tool-version: 0.3.2\n", 1
        )
        p.write_text(text)
        cm, _body, _ep = parse_file(p)
        # write_file with explicit baseline_cm + a mutated episode
        ep_mut = ep.model_copy(update={"title": "New Title"})
        write_file(p, ep_mut, ep_mut.body or "", exclude_none=True, baseline_cm=cm)
        out = p.read_text()
        assert "x-seahorse-author-tool-version: 0.3.2" in out  # x-* survived
        assert "title: New Title" in out
        # the caller's cm was not mutated in place (deepcopy): its title is still
        # the original "Madrid", not "New Title" (the copy was mutated, not cm).
        assert cm["title"] == "Madrid"


class TestMvpPhase:
    def test_mvp1_reparse_baseline_with_expired_at_does_not_crash(
        self, vault: Path
    ) -> None:
        # Regression: serialize() with baseline_cm=None re-parses the
        # existing file with the MVP phase. A later-release file with a non-null
        # expired_at (decayed note) must not raise FrontmatterInvalid on the
        # re-parse when the caller passes mvp="1".
        ep = make_episode(expired_at=datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC))
        p = vault / "note.md"
        # write the later-release file (expired_at present) under mvp="1"
        serialize(ep, p, exclude_none=False, mvp="1")
        # a second write (re-parse of the later-release baseline) must not crash
        ep2 = hydrate(p, mvp="1")
        serialize(ep2, p, exclude_none=False, mvp="1")
        # and the expired_at survives the round-trip
        _cm, _body, ep3 = parse_file(p, mvp="1")
        assert ep3.expired_at is not None


class TestExcludeNoneByPhase:
    def test_mvp0_omits_null_fields(self, vault: Path) -> None:
        ep = make_episode()  # invalid_at=None, expired_at=None, supersedes=None
        text = _write_round_trip(vault, ep, exclude_none=True)
        assert "invalid_at" not in text
        assert "expired_at" not in text
        assert "supersedes" not in text  # the key itself absent (None value)

    def test_mvp1_writes_nulls_explicitly(self, vault: Path) -> None:
        ep = make_episode()
        text = _write_round_trip(vault, ep, exclude_none=False)
        assert "invalid_at: null" in text
        assert "expired_at: null" in text


class TestParseRejection:
    def test_naive_created_at_raises_frontmatter_invalid(self, vault: Path) -> None:
        p = vault / "note.md"
        p.write_text(
            "---\n"
            "id: 01234567-89ab-7def-8123-456789abcdef\n"
            "created_at: 2026-07-16T12:00:00\n"  # naive, no Z
            "schema_version: 0.1.0\n"
            "provenance: {}\n"
            "---\nbody\n"
        )
        with pytest.raises(FrontmatterInvalid) as exc_info:
            parse_file(p, mvp="0")
        assert exc_info.value.path == p

    def test_expired_at_non_null_mvp0_raises(self, vault: Path) -> None:
        p = vault / "note.md"
        p.write_text(
            "---\n"
            "id: 01234567-89ab-7def-8123-456789abcdef\n"
            "created_at: 2026-07-16T12:00:00Z\n"
            "schema_version: 0.1.0\n"
            "provenance: {}\n"
            "expired_at: 2026-07-16T12:00:00Z\n"
            "---\nbody\n"
        )
        with pytest.raises(FrontmatterInvalid):
            parse_file(p, mvp="0")

    def test_expired_at_non_null_mvp1_accepted(self, vault: Path) -> None:
        p = vault / "note.md"
        p.write_text(
            "---\n"
            "id: 01234567-89ab-7def-8123-456789abcdef\n"
            "created_at: 2026-07-16T12:00:00Z\n"
            "schema_version: 0.1.0\n"
            "provenance: {}\n"
            "expired_at: 2026-07-16T12:00:00Z\n"
            "---\nbody\n"
        )
        _cm, _body, ep = parse_file(p, mvp="1")
        assert ep.expired_at is not None

    def test_missing_required_field_raises(self, vault: Path) -> None:
        # A case-A file (no frontmatter) cannot parse as a valid episode.
        p = vault / "note.md"
        p.write_text("no frontmatter at all\n")
        with pytest.raises(FrontmatterInvalid):
            parse_file(p, mvp="0")


class TestUnquotedZTimestampRoundTrip:
    """A third-party writer emits timestamps as UNQUOTED YAML timestamps with a
    ``Z`` suffix (VESTIGIA round-trip report, 2026-09-14). The reader parses them
    into aware ``TimeStamp`` objects and passes its own ``_reject_naive`` guard —
    but the writer re-emitted the baseline objects naively, so the OUTPUT failed
    Seahorse's own read guard. Contract: the writer's output ALWAYS round-trips
    through the reader (aware-UTC ``Z`` on every timestamp), and the body +
    ``x-*`` fields survive unchanged."""

    VESTIGIA_NOTE = (
        "---\n"
        "id: 01234567-89ab-7def-8123-456789abcdef\n"
        "created_at: 2026-09-03T09:14:22Z\n"
        "valid_at: 2026-09-01T00:00:00Z\n"
        "schema_version: 0.1.0\n"
        "provenance: {agent_id: vestigia, session_id: v1, source_type: agent,"
        " extraction_mode: skip}\n"
        "x-vestigia-source: policy-rotation\n"
        "x-vestigia-reviewer: denis\n"
        "---\n"
        "# Rotation policy\n\n"
        "Token rotation ran at 09:14:22 UTC on the 3rd.\n"
    )

    @staticmethod
    def _write_note(vault: Path) -> Path:
        p = vault / "vestigia-note.md"
        p.write_text(TestUnquotedZTimestampRoundTrip.VESTIGIA_NOTE)
        return p

    def test_write_unchanged_reparses_preserving_body_and_x_keys(
        self, vault: Path
    ) -> None:
        p = self._write_note(vault)
        cm, body, ep = parse_file(p)
        assert ep.created_at.tzinfo is not None  # parse side is aware
        assert ep.valid_at.tzinfo is not None

        write_file(p, ep, body, exclude_none=True, baseline_cm=cm)
        out = p.read_text()
        # both timestamps are re-emitted with the Z suffix (aware-UTC canon)
        assert "created_at: 2026-09-03T09:14:22Z" in out
        assert "valid_at: 2026-09-01T00:00:00Z" in out
        # x-* fields and the body survive byte-a-byte
        assert "x-vestigia-source: policy-rotation" in out
        assert "x-vestigia-reviewer: denis" in out

        # the writer's output passes Seahorse's own read guard
        cm2, body2, ep2 = parse_file(p)
        assert ep2.created_at == ep.created_at
        assert ep2.valid_at == ep.valid_at
        assert body2 == body

    def test_write_parse_write_is_byte_identical(self, vault: Path) -> None:
        p = self._write_note(vault)
        cm, body, ep = parse_file(p)
        write_file(p, ep, body, exclude_none=True, baseline_cm=cm)
        first = p.read_text()
        cm2, body2, ep2 = parse_file(p)
        write_file(p, ep2, body2, exclude_none=True, baseline_cm=cm2)
        assert p.read_text() == first

    def test_reparse_baseline_path_also_preserves_z(self, vault: Path) -> None:
        # the write_file default path (baseline_cm=None → internal re-parse)
        # must hit the same fix: the caller often has no baseline in hand.
        p = self._write_note(vault)
        _cm, body, ep = parse_file(p)
        write_file(p, ep, body, exclude_none=True)
        _cm2, _body2, ep2 = parse_file(p)  # must not raise FrontmatterInvalid
        assert "created_at: 2026-09-03T09:14:22Z" in p.read_text()
        assert ep2.created_at == ep.created_at


class TestConsolidatedExtractionModeRoundTrip:
    def test_consolidated_round_trips_in_provenance(self, vault: Path) -> None:
        # A batch-distilled "stable knowledge note" carries
        # ``extraction_mode=consolidated``. The schema is freeform, so this value
        # must round-trip idempotently — it is portable even though the engine
        # does not produce it yet (fail-loud honesty: schema-valid, not built).
        ep = make_episode(
            cognitive_type="semantic",
            provenance={
                "agent_id": "seahorse/distill",
                "session_id": "consolidator-1",
                "source_type": "system",
                "extraction_mode": "consolidated",
                "model_used": None,
            },
        )
        text = _write_round_trip(vault, ep, exclude_none=False)
        assert "extraction_mode: consolidated" in text
        _cm, _body, ep2 = parse_file(vault / "note.md")
        assert ep2.provenance["extraction_mode"] == "consolidated"
        assert ep2.provenance["agent_id"] == "seahorse/distill"

    def test_consolidated_write_parse_write_is_byte_identical(self, vault: Path) -> None:
        ep = make_episode(
            cognitive_type="semantic",
            provenance={
                "agent_id": "seahorse/distill",
                "session_id": "consolidator-1",
                "source_type": "system",
                "extraction_mode": "consolidated",
                "model_used": None,
            },
        )
        p = vault / "note.md"
        serialize(ep, p, exclude_none=True)
        first = p.read_text()
        ep2 = hydrate(p)
        serialize(ep2, p, exclude_none=True)
        assert p.read_text() == first


class TestSupersedesReasonRoundTrip:
    def test_supersedes_reason_serializes_and_parses(self, vault: Path) -> None:
        ep = make_episode(
            supersedes="11111111-1111-7111-8111-111111111111",
            supersedes_reason="correction",
        )
        text = _write_round_trip(vault, ep, exclude_none=False)
        assert "supersedes_reason: correction" in text
        _cm, _body, ep2 = parse_file(vault / "note.md")
        assert ep2.supersedes_reason == "correction"


class TestProceduralCognitiveTypeRoundTrip:
    """The ``procedural`` cognitive type is no longer reserved: the wire enum and
    COGNITIVE_TYPES already accept it; the frontmatter round-trip must preserve it
    idempotently (a skill is a portable canonical-format .md)."""

    def test_procedural_round_trips_in_frontmatter(self, vault: Path) -> None:
        ep = make_episode(
            cognitive_type="procedural",
            provenance={
                "agent_id": "seahorse/skill",
                "session_id": "s1",
                "source_type": "agent",
                "extraction_mode": "skip",
                "x-seahorse-skill-trigger": "user asks how to do X",
                "x-seahorse-skill-version": "1.0",
            },
        )
        text = _write_round_trip(vault, ep, exclude_none=False)
        assert "cognitive_type: procedural" in text
        _cm, _body, ep2 = parse_file(vault / "note.md")
        assert ep2.cognitive_type == "procedural"
        assert ep2.provenance["x-seahorse-skill-trigger"] == "user asks how to do X"

    def test_procedural_write_parse_write_is_byte_identical(self, vault: Path) -> None:
        ep = make_episode(
            cognitive_type="procedural",
            provenance={
                "agent_id": "seahorse/skill",
                "session_id": "s1",
                "source_type": "agent",
                "extraction_mode": "skip",
            },
        )
        p = vault / "note.md"
        serialize(ep, p, exclude_none=True)
        first = p.read_text()
        ep2 = hydrate(p)
        serialize(ep2, p, exclude_none=True)
        assert p.read_text() == first