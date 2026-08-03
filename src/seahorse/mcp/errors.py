"""JSON-RPC error translation for the MCP profile (#13).

Two categories per f5-13 §5.3 (R8: #13 only translates, it never invents
``SeahorseError`` codes):

- **Cat A — exceptions with a stable ``.code``** (``SeahorseError`` and its
  subclasses from #12, plus ``EngineError`` from #2): the ``code`` string is
  surfaced as ``data.seahorse_code`` and mapped to a JSON-RPC ``-32xxx`` code.
  The caller matches on ``seahorse_code``.
- **Cat B — propagated exceptions WITHOUT a stable code** (``FullBatchTooLarge``,
  ``PitFullNotSupported``, ``NotInMVP0`` from #8; ``InvalidationConflictError``,
  ``NotFound`` from #2; ``IntegrityError`` from #6): surfaced as
  ``data.exception_class`` (no synthetic ``seahorse_code`` — that would lie
  about a code the lower component does not own).
- **Wire-shape errors** (detected by #13 before the facade is touched):
  ``-32602`` with ``data.wire_shape_error`` and no ``seahorse_code`` — the
  request never reached #12, so a seahorse code would be wrong.
- **Generic fallback**: any uncatalogued ``Exception`` → ``-32603`` with
  ``exception_class`` (fail-loud, no swallow).

Drift reconciled vs f5-13 (which cites an idealized catalog): #13 maps the codes
#12 actually raises. ``E_INVALID_SOURCE_TYPE`` (f5-13) → ``E_MISSING_SOURCE_TYPE``
(real). ``E_INVALID_COGNITIVE_TYPE`` / ``E_VALID_AT_HUMAN_ONLY`` are NOT raised
by #12 in MVP-0 (the facade does not validate ``cognitive_type`` — that is
#13's wire-shape job — and does not guard agent ``valid_at``); they are kept
here only because the engine owns ``E_VALID_AT_HUMAN_ONLY`` and it can
propagate.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# ---------------------------------------------------------------------------
# Wire-shape error (raised by validate.py BEFORE the facade is called).
# ---------------------------------------------------------------------------


class WireShapeError(Exception):
    """Raised when JSON-RPC params fail wire-shape validation (Cat: wire).

    Carries ``field`` (dotted path, optional) and ``detail``. Translated to
    JSON-RPC ``-32602`` with ``data.wire_shape_error`` — NO ``seahorse_code``
    because the request never reached #12.
    """

    __slots__ = ("detail", "field")

    def __init__(self, detail: str, *, field: str | None = None) -> None:
        self.detail = detail
        self.field = field
        super().__init__(f"{field or '<root>'}: {detail}")


# ---------------------------------------------------------------------------
# Cat A — code → JSON-RPC code (server-defined -32000..-32099).
# ---------------------------------------------------------------------------

# Facade codes (8) — the codes #12 actually raises in MVP-0.
_CAT_A_FACADE = {
    "E_EMPTY_BODY": -32001,
    "E_MISSING_SOURCE_TYPE": -32002,
    "E_INVALID_EXTRACTION_MODE": -32003,
    "E_EMPTY_QUERY": -32004,
    "E_INVALID_PIT_KIND": -32005,
    "E_PIT_REQUIRES_T": -32006,
    "E_PIT_RECALL_MVP_0": -32007,
    "E_NOT_IN_MVP_0_1": -32008,
}

# Engine codes (propagated via EngineError; they carry ``.code``).
_CAT_A_ENGINE = {
    "E_COLLISION_EXISTS": -32009,
    "E_PENDING_CANNOT_INVALIDATE": -32010,
    "E_DANGLING_SUPERSEDES": -32011,
    "E_SKIP_CONTRACT_VIOLATED": -32012,
    "E_NOT_IN_MVP_0": -32013,
    "E_VALID_AT_HUMAN_ONLY": -32014,
    "E_EXPIRED_AT_NON_NULL": -32015,
    "E_CREATED_AT_ENGINE_OWNED": -32016,
    "E_MONOTONICITY_VIOLATED": -32017,
}

# Frontmatter codes (4) — owned by #3 (commit 5), mirrored in the CLI sister
# projection (cli/exit_codes.py) as exit codes 90–93. ``#13`` does not currently
# surface frontmatter errors (the MCP tools do not call the frontmatter codec),
# but the codes are mirrored here so the two sister projections share a single
# point of change — a future MCP surface that surfaces a frontmatter error
# already has a stable ``-32xxx`` code.
_CAT_A_FRONTMATTER = {
    "E_FRONTMATTER_INVALID": -32018,
    "E_MIGRATION_ABORTED": -32019,
    "E_X_RESERVED_COLLISION": -32020,
    "E_SUBJECT_EMPTY": -32021,
}

CAT_A: dict[str, int] = {**_CAT_A_FACADE, **_CAT_A_ENGINE, **_CAT_A_FRONTMATTER}

# ---------------------------------------------------------------------------
# Cat B — exception_class → JSON-RPC code (no stable code).
# ---------------------------------------------------------------------------

CAT_B: dict[str, int] = {
    "FullBatchTooLarge": -32602,  # invalid params: len > MAX_FULL_BATCH
    "PitFullNotSupported": -32050,  # server error: full + pit MVP-0
    "NotInMVP0": -32602,  # invalid params: axis not in MVP-0 set
    # State conflict (already-in-state), NOT an implementation bug — sits in the
    # server-defined band next to NotFound (-32052), not on -32603 (Internal).
    "InvalidationConflictError": -32051,
    "NotFound": -32052,  # server error: no vigente to mutate
    "IntegrityError": -32603,  # internal: storage constraint
}

# ---------------------------------------------------------------------------
# Component-of-origin attribution for ``data.component``.
# ---------------------------------------------------------------------------

_ORIGIN_BY_CLASS = {
    # SeahorseError subclasses → #12
    "SeahorseError": "#12",
    "InvalidPITKind": "#12",
    "PitRecallNotSupportedMVP0": "#12",
    "EmptyQueryError": "#12",
    # #11 retrieval — plain Exception (no .code), raised at the recall entrypoint
    # on an unknown pit.kind. Distinct __name__ from #12's InvalidPITKind (C8.6).
    "RetrievalInvalidPITKind": "#11",
    # EngineError → #2
    "EngineError": "#2",
    # disclosure exceptions → #8
    "FullBatchTooLarge": "#8",
    "PitFullNotSupported": "#8",
    "NotInMVP0": "#8",
    # engine contracts → #2
    "NotFound": "#2",
    "InvalidationConflictError": "#2",
    # storage → #6
    "IntegrityError": "#6",
    # frontmatter (#3, commit 5)
    "FrontmatterInvalid": "#3",
    "MigrationError": "#3",
    "XReservedCollision": "#3",
    "SubjectEmpty": "#3",
}


def _origin_of(exc: BaseException) -> str:
    cls = type(exc).__name__
    for name, comp in _ORIGIN_BY_CLASS.items():
        if cls == name:
            return comp
    # EngineError subclasses (if any) carry .code → #2
    if hasattr(exc, "code") and hasattr(exc, "context"):
        return "#2"
    return "#13"


def _rpc_error(
    request_id: Any, code: int, message: str, data: Mapping[str, Any] | None
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = dict(data)
    return {"jsonrpc": "2.0", "id": request_id, "error": err}


def translate(exc: BaseException, request_id: Any) -> dict[str, Any]:
    """Translate a raised exception into a JSON-RPC error response."""
    # Wire-shape error (Cat: wire) — detected by #13, never reached #12.
    if isinstance(exc, WireShapeError):
        data: dict[str, Any] = {
            "wire_shape_error": True,
            "detail": exc.detail,
            "component": "#13",
        }
        if exc.field is not None:
            data["field"] = exc.field
        return _rpc_error(request_id, -32602, "Invalid params", data)

    # Cat A — has a stable ``.code`` (SeahorseError or EngineError).
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in CAT_A:
        return _rpc_error(
            request_id,
            CAT_A[code],
            _message_for(code),
            {
                "seahorse_code": code,
                "detail": _detail_of(exc),
                "component": _origin_of(exc),
            },
        )

    # Cat B — exception class without a stable code.
    cls = type(exc).__name__
    if cls in CAT_B:
        return _rpc_error(
            request_id,
            CAT_B[cls],
            _message_for_class(cls),
            {
                "exception_class": cls,
                "detail": str(exc),
                "component": _origin_of(exc),
            },
        )

    # Generic fallback — fail-loud, no swallow, no synthetic code. The component
    # is resolved via ``_origin_of`` (C8.6 [24]): a plain-Exception class that IS
    # in ``_ORIGIN_BY_CLASS`` — e.g. #11's ``RetrievalInvalidPITKind`` (no ``.code``,
    # not in CAT_B) — now attributes to its real owner instead of being masked as
    # ``#13``. Unknown classes still fall back to ``#13``.
    return _rpc_error(
        request_id,
        -32603,
        "Internal error",
        {"exception_class": cls, "detail": str(exc), "component": _origin_of(exc)},
    )


def wire_shape_response(request_id: Any, field: str | None, detail: str) -> dict[str, Any]:
    """Build a JSON-RPC wire-shape error response directly (for handler use)."""
    data: dict[str, Any] = {"wire_shape_error": True, "detail": detail, "component": "#13"}
    if field is not None:
        data["field"] = field
    return _rpc_error(request_id, -32602, "Invalid params", data)


_MESSAGE_BY_CODE = {
    "E_EMPTY_BODY": "Empty body",
    "E_MISSING_SOURCE_TYPE": "Missing source type",
    "E_INVALID_EXTRACTION_MODE": "Invalid extraction mode",
    "E_EMPTY_QUERY": "Empty query",
    "E_INVALID_PIT_KIND": "Invalid PIT kind",
    "E_PIT_REQUIRES_T": "PIT requires t",
    "E_PIT_RECALL_MVP_0": "PIT recall not supported in MVP-0",
    "E_NOT_IN_MVP_0_1": "Primitive not in MVP-0/MVP-1",
    "E_COLLISION_EXISTS": "Collision exists",
    "E_PENDING_CANNOT_INVALIDATE": "PENDING cannot invalidate",
    "E_DANGLING_SUPERSEDES": "Dangling supersedes",
    "E_SKIP_CONTRACT_VIOLATED": "Skip contract violated",
    "E_NOT_IN_MVP_0": "Not in MVP-0",
    "E_VALID_AT_HUMAN_ONLY": "valid_at human-only",
    "E_EXPIRED_AT_NON_NULL": "expired_at non-null",
    "E_CREATED_AT_ENGINE_OWNED": "created_at engine-owned",
    "E_MONOTONICITY_VIOLATED": "Monotonicity violated",
    # frontmatter (#3, commit 5)
    "E_FRONTMATTER_INVALID": "Frontmatter invalid",
    "E_MIGRATION_ABORTED": "Migration aborted",
    "E_X_RESERVED_COLLISION": "X reserved collision",
    "E_SUBJECT_EMPTY": "Subject empty",
}

_MESSAGE_BY_CLASS = {
    "FullBatchTooLarge": "Full batch too large",
    "PitFullNotSupported": "PIT full not supported in MVP-0",
    "NotInMVP0": "Timeline axis not in MVP-0",
    "InvalidationConflictError": "Invalidation conflict",
    "NotFound": "Not found",
    "IntegrityError": "Storage integrity error",
}


def _message_for(code: str) -> str:
    return _MESSAGE_BY_CODE.get(code, "Seahorse error")


def _message_for_class(cls: str) -> str:
    return _MESSAGE_BY_CLASS.get(cls, "Internal error")


def _detail_of(exc: BaseException) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str):
        return detail
    return str(exc)


__all__ = [
    "WireShapeError",
    "CAT_A",
    "CAT_B",
    "translate",
    "wire_shape_response",
]