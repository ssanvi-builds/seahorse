"""``frontmatter.schema`` — write-path validation + enums + Provenance.

Covers the split validation surfaces:
- read path (parse_file) fires only the canonical model's ``_reject_naive`` and
  context-gated ``_expired_null_mvp0`` (test_round_trip exercises those);
- write path (``validate_for_write``) adds UUIDv7 + self-supersede.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from seahorse.contracts.episode import Episode
from seahorse.frontmatter.errors import FrontmatterInvalid
from seahorse.frontmatter.schema import (
    CognitiveType,
    Provenance,
    SupersedesReason,
    validate_for_write,
)

VALID_ID = "01234567-89ab-7def-8123-456789abcdef"
CREATED = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _base_data() -> dict:
    return {
        "id": VALID_ID,
        "created_at": CREATED,
        "schema_version": "0.1.0",
        "provenance": {"agent_id": "m", "session_id": "s", "extraction_mode": "skip"},
    }


class TestValidateForWrite:
    def test_valid_uuidv7_passes(self) -> None:
        ep = validate_for_write(_base_data(), mvp="0")
        assert isinstance(ep, Episode)
        assert ep.id == VALID_ID

    def test_rejects_non_uuidv7_id(self) -> None:
        data = _base_data()
        data["id"] = "e1"
        with pytest.raises(FrontmatterInvalid) as exc_info:
            validate_for_write(data)
        assert "UUIDv7" in str(exc_info.value)

    def test_rejects_self_supersede(self) -> None:
        data = _base_data()
        data["supersedes"] = VALID_ID
        with pytest.raises(FrontmatterInvalid) as exc_info:
            validate_for_write(data)
        assert "supersedes must differ" in str(exc_info.value)

    def test_supersedes_other_id_passes(self) -> None:
        data = _base_data()
        data["supersedes"] = "11111111-1111-7111-8111-111111111111"
        ep = validate_for_write(data)
        assert ep.supersedes == "11111111-1111-7111-8111-111111111111"

    def test_rejects_naive_created_at(self) -> None:
        data = _base_data()
        data["created_at"] = datetime(2026, 7, 16, 12, 0, 0)  # naive
        with pytest.raises(FrontmatterInvalid):
            validate_for_write(data)

    def test_rejects_non_null_expired_at_in_mvp0(self) -> None:
        data = _base_data()
        data["expired_at"] = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(FrontmatterInvalid):
            validate_for_write(data, mvp="0")

    def test_accepts_non_null_expired_at_in_mvp1(self) -> None:
        data = _base_data()
        data["expired_at"] = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
        ep = validate_for_write(data, mvp="1")
        assert ep.expired_at is not None

    def test_source_path_attached_to_error(self) -> None:
        data = _base_data()
        data["_source_path"] = "/vault/note.md"
        data["id"] = "bad"
        with pytest.raises(FrontmatterInvalid) as exc_info:
            validate_for_write(data)
        assert str(exc_info.value.path) == "/vault/note.md"


class TestEpisodeReadPathValidators:
    """The canonical model's context-gated validators (no validate_for_write)."""

    def test_expired_at_non_null_without_context_passes(self) -> None:
        # No context → the guard does not fire → existing guard tests still work.
        ep = Episode(
            id="e1",
            created_at=CREATED,
            schema_version="0.1.0",
            provenance={},
            expired_at=datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC),
        )
        assert ep.expired_at is not None

    def test_expired_at_non_null_with_mvp0_context_raises(self) -> None:
        with pytest.raises(ValidationError):
            Episode.model_validate(
                {
                    "id": VALID_ID,
                    "created_at": CREATED,
                    "schema_version": "0.1.0",
                    "provenance": {},
                    "expired_at": "2026-07-16T12:00:00Z",
                },
                context={"mvp": "0"},
            )

    def test_naive_created_at_rejected_on_validation(self) -> None:
        with pytest.raises(ValidationError):
            Episode.model_validate(
                {
                    "id": VALID_ID,
                    "created_at": "2026-07-16T12:00:00",  # naive, no Z
                    "schema_version": "0.1.0",
                    "provenance": {},
                }
            )


class TestProvenance:
    def test_dumps_to_plain_dict_for_episode_construction(self) -> None:
        prov = Provenance(
            agent_id="seahorse/migrator",
            session_id="s1",
            source_type="human",
            extraction_mode="skip",
        )
        d = prov.model_dump()
        ep = Episode(
            id=VALID_ID,
            created_at=CREATED,
            schema_version="0.1.0",
            provenance=d,
        )
        assert ep.provenance == d
        assert ep.provenance["agent_id"] == "seahorse/migrator"

    def test_prompt_hash_must_be_64_hex(self) -> None:
        with pytest.raises(ValidationError):
            Provenance(
                agent_id="m", session_id="s", extraction_mode="skip",
                prompt_hash="abc",
            )

    def test_prompt_hash_valid_64_hex_passes_and_round_trips(self) -> None:
        # The happy path: a real 64-char hex SHA-256 passes the validator and
        # survives model_dump (so it can flow into the Episode provenance dict).
        hex64 = "a" * 64
        prov = Provenance(
            agent_id="m", session_id="s", extraction_mode="skip",
            prompt_hash=hex64,
        )
        assert prov.prompt_hash == hex64
        d = prov.model_dump()
        assert d["prompt_hash"] == hex64
        # and it round-trips into an Episode provenance dict
        ep = Episode(
            id=VALID_ID, created_at=CREATED, schema_version="0.1.0",
            provenance=d,
        )
        assert ep.provenance["prompt_hash"] == hex64

    def test_extra_fields_allowed_for_importer_metadata(self) -> None:
        prov = Provenance(
            agent_id="m", session_id="s", extraction_mode="skip",
            importer_vendor="obsidian-importer",
        )
        assert prov.model_dump()["importer_vendor"] == "obsidian-importer"


def test_cognitive_type_values() -> None:
    assert CognitiveType.SEMANTIC == "semantic"
    assert CognitiveType.EPISODIC == "episodic"


def test_supersedes_reason_values() -> None:
    assert SupersedesReason.CORRECTION == "correction"
    assert SupersedesReason.REVALIDATION == "revalidation"