"""Exit-code translation for the CLI projection (#14).

The sister of ``seahorse.mcp.errors`` (#13): both translate the SAME
``SeahorseError`` / ``EngineError`` catalog, #13 to JSON-RPC ``-32xxx`` codes,
#14 to process exit codes in the ``sysexits.h`` application band (64+). The
catalog is mirrored from #13's ``CAT_A`` / ``CAT_B`` so a change in #12 moves
both tables in lockstep (single point of change, f5-14 §3.3).

Three categories (f5-14 §3.3), consistent with #13:

- **Cat A — exceptions with a stable ``.code``** (``SeahorseError`` from #12 and
  ``EngineError`` from #2): the ``code`` is surfaced as ``seahorse_code`` on
  stderr and mapped to a unique exit code. The caller matches on
  ``seahorse_code``.
- **Cat B — propagated exceptions WITHOUT a stable code** (``FullBatchTooLarge``,
  ``PitFullNotSupported``, ``NotInMVP0`` from #8; ``InvalidationConflictError``,
  ``NotFound`` from #2; ``IntegrityError`` from #6): surfaced as
  ``exception_class`` (no synthetic ``seahorse_code`` — that would lie about a
  code the lower component does not own).
- **Cat C — CLI-owned exit codes** (prefixed ``CLI_``, NOT from the #12
  catalog): bootstrap/config/reserved-feature errors of the CLI surface itself.

Drift reconciled vs f5-14 §3.3 (which cites an idealized 12-Cat-A table): the
real catalog #12/#2 raise is 17 Cat A codes (8 facade + 9 engine), mirrored
from #13's ``CAT_A``. ``E_INVALID_SOURCE_TYPE`` (f5-14) → ``E_MISSING_SOURCE_TYPE``
(real). ``E_INVALID_COGNITIVE_TYPE`` is NOT raised by #12 in MVP-0 (the facade
does not validate ``cognitive_type`` — engine/#1 authority) so it is not
mapped. ``InvalidPITKind`` carries ``.code = E_INVALID_PIT_KIND`` → Cat A (the
f5-14 table wrongly listed it Cat B). ``HopsCapExceeded`` / ``BfsKnownAtUnsupported``
/ ``SubjectDerivationError`` are NOT in code in MVP-0 (#11 / real #5 unbuilt)
and are not mapped — parity with #13's ``CAT_B``.

SO-14-05 resolution (2026-07-20): ``expire`` / ``revalidate`` are CLI-intercepted
at the CLI layer (Cat C ``CLI_NOT_IN_MVP_0`` = 75), never reaching
``facade.expire``/``revalidate`` (which raise ``E_NOT_IN_MVP_0_1``). ``--tag`` is
not exposed in MVP-0 (the facade rejects non-empty tags with
``E_NOT_IN_MVP_0_1``; ADR-10 honesty + YAGNI — tags are an MVP-1 enabler).
``E_NOT_IN_MVP_0_1`` is therefore currently UNREACHABLE via #14; it is mapped at
71 for parity with #13 and as defense-in-depth if a future surface routes here.

Exit-code layout (64–99, ``sysexits.h`` application band):

- ``0``  success, ``1`` general/unhandled, ``2`` usage/argparse.
- Cat A (17): 64–74, 76–81 (75 skipped — Cat C anchor).
- Cat C (3):  75 ``CLI_NOT_IN_MVP_0``, 82 ``CLI_VAULT_NOT_FOUND``,
  83 ``CLI_CONFIG_INVALID``.
- Cat B (6):  84–89.
- Reserved:   90–99 for future #12 codes or CLI-owned codes.

References:
- f5-14 §3.3 (exit-code table, as-designed — reconciled here against real code)
- seahorse/mcp/errors.py (sister projection; mirrored catalog)
- SO-14-05 (expire/revalidate CLI-intercept decision)
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# POSIX anchors.
# ---------------------------------------------------------------------------
EXIT_SUCCESS = 0
EXIT_GENERAL = 1  # uncatalogued Exception (fail-loud, no swallow)
EXIT_USAGE = 2  # argparse/Typer usage error

# ---------------------------------------------------------------------------
# Cat A — stable SeahorseError.code / EngineError.code → exit code.
# Mirrors seahorse.mcp.errors.CAT_A (-32001..-32017) in the same order so the
# two tables stay parallel (single point of change).
# ---------------------------------------------------------------------------
_CAT_A_FACADE = {
    "E_EMPTY_BODY": 64,
    "E_MISSING_SOURCE_TYPE": 65,
    "E_INVALID_EXTRACTION_MODE": 66,
    "E_EMPTY_QUERY": 67,
    "E_INVALID_PIT_KIND": 68,
    "E_PIT_REQUIRES_T": 69,
    "E_PIT_RECALL_MVP_0": 70,
    # Currently unreachable via #14 (expire/revalidate CLI-intercepted, --tag
    # not exposed); mapped for parity with #13 + defense-in-depth.
    "E_NOT_IN_MVP_0_1": 71,
}

_CAT_A_ENGINE = {
    "E_COLLISION_EXISTS": 72,
    "E_PENDING_CANNOT_INVALIDATE": 73,
    "E_DANGLING_SUPERSEDES": 74,
    # 75 reserved for Cat C CLI_NOT_IN_MVP_0 (SO-14-05 anchor).
    "E_SKIP_CONTRACT_VIOLATED": 76,
    "E_NOT_IN_MVP_0": 77,
    "E_VALID_AT_HUMAN_ONLY": 78,
    "E_EXPIRED_AT_NON_NULL": 79,
    "E_CREATED_AT_ENGINE_OWNED": 80,
    "E_MONOTONICITY_VIOLATED": 81,
}

CAT_A: dict[str, int] = {**_CAT_A_FACADE, **_CAT_A_ENGINE}

# ---------------------------------------------------------------------------
# Cat B — exception class (no stable code) → exit code.
# Mirrors seahorse.mcp.errors.CAT_B classes.
# ---------------------------------------------------------------------------
CAT_B: dict[str, int] = {
    "FullBatchTooLarge": 84,
    "PitFullNotSupported": 85,
    "NotInMVP0": 86,
    "InvalidationConflictError": 87,
    "NotFound": 88,
    "IntegrityError": 89,
}

# ---------------------------------------------------------------------------
# Cat C — CLI-owned exit codes (prefixed CLI_, NOT from the #12 catalog).
# ---------------------------------------------------------------------------
CLI_NOT_IN_MVP_0 = 75  # SO-14-05: expire/revalidate + unbuilt-dependency stubs
CLI_VAULT_NOT_FOUND = 82
CLI_CONFIG_INVALID = 83

# ---------------------------------------------------------------------------
# Component-of-origin attribution for stderr ``component:`` (parity with #13).
# ---------------------------------------------------------------------------
_ORIGIN_BY_CLASS = {
    "SeahorseError": "#12",
    "InvalidPITKind": "#12",
    "PitRecallNotSupportedMVP0": "#12",
    "EmptyQueryError": "#12",
    "EngineError": "#2",
    "FullBatchTooLarge": "#8",
    "PitFullNotSupported": "#8",
    "NotInMVP0": "#8",
    "NotFound": "#2",
    "InvalidationConflictError": "#2",
    "IntegrityError": "#6",
}

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
}

_MESSAGE_BY_CLASS = {
    "FullBatchTooLarge": "Full batch too large",
    "PitFullNotSupported": "PIT full not supported in MVP-0",
    "NotInMVP0": "Timeline axis not in MVP-0",
    "InvalidationConflictError": "Invalidation conflict",
    "NotFound": "Not found",
    "IntegrityError": "Storage integrity error",
}


def _origin_of(exc: BaseException) -> str:
    cls = type(exc).__name__
    if cls in _ORIGIN_BY_CLASS:
        return _ORIGIN_BY_CLASS[cls]
    if hasattr(exc, "code") and hasattr(exc, "context"):
        return "#2"  # EngineError subclass carrying .code
    return "#14"


def _detail_of(exc: BaseException) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str):
        return detail
    return str(exc)


def translate(exc: BaseException) -> tuple[int, dict[str, Any]]:
    """Translate a raised exception into ``(exit_code, error_info)``.

    ``error_info`` is the structured payload printed to stderr (human form for
    text mode, JSON for ``--json``). It always carries ``exit_code`` and
    ``component``; Cat A adds ``seahorse_code``, Cat B adds ``exception_class``.

    ``CliError`` (Cat C, CLI-owned) short-circuits with its own ``exit_code``.
    """
    # Lazy import breaks the errors ↔ exit_codes cycle (errors imports our
    # int constants at module load; we only need the class here at call time).
    from seahorse.cli.errors import CliError

    # Cat C — CLI-owned (CliError carries its exit code).
    if isinstance(exc, CliError):
        return exc.exit_code, exc.info()

    # Cat A — stable .code (SeahorseError or EngineError).
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in CAT_A:
        return CAT_A[code], {
            "seahorse_code": code,
            "detail": _detail_of(exc),
            "component": _origin_of(exc),
            "exit_code": CAT_A[code],
        }

    # Cat B — exception class without a stable code.
    cls = type(exc).__name__
    if cls in CAT_B:
        return CAT_B[cls], {
            "exception_class": cls,
            "detail": str(exc),
            "component": _origin_of(exc),
            "exit_code": CAT_B[cls],
        }

    # Generic fallback — fail-loud, no swallow, no synthetic code.
    return EXIT_GENERAL, {
        "exception_class": cls,
        "detail": str(exc),
        "component": "#14",
        "exit_code": EXIT_GENERAL,
    }


def message_for(exc: BaseException) -> str:
    """Short human message for an exception (used in the stderr header)."""
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in _MESSAGE_BY_CODE:
        return _MESSAGE_BY_CODE[code]
    cls = type(exc).__name__
    return _MESSAGE_BY_CLASS.get(cls, "Internal error")


__all__ = [
    "EXIT_SUCCESS",
    "EXIT_GENERAL",
    "EXIT_USAGE",
    "CAT_A",
    "CAT_B",
    "CLI_NOT_IN_MVP_0",
    "CLI_VAULT_NOT_FOUND",
    "CLI_CONFIG_INVALID",
    "translate",
    "message_for",
]