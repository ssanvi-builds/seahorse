"""Validate the progressive disclosure payload types match the signed contract.

Guards the frozen shapes (``IndexRow``, ``TimelineEntry``, ``TimelineWindow``,
``FullDetail``, ``EpisodeProvenance``, ``PITPoint``), the constant pins, the
first-release / later-release axis split, and the deterministic truncation that
makes the token target reproducible.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from seahorse.contracts.engine import FreshnessView
from seahorse.contracts.episode import Episode
from seahorse.disclosure import types as dt
from seahorse.disclosure.types import (
    MAX_FULL_BATCH,
    MAX_TIMELINE_WINDOW,
    MVP0_AXES,
    SUBJECT_MAX_CHARS,
    SUMMARY_MAX_CHARS,
    TOP_K,
    EpisodeProvenance,
    FullBatchTooLarge,
    FullDetail,
    IndexRow,
    NotInMVP0,
    PitFullNotSupported,
    PITPoint,
    TimelineEntry,
    TimelineWindow,
)

_NOW = datetime(2026, 7, 16, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Constant pins.
# ---------------------------------------------------------------------------


def test_constant_pins_match_signed_contract():
    assert TOP_K == 10
    assert MAX_TIMELINE_WINDOW == 20
    assert MAX_FULL_BATCH == 5
    assert SUMMARY_MAX_CHARS == 200
    assert SUBJECT_MAX_CHARS == 160


def test_mvp0_axes_exactly_two_and_mvp1_axes_excluded():
    assert frozenset({"supersedes_chain", "fact_id_scope"}) == MVP0_AXES
    for mv1 in ("created_at", "valid_at", "graph_bfs"):
        assert mv1 not in MVP0_AXES


# ---------------------------------------------------------------------------
# IndexRow — frozen, no body, score passthrough, freshness flags.
# ---------------------------------------------------------------------------


def _make_index_row(**overrides) -> IndexRow:
    base = {
        "ep_id": "e1",
        "fact_id": "abc",
        "subject": "S",
        "title": None,
        "summary": None,
        "cognitive_type": "fact",
        "skip_extraction": False,
        "valid_at": None,
        "invalid_at": None,
        "created_at": _NOW,
        "score": 0.0,
        "stale": False,
        "pending_ingest": False,
    }
    base.update(overrides)
    return IndexRow(**base)


def test_index_row_has_exactly_13_fields():
    assert len(dataclasses.fields(IndexRow)) == 13


def test_index_row_field_names_match_signed_contract():
    names = {f.name for f in dataclasses.fields(IndexRow)}
    expected = {
        "ep_id",
        "fact_id",
        "subject",
        "title",
        "summary",
        "cognitive_type",
        "skip_extraction",
        "valid_at",
        "invalid_at",
        "created_at",
        "score",
        "stale",
        "pending_ingest",
    }
    assert names == expected


def test_index_row_has_no_body_field():
    row = _make_index_row()
    assert not hasattr(row, "body")
    assert not hasattr(row, "body_md")


def test_index_row_is_frozen():
    row = _make_index_row()
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.score = 0.99  # type: ignore[misc]


def test_index_row_score_is_passthrough_float_not_none():
    # score is a float (passthrough from hybrid retrieval); never None on the INDEX level.
    row = _make_index_row(score=0.42)
    assert row.score == 0.42
    assert isinstance(row.score, float)


# ---------------------------------------------------------------------------
# TimelineEntry — score ALWAYS None in the first release.
# ---------------------------------------------------------------------------


def test_timeline_entry_score_defaults_none_and_is_optional():
    entry = TimelineEntry(
        ep_id="e1",
        fact_id="abc",
        subject="S",
        title=None,
        summary=None,
        cognitive_type="fact",
        valid_at=None,
        invalid_at=None,
        created_at=_NOW,
        supersedes=None,
    )
    assert entry.score is None


def test_timeline_entry_has_no_body_field():
    entry = TimelineEntry(
        ep_id="e1",
        fact_id="abc",
        subject="S",
        title=None,
        summary=None,
        cognitive_type="fact",
        valid_at=None,
        invalid_at=None,
        created_at=_NOW,
        supersedes=None,
    )
    assert not hasattr(entry, "body")
    assert not hasattr(entry, "body_md")


def test_timeline_entry_is_frozen():
    entry = TimelineEntry(
        ep_id="e1",
        fact_id="abc",
        subject="S",
        title=None,
        summary=None,
        cognitive_type="fact",
        valid_at=None,
        invalid_at=None,
        created_at=_NOW,
        supersedes=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.ep_id = "x"  # type: ignore[misc]


def test_timeline_window_holds_entries_tuple_and_axis():
    entry = TimelineEntry(
        ep_id="e1",
        fact_id="abc",
        subject="S",
        title=None,
        summary=None,
        cognitive_type="fact",
        valid_at=None,
        invalid_at=None,
        created_at=_NOW,
        supersedes=None,
    )
    win = TimelineWindow(anchor_ep_id="e1", axis="supersedes_chain", entries=(entry,))
    assert win.anchor_ep_id == "e1"
    assert win.axis == "supersedes_chain"
    assert isinstance(win.entries, tuple)
    assert win.pit is None


# ---------------------------------------------------------------------------
# FullDetail — the ONLY level that carries an Episode (with body).
# ---------------------------------------------------------------------------


def _make_episode(**overrides) -> Episode:
    base = {
        "id": "e1",
        "created_at": _NOW,
        "schema_version": "1.1",
        "provenance": {"agent_id": "a", "source_type": "human"},
        "body": "body text",
        "subject": "S",
        "fact_id": "abc",
    }
    base.update(overrides)
    return Episode(**base)


def test_full_detail_carries_episode_with_body():
    ep = _make_episode()
    fv = FreshnessView(fact_id="abc", age_days=0, stale=False, pending_ingest=False, regime="human")
    prov = EpisodeProvenance(
        agent_id="a", session_id=None, source_type="human", extraction_mode=None, model_used=None
    )
    fd = FullDetail(episode=ep, provenance=prov, freshness=fv)
    assert fd.episode.body == "body text"
    assert fd.pit is None


def test_episode_provenance_is_typed_and_all_nullable():
    prov = EpisodeProvenance(
        agent_id=None, session_id=None, source_type=None, extraction_mode=None, model_used=None
    )
    assert prov.agent_id is None
    assert prov.session_id is None
    assert prov.source_type is None
    assert prov.extraction_mode is None
    assert prov.model_used is None


# ---------------------------------------------------------------------------
# PITPoint — the two PIT axes.
# ---------------------------------------------------------------------------


def test_pitpoint_carries_kind_and_t():
    p = PITPoint(kind="state_at", t=_NOW)
    assert p.kind == "state_at"
    assert p.t == _NOW


# ---------------------------------------------------------------------------
# Exceptions — fail-loud, typed, carry context.
# ---------------------------------------------------------------------------


def test_full_batch_too_large_carries_requested_and_cap():
    err = FullBatchTooLarge(8, MAX_FULL_BATCH)
    assert err.requested == 8
    assert err.cap == MAX_FULL_BATCH
    assert "8" in str(err) and "5" in str(err)


def test_pit_full_not_supported_is_exception():
    assert issubclass(PitFullNotSupported, Exception)
    # Constructible with no args (first-release guard).
    err = PitFullNotSupported()
    assert isinstance(err, Exception)


def test_not_in_mvp0_carries_axis():
    err = NotInMVP0("graph_bfs")
    assert err.axis == "graph_bfs"
    assert "graph_bfs" in str(err)


def test_module_exposes_all_names():
    for name in (
        "TOP_K",
        "MAX_TIMELINE_WINDOW",
        "MAX_FULL_BATCH",
        "SUMMARY_MAX_CHARS",
        "SUBJECT_MAX_CHARS",
        "TimelineAxis",
        "MVP0_AXES",
        "PITPoint",
        "IndexRow",
        "TimelineEntry",
        "TimelineWindow",
        "EpisodeProvenance",
        "FullDetail",
        "FullBatchTooLarge",
        "PitFullNotSupported",
        "NotInMVP0",
    ):
        assert hasattr(dt, name), name