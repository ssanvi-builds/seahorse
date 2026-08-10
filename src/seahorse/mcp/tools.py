"""7 MCP tool handlers + dispatch (#13, MVP-0).

Each handler is the same three-step pipeline, with NO domain logic:
1. ``validate(args, schema_for(tool))`` — wire-shape only (raises
   ``WireShapeError`` → JSON-RPC ``-32602``; the facade is never touched on a
   wire-shape failure, so guards fire before any read).
2. ``deserialize`` — wire JSON → Python kwargs/payload + resolve PIT via
   ``facade.build_pit`` (the ONE facade call that owns PIT precedence + kind
   validation; #13 never replicates it).
3. ``facade.<method>(**kwargs)`` — delegate; ``serialize`` the result.

Delegation purity (R2 f5-13): #13 does not fuse, project, extract, derive
subject/fact_id, build supersedes, synthesize effective provenance in
``improve`` (the facade does), override ``now`` in ``forget`` (always
``now=None`` — the wire has no ``now``), or replicate ``MAX_FULL_BATCH`` (the
wire schema enforces it; #8 owns the runtime guard). Exceptions from the
facade are translated by ``errors.translate`` (Cat A / Cat B / generic).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from seahorse.facade.facade import MemoryFacade
from seahorse.mcp.deserialize import (
    build_provenance,
    build_remember_payload,
    extract_pit,
    parse_dt,
)
from seahorse.mcp.errors import translate
from seahorse.mcp.serialize import success_response
from seahorse.mcp.validate import validate
from seahorse.mcp.wire_schema import schema_for

_RequestId = Any


def _resolve_pit(facade: MemoryFacade, args: dict[str, Any], *, t_field: str) -> Any:
    """Extract the 3 PIT inputs and delegate precedence + kind validation.

    ``extract_pit`` is a pure codec (no facade); ``facade.build_pit`` owns the
    precedence (``pit`` wins over ``pit_kind``+``t``) and the kind validation
    (``InvalidPITKind`` / ``E_PIT_REQUIRES_T``). #13 never replicates either.
    """
    pit, pit_kind, t = extract_pit(args, t_field=t_field)
    return facade.build_pit(pit, pit_kind=pit_kind, t=t)


# ---------------------------------------------------------------------------
# Handlers — each: validate → deserialize → facade → serialize.
# ---------------------------------------------------------------------------


def handle_remember(facade: MemoryFacade, args: dict[str, Any], request_id: _RequestId) -> dict:
    validate(args, schema_for("remember"))
    payload = build_remember_payload(args)
    result = facade.remember(
        payload,
        skip_extraction=args.get("skip_extraction"),
        extraction_mode=args.get("extraction_mode"),
    )
    return success_response(request_id, result)


def handle_recall(facade: MemoryFacade, args: dict[str, Any], request_id: _RequestId) -> dict:
    validate(args, schema_for("recall"))
    pit = _resolve_pit(facade, args, t_field="pit_t")
    kwargs: dict[str, Any] = {"query": args["query"], "pit": pit}
    # k defaults to TOP_K in the facade — only override when the caller set it
    # (passing k=None would clobber the default with an invalid int).
    if args.get("k") is not None:
        kwargs["k"] = args["k"]
    if args.get("cognitive_type") is not None:
        kwargs["cognitive_type"] = args["cognitive_type"]
    if args.get("subject_filter") is not None:
        kwargs["subject_filter"] = args["subject_filter"]
    result = facade.recall(**kwargs)
    return success_response(request_id, result)


def handle_recall_timeline(
    facade: MemoryFacade, args: dict[str, Any], request_id: _RequestId
) -> dict:
    validate(args, schema_for("recall_timeline"))
    pit = _resolve_pit(facade, args, t_field="pit_t")
    axis = args.get("axis", "supersedes_chain")
    hops = args.get("hops", 1)
    result = facade.recall_timeline(args["anchor_ep_id"], axis=axis, pit=pit, hops=hops)
    return success_response(request_id, result)


def handle_recall_full(facade: MemoryFacade, args: dict[str, Any], request_id: _RequestId) -> dict:
    validate(args, schema_for("recall_full"))
    pit = _resolve_pit(facade, args, t_field="pit_t")
    result = facade.recall_full(args["ep_ids"], pit=pit)
    return success_response(request_id, result)


def handle_improve(facade: MemoryFacade, args: dict[str, Any], request_id: _RequestId) -> dict:
    validate(args, schema_for("improve"))
    by = build_provenance(args["by"])
    valid_at_raw = args.get("valid_at")
    # reason defaults to "correction" in the facade; mirror that explicitly so
    # the wire→facade boundary is observable (recording tests assert the value).
    reason = args.get("reason", "correction")
    result = facade.improve(
        args["ep_id"],
        args["new_body"],
        by=by,
        valid_at=parse_dt(valid_at_raw) if isinstance(valid_at_raw, str) else None,
        reason=reason,
    )
    return success_response(request_id, result)


def handle_forget(facade: MemoryFacade, args: dict[str, Any], request_id: _RequestId) -> dict:
    validate(args, schema_for("forget"))
    by = build_provenance(args["by"])
    # OQ #13 DECIDIDA: the wire has NO `now`; #13 never overrides the clock.
    # Passing now=None explicitly documents the decision and keeps the
    # delegation-purity invariant testable (recording asserts now is None,
    # never a caller-provided value).
    result = facade.forget(args["ep_id"], reason=args["reason"], by=by, now=None)
    return success_response(request_id, result)


def handle_build_pit(facade: MemoryFacade, args: dict[str, Any], request_id: _RequestId) -> dict:
    validate(args, schema_for("build_pit"))
    # build_pit's loose timestamp field is "t" (NOT "pit_t" — the wire differs).
    pit, pit_kind, t = extract_pit(args, t_field="t")
    result = facade.build_pit(pit, pit_kind=pit_kind, t=t)
    return success_response(request_id, result)


# ---------------------------------------------------------------------------
# Dispatch table + tool list (advertised via tools/list).
# ---------------------------------------------------------------------------

TOOL_HANDLERS: dict[str, Callable[[MemoryFacade, dict[str, Any], _RequestId], dict]] = {
    "remember": handle_remember,
    "recall": handle_recall,
    "recall_timeline": handle_recall_timeline,
    "recall_full": handle_recall_full,
    "improve": handle_improve,
    "forget": handle_forget,
    "build_pit": handle_build_pit,
}

TOOL_LIST: list[dict[str, Any]] = [
    {
        "name": "remember",
        "description": "Persist a fact (episodic memory write). Delegates to the "
        "write-path; MVP-0 uses the deterministic skip extraction path.",
        "inputSchema": schema_for("remember"),
    },
    {
        "name": "recall",
        "description": "Recall the INDEX level (vigente listing, no body). MVP-0: "
        "no ranking, no PIT (PIT recall is refused before any read).",
        "inputSchema": schema_for("recall"),
    },
    {
        "name": "recall_timeline",
        "description": "Recall the TIMELINE level for an anchor episode (no body). "
        "MVP-0 axes: supersedes_chain, fact_id_scope.",
        "inputSchema": schema_for("recall_timeline"),
    },
    {
        "name": "recall_full",
        "description": "Recall the FULL level (hydrates body). Batch capped at "
        "MAX_FULL_BATCH; PIT in FULL is not supported in MVP-0.",
        "inputSchema": schema_for("recall_full"),
    },
    {
        "name": "improve",
        "description": "Improve a fact (human edit): supersedes the target episode "
        "with a new body. The facade synthesizes the effective provenance.",
        "inputSchema": schema_for("improve"),
    },
    {
        "name": "forget",
        "description": "Forget a fact (soft-delete): sets invalid_at. The clock "
        "(`now`) is NOT exposed to the agent — the facade owns it.",
        "inputSchema": schema_for("forget"),
    },
    {
        "name": "build_pit",
        "description": "Build a point-in-time (PIT) carrier for the bi-temporal "
        "axes (state_at | known_at, never mixed). Returns the resolved PIT or null.",
        "inputSchema": schema_for("build_pit"),
    },
]


def dispatch(
    tool_name: str, args: dict[str, Any], facade: MemoryFacade, request_id: _RequestId
) -> dict:
    """Dispatch a ``tools/call`` to the handler and translate any exception.

    Wire-shape failures, facade errors (Cat A/B), and generic exceptions all
    become JSON-RPC error responses via ``translate``. A successful call
    returns a ``tools/call`` success result envelope.
    """
    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32601,
                "message": "Method not found",
                "data": {"unknown_tool": tool_name},
            },
        }
    try:
        return handler(facade, args, request_id)
    except Exception as exc:  # noqa: BLE001 — translate is the fail-loud boundary
        return translate(exc, request_id)


__all__ = [
    "TOOL_HANDLERS",
    "TOOL_LIST",
    "dispatch",
    "handle_remember",
    "handle_recall",
    "handle_recall_timeline",
    "handle_recall_full",
    "handle_improve",
    "handle_forget",
    "handle_build_pit",
]