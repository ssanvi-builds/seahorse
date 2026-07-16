"""Tests for the minimal JSON Schema validator subset (#13 wire-shape)."""

from __future__ import annotations

import pytest

from seahorse.mcp.errors import WireShapeError
from seahorse.mcp.validate import validate
from seahorse.mcp.wire_schema import DEFS, RECALL_SCHEMA, REMEMBER_SCHEMA


class TestType:
    def test_string_ok(self) -> None:
        validate("hi", {"type": "string"})

    def test_string_wrong_type(self) -> None:
        with pytest.raises(WireShapeError):
            validate(123, {"type": "string"})

    def test_union_type_accepts_null(self) -> None:
        validate(None, {"type": ["string", "null"]})

    def test_integer_rejects_bool(self) -> None:
        # bool is a subclass of int — the validator excludes it explicitly.
        with pytest.raises(WireShapeError):
            validate(True, {"type": "integer"})

    def test_number_accepts_int(self) -> None:
        validate(5, {"type": "number"})

    def test_number_rejects_bool(self) -> None:
        with pytest.raises(WireShapeError):
            validate(False, {"type": "number"})


class TestEnum:
    def test_enum_member_ok(self) -> None:
        validate("skip", {"type": "string", "enum": ["skip", "llm"]})

    def test_enum_non_member_rejected(self) -> None:
        with pytest.raises(WireShapeError):
            validate("llm_partial", {"type": "string", "enum": ["skip", "llm"]})

    def test_enum_null_member_ok(self) -> None:
        validate(None, {"type": ["string", "null"], "enum": ["skip", "llm", None]})


class TestStringLength:
    def test_min_length_ok(self) -> None:
        validate("ab", {"type": "string", "minLength": 1})

    def test_min_length_violated(self) -> None:
        with pytest.raises(WireShapeError):
            validate("", {"type": "string", "minLength": 1})

    def test_max_length_violated(self) -> None:
        with pytest.raises(WireShapeError):
            validate("aaa", {"type": "string", "maxLength": 2})


class TestNumberBounds:
    def test_minimum_ok(self) -> None:
        validate(5, {"type": "integer", "minimum": 1})

    def test_minimum_violated(self) -> None:
        with pytest.raises(WireShapeError):
            validate(0, {"type": "integer", "minimum": 1})

    def test_maximum_violated(self) -> None:
        with pytest.raises(WireShapeError):
            validate(11, {"type": "integer", "maximum": 10})


class TestArray:
    def test_min_items_violated(self) -> None:
        with pytest.raises(WireShapeError):
            validate([], {"type": "array", "minItems": 1})

    def test_max_items_violated(self) -> None:
        with pytest.raises(WireShapeError):
            validate(["a", "b"], {"type": "array", "maxItems": 1})

    def test_items_recurse(self) -> None:
        with pytest.raises(WireShapeError):
            validate([123], {"type": "array", "items": {"type": "string"}})


class TestObject:
    def test_required_missing(self) -> None:
        with pytest.raises(WireShapeError):
            validate({}, {"type": "object", "required": ["a"]})

    def test_required_present(self) -> None:
        validate(
            {"a": 1},
            {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}},
        )

    def test_additional_properties_rejects_unknown(self) -> None:
        with pytest.raises(WireShapeError):
            validate(
                {"a": 1, "b": 2},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"a": {"type": "integer"}},
                },
            )

    def test_additional_properties_allows_known(self) -> None:
        validate(
            {"a": 1},
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"a": {"type": "integer"}},
            },
        )

    def test_nested_property_validated(self) -> None:
        with pytest.raises(WireShapeError):
            validate(
                {"a": "not-int"},
                {"type": "object", "properties": {"a": {"type": "integer"}}},
            )


class TestDateTimeFormat:
    def test_valid_zulu(self) -> None:
        validate("2026-07-16T12:00:00Z", {"type": "string", "format": "date-time"})

    def test_valid_offset(self) -> None:
        validate("2026-07-16T12:00:00+02:00", {"type": "string", "format": "date-time"})

    def test_naive_rejected(self) -> None:
        with pytest.raises(WireShapeError):
            validate("2026-07-16T12:00:00", {"type": "string", "format": "date-time"})

    def test_garbage_rejected(self) -> None:
        with pytest.raises(WireShapeError):
            validate("not-a-date", {"type": "string", "format": "date-time"})


class TestRef:
    def test_ref_resolves(self) -> None:
        validate(
            {"kind": "state_at", "t": "2026-07-16T12:00:00Z"},
            {"$ref": "#/$defs/PITPoint"},
            defs=DEFS,
        )

    def test_ref_unresolved(self) -> None:
        with pytest.raises(WireShapeError):
            validate({}, {"$ref": "#/$defs/Nope"}, defs=DEFS)


class TestOneOfAnyOf:
    def test_one_of_exactly_one(self) -> None:
        validate(None, {"oneOf": [{"type": "null"}, {"type": "string"}]})

    def test_one_of_zero_matches(self) -> None:
        with pytest.raises(WireShapeError):
            validate(123, {"oneOf": [{"type": "null"}, {"type": "string"}]})

    def test_one_of_two_matches_rejected(self) -> None:
        # both subs match a non-null object → oneOf requires exactly one
        with pytest.raises(WireShapeError):
            validate(5, {"oneOf": [{"type": "number"}, {"type": "integer"}]})

    def test_any_of_at_least_one(self) -> None:
        validate(5, {"anyOf": [{"type": "string"}, {"type": "integer"}]})

    def test_any_of_zero_matches(self) -> None:
        with pytest.raises(WireShapeError):
            validate(5.5, {"anyOf": [{"type": "string"}, {"type": "boolean"}]})

    def test_one_of_does_not_short_circuit_other_keywords(self) -> None:
        # oneOf matching must NOT skip the top-level type check (regression for
        # the pit oneOf bug: oneOf matched, then early-returned, skipping type).
        with pytest.raises(WireShapeError):
            validate(123, {"oneOf": [{"type": "null"}], "type": "string"})


class TestConst:
    def test_const_ok(self) -> None:
        validate("1.1", {"const": "1.1"})

    def test_const_mismatch(self) -> None:
        with pytest.raises(WireShapeError):
            validate("1.0", {"const": "1.1"})


class TestRealSchemas:
    """Validate against the actual tool schemas — drift detector.

    The schemas use ``$ref`` to ``$defs``, so ``defs=DEFS`` is passed (the
    handlers resolve via ``schema_for(tool)`` which inlines ``$defs``).
    """

    def test_remember_minimal_ok(self) -> None:
        validate(
            {"body": "hello", "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"}},
            REMEMBER_SCHEMA,
            defs=DEFS,
        )

    def test_remember_rejects_subject(self) -> None:
        with pytest.raises(WireShapeError):
            validate(
                {
                    "body": "hello",
                    "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"},
                    "subject": "Sergio",
                },
                REMEMBER_SCHEMA,
                defs=DEFS,
            )

    def test_remember_rejects_supersedes(self) -> None:
        with pytest.raises(WireShapeError):
            validate(
                {
                    "body": "hello",
                    "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"},
                    "supersedes": "ep-1",
                },
                REMEMBER_SCHEMA,
                defs=DEFS,
            )

    def test_remember_rejects_bad_cognitive_type(self) -> None:
        with pytest.raises(WireShapeError):
            validate(
                {
                    "body": "hello",
                    "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"},
                    "cognitive_type": "preference",
                },
                REMEMBER_SCHEMA,
                defs=DEFS,
            )

    def test_remember_rejects_body_too_long(self) -> None:
        with pytest.raises(WireShapeError):
            validate(
                {
                    "body": "x" * 100_000,
                    "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"},
                },
                REMEMBER_SCHEMA,
                defs=DEFS,
            )

    def test_remember_rejects_bad_source_type(self) -> None:
        with pytest.raises(WireShapeError):
            validate(
                {
                    "body": "hello",
                    "by": {"agent_id": "a", "session_id": "s", "source_type": "wizard"},
                },
                REMEMBER_SCHEMA,
                defs=DEFS,
            )

    def test_remember_rejects_reserved_extraction_mode(self) -> None:
        with pytest.raises(WireShapeError):
            validate(
                {
                    "body": "hello",
                    "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"},
                    "extraction_mode": "llm_partial",
                },
                REMEMBER_SCHEMA,
                defs=DEFS,
            )

    def test_recall_rejects_anchor_ep_id(self) -> None:
        with pytest.raises(WireShapeError):
            validate({"query": "x", "anchor_ep_id": "ep-1"}, RECALL_SCHEMA, defs=DEFS)

    def test_recall_rejects_hops(self) -> None:
        with pytest.raises(WireShapeError):
            validate({"query": "x", "hops": 2}, RECALL_SCHEMA, defs=DEFS)

    def test_recall_rejects_empty_query(self) -> None:
        with pytest.raises(WireShapeError):
            validate({"query": ""}, RECALL_SCHEMA, defs=DEFS)


class TestErrorFieldPath:
    def test_error_carries_field(self) -> None:
        with pytest.raises(WireShapeError) as exc_info:
            validate(
                {"body": 123, "by": {"agent_id": "a", "session_id": "s", "source_type": "agent"}},
                REMEMBER_SCHEMA,
                defs=DEFS,
            )
        assert exc_info.value.field is not None
        assert "body" in exc_info.value.field