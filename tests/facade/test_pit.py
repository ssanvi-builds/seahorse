"""Tests for ``MemoryFacade.build_pit`` — PITPoint construction + validation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.disclosure.types import PITPoint
from seahorse.facade.errors import E_PIT_REQUIRES_T, InvalidPITKind, SeahorseError


class TestBuildPit:
    def test_ready_pit_returned(self, facade) -> None:
        pit = PITPoint(kind="state_at", t=datetime(2026, 1, 1, tzinfo=UTC))
        assert facade.build_pit(pit) is pit

    def test_kind_and_t_build_pit(self, facade) -> None:
        t = datetime(2026, 1, 1, tzinfo=UTC)
        pit = facade.build_pit(pit_kind="state_at", t=t)
        assert pit is not None
        assert pit.kind == "state_at"
        assert pit.t == t

    def test_known_at_kind(self, facade) -> None:
        t = datetime(2026, 1, 1, tzinfo=UTC)
        pit = facade.build_pit(pit_kind="known_at", t=t)
        assert pit is not None
        assert pit.kind == "known_at"

    def test_all_none_returns_none(self, facade) -> None:
        assert facade.build_pit() is None

    def test_pit_wins_over_kind_t(self, facade) -> None:
        ready = PITPoint(kind="state_at", t=datetime(2026, 1, 1, tzinfo=UTC))
        # pit_kind/t given but pit wins; the kind is still validated.
        result = facade.build_pit(ready, pit_kind="known_at", t=datetime(2025, 1, 1, tzinfo=UTC))
        assert result is ready


class TestBuildPitValidation:
    def test_invalid_kind_in_ready_pit(self, facade) -> None:
        pit = PITPoint(kind="future", t=datetime(2026, 1, 1, tzinfo=UTC))  # type: ignore[arg-type]
        with pytest.raises(InvalidPITKind):
            facade.build_pit(pit)

    def test_invalid_kind_in_kwarg(self, facade) -> None:
        with pytest.raises(InvalidPITKind):
            facade.build_pit(pit_kind="future", t=datetime(2026, 1, 1, tzinfo=UTC))  # type: ignore[arg-type]

    def test_kind_without_t_raises(self, facade) -> None:
        with pytest.raises(SeahorseError) as exc:
            facade.build_pit(pit_kind="state_at")
        assert exc.value.code == E_PIT_REQUIRES_T

    def test_invalid_kind_without_t_raises_requires_t_first(self, facade) -> None:
        # Invalid pit_kind AND missing t: the t-check fires before kind
        # validation, so E_PIT_REQUIRES_T wins. The caller must supply a t
        # before learning the kind is invalid.
        with pytest.raises(SeahorseError) as exc:
            facade.build_pit(pit_kind="future")  # type: ignore[arg-type]
        assert exc.value.code == E_PIT_REQUIRES_T

    def test_kind_without_t_before_construction(self, facade) -> None:
        # No PITPoint is built when t is missing.
        with pytest.raises(SeahorseError):
            facade.build_pit(pit_kind="known_at")