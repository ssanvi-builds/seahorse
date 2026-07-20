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

import pytest

from seahorse.cli.errors import (
    CliConfigInvalid,
    CliError,
    CliNotInMVP0,
    CliUsageError,
    CliVaultNotFound,
)
from seahorse.cli.exit_codes import (
    CAT_A,
    CAT_B,
    CLI_CONFIG_INVALID,
    CLI_NOT_IN_MVP_0,
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


def test_cat_a_table_is_17_codes_no_collision_with_75():
    """17 Cat A codes, all in 64–81, none using 75 (reserved for Cat C)."""
    assert len(CAT_A) == 17
    exits = list(CAT_A.values())
    assert len(set(exits)) == 17  # unique
    assert all(64 <= e <= 81 for e in exits)
    assert 75 not in exits


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


def test_cat_b_table_is_six_classes():
    assert len(CAT_B) == 6
    assert sorted(CAT_B.values()) == [84, 85, 86, 87, 88, 89]


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