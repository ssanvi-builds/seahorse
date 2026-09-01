"""Exit-code translation for the CLI projection.

The sibling of ``seahorse.mcp.errors``: both translate the SAME
``SeahorseError`` / ``EngineError`` catalog, the MCP server to JSON-RPC
``-32xxx`` codes, the CLI to process exit codes in the ``sysexits.h``
application band (64+). The catalog is mirrored from the MCP server's
``CAT_A`` / ``CAT_B`` so a change in the facade moves both tables in lockstep
(single point of change).

Three categories, consistent with the MCP server:

- **Cat A — exceptions with a stable ``.code``** (``SeahorseError`` from the
  facade and ``EngineError`` from the engine): the ``code`` is surfaced as
  ``seahorse_code`` on stderr and mapped to a unique exit code. The caller
  matches on ``seahorse_code``.
- **Cat B — propagated exceptions WITHOUT a stable code** (``FullBatchTooLarge``,
  ``PitFullNotSupported``, ``NotInMVP0`` from the disclosure layer;
  ``InvalidationConflictError``, ``NotFound`` from the engine; ``IntegrityError``
  from persistence): surfaced as ``exception_class`` (no synthetic
  ``seahorse_code`` — that would lie about a code the lower component does not
  own).
- **Cat C — CLI-owned exit codes** (prefixed ``CLI_``, NOT from the facade
  catalog): bootstrap/config/reserved-feature errors of the CLI surface itself.

The real catalog the facade and engine raise is 17 Cat A codes (8 facade + 9
engine), mirrored from the MCP server's ``CAT_A``. ``E_INVALID_SOURCE_TYPE``
→ ``E_MISSING_SOURCE_TYPE`` (the real code). ``E_INVALID_COGNITIVE_TYPE`` is
NOT raised in the current release (the facade does not validate
``cognitive_type`` — the engine is authoritative) so it is not mapped.
``InvalidPITKind`` carries ``.code = E_INVALID_PIT_KIND`` → Cat A.
``HopsCapExceeded`` / ``BfsKnownAtUnsupported`` / ``SubjectDerivationError``
are not in code in the current release and are not mapped — parity with the
MCP server's ``CAT_B``.

``expire`` / ``revalidate`` are CLI-intercepted at the CLI layer (Cat C
``CLI_NOT_IN_MVP_0`` = 75), never reaching ``facade.expire``/``revalidate``
(which raise ``E_NOT_IN_MVP_0_1``). ``--tag`` is not exposed in the current
release (the facade rejects non-empty tags with ``E_NOT_IN_MVP_0_1``;
fail-loud honesty + YAGNI — tags are a later-release enabler).
``E_NOT_IN_MVP_0_1`` is therefore currently UNREACHABLE via the CLI; it is
mapped at 71 for parity with the MCP server and as defense-in-depth if a
future surface routes here.

Exit-code layout (64–99, ``sysexits.h`` application band):

- ``0``  success, ``1`` general/unhandled, ``2`` usage/argparse.
- Cat A (21): 64–74, 76–81 (75 skipped — Cat C anchor), 90–93 (frontmatter).
  The 4 frontmatter codes live in the previously-reserved 90–93 band because
  they are a distinct component origin (the frontmatter migrator, not the
  facade/engine) and the 64–81 band was already full.
- Cat C (5):  75 ``CLI_NOT_IN_MVP_0`` (reserved/stub honesty),
  82 ``CLI_VAULT_NOT_FOUND``, 83 ``CLI_CONFIG_INVALID``,
  94 ``CLI_REBUILD_CONFLICTS`` (index-rebuild conflict honesty),
  97 ``CLI_MIGRATION_DEFERRED`` (frontmatter migrate case-D honesty).
- Cat B (6):  84–89.

NOTE — 75 overload resolved: ``CLI_NOT_IN_MVP_0`` and ``CLI_REBUILD_CONFLICTS``
shared exit code 75 (distinguishable only on the structured ``cli_code``
payload, not on the int). ``CLI_REBUILD_CONFLICTS`` was split onto a fresh
90–99 slot (94 — frontmatter Cat A took 90–93, so 94 is the next free
CLI-owned slot) so the two are distinct even on the int exit code. Both stay
CLI-owned Cat C (never Cat A), so they never collide with the 21-code Cat A
table; 94 is also outside the Cat A 64–81 / 90–93 set.
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
    # Currently unreachable via the CLI (expire/revalidate CLI-intercepted,
    # --tag not exposed); mapped for parity with the MCP server +
    # defense-in-depth.
    "E_NOT_IN_MVP_0_1": 71,
}

_CAT_A_ENGINE = {
    "E_COLLISION_EXISTS": 72,
    "E_PENDING_CANNOT_INVALIDATE": 73,
    "E_DANGLING_SUPERSEDES": 74,
    # 75 reserved for Cat C CLI_NOT_IN_MVP_0.
    "E_SKIP_CONTRACT_VIOLATED": 76,
    "E_NOT_IN_MVP_0": 77,
    "E_VALID_AT_HUMAN_ONLY": 78,
    "E_EXPIRED_AT_NON_NULL": 79,
    "E_CREATED_AT_ENGINE_OWNED": 80,
    "E_MONOTONICITY_VIOLATED": 81,
}

# Frontmatter codes (4) — owned by the frontmatter migrator, distinct
# component origin. Placed in the 90–93 band (previously reserved) so they do
# not displace the 64–81 domain band. Mirrors seahorse.mcp.errors._CAT_A_FRONTMATTER.
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
    # Procedural-skill validation (canonical body) — a facade-client domain
    # error, propagated without a stable code (parity with the MCP server).
    "ProceduralError": 96,
}

# ---------------------------------------------------------------------------
# Cat C — CLI-owned exit codes (prefixed CLI_, NOT from the facade catalog).
# ---------------------------------------------------------------------------
CLI_NOT_IN_MVP_0 = 75  # expire/revalidate + unbuilt-dependency stubs
CLI_VAULT_NOT_FOUND = 82
CLI_CONFIG_INVALID = 83
# Fail-loud honesty: index rebuild reports conflicts and fails loud (no
# auto-pick). Split off the 75 overload (shared with CLI_NOT_IN_MVP_0) onto a
# fresh 90–99 slot so the rebuild-conflict concern is distinct from the
# reserved-stub 75 even on the int exit code. Both stay Cat C (never Cat A).
CLI_REBUILD_CONFLICTS = 94
# ``seahorse observe start`` when the observer is already running.
CLI_OBSERVER_RUNNING = 95
# Frontmatter migrate: apply met incompatible notes (case D). Fail-loud
# honesty — the run completes (A/B/C migrated, D logged) but the vault is not
# fully migrated, so scripts/chained commands must see it. 96 is Cat B
# (ProceduralError), so 97 is the next free Cat C slot.
CLI_MIGRATION_DEFERRED = 97
# ``seahorse materialize`` when the vault has no ``[materialize]`` section
# (materialization is opt-in). Fail-loud with the setup hint — the operator
# runs ``seahorse setup`` or adds the section by hand.
CLI_MATERIALIZE_NOT_CONFIGURED = 98

# ---------------------------------------------------------------------------
# Component-of-origin attribution for stderr ``component:`` (parity with the MCP server).
# ---------------------------------------------------------------------------
_ORIGIN_BY_CLASS = {
    "SeahorseError": "#12",
    "InvalidPITKind": "#12",
    "PitRecallNotSupportedMVP0": "#12",
    "EmptyQueryError": "#12",
    # Retrieval — plain Exception (no .code), raised at the recall entrypoint
    # on an unknown pit.kind. Distinct __name__ from the facade's
    # InvalidPITKind so the table attributes each to its real owner.
    "RetrievalInvalidPITKind": "#11",
    "EngineError": "#2",
    "FullBatchTooLarge": "#8",
    "PitFullNotSupported": "#8",
    "NotInMVP0": "#8",
    "NotFound": "#2",
    "InvalidationConflictError": "#2",
    "IntegrityError": "#6",
    # procedural skills — client of the facade
    "ProceduralError": "#procedural",
    # frontmatter (the frontmatter migrator)
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
    "E_PIT_RECALL_MVP_0": "PIT recall not supported in the current release",
    "E_NOT_IN_MVP_0_1": "Primitive not available in the current release",
    "E_COLLISION_EXISTS": "Collision exists",
    "E_PENDING_CANNOT_INVALIDATE": "PENDING cannot invalidate",
    "E_DANGLING_SUPERSEDES": "Dangling supersedes",
    "E_SKIP_CONTRACT_VIOLATED": "Skip contract violated",
    "E_NOT_IN_MVP_0": "Not available in the current release",
    "E_VALID_AT_HUMAN_ONLY": "valid_at human-only",
    "E_EXPIRED_AT_NON_NULL": "expired_at non-null",
    "E_CREATED_AT_ENGINE_OWNED": "created_at engine-owned",
    "E_MONOTONICITY_VIOLATED": "Monotonicity violated",
    # frontmatter (the frontmatter migrator)
    "E_FRONTMATTER_INVALID": "Frontmatter invalid",
    "E_MIGRATION_ABORTED": "Migration aborted",
    "E_X_RESERVED_COLLISION": "X reserved collision",
    "E_SUBJECT_EMPTY": "Subject empty",
}

_MESSAGE_BY_CLASS = {
    "FullBatchTooLarge": "Full batch too large",
    "PitFullNotSupported": "PIT full not supported in the current release",
    "NotInMVP0": "Timeline axis not available in the current release",
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
    # is resolved via ``_origin_of``: a plain-Exception class that IS in
    # ``_ORIGIN_BY_CLASS`` — e.g. the retrieval layer's ``RetrievalInvalidPITKind``
    # (no ``.code``, not in CAT_B) — now attributes to its real owner instead of
    # being masked as the CLI. Unknown classes still fall back to the CLI.
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
    "CLI_OBSERVER_RUNNING",
    "CLI_MIGRATION_DEFERRED",
    "translate",
    "message_for",
]