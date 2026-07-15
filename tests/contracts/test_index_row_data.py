"""Validate IndexRowData matches the signed SO-2 2e contract (f5-08 §3.1).

The frozen 14-field shape is the stable frontier #8 owns and #6/#11/#16 consume.
This test guards the freeze: a silent field add/remove here fails the build.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from seahorse.contracts import index as index_mod
from seahorse.contracts.index import (
    MAX_HOPS_MVP1,
    HopsCapExceeded,
    IndexRowData,
    PITKind,
)

_BASE_ROW: dict = {
    "ep_id": "e1",
    "fact_id": "abc",
    "subject": "S",
    "title": None,
    "summary": None,
    "cognitive_type": "fact",
    "source_type": "agent",
    "schema_version": "3.1",
    "skip_extraction": False,
    "valid_at": None,
    "invalid_at": None,
    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    "expired_at": None,
    "supersedes": None,
}


def _make_row(**overrides) -> IndexRowData:
    return IndexRowData(**{**_BASE_ROW, **overrides})


def test_index_row_data_has_exactly_14_fields():
    # SO-2 2e freeze: 14 fields.
    assert len(dataclasses.fields(IndexRowData)) == 14


def test_index_row_data_field_names_match_signed_contract():
    names = {f.name for f in dataclasses.fields(IndexRowData)}
    expected = {
        "ep_id",
        "fact_id",
        "subject",
        "title",
        "summary",
        "cognitive_type",
        "source_type",
        "schema_version",
        "skip_extraction",
        "valid_at",
        "invalid_at",
        "created_at",
        "expired_at",
        "supersedes",
    }
    assert names == expected


def test_index_row_data_has_no_body_field():
    # The whole point of the row-level: NO body / body_md.
    row = _make_row()
    assert not hasattr(row, "body")
    assert not hasattr(row, "body_md")


def test_index_row_data_is_frozen():
    row = _make_row()
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.ep_id = "changed"  # type: ignore[misc]


def test_index_row_data_required_and_nullable_fields():
    # created_at is NOT NULL (F3.1); the rest of the bi-temporal fields are nullable.
    row = _make_row()
    assert row.created_at is not None
    assert row.valid_at is None  # PENDING_INGEST legitimate
    assert row.invalid_at is None
    assert row.expired_at is None
    assert row.supersedes is None


def test_pitkind_is_state_at_or_known_at():
    # ADR-03: two PIT axes, never mixed.
    import typing

    args = typing.get_args(PITKind)
    assert args == ("state_at", "known_at")


def test_max_hops_mvp1_is_two():
    assert MAX_HOPS_MVP1 == 2


def test_hops_cap_exceeded_carries_hops_and_cap():
    err = HopsCapExceeded(3, 2)
    assert err.hops == 3
    assert err.cap == 2
    assert "3" in str(err) and "2" in str(err)


def test_module_exposes_pitkind_constant_and_exception():
    assert index_mod.PITKind is PITKind
    assert index_mod.MAX_HOPS_MVP1 == 2
    assert issubclass(index_mod.HopsCapExceeded, Exception)
