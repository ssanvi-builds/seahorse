"""Exit-code translation (#14) — mirrors ``tests/mcp/test_errors.py`` over the
same catalog, but to process exit codes (64+) instead of JSON-RPC ``-32xxx``.

Covers:
- Cat A (17 ``SeahorseError``/``EngineError`` codes → 64–81, 75 skipped).
- Cat B (6 classes → 84–89), surfaced as ``exception_class`` (no synthetic code).
- Cat C (``CliError`` short-circuits with its own ``exit_code``).
- ``message_for`` short labels.
- Generic ``Exception`` → exit 1 (fail-loud, no swallow, no synthetic code).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from seahorse.cli.errors import (
    CliConfigInvalid,
    CliError,
    CliNotInMVP0,
    CliRebuildConflicts,
    CliUsageError,
    CliVaultNotFound,
)
from seahorse.cli.exit_codes import (
    CAT_A,
    CAT_B,
    CLI_CONFIG_INVALID,
    CLI_NOT_IN_MVP_0,
    CLI_REBUILD_CONFLICTS,
    CLI_VAULT_NOT_FOUND,
    EXIT_GENERAL,
    EXIT_SUCCESS,
    EXIT_USAGE,
    message_for,
    translate,
)

# Import the real Cat B classes used by #8/#2/#6.
from seahorse.contracts.engine import InvalidationConflictError, NotFound
from seahorse.disclosure.types import FullBatchTooLarge, NotInMVP0, PitFullNotSupported
from seahorse.engine.errors import EngineError
from seahorse.facade.errors import (
    EmptyQueryError,
    InvalidPITKind,
    PitRecallNotSupportedMVP0,
    SeahorseError,
)
from seahorse.frontmatter.errors import (
    FrontmatterInvalid,
    MigrationError,
    SubjectEmpty,
    XReservedCollision,
)

# ``IntegrityError`` in CAT_B is stdlib ``sqlite3.IntegrityError`` — #6 raises
# the native constraint error, matched by class name.


# ---------------------------------------------------------------------------
# Cat A — every code in the catalog maps to a unique exit in 64–81.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "exc_cls"),
    [
        ("E_EMPTY_BODY", SeahorseError),
        ("E_MISSING_SOURCE_TYPE", SeahorseError),
        ("E_INVALID_EXTRACTION_MODE", SeahorseError),
        ("E_EMPTY_QUERY", EmptyQueryError),
        ("E_INVALID_PIT_KIND", InvalidPITKind),
        ("E_PIT_REQUIRES_T", SeahorseError),
        ("E_PIT_RECALL_MVP_0", PitRecallNotSupportedMVP0),
        ("E_NOT_IN_MVP_0_1", SeahorseError),
        ("E_COLLISION_EXISTS", EngineError),
        ("E_PENDING_CANNOT_INVALIDATE", EngineError),
        ("E_DANGLING_SUPERSEDES", EngineError),
        ("E_SKIP_CONTRACT_VIOLATED", EngineError),
        ("E_NOT_IN_MVP_0", EngineError),
        ("E_VALID_AT_HUMAN_ONLY", EngineError),
        ("E_EXPIRED_AT_NON_NULL", EngineError),
        ("E_CREATED_AT_ENGINE_OWNED", EngineError),
        ("E_MONOTONICITY_VIOLATED", EngineError),
    ],
)
def test_cat_a_code_maps_to_exit(code, exc_cls):
    """Each Cat A code → its mapped exit code, with seahorse_code on stderr."""
    if exc_cls is SeahorseError:
        exc = SeahorseError(code=code, detail="x")
    elif exc_cls is EngineError:
        exc = EngineError(code)
    elif exc_cls is InvalidPITKind:
        exc = InvalidPITKind("bogus")
    else:
        exc = exc_cls()  # EmptyQueryError / PitRecallNotSupportedMVP0
    exit_code, info = translate(exc)
    assert exit_code == CAT_A[code]
    assert info["seahorse_code"] == code
    assert info["exit_code"] == exit_code
    assert "component" in info


def test_cat_a_frontmatter_codes_map_to_exit():
    """The 4 frontmatter Cat A codes (#3, commit 5) → 90–93 with seahorse_code.

    These mirror ``seahorse.mcp.errors`` (parity, single point of change) and
    carry ``.code`` as a class attribute (``FrontmatterInvalid`` etc.).
    """
    cases = [
        ("E_FRONTMATTER_INVALID", FrontmatterInvalid(Path("/n.md"), ValueError("bad"))),
        ("E_MIGRATION_ABORTED", MigrationError("boom")),
        ("E_X_RESERVED_COLLISION", XReservedCollision(Path("/n.md"), "x-valid-at")),
        ("E_SUBJECT_EMPTY", SubjectEmpty(Path("/n.md"))),
    ]
    for code, exc in cases:
        exit_code, info = translate(exc)
        assert exit_code == CAT_A[code], code
        assert info["seahorse_code"] == code
        assert info["component"] == "#3"
        assert info["exit_code"] == exit_code


def test_cat_a_table_is_21_codes_no_collision_with_75():
    """21 Cat A codes (17 domain + 4 frontmatter), unique, none using 75."""
    assert len(CAT_A) == 21
    exits = list(CAT_A.values())
    assert len(set(exits)) == 21  # unique
    # 17 domain codes in 64–81, 4 frontmatter codes in 90–93 (the future band).
    assert all(64 <= e <= 93 for e in exits)
    assert 75 not in exits
    assert {90, 91, 92, 93} <= set(exits)


def test_invalid_pit_kind_carries_code():
    """InvalidPITKind is Cat A (.code), not Cat B (f5-14 table was wrong)."""
    exc = InvalidPITKind("bogus")
    code, info = translate(exc)
    assert code == CAT_A["E_INVALID_PIT_KIND"]
    assert info["seahorse_code"] == "E_INVALID_PIT_KIND"


# ---------------------------------------------------------------------------
# Cat B — class → exit 84–89, surfaced as exception_class (no synthetic code).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (FullBatchTooLarge(6, 5), 84),
        (PitFullNotSupported(), 85),
        (NotInMVP0("axis"), 86),
        (InvalidationConflictError("conflict"), 87),
        (NotFound("ep-1"), 88),
        (sqlite3.IntegrityError("uq_one_active_per_subject"), 89),
    ],
)
def test_cat_b_class_maps_to_exit(exc, expected):
    code, info = translate(exc)
    assert code == expected
    assert info["exception_class"] == type(exc).__name__
    assert "seahorse_code" not in info  # no synthetic code (would lie)


def test_cat_b_table_is_seven_classes():
    # Sprint C: ProceduralError (procedural-skill validation) added at 96.
    assert len(CAT_B) == 7
    assert sorted(CAT_B.values()) == [84, 85, 86, 87, 88, 89, 96]


# ---------------------------------------------------------------------------
# Cat C — CliError short-circuits with its own exit_code.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (CliNotInMVP0("expire", reason="mediano"), CLI_NOT_IN_MVP_0),
        (CliVaultNotFound(), CLI_VAULT_NOT_FOUND),
        (CliConfigInvalid("bad"), CLI_CONFIG_INVALID),
        (CliUsageError("--body too long"), EXIT_USAGE),
        (CliRebuildConflicts(2), CLI_REBUILD_CONFLICTS),
    ],
)
def test_cat_c_cli_error_short_circuits(exc, expected):
    code, info = translate(exc)
    assert code == expected
    assert info["cli_code"] == exc.name
    assert info["exit_code"] == expected
    assert info["component"] == "#14"


def test_cli_error_subclasses_are_cli_error():
    """translate short-circuits on the base class — all subclasses covered."""
    for exc in (
        CliNotInMVP0("x", reason="y"),
        CliVaultNotFound(),
        CliConfigInvalid("z"),
        CliUsageError("w"),
        CliRebuildConflicts(1),
    ):
        assert isinstance(exc, CliError)


# ---------------------------------------------------------------------------
# Generic fallback — uncatalogued Exception → exit 1, no synthetic code.
# ---------------------------------------------------------------------------


def test_generic_exception_exits_1():
    code, info = translate(RuntimeError("boom"))
    assert code == EXIT_GENERAL
    assert info["exception_class"] == "RuntimeError"
    assert "seahorse_code" not in info


def test_success_constant():
    assert EXIT_SUCCESS == 0
    assert EXIT_USAGE == 2
    assert EXIT_GENERAL == 1


# ---------------------------------------------------------------------------
# message_for — short labels for stderr headers.
# ---------------------------------------------------------------------------


def test_message_for_cat_a():
    assert message_for(SeahorseError(code="E_EMPTY_BODY", detail="x")) == "Empty body"
    assert message_for(SeahorseError(code="E_COLLISION_EXISTS", detail="x")) == "Collision exists"


def test_message_for_cat_b():
    assert message_for(NotFound("ep")) == "Not found"
    assert message_for(FullBatchTooLarge(6, 5)) == "Full batch too large"


def test_message_for_unknown_is_internal_error():
    assert message_for(RuntimeError("x")) == "Internal error"


# ---------------------------------------------------------------------------
# #13 parity — the CLI Cat A table mirrors seahorse.mcp.errors.CAT_A (single
# point of change). The two sister projections translate the SAME catalog.
# ---------------------------------------------------------------------------


def test_cli_cat_a_keys_mirrors_mcp_cat_a_keys():
    """Every CLI Cat A code has a matching MCP Cat A code (parity, f5-14 §3.3)."""
    from seahorse.mcp.errors import CAT_A as MCP_CAT_A

    assert set(CAT_A) == set(MCP_CAT_A)


def test_frontmatter_cat_a_origin_is_component_3():
    """The 4 frontmatter codes attribute to #3 (not #12/#2)."""
    from seahorse.mcp.errors import _ORIGIN_BY_CLASS as MCP_ORIGIN

    for cls in ("FrontmatterInvalid", "MigrationError", "XReservedCollision", "SubjectEmpty"):
        assert MCP_ORIGIN.get(cls) == "#3", cls


def test_message_for_frontmatter_cat_a():
    assert (
        message_for(FrontmatterInvalid(Path("/n"), ValueError("x")))
        == "Frontmatter invalid"
    )
    assert message_for(MigrationError("x")) == "Migration aborted"
    assert message_for(XReservedCollision(Path("/n"), "x-valid-at")) == "X reserved collision"
    assert message_for(SubjectEmpty(Path("/n"))) == "Subject empty"


def test_rebuild_conflicts_message_includes_count():
    exc = CliRebuildConflicts(3)
    code, info = translate(exc)
    assert code == CLI_REBUILD_CONFLICTS
    assert info["cli_code"] == "CLI_REBUILD_CONFLICTS"
    assert "3" in info["detail"]


def test_cli_rebuild_conflicts_distinct_from_not_in_mvp0():
    """Commit 6 split: ``CLI_REBUILD_CONFLICTS`` no longer overloads 75.

    ``CLI_NOT_IN_MVP_0`` (75) is the honest-stub code for reserved commands
    (``index verify`` / ``vigentes`` / ``activos-ahora`` / ``expire`` /
    ``revalidate``). ``CLI_REBUILD_CONFLICTS`` is the ADR-10 honesty code for
    ``index rebuild`` conflicts — a distinct concern that previously shared 75
    and was only disambiguated by the symbolic ``cli_code``. Commit 6 splits it
    onto a fresh 90–99 slot (94) so the two are distinct even on the int exit
    code. Both stay Cat C (CLI-owned), never Cat A.
    """
    assert CLI_REBUILD_CONFLICTS == 94  # fresh slot, pinned for the regression guard
    assert CLI_NOT_IN_MVP_0 == 75
    assert CLI_REBUILD_CONFLICTS != CLI_NOT_IN_MVP_0
    # 94 lives in the 90–99 band (frontmatter Cat A took 90–93; 94 is the next
    # free CLI-owned slot). It must NOT collide with any Cat A exit.
    assert 94 not in CAT_A.values()
    # And the rebuild-conflicts exception still carries the symbolic name.
    code, info = translate(CliRebuildConflicts(1))
    assert code == 94
    assert info["cli_code"] == "CLI_REBUILD_CONFLICTS"