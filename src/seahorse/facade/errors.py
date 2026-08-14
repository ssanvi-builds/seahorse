"""Facade error vocabulary (owned by the facade).

The facade raises ``SeahorseError`` (stable ``code`` + ``detail``) at the
boundary for shape/validation failures and for later-release primitives refused
in the current release. Engine errors (``seahorse.engine.errors.EngineError``)
are propagated verbatim — the facade never re-wraps them. The caller matches on
``err.code`` either way, so the two vocabularies coexist without overlap (engine
codes start with ``E_`` and live in ``seahorse.engine.errors``; facade codes
below are unique strings).
"""

from __future__ import annotations


class SeahorseError(Exception):
    """Facade-level error identified by a stable ``code`` string + ``detail``.

    Raised by the facade at the boundary: shape validation, PIT-kind validation,
    and later-release primitives refused in the current release
    (``expire``/``revalidate``). Domain failures from the engine (collisions,
    invalidation conflicts, not-found, valid_at guard) surface as ``EngineError``
    and are propagated unchanged.
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
E_NOT_IN_MVP_0_1 = "E_NOT_IN_MVP_0_1"  # later-release primitive refused in the current release
E_PIT_RECALL_MVP_0 = "E_PIT_RECALL_MVP_0"  # PIT recall not supported in the current release


class InvalidPITKind(SeahorseError):
    """Raised when a PIT kind is not ``state_at`` or ``known_at``."""

    def __init__(self, kind: str) -> None:
        super().__init__(
            code=E_INVALID_PIT_KIND,
            detail=f"pit kind={kind!r}; expected 'state_at' or 'known_at'",
        )
        self.kind = kind


class PitRecallNotSupportedMVP0(SeahorseError):
    """Raised when ``recall`` is called with a PIT in the current release.

    Current-release recall is the current-state listing (no ranking, no PIT).
    PIT-aware recall is the hybrid retrieval path (a later release). Refusing
    before any read keeps the two-axes-never-mixed invariant and fails loud
    instead of silently degrading.
    """

    def __init__(self) -> None:
        super().__init__(
            code=E_PIT_RECALL_MVP_0,
            detail="PIT recall is not supported in this release; call recall without pit",
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