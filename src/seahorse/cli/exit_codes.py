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
- Cat A (21): 64–74, 76–81 (75 skipped — Cat C anchor), 90–93 (frontmatter #3,
  commit 5). The 4 frontmatter codes live in the previously-reserved 90–93 band
  because they are a distinct component origin (#3, not #12/#2) and the 64–81
  band was already full.
- Cat C (4):  75 ``CLI_NOT_IN_MVP_0`` (reserved/stub honesty, SO-14-05),
  82 ``CLI_VAULT_NOT_FOUND``, 83 ``CLI_CONFIG_INVALID``,
  94 ``CLI_REBUILD_CONFLICTS`` (ADR-10 index-rebuild conflict honesty).
- Cat B (6):  84–89.

NOTE — 75 overload resolved (commit 6): ``CLI_NOT_IN_MVP_0`` and
``CLI_REBUILD_CONFLICTS`` shared exit code 75 in commit 5 (distinguishable only
on the structured ``cli_code`` payload, not on the int). Commit 6 split
``CLI_REBUILD_CONFLICTS`` onto a fresh 90–99 slot (94 — frontmatter Cat A took
90–93, so 94 is the next free CLI-owned slot) so the two are distinct even on
the int exit code. Both stay CLI-owned Cat C (never Cat A), so they never
collide with the 21-code Cat A table; 94 is also outside the Cat A
64–81 / 90–93 set.

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

# Frontmatter codes (4) — owned by #3 (commit 5), distinct component origin.
# Placed in the 90–93 band (previously reserved) so they do not displace the
# 64–81 domain band. Mirrors seahorse.mcp.errors._CAT_A_FRONTMATTER.
_CAT_A_FRONTMATTER = {
    "E_FRONTMATTER_INVALID": 90,
    "E_MIGRATION_ABORTED": 91,
    "E_X_RESERVED_COLLISION": 92,
    "E_SUBJECT_EMPTY": 93,
}

CAT_A: dict[str, int] = {**_CAT_A_FACADE, **_CAT_A_ENGINE, **_CAT_A_FRONTMATTER}

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
# ADR-10 honesty: index rebuild reports conflicts and fails loud (no auto-pick).
# Commit 6 split this off the 75 overload (shared with CLI_NOT_IN_MVP_0) onto a
# fresh 90–99 slot so the rebuild-conflict concern is distinct from the
# reserved-stub 75 even on the int exit code. Both stay Cat C (never Cat A).
CLI_REBUILD_CONFLICTS = 94

# ---------------------------------------------------------------------------
# Component-of-origin attribution for stderr ``component:`` (parity with #13).
# ---------------------------------------------------------------------------
_ORIGIN_BY_CLASS = {
    "SeahorseError": "#12",
    "InvalidPITKind": "#12",
    "PitRecallNotSupportedMVP0": "#12",
    "EmptyQueryError": "#12",
    # #11 retrieval — plain Exception (no .code), raised at the recall
    # entrypoint on an unknown pit.kind. Distinct __name__ from #12's
    # InvalidPITKind (C8.6) so the table attributes each to its real owner.
    "RetrievalInvalidPITKind": "#11",
    "EngineError": "#2",
    "FullBatchTooLarge": "#8",
    "PitFullNotSupported": "#8",
    "NotInMVP0": "#8",
    "NotFound": "#2",
    "InvalidationConflictError": "#2",
    "IntegrityError": "#6",
    # frontmatter (#3, commit 5)
    "FrontmatterInvalid": "#3",
    "MigrationError": "#3",
    "XReservedCollision": "#3",
    "SubjectEmpty": "#3",
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

    # Generic fallback — fail-loud, no swallow, no synthetic code. The component
    # is resolved via ``_origin_of`` (C8.6 [24]): a plain-Exception class that IS
    # in ``_ORIGIN_BY_CLASS`` — e.g. #11's ``RetrievalInvalidPITKind`` (no ``.code``,
    # not in CAT_B) — now attributes to its real owner instead of being masked as
    # ``#14``. Unknown classes still fall back to ``#14``.
    return EXIT_GENERAL, {
        "exception_class": cls,
        "detail": str(exc),
        "component": _origin_of(exc),
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
    "CLI_REBUILD_CONFLICTS",
    "translate",
    "message_for",
]