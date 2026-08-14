"""Tests for the 7 tool wire schemas (the MCP server) — drift detector.

Locks the reconciled drift:
- ``cognitive_type`` enum = the 6 canonical values + null (NOT the divergent
  ``["semantic","episodic","procedural",null]``; NOT ``preference``).
- ``source_type`` enum = 4 values. ``extraction_mode`` = skip|llm|consolidated|
  null (``consolidated`` schema-valid, single-sourced from the facade Literal;
  rejects reserved ``llm_partial``). ``reason`` has no ``decay``.
- ``recall`` has no ``anchor_ep_id``/``hops`` (the first release). ``forget``
  has no ``now`` (not exposed to MCP agents).
- ``recall_full.ep_ids`` ``maxItems == MAX_FULL_BATCH`` (wire-level REJECT).
"""

from __future__ import annotations

from typing import get_args

from seahorse.constants import (
    COGNITIVE_TYPES,
    EP_ID_MAX_CHARS,
    PROVENANCE_ID_MAX_CHARS,
    PROVENANCE_SHORT_MAX_CHARS,
    SOURCE_TYPES,
    SUBJECT_FILTER_MAX_CHARS,
    TAG_MAX_CHARS,
)
from seahorse.contracts.index import MAX_HOPS_MVP1, PIT_KIND_VALUES, PITKind
from seahorse.disclosure.types import MAX_FULL_BATCH
from seahorse.mcp.wire_schema import (
    BUILD_PIT_SCHEMA,
    DEFS,
    FORGET_SCHEMA,
    IMPROVE_SCHEMA,
    RECALL_FULL_SCHEMA,
    RECALL_SCHEMA,
    RECALL_TIMELINE_SCHEMA,
    REMEMBER_SCHEMA,
    TOOL_SCHEMAS,
    schema_for,
)


class TestToolRoster:
    def test_exactly_twelve_tools(self) -> None:
        # + skill_add, skill_show, and the deferred read-only facade tools
        # (freshness_view, audit_log, follow_supersedes_chain).
        assert set(TOOL_SCHEMAS) == {
            "remember",
            "recall",
            "recall_timeline",
            "recall_full",
            "improve",
            "forget",
            "build_pit",
            "skill_add",
            "skill_show",
            "freshness_view",
            "audit_log",
            "follow_supersedes_chain",
        }

    def test_all_schemas_have_additional_properties_false(self) -> None:
        for name, schema in TOOL_SCHEMAS.items():
            assert schema["additionalProperties"] is False, name

    def test_all_schemas_are_objects(self) -> None:
        for name, schema in TOOL_SCHEMAS.items():
            assert schema["type"] == "object", name

    def test_schema_for_inlines_defs(self) -> None:
        s = schema_for("remember")
        assert s["$defs"] is DEFS
        # and the original is untouched
        assert "$defs" not in REMEMBER_SCHEMA


class TestCognitiveTypeEnum:
    """6 canonical values + null, single source from constants."""

    def _cog_enum(self) -> list:
        return REMEMBER_SCHEMA["properties"]["cognitive_type"]["enum"]

    def test_six_f31_values_plus_null(self) -> None:
        enum = self._cog_enum()
        assert set(enum) == set(COGNITIVE_TYPES) | {None}
        assert len(enum) == 7

    def test_contains_active_values(self) -> None:
        enum = self._cog_enum()
        for v in ("episodic", "semantic", "social", "project_doc"):
            assert v in enum

    def test_contains_reserved_values(self) -> None:
        enum = self._cog_enum()
        for v in ("procedural", "working"):
            assert v in enum

    def test_does_not_contain_preference(self) -> None:
        assert "preference" not in self._cog_enum()

    def test_same_enum_across_tools(self) -> None:
        recall_cog = RECALL_SCHEMA["properties"]["cognitive_type"]["enum"]
        assert recall_cog == self._cog_enum()


class TestSourceTypeEnum:
    def test_four_values(self) -> None:
        enum = DEFS["Provenance"]["properties"]["source_type"]["enum"]
        assert set(enum) == set(SOURCE_TYPES)
        assert len(enum) == 4

    def test_contains_agent_human_importer_system(self) -> None:
        enum = DEFS["Provenance"]["properties"]["source_type"]["enum"]
        for v in ("agent", "human", "importer", "system"):
            assert v in enum


class TestProvenanceDef:
    def test_required_fields(self) -> None:
        assert set(DEFS["Provenance"]["required"]) == {"agent_id", "session_id", "source_type"}

    def test_additional_properties_false(self) -> None:
        assert DEFS["Provenance"]["additionalProperties"] is False

    def test_extraction_mode_enum_includes_consolidated(self) -> None:
        enum = DEFS["Provenance"]["properties"]["extraction_mode"]["enum"]
        assert set(enum) == {"skip", "llm", "consolidated", None}
        assert "llm_partial" not in enum

    def test_extraction_mode_enum_single_sourced_from_facade_literal(self) -> None:
        # The wire enum is single-sourced from the facade
        # ``ExtractionMode`` Literal (+ None for nullability), so a schema-value
        # change lives in one place — the two sister projections cannot drift.
        from typing import get_args

        from seahorse.facade.types import ExtractionMode

        enum = DEFS["Provenance"]["properties"]["extraction_mode"]["enum"]
        assert set(enum) - {None} == set(get_args(ExtractionMode))


class TestPITPointDef:
    def test_required_kind_and_t(self) -> None:
        assert set(DEFS["PITPoint"]["required"]) == {"kind", "t"}

    def test_kind_enum_only_two_axes(self) -> None:
        # The ``PITPoint.kind`` enum is single-sourced from ``PIT_KIND_VALUES``
        # (the PITKind Literal), NOT hardcoded — a future axis change lives in
        # one place. ``kind`` is required, so the enum carries no ``None``
        # (unlike the nullable loose ``pit_kind`` input field).
        assert DEFS["PITPoint"]["properties"]["kind"]["enum"] == sorted(PIT_KIND_VALUES)
        assert set(DEFS["PITPoint"]["properties"]["kind"]["enum"]) == set(PIT_KIND_VALUES)
        assert None not in DEFS["PITPoint"]["properties"]["kind"]["enum"]

    def test_t_is_date_time(self) -> None:
        assert DEFS["PITPoint"]["properties"]["t"]["format"] == "date-time"

    def test_additional_properties_false(self) -> None:
        assert DEFS["PITPoint"]["additionalProperties"] is False


class TestRememberSchema:
    def test_required_body_and_by(self) -> None:
        assert set(REMEMBER_SCHEMA["required"]) == {"body", "by"}

    def test_body_caps(self) -> None:
        body = REMEMBER_SCHEMA["properties"]["body"]
        assert body["minLength"] == 1
        from seahorse.constants import BODY_MAX_CHARS

        assert body["maxLength"] == BODY_MAX_CHARS

    def test_tags_max_items(self) -> None:
        from seahorse.constants import TAGS_MAX_ITEMS

        assert REMEMBER_SCHEMA["properties"]["tags"]["maxItems"] == TAGS_MAX_ITEMS

    def test_no_subject_property(self) -> None:
        assert "subject" not in REMEMBER_SCHEMA["properties"]

    def test_no_supersedes_property(self) -> None:
        assert "supersedes" not in REMEMBER_SCHEMA["properties"]

    def test_by_is_ref_to_provenance(self) -> None:
        assert REMEMBER_SCHEMA["properties"]["by"]["$ref"] == "#/$defs/Provenance"


class TestRecallSchema:
    def test_required_query(self) -> None:
        assert RECALL_SCHEMA["required"] == ["query"]

    def test_query_caps(self) -> None:
        from seahorse.constants import QUERY_MAX_CHARS

        q = RECALL_SCHEMA["properties"]["query"]
        assert q["minLength"] == 1
        assert q["maxLength"] == QUERY_MAX_CHARS

    def test_no_anchor_ep_id(self) -> None:
        assert "anchor_ep_id" not in RECALL_SCHEMA["properties"]

    def test_no_hops(self) -> None:
        assert "hops" not in RECALL_SCHEMA["properties"]

    def test_k_minimum_is_one(self) -> None:
        assert RECALL_SCHEMA["properties"]["k"]["minimum"] == 1

    def test_has_pit_inputs(self) -> None:
        for key in ("pit", "pit_kind", "pit_t"):
            assert key in RECALL_SCHEMA["properties"]


class TestRecallTimelineSchema:
    def test_required_anchor_ep_id(self) -> None:
        assert RECALL_TIMELINE_SCHEMA["required"] == ["anchor_ep_id"]

    def test_axis_enum_mvp0_plus_graph_bfs(self) -> None:
        # graph_bfs (the BFS axis, a later-release feature) is materialized;
        # created_at/valid_at stay out of the wire enum until their own
        # later-release materialization.
        enum = RECALL_TIMELINE_SCHEMA["properties"]["axis"]["enum"]
        assert set(enum) == {"supersedes_chain", "fact_id_scope", "graph_bfs"}

    def test_axis_no_unmaterialized_mvp1_values(self) -> None:
        enum = RECALL_TIMELINE_SCHEMA["properties"]["axis"]["enum"]
        assert "created_at" not in enum
        assert "valid_at" not in enum

    def test_hops_capped_to_max(self) -> None:
        hops = RECALL_TIMELINE_SCHEMA["properties"]["hops"]
        assert hops["minimum"] == 1
        assert hops["maximum"] == MAX_HOPS_MVP1


class TestRecallFullSchema:
    def test_required_ep_ids(self) -> None:
        assert RECALL_FULL_SCHEMA["required"] == ["ep_ids"]

    def test_ep_ids_max_items_is_max_full_batch(self) -> None:
        assert RECALL_FULL_SCHEMA["properties"]["ep_ids"]["maxItems"] == MAX_FULL_BATCH

    def test_ep_ids_min_items_one(self) -> None:
        assert RECALL_FULL_SCHEMA["properties"]["ep_ids"]["minItems"] == 1


class TestImproveSchema:
    def test_required_fields(self) -> None:
        assert set(IMPROVE_SCHEMA["required"]) == {"ep_id", "new_body", "by"}

    def test_reason_enum_no_decay(self) -> None:
        enum = IMPROVE_SCHEMA["properties"]["reason"]["enum"]
        assert set(enum) == {"contradiction", "correction", "merge", "revalidation"}
        assert "decay" not in enum

    def test_reason_not_required(self) -> None:
        # facade defaults reason to "correction"
        assert "reason" not in IMPROVE_SCHEMA["required"]

    def test_new_body_cap(self) -> None:
        from seahorse.constants import BODY_MAX_CHARS

        assert IMPROVE_SCHEMA["properties"]["new_body"]["maxLength"] == BODY_MAX_CHARS

    def test_reason_cap(self) -> None:
        from seahorse.constants import REASON_MAX_CHARS

        assert IMPROVE_SCHEMA["properties"]["reason"]["maxLength"] == REASON_MAX_CHARS


class TestForgetSchema:
    def test_required_fields(self) -> None:
        assert set(FORGET_SCHEMA["required"]) == {"ep_id", "reason", "by"}

    def test_no_now_property(self) -> None:
        # Backdating risk; not exposed to MCP agents
        assert "now" not in FORGET_SCHEMA["properties"]

    def test_reason_required_and_capped(self) -> None:
        from seahorse.constants import REASON_MAX_CHARS

        reason = FORGET_SCHEMA["properties"]["reason"]
        assert reason["minLength"] == 1
        assert reason["maxLength"] == REASON_MAX_CHARS

    def test_reason_is_freeform_string(self) -> None:
        # forget reason is NOT enum-constrained (unlike improve)
        assert "enum" not in FORGET_SCHEMA["properties"]["reason"]


class TestBuildPitSchema:
    def test_any_of_allows_pit_only(self) -> None:
        assert {"required": ["pit"]} in BUILD_PIT_SCHEMA["anyOf"]

    def test_any_of_allows_loose_pit_kind(self) -> None:
        # t is NOT required alongside pit_kind — the facade owns E_PIT_REQUIRES_T.
        assert {"required": ["pit_kind"]} in BUILD_PIT_SCHEMA["anyOf"]

    def test_uses_t_not_pit_t(self) -> None:
        # build_pit's loose timestamp field is "t" (not "pit_t")
        assert "t" in BUILD_PIT_SCHEMA["properties"]
        assert "pit_t" not in BUILD_PIT_SCHEMA["properties"]

    def test_pit_kind_enum_includes_null(self) -> None:
        enum = BUILD_PIT_SCHEMA["properties"]["pit_kind"]["enum"]
        assert set(enum) == {"state_at", "known_at", None}

    def test_pit_kind_enum_derived_from_contract(self) -> None:
        # Single-source: the wire enum (minus null) == the PITKind Literal args.
        # A future change to PITKind must flow here automatically — hardcoding
        # would silently drift.
        enum = BUILD_PIT_SCHEMA["properties"]["pit_kind"]["enum"]
        assert {v for v in enum if v is not None} == set(get_args(PITKind))


class TestWireCaps:
    """maxLength on every free-text field the engine persists verbatim.

    The wire is the DoS + receiver token-budget guard (constants.py docstring),
    so each free-text field needs a wire-level cap — otherwise a multi-megabyte
    agent_id passes wire-shape and is stored.
    """

    def test_tags_item_max_length(self) -> None:
        items = REMEMBER_SCHEMA["properties"]["tags"]["items"]
        assert items["maxLength"] == TAG_MAX_CHARS

    def test_provenance_id_fields_max_length(self) -> None:
        props = DEFS["Provenance"]["properties"]
        assert props["agent_id"]["maxLength"] == PROVENANCE_ID_MAX_CHARS
        assert props["session_id"]["maxLength"] == PROVENANCE_ID_MAX_CHARS

    def test_provenance_short_fields_max_length(self) -> None:
        props = DEFS["Provenance"]["properties"]
        for field in ("model_used", "prompt_hash", "importer_vendor", "source_record_id"):
            assert props[field]["maxLength"] == PROVENANCE_SHORT_MAX_CHARS, field

    def test_subject_filter_max_length(self) -> None:
        sf = RECALL_SCHEMA["properties"]["subject_filter"]
        assert sf["maxLength"] == SUBJECT_FILTER_MAX_CHARS

    def test_anchor_ep_id_max_length(self) -> None:
        assert RECALL_TIMELINE_SCHEMA["properties"]["anchor_ep_id"]["maxLength"] == EP_ID_MAX_CHARS

    def test_ep_id_max_length_improve_and_forget(self) -> None:
        assert IMPROVE_SCHEMA["properties"]["ep_id"]["maxLength"] == EP_ID_MAX_CHARS
        assert FORGET_SCHEMA["properties"]["ep_id"]["maxLength"] == EP_ID_MAX_CHARS

    def test_recall_full_ep_id_items_max_length(self) -> None:
        items = RECALL_FULL_SCHEMA["properties"]["ep_ids"]["items"]
        assert items["maxLength"] == EP_ID_MAX_CHARS