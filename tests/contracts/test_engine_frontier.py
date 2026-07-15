"""Validate the #2-owned frontier extension: WriteResult + FreshnessView.

SO-8c (f6-signoffs.md): ``WriteResult`` separates the episode UUID (``ep_id``)
from the subject hash (``fact_id = SHA-256(subject)[:32]``). The 4-field shape is
the stable frontier #12/#13/#14/#16 consume; a silent field add/remove here
fails the build. ``FreshnessView`` (5 fields) is the #13 freshness snapshot.
"""

from __future__ import annotations

import dataclasses
import typing

import pytest

from seahorse.contracts import FreshnessView, WriteResult  # re-export path
from seahorse.contracts import engine as engine_mod


def test_write_result_has_exactly_4_fields():
    # SO-8c: ep_id, fact_id, status, collisions_detected.
    assert len(dataclasses.fields(WriteResult)) == 4


def test_write_result_field_names_match_signed_contract():
    names = {f.name for f in dataclasses.fields(WriteResult)}
    assert names == {"ep_id", "fact_id", "status", "collisions_detected"}


def test_write_result_is_frozen():
    wr = WriteResult(ep_id="e1", fact_id="f1", status="ACTIVE", collisions_detected=[])
    with pytest.raises(dataclasses.FrozenInstanceError):
        wr.status = "COLLISION"  # type: ignore[misc]


def test_write_result_collision_shape_uses_none_ids():
    # SO-3b fail-loud: on collision ep_id and fact_id are None.
    wr = WriteResult(ep_id=None, fact_id=None, status="COLLISION", collisions_detected=[object()])
    assert wr.ep_id is None
    assert wr.fact_id is None
    assert wr.status == "COLLISION"
    assert wr.collisions_detected  # non-empty


def test_write_result_ep_id_and_fact_id_are_distinct_concepts():
    # SO-8c: ep_id is the episode UUID; fact_id is SHA-256(subject)[:32].
    wr = WriteResult(
        ep_id="0195d3e4-uuid-v7", fact_id="a1b2c3d4", status="ACTIVE", collisions_detected=[]
    )
    assert wr.ep_id != wr.fact_id


def test_freshness_view_has_exactly_5_fields():
    assert len(dataclasses.fields(FreshnessView)) == 5


def test_freshness_view_field_names_match_signed_contract():
    names = {f.name for f in dataclasses.fields(FreshnessView)}
    assert names == {"fact_id", "age_days", "stale", "pending_ingest", "regime"}


def test_freshness_view_fact_id_accepts_none():
    # fact_id is str | None: an episode with no derivable subject has fact_id None.
    hints = typing.get_type_hints(FreshnessView)
    assert hints["fact_id"] == str | None
    fv = FreshnessView(
        fact_id=None, age_days=0, stale=False, pending_ingest=False, regime="agent"
    )
    assert fv.fact_id is None


def test_freshness_view_is_frozen():
    fv = FreshnessView(fact_id="f1", age_days=3, stale=False, pending_ingest=True, regime="agent")
    with pytest.raises(dataclasses.FrozenInstanceError):
        fv.age_days = 99  # type: ignore[misc]


def test_engine_module_exposes_write_result_and_freshness_view():
    assert engine_mod.WriteResult is WriteResult
    assert engine_mod.FreshnessView is FreshnessView