"""JSON Schema wire shapes for the MCP tools.

Source of truth for both ``tools/list`` (the ``inputSchema`` advertised to
clients) and ``validate.py`` (wire-shape enforcement before the facade is
touched). Keeping the schema as the single source means the advertised
contract and the enforced contract cannot drift.

The wire enums follow the current model:
- ``cognitive_type`` enum = the 6 canonical values (``episodic``, ``semantic``,
  ``social``, ``project_doc`` active + ``procedural``, ``working`` reserved)
  + ``null`` — derived from ``seahorse.constants.COGNITIVE_TYPES``.
- ``source_type`` enum = the 4 values from ``SOURCE_TYPES``.
- ``extraction_mode`` enum = ``["skip","llm","consolidated",null]`` —
  ``consolidated`` is schema-valid (a round-trippable marker for batch
  distillation) but NOT routable by single-episode ingestion (the facade
  refuses it with ``E_INVALID_EXTRACTION_MODE``); ``llm_partial`` stays
  RESERVED and rejected at wire-shape.
- ``reason`` enum has NO ``decay`` (mediated via the omitted ``expire``, a
  medium-term goal).
- ``recall`` has NO ``anchor_ep_id``/``hops`` in the first release (rejected
  by ``additionalProperties: false``).
- ``forget`` has NO ``now`` (backdating risk — not exposed to MCP agents).
- ``recall_full.ep_ids`` ``maxItems = MAX_FULL_BATCH`` (wire-level REJECT —
  ``FullBatchTooLarge`` is a progressive-disclosure exception without a stable
  code).
"""

from __future__ import annotations

from typing import Any, get_args

from seahorse.constants import (
    BODY_MAX_CHARS,
    COGNITIVE_TYPES,
    EP_ID_MAX_CHARS,
    PROVENANCE_ID_MAX_CHARS,
    PROVENANCE_SHORT_MAX_CHARS,
    QUERY_MAX_CHARS,
    REASON_MAX_CHARS,
    SOURCE_TYPES,
    SUBJECT_FILTER_MAX_CHARS,
    TAG_MAX_CHARS,
    TAGS_MAX_ITEMS,
)
from seahorse.contracts.index import MAX_HOPS_MVP1, PIT_KIND_VALUES
from seahorse.disclosure.types import MAX_FULL_BATCH, SUMMARY_MAX_CHARS
from seahorse.facade.types import ExtractionMode

# The canonical cognitive_type enum (6 values + null). Single source: constants.
_COGNITIVE_ENUM: list[Any] = sorted(COGNITIVE_TYPES) + [None]
_SOURCE_ENUM: list[Any] = sorted(SOURCE_TYPES)

# PIT kinds (the two axes are never mixed). Single source: the PITKind
# Literal via PIT_KIND_VALUES — a future kind change lives in one place.
# ``_PIT_KIND_ENUM`` carries ``None`` for the nullable loose input fields
# (``pit_kind``); ``_PIT_KIND_REQUIRED_ENUM`` is the non-nullable ``PITPoint.kind``
# enum (``kind`` is required in the $def, so ``None`` is not a valid value).
_PIT_KIND_ENUM: list[Any] = sorted(PIT_KIND_VALUES) + [None]
_PIT_KIND_REQUIRED_ENUM: list[Any] = sorted(PIT_KIND_VALUES)
# The canonical extraction_mode enum (skip/llm/consolidated + null). Single
# source: the facade ``ExtractionMode`` Literal — a schema-value change lives
# in one place. ``consolidated`` is schema-valid but NOT routable by
# single-episode ingestion; ``llm_partial`` stays reserved.
_EXTRACTION_MODE_ENUM: list[Any] = sorted(get_args(ExtractionMode)) + [None]
_REASON_ENUM: list[Any] = ["contradiction", "correction", "merge", "revalidation"]
_AXIS_ENUM: list[Any] = ["supersedes_chain", "fact_id_scope", "graph_bfs"]

# ---------------------------------------------------------------------------
# $defs — shared JSON Schema reusables.
# ---------------------------------------------------------------------------

DEFS: dict[str, dict[str, Any]] = {
    "PITPoint": {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "t"],
        "properties": {
            "kind": {"type": "string", "enum": _PIT_KIND_REQUIRED_ENUM},
            "t": {"type": "string", "format": "date-time"},
        },
    },
    "Provenance": {
        "type": "object",
        "additionalProperties": False,
        "required": ["agent_id", "session_id", "source_type"],
        "properties": {
            "agent_id": {"type": "string", "maxLength": PROVENANCE_ID_MAX_CHARS},
            "session_id": {"type": "string", "maxLength": PROVENANCE_ID_MAX_CHARS},
            "source_type": {"type": "string", "enum": _SOURCE_ENUM},
            "extraction_mode": {"type": ["string", "null"], "enum": _EXTRACTION_MODE_ENUM},
            "model_used": {
                "type": ["string", "null"],
                "maxLength": PROVENANCE_SHORT_MAX_CHARS,
            },
            "prompt_hash": {
                "type": ["string", "null"],
                "maxLength": PROVENANCE_SHORT_MAX_CHARS,
            },
            "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "importer_vendor": {
                "type": ["string", "null"],
                "maxLength": PROVENANCE_SHORT_MAX_CHARS,
            },
            "source_record_id": {
                "type": ["string", "null"],
                "maxLength": PROVENANCE_SHORT_MAX_CHARS,
            },
        },
    },
}

# A reusable PIT-input fragment (pit object OR loose pit_kind + pit_t).
# ``pit`` wins over ``pit_kind``+``pit_t`` at the FACADE layer (facade.build_pit
# precedence, invoked by the handlers), NOT at the wire or deserialize layer.
_PIT_INPUT_PROPS: dict[str, Any] = {
    "pit": {"oneOf": [{"$ref": "#/$defs/PITPoint"}, {"type": "null"}]},
    "pit_kind": {"type": ["string", "null"], "enum": _PIT_KIND_ENUM},
    "pit_t": {"type": ["string", "null"], "format": "date-time"},
}


# ---------------------------------------------------------------------------
# Tool input schemas.
# ---------------------------------------------------------------------------

REMEMBER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["body", "by"],
    "properties": {
        "body": {"type": "string", "minLength": 1, "maxLength": BODY_MAX_CHARS},
        "by": {"$ref": "#/$defs/Provenance"},
        "valid_at": {"type": ["string", "null"], "format": "date-time"},
        "cognitive_type": {"type": ["string", "null"], "enum": _COGNITIVE_ENUM},
        "summary": {"type": ["string", "null"], "maxLength": SUMMARY_MAX_CHARS},
        "tags": {
            "type": "array",
            "items": {"type": "string", "maxLength": TAG_MAX_CHARS},
            "maxItems": TAGS_MAX_ITEMS,
        },
        "skip_extraction": {"type": ["boolean", "null"]},
        "extraction_mode": {"type": ["string", "null"], "enum": _EXTRACTION_MODE_ENUM},
    },
}

RECALL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query"],
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": QUERY_MAX_CHARS},
        "k": {"type": "integer", "minimum": 1},
        "cognitive_type": {"type": ["string", "null"], "enum": _COGNITIVE_ENUM},
        "subject_filter": {"type": ["string", "null"], "maxLength": SUBJECT_FILTER_MAX_CHARS},
        **_PIT_INPUT_PROPS,
    },
}

RECALL_TIMELINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["anchor_ep_id"],
    "properties": {
        "anchor_ep_id": {"type": "string", "minLength": 1, "maxLength": EP_ID_MAX_CHARS},
        "axis": {"type": "string", "enum": _AXIS_ENUM},
        "hops": {"type": "integer", "minimum": 1, "maximum": MAX_HOPS_MVP1},
        **_PIT_INPUT_PROPS,
    },
}

RECALL_FULL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ep_ids"],
    "properties": {
        "ep_ids": {
            "type": "array",
            "items": {"type": "string", "maxLength": EP_ID_MAX_CHARS},
            "minItems": 1,
            "maxItems": MAX_FULL_BATCH,
        },
        **_PIT_INPUT_PROPS,
    },
}

IMPROVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ep_id", "new_body", "by"],
    "properties": {
        "ep_id": {"type": "string", "minLength": 1, "maxLength": EP_ID_MAX_CHARS},
        "new_body": {"type": "string", "minLength": 1, "maxLength": BODY_MAX_CHARS},
        "by": {"$ref": "#/$defs/Provenance"},
        "valid_at": {"type": ["string", "null"], "format": "date-time"},
        "reason": {
            "type": "string",
            "enum": _REASON_ENUM,
            "minLength": 1,
            "maxLength": REASON_MAX_CHARS,
        },
    },
}

FORGET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ep_id", "reason", "by"],
    "properties": {
        "ep_id": {"type": "string", "minLength": 1, "maxLength": EP_ID_MAX_CHARS},
        "reason": {"type": "string", "minLength": 1, "maxLength": REASON_MAX_CHARS},
        "by": {"$ref": "#/$defs/Provenance"},
    },
}

BUILD_PIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    # At least one of: a pit object, a pit_kind (with optional t), or all-null.
    # t is NOT required alongside pit_kind at the wire — the facade owns the
    # "pit_kind requires t" invariant (E_PIT_REQUIRES_T, t-before-kind). Forcing
    # it here would pre-empt the facade's fail-loud and break delegation purity.
    "anyOf": [
        {"required": ["pit"]},
        {"required": ["pit_kind"]},
        {"properties": {"pit": {"type": "null"}, "pit_kind": {"type": "null"}}},
    ],
    "properties": {
        "pit": {"oneOf": [{"$ref": "#/$defs/PITPoint"}, {"type": "null"}]},
        "pit_kind": {"type": ["string", "null"], "enum": _PIT_KIND_ENUM},
        "t": {"type": ["string", "null"], "format": "date-time"},
    },
}

# Procedural skills + deferred read-only facade tools.
SKILL_ADD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["body", "by"],
    "properties": {
        "body": {"type": "string", "minLength": 1, "maxLength": BODY_MAX_CHARS},
        "by": {"$ref": "#/$defs/Provenance"},
        "title": {"type": ["string", "null"], "maxLength": SUBJECT_FILTER_MAX_CHARS},
        "trigger": {"type": ["string", "null"], "maxLength": QUERY_MAX_CHARS},
        "scope": {"type": ["string", "null"], "maxLength": QUERY_MAX_CHARS},
        "version": {"type": ["string", "null"], "maxLength": QUERY_MAX_CHARS},
    },
}

SKILL_SHOW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ep_id"],
    "properties": {
        "ep_id": {"type": "string", "minLength": 1, "maxLength": EP_ID_MAX_CHARS},
        "min_trust": {"type": ["string", "null"], "enum": ["low", "medium", "high"]},
    },
}

SKILL_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [],
    "properties": {
        "top_k": {"type": "integer", "minimum": 1},
    },
}

SKILL_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query"],
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": QUERY_MAX_CHARS},
        "top_k": {"type": "integer", "minimum": 1},
    },
}

FRESHNESS_VIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ep_id"],
    "properties": {
        "ep_id": {"type": "string", "minLength": 1, "maxLength": EP_ID_MAX_CHARS},
    },
}

AUDIT_LOG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ep_id"],
    "properties": {
        "ep_id": {"type": "string", "minLength": 1, "maxLength": EP_ID_MAX_CHARS},
    },
}

FOLLOW_SUPERSEDES_CHAIN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ep_id"],
    "properties": {
        "ep_id": {"type": "string", "minLength": 1, "maxLength": EP_ID_MAX_CHARS},
    },
}


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "remember": REMEMBER_SCHEMA,
    "recall": RECALL_SCHEMA,
    "recall_timeline": RECALL_TIMELINE_SCHEMA,
    "recall_full": RECALL_FULL_SCHEMA,
    "improve": IMPROVE_SCHEMA,
    "forget": FORGET_SCHEMA,
    "build_pit": BUILD_PIT_SCHEMA,
    "skill_add": SKILL_ADD_SCHEMA,
    "skill_show": SKILL_SHOW_SCHEMA,
    "skill_list": SKILL_LIST_SCHEMA,
    "skill_search": SKILL_SEARCH_SCHEMA,
    "freshness_view": FRESHNESS_VIEW_SCHEMA,
    "audit_log": AUDIT_LOG_SCHEMA,
    "follow_supersedes_chain": FOLLOW_SUPERSEDES_CHAIN_SCHEMA,
}


def schema_for(tool_name: str) -> dict[str, Any]:
    """Return the inputSchema for a tool, with ``$defs`` inlined for advertise."""
    if tool_name not in TOOL_SCHEMAS:
        raise KeyError(tool_name)
    schema = dict(TOOL_SCHEMAS[tool_name])
    schema["$defs"] = DEFS
    return schema


__all__ = [
    "DEFS",
    "REMEMBER_SCHEMA",
    "RECALL_SCHEMA",
    "RECALL_TIMELINE_SCHEMA",
    "RECALL_FULL_SCHEMA",
    "IMPROVE_SCHEMA",
    "FORGET_SCHEMA",
    "BUILD_PIT_SCHEMA",
    "SKILL_ADD_SCHEMA",
    "SKILL_SHOW_SCHEMA",
    "SKILL_LIST_SCHEMA",
    "SKILL_SEARCH_SCHEMA",
    "FRESHNESS_VIEW_SCHEMA",
    "AUDIT_LOG_SCHEMA",
    "FOLLOW_SUPERSEDES_CHAIN_SCHEMA",
    "TOOL_SCHEMAS",
    "schema_for",
]