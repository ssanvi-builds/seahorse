"""Tests for the #12 facade error vocabulary.

#12 raises ``SeahorseError`` (with a stable ``code`` + ``detail``) at the
boundary for shape/validation failures and for MVP-1 primitives refused in
MVP-0. Engine errors (``EngineError``) are propagated verbatim — the caller
matches on ``err.code`` either way.
"""

from __future__ import annotations

import pytest

from seahorse.facade import errors
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


class TestSeahorseError:
    def test_carries_stable_code_and_detail(self) -> None:
        err = SeahorseError(code="E_X", detail="boom")
        assert err.code == "E_X"
        assert err.detail == "boom"
        assert "E_X" in str(err)
        assert "boom" in str(err)

    def test_is_an_exception(self) -> None:
        with pytest.raises(SeahorseError, match="E_X") as exc_info:
            raise SeahorseError(code="E_X", detail="boom")
        assert exc_info.value.code == "E_X"

    def test_code_and_detail_are_keyword_only(self) -> None:
        # The keyword-only signature prevents positional drift at raise sites.
        with pytest.raises(TypeError):
            SeahorseError("E_X", "boom")  # type: ignore[misc]


class TestSubclassesPresetCode:
    def test_invalid_pit_kind(self) -> None:
        err = InvalidPITKind("future")
        assert isinstance(err, SeahorseError)
        assert err.code == E_INVALID_PIT_KIND
        assert err.kind == "future"

    def test_pit_recall_not_supported(self) -> None:
        err = PitRecallNotSupportedMVP0()
        assert isinstance(err, SeahorseError)
        assert err.code == E_PIT_RECALL_MVP_0

    def test_empty_query(self) -> None:
        err = EmptyQueryError()
        assert isinstance(err, SeahorseError)
        assert err.code == E_EMPTY_QUERY


class TestCodeConstantsAreUnique:
    def test_all_codes_are_distinct_strings(self) -> None:
        codes = [
            E_EMPTY_BODY,
            E_MISSING_SOURCE_TYPE,
            E_INVALID_EXTRACTION_MODE,
            E_EMPTY_QUERY,
            E_INVALID_PIT_KIND,
            E_PIT_REQUIRES_T,
            E_NOT_IN_MVP_0_1,
            E_PIT_RECALL_MVP_0,
        ]
        assert all(isinstance(c, str) and c.startswith("E_") for c in codes)
        assert len(set(codes)) == len(codes)

    def test_module_exposes_all_codes(self) -> None:
        for name in (
            "E_EMPTY_BODY",
            "E_MISSING_SOURCE_TYPE",
            "E_INVALID_EXTRACTION_MODE",
            "E_EMPTY_QUERY",
            "E_INVALID_PIT_KIND",
            "E_PIT_REQUIRES_T",
            "E_NOT_IN_MVP_0_1",
            "E_PIT_RECALL_MVP_0",
        ):
            assert hasattr(errors, name)