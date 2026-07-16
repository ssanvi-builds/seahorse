"""Facade error vocabulary (owned by #12).

#12 raises ``SeahorseError`` (stable ``code`` + ``detail``) at the boundary for
shape/validation failures and for MVP-1 primitives refused in MVP-0. Engine
errors (``seahorse.engine.errors.EngineError``) are propagated verbatim — #12
never re-wraps them. The caller matches on ``err.code`` either way, so the two
vocabularies coexist without overlap (engine codes start with ``E_`` and live
in ``seahorse.engine.errors``; facade codes below are unique strings).

References:
- f5-12 §4 (SeahorseError, InvalidPITKind, PitRecallNotSupportedMVP0, EmptyQueryError)
- seahorse/engine/errors.py (EngineError — propagated, not re-wrapped)
"""

from __future__ import annotations


class SeahorseError(Exception):
    """Facade-level error identified by a stable ``code`` string + ``detail``.

    Raised by #12 at the boundary: shape validation, PIT-kind validation, and
    MVP-1 primitives refused in MVP-0 (``expire``/``revalidate``). Domain
    failures from the engine (collisions, invalidation conflicts, not-found,
    valid_at guard) surface as ``EngineError`` and are propagated unchanged.
    """

    __slots__ = ("code", "detail")

    def __init__(self, *, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


# Stable code constants — unique strings, never renamed (callers match on them).
E_EMPTY_BODY = "E_EMPTY_BODY"
E_MISSING_SOURCE_TYPE = "E_MISSING_SOURCE_TYPE"
E_INVALID_EXTRACTION_MODE = "E_INVALID_EXTRACTION_MODE"
E_EMPTY_QUERY = "E_EMPTY_QUERY"
E_INVALID_PIT_KIND = "E_INVALID_PIT_KIND"
E_PIT_REQUIRES_T = "E_PIT_REQUIRES_T"
E_NOT_IN_MVP_0_1 = "E_NOT_IN_MVP_0_1"  # MVP-1 primitive refused in MVP-0
E_PIT_RECALL_MVP_0 = "E_PIT_RECALL_MVP_0"  # PIT recall not supported in MVP-0


class InvalidPITKind(SeahorseError):
    """Raised when a PIT kind is not ``state_at`` or ``known_at`` (ADR-03)."""

    def __init__(self, kind: str) -> None:
        super().__init__(
            code=E_INVALID_PIT_KIND,
            detail=f"pit kind={kind!r}; expected 'state_at' or 'known_at'",
        )
        self.kind = kind


class PitRecallNotSupportedMVP0(SeahorseError):
    """Raised when ``recall`` is called with a PIT in MVP-0.

    MVP-0 recall is the G2 vigente listing (no ranking, no PIT). PIT-aware
    recall is the #11 path (MVP-1). Refusing before any read keeps ADR-03's
    two-axes-never-mixed invariant and fails loud instead of silently
    degrading (ADR-10 honesty).
    """

    def __init__(self) -> None:
        super().__init__(
            code=E_PIT_RECALL_MVP_0,
            detail="PIT recall is not supported in MVP-0; call recall without pit",
        )


class EmptyQueryError(SeahorseError):
    """Raised when ``recall`` is called with an empty query string."""

    def __init__(self) -> None:
        super().__init__(
            code=E_EMPTY_QUERY,
            detail="recall query must be a non-empty string",
        )


__all__ = [
    "SeahorseError",
    "InvalidPITKind",
    "PitRecallNotSupportedMVP0",
    "EmptyQueryError",
    "E_EMPTY_BODY",
    "E_MISSING_SOURCE_TYPE",
    "E_INVALID_EXTRACTION_MODE",
    "E_EMPTY_QUERY",
    "E_INVALID_PIT_KIND",
    "E_PIT_REQUIRES_T",
    "E_NOT_IN_MVP_0_1",
    "E_PIT_RECALL_MVP_0",
]