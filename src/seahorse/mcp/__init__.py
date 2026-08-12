"""#13 MCP Profile ``io.seahorse.memory/v1`` — JSON-RPC 2.0 over stdio.

A thin translation layer: it exposes the 4 memory-native primitives of #12
(plus the 3 progressive-disclosure reads) as MCP tools with IDENTICAL
semantics, and owns ONLY wire-shape validation + JSON↔Python translation. No
domain logic lives here (R2 f5-13 §0): no fusion (#11), no projection (#8), no
extraction (#5/#4), no subject/fact_id derivation, no supersedes construction,
no effective-provenance synthesis in ``improve``, no ``now`` override in
``forget``, no ``MAX_FULL_BATCH`` replication.

Transport: stdlib JSON-RPC 2.0 over stdio (newline-delimited), NOT the
``mcp``/FastMCP SDK. The code repo is stdlib-only (``dependencies = []``) and
``main`` is clone-and-run; the SDK drags pydantic+httpx+anyio+starlette+uvicorn.
MCP is JSON-RPC 2.0 standard and SDK-independent. MCP spec pinned
``2025-11-25``.

Tool surface (12 tools): the 7 memory primitives (``remember``, ``recall``,
``recall_timeline``, ``recall_full``, ``improve``, ``forget``, ``build_pit``)
plus 5 procedural / read-only tools (``skill_add``, ``skill_show``,
``freshness_view``, ``audit_log``, ``follow_supersedes_chain``).
``expire``/``revalidate`` remain deferred (MVP-1 / mediano).
"""

from __future__ import annotations

from seahorse.mcp.deserialize import (
    build_provenance,
    build_remember_payload,
    extract_pit,
    parse_dt,
    parse_pit_point,
)
from seahorse.mcp.errors import CAT_A, CAT_B, WireShapeError, translate, wire_shape_response
from seahorse.mcp.profile import PROFILE_URI, build_server, handle_request, serve
from seahorse.mcp.serialize import (
    _iso_z,
    success_response,
    to_json,
    to_text_content,
    to_wire,
)
from seahorse.mcp.tools import TOOL_HANDLERS, TOOL_LIST, dispatch
from seahorse.mcp.validate import validate
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

__all__ = [
    # profile / server
    "PROFILE_URI",
    "build_server",
    "handle_request",
    "serve",
    # tools
    "TOOL_HANDLERS",
    "TOOL_LIST",
    "dispatch",
    # wire schema
    "DEFS",
    "REMEMBER_SCHEMA",
    "RECALL_SCHEMA",
    "RECALL_TIMELINE_SCHEMA",
    "RECALL_FULL_SCHEMA",
    "IMPROVE_SCHEMA",
    "FORGET_SCHEMA",
    "BUILD_PIT_SCHEMA",
    "TOOL_SCHEMAS",
    "schema_for",
    # validate
    "validate",
    # serialize
    "to_wire",
    "to_json",
    "to_text_content",
    "success_response",
    "_iso_z",
    # deserialize
    "parse_dt",
    "parse_pit_point",
    "extract_pit",
    "build_provenance",
    "build_remember_payload",
    # errors
    "WireShapeError",
    "CAT_A",
    "CAT_B",
    "translate",
    "wire_shape_response",
]