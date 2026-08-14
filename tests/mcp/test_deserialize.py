"""Tests for the wire JSON → Python payload codec (the MCP server, pure)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.disclosure.types import PITPoint
from seahorse.facade.types import RememberPayload
from seahorse.mcp.deserialize import (
    build_provenance,
    build_remember_payload,
    extract_pit,
    parse_dt,
    parse_pit_point,
)


class TestParseDt:
    def test_zulu_to_utc(self) -> None:
        dt = parse_dt("2026-07-16T12:00:00Z")
        assert dt == datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)

    def test_offset_to_utc(self) -> None:
        dt = parse_dt("2026-07-16T14:00:00+02:00")
        assert dt == datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)

    def test_microseconds(self) -> None:
        dt = parse_dt("2026-07-16T12:00:00.123456Z")
        assert dt.microsecond == 123456

    def test_timezone_aware(self) -> None:
        dt = parse_dt("2026-07-16T12:00:00Z")
        assert dt.tzinfo is not None

    def test_non_string_raises(self) -> None:
        with pytest.raises(TypeError):
            parse_dt(123)  # type: ignore[arg-type]


class TestParsePitPoint:
    def test_builds_pitpoint(self) -> None:
        pit = parse_pit_point({"kind": "state_at", "t": "2026-07-16T12:00:00Z"})
        assert isinstance(pit, PITPoint)
        assert pit.kind == "state_at"
        assert pit.t == datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)

    def test_known_at_kind(self) -> None:
        pit = parse_pit_point({"kind": "known_at", "t": "2026-07-16T12:00:00Z"})
        assert pit.kind == "known_at"


class TestExtractPit:
    def test_pit_object_parsed(self) -> None:
        args = {"pit": {"kind": "state_at", "t": "2026-07-16T12:00:00Z"}}
        pit, pit_kind, t = extract_pit(args, t_field="pit_t")
        assert isinstance(pit, PITPoint)
        assert pit_kind is None
        assert t is None

    def test_loose_pit_kind_and_t(self) -> None:
        args = {"pit_kind": "known_at", "pit_t": "2026-07-16T12:00:00Z"}
        pit, pit_kind, t = extract_pit(args, t_field="pit_t")
        assert pit is None
        assert pit_kind == "known_at"
        assert t == datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)

    def test_build_pit_uses_t_field_not_pit_t(self) -> None:
        # build_pit wire shape uses "t" (not "pit_t") for the loose timestamp
        args = {"pit_kind": "state_at", "t": "2026-07-16T12:00:00Z"}
        pit, pit_kind, t = extract_pit(args, t_field="t")
        assert pit_kind == "state_at"
        assert t == datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)

    def test_all_none(self) -> None:
        pit, pit_kind, t = extract_pit({}, t_field="pit_t")
        assert pit is None
        assert pit_kind is None
        assert t is None

    def test_precedence_not_resolved_here(self) -> None:
        # extract_pit returns BOTH pit and pit_kind+t when both present; the
        # handler delegates precedence to facade.build_pit (delegation purity).
        args = {
            "pit": {"kind": "state_at", "t": "2026-07-16T12:00:00Z"},
            "pit_kind": "known_at",
            "pit_t": "2026-07-16T13:00:00Z",
        }
        pit, pit_kind, t = extract_pit(args, t_field="pit_t")
        assert pit is not None  # pit object parsed
        assert pit_kind == "known_at"  # loose kind also returned
        assert t is not None  # loose t also parsed


class TestBuildProvenance:
    def test_passthrough(self) -> None:
        by = {"agent_id": "a", "session_id": "s", "source_type": "agent"}
        out = build_provenance(by)
        assert out == by

    def test_returns_copy_not_original(self) -> None:
        by = {"agent_id": "a", "session_id": "s", "source_type": "agent"}
        out = build_provenance(by)
        out["agent_id"] = "changed"
        assert by["agent_id"] == "a"  # immutability: original untouched

    def test_preserves_importer_fields(self) -> None:
        by = {
            "agent_id": "a",
            "session_id": "s",
            "source_type": "importer",
            "importer_vendor": "obsidian",
            "source_record_id": "rec-1",
        }
        out = build_provenance(by)
        assert out["importer_vendor"] == "obsidian"
        assert out["source_record_id"] == "rec-1"


class TestBuildRememberPayload:
    def _minimal_args(self) -> dict:
        return {
            "body": "Sergio lives in Madrid",
            "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"},
        }

    def test_builds_frozen_payload(self) -> None:
        payload = build_remember_payload(self._minimal_args())
        assert isinstance(payload, RememberPayload)
        assert payload.body == "Sergio lives in Madrid"

    def test_schema_version_pinned(self) -> None:
        payload = build_remember_payload(self._minimal_args())
        assert payload.schema_version == "1.1"

    def test_tags_converted_to_tuple(self) -> None:
        args = self._minimal_args()
        args["tags"] = ["a", "b"]
        payload = build_remember_payload(args)
        assert payload.tags == ("a", "b")
        assert isinstance(payload.tags, tuple)

    def test_tags_absent_defaults_empty_tuple(self) -> None:
        payload = build_remember_payload(self._minimal_args())
        assert payload.tags == ()

    def test_valid_at_parsed(self) -> None:
        args = self._minimal_args()
        args["valid_at"] = "2026-07-16T12:00:00Z"
        payload = build_remember_payload(args)
        assert payload.valid_at == datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)

    def test_valid_at_null_stays_none(self) -> None:
        args = self._minimal_args()
        args["valid_at"] = None
        payload = build_remember_payload(args)
        assert payload.valid_at is None

    def test_cognitive_type_passed_through(self) -> None:
        args = self._minimal_args()
        args["cognitive_type"] = "semantic"
        payload = build_remember_payload(args)
        assert payload.cognitive_type == "semantic"

    def test_no_title_field_forwarded(self) -> None:
        # title is NOT on the wire; engine derives it from body H1
        payload = build_remember_payload(self._minimal_args())
        assert payload.title is None

    def test_provenance_built_from_by(self) -> None:
        payload = build_remember_payload(self._minimal_args())
        assert payload.by["source_type"] == "agent"
        assert payload.by["agent_id"] == "a"