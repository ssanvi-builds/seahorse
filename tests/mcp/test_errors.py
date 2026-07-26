"""Tests for JSON-RPC error translation (#13) — Cat A, Cat B, wire, fallback."""

from __future__ import annotations

import sqlite3

from seahorse.contracts.engine import InvalidationConflictError, NotFound
from seahorse.disclosure.types import FullBatchTooLarge, NotInMVP0, PitFullNotSupported
from seahorse.engine.errors import EngineError
from seahorse.facade.errors import (
    E_EMPTY_BODY,
    E_EMPTY_QUERY,
    E_INVALID_EXTRACTION_MODE,
    E_INVALID_PIT_KIND,
    E_MISSING_SOURCE_TYPE,
    E_NOT_IN_MVP_0_1,
    E_PIT_RECALL_MVP_0,
    E_PIT_REQUIRES_T,
    EmptyQueryError,
    InvalidPITKind,
    PitRecallNotSupportedMVP0,
    SeahorseError,
)
from seahorse.mcp.errors import CAT_A, CAT_B, WireShapeError, translate, wire_shape_response


def _err(exc: Exception, request_id: object = 1) -> dict:
    return translate(exc, request_id)


class TestCatAFacade:
    """Each facade code → its JSON-RPC -32xxx + data.seahorse_code."""

    def test_empty_body(self) -> None:
        resp = _err(SeahorseError(code=E_EMPTY_BODY, detail="x"))
        assert resp["error"]["code"] == CAT_A[E_EMPTY_BODY]
        assert resp["error"]["data"]["seahorse_code"] == E_EMPTY_BODY
        assert resp["error"]["data"]["component"] == "#12"

    def test_missing_source_type(self) -> None:
        resp = _err(SeahorseError(code=E_MISSING_SOURCE_TYPE, detail="x"))
        assert resp["error"]["data"]["seahorse_code"] == E_MISSING_SOURCE_TYPE

    def test_invalid_extraction_mode(self) -> None:
        resp = _err(SeahorseError(code=E_INVALID_EXTRACTION_MODE, detail="x"))
        assert resp["error"]["data"]["seahorse_code"] == E_INVALID_EXTRACTION_MODE

    def test_empty_query_subclass(self) -> None:
        resp = _err(EmptyQueryError())
        assert resp["error"]["data"]["seahorse_code"] == E_EMPTY_QUERY

    def test_invalid_pit_kind_subclass(self) -> None:
        resp = _err(InvalidPITKind("bogus"))
        assert resp["error"]["data"]["seahorse_code"] == E_INVALID_PIT_KIND

    def test_pit_requires_t(self) -> None:
        resp = _err(SeahorseError(code=E_PIT_REQUIRES_T, detail="x"))
        assert resp["error"]["data"]["seahorse_code"] == E_PIT_REQUIRES_T

    def test_pit_recall_mvp0_subclass(self) -> None:
        resp = _err(PitRecallNotSupportedMVP0())
        assert resp["error"]["data"]["seahorse_code"] == E_PIT_RECALL_MVP_0

    def test_not_in_mvp_0_1(self) -> None:
        resp = _err(SeahorseError(code=E_NOT_IN_MVP_0_1, detail="x"))
        assert resp["error"]["data"]["seahorse_code"] == E_NOT_IN_MVP_0_1


class TestCatAEngine:
    """EngineError(code) propagates with seahorse_code, component #2."""

    def test_collision_exists(self) -> None:
        resp = _err(EngineError("E_COLLISION_EXISTS", subject="s"))
        assert resp["error"]["code"] == CAT_A["E_COLLISION_EXISTS"]
        assert resp["error"]["data"]["seahorse_code"] == "E_COLLISION_EXISTS"
        assert resp["error"]["data"]["component"] == "#2"

    def test_pending_cannot_invalidate(self) -> None:
        resp = _err(EngineError("E_PENDING_CANNOT_INVALIDATE"))
        assert resp["error"]["data"]["seahorse_code"] == "E_PENDING_CANNOT_INVALIDATE"
        assert resp["error"]["data"]["component"] == "#2"

    def test_engine_error_message_present(self) -> None:
        resp = _err(EngineError("E_DANGLING_SUPERSEDES", ep_id="e1"))
        assert "message" in resp["error"]


class TestCatAFrontmatter:
    """The 4 frontmatter codes (#3, commit 5) mirror the CLI table (parity).

    ``#13`` does not currently surface frontmatter errors (the MCP tools do not
    call the frontmatter codec), but the codes are mirrored here so the two
    sister projections share a single point of change — a future MCP surface
    that surfaces a frontmatter error already has a stable ``-32xxx`` code.
    """

    def test_frontmatter_invalid(self) -> None:
        from pathlib import Path

        from seahorse.frontmatter.errors import FrontmatterInvalid

        resp = _err(FrontmatterInvalid(Path("/n.md"), ValueError("bad")))
        assert resp["error"]["code"] == CAT_A["E_FRONTMATTER_INVALID"] == -32018
        assert resp["error"]["data"]["seahorse_code"] == "E_FRONTMATTER_INVALID"
        assert resp["error"]["data"]["component"] == "#3"

    def test_migration_aborted(self) -> None:
        from seahorse.frontmatter.errors import MigrationError

        resp = _err(MigrationError("boom"))
        assert resp["error"]["code"] == CAT_A["E_MIGRATION_ABORTED"] == -32019
        assert resp["error"]["data"]["component"] == "#3"

    def test_x_reserved_collision(self) -> None:
        from pathlib import Path

        from seahorse.frontmatter.errors import XReservedCollision

        resp = _err(XReservedCollision(Path("/n.md"), "x-valid-at"))
        assert resp["error"]["code"] == CAT_A["E_X_RESERVED_COLLISION"] == -32020
        assert resp["error"]["data"]["component"] == "#3"

    def test_subject_empty(self) -> None:
        from pathlib import Path

        from seahorse.frontmatter.errors import SubjectEmpty

        resp = _err(SubjectEmpty(Path("/n.md")))
        assert resp["error"]["code"] == CAT_A["E_SUBJECT_EMPTY"] == -32021
        assert resp["error"]["data"]["component"] == "#3"


class TestCatB:
    """Propagated exceptions without a stable code → exception_class, no synthetic code."""

    def test_full_batch_too_large(self) -> None:
        resp = _err(FullBatchTooLarge(10, 5))
        assert resp["error"]["code"] == CAT_B["FullBatchTooLarge"]
        assert resp["error"]["data"]["exception_class"] == "FullBatchTooLarge"
        assert "seahorse_code" not in resp["error"]["data"]
        assert resp["error"]["data"]["component"] == "#8"

    def test_pit_full_not_supported(self) -> None:
        resp = _err(PitFullNotSupported())
        assert resp["error"]["code"] == CAT_B["PitFullNotSupported"]
        assert resp["error"]["data"]["exception_class"] == "PitFullNotSupported"

    def test_not_in_mvp0(self) -> None:
        resp = _err(NotInMVP0("created_at"))
        assert resp["error"]["data"]["exception_class"] == "NotInMVP0"

    def test_not_found(self) -> None:
        resp = _err(NotFound("ep-1"))
        assert resp["error"]["code"] == CAT_B["NotFound"]
        assert resp["error"]["data"]["exception_class"] == "NotFound"
        assert resp["error"]["data"]["component"] == "#2"

    def test_invalidation_conflict(self) -> None:
        resp = _err(InvalidationConflictError("ep-1"))
        assert resp["error"]["code"] == CAT_B["InvalidationConflictError"]
        assert resp["error"]["data"]["component"] == "#2"

    def test_invalidation_conflict_is_state_conflict_not_internal(self) -> None:
        # InvalidationConflictError is a legitimate state conflict (already
        # invalidated), NOT an implementation bug — so it sits in the
        # server-defined band (-32051), not on -32603 (Internal error).
        resp = _err(InvalidationConflictError("ep-1"))
        assert resp["error"]["code"] == -32051
        assert resp["error"]["code"] != -32603

    def test_integrity_error_sqlite3(self) -> None:
        # stdlib sqlite3.IntegrityError is what #6 actually raises
        resp = _err(sqlite3.IntegrityError("uq_one_active_per_subject"))
        assert resp["error"]["code"] == CAT_B["IntegrityError"]
        assert resp["error"]["data"]["exception_class"] == "IntegrityError"
        assert resp["error"]["data"]["component"] == "#6"


class TestWireShapeError:
    """Wire-shape failure → -32602, wire_shape_error flag, NO seahorse_code."""

    def test_translates_to_32602(self) -> None:
        resp = _err(WireShapeError("bad shape", field="body"))
        assert resp["error"]["code"] == -32602
        assert resp["error"]["data"]["wire_shape_error"] is True
        assert "seahorse_code" not in resp["error"]["data"]
        assert resp["error"]["data"]["component"] == "#13"
        assert resp["error"]["data"]["field"] == "body"

    def test_no_field_omitted(self) -> None:
        resp = _err(WireShapeError("bad shape"))
        assert "field" not in resp["error"]["data"]

    def test_wire_shape_response_helper(self) -> None:
        resp = wire_shape_response(7, "query", "empty")
        assert resp["id"] == 7
        assert resp["error"]["code"] == -32602
        assert resp["error"]["data"]["wire_shape_error"] is True
        assert resp["error"]["data"]["field"] == "query"


class TestGenericFallback:
    """Uncatalogued Exception → -32603, no swallow, no synthetic code."""

    def test_generic_exception(self) -> None:
        resp = _err(RuntimeError("boom"))
        assert resp["error"]["code"] == -32603
        assert resp["error"]["data"]["exception_class"] == "RuntimeError"
        assert resp["error"]["data"]["detail"] == "boom"
        assert "seahorse_code" not in resp["error"]["data"]
        assert resp["error"]["data"]["component"] == "#13"

    def test_key_error_is_generic(self) -> None:
        resp = _err(KeyError("missing"))
        assert resp["error"]["code"] == -32603


class TestResponseEnvelope:
    def test_error_envelope_jsonrpc(self) -> None:
        resp = _err(SeahorseError(code=E_EMPTY_BODY, detail="x"), request_id="req-7")
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == "req-7"
        assert "error" in resp
        assert "result" not in resp

    def test_cat_a_codes_are_distinct(self) -> None:
        codes = list(CAT_A.values())
        assert len(codes) == len(set(codes))  # no collisions

    def test_all_cat_a_codes_in_server_range(self) -> None:
        for code in CAT_A.values():
            assert -32099 <= code <= -32000


class TestCatADriftGuard:
    """CAT_A must cover EVERY E_* code the facade and engine can raise.

    Without this guard, a new ``E_*`` added to ``facade/errors.py`` or
    ``engine/errors.py`` but not to ``CAT_A`` would silently fall through
    ``translate`` to the generic -32603 fallback (losing the stable
    ``seahorse_code``). The guard fails loud until CAT_A is updated.
    """

    def test_cat_a_covers_all_facade_codes(self) -> None:
        from seahorse.facade.errors import (
            E_EMPTY_BODY,
            E_EMPTY_QUERY,
            E_INVALID_EXTRACTION_MODE,
            E_INVALID_PIT_KIND,
            E_MISSING_SOURCE_TYPE,
            E_NOT_IN_MVP_0_1,
            E_PIT_RECALL_MVP_0,
            E_PIT_REQUIRES_T,
        )

        facade_codes = {
            E_EMPTY_BODY,
            E_MISSING_SOURCE_TYPE,
            E_INVALID_EXTRACTION_MODE,
            E_EMPTY_QUERY,
            E_INVALID_PIT_KIND,
            E_PIT_REQUIRES_T,
            E_NOT_IN_MVP_0_1,
            E_PIT_RECALL_MVP_0,
        }
        assert set(CAT_A) >= facade_codes

    def test_cat_a_covers_all_engine_codes(self) -> None:
        from seahorse.engine.errors import (
            E_COLLISION_EXISTS,
            E_CREATED_AT_ENGINE_OWNED,
            E_DANGLING_SUPERSEDES,
            E_EXPIRED_AT_NON_NULL,
            E_MONOTONICITY_VIOLATED,
            E_NOT_IN_MVP_0,
            E_PENDING_CANNOT_INVALIDATE,
            E_SKIP_CONTRACT_VIOLATED,
            E_VALID_AT_HUMAN_ONLY,
        )

        engine_codes = {
            E_COLLISION_EXISTS,
            E_PENDING_CANNOT_INVALIDATE,
            E_DANGLING_SUPERSEDES,
            E_VALID_AT_HUMAN_ONLY,
            E_SKIP_CONTRACT_VIOLATED,
            E_NOT_IN_MVP_0,
            E_EXPIRED_AT_NON_NULL,
            E_CREATED_AT_ENGINE_OWNED,
            E_MONOTONICITY_VIOLATED,
        }
        assert set(CAT_A) >= engine_codes