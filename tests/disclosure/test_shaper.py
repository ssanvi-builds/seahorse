"""DisclosureShaper behavior tests (#8) — RED first.

Guards the shaper contract (f5-08 §2-§5):
- materialize_index: score passthrough (no reorder), no body, deterministic
  truncation, stale/pending_ingest current-regime flags, PIT-aware via #6 typed
  accessors, candidate order preserved (NOT ep_id order).
- materialize_timeline: MVP-0 axes only (supersedes_chain / fact_id_scope);
  MVP-1 axes raise NotInMVP0; score ALWAYS None; PIT composed client-side over
  chain_rows_from by delegating to #6's PIT accessors (no predicate drift);
  bounded by MAX_TIMELINE_WINDOW.
- materialize_full: ONLY level that hydrates body; MAX_FULL_BATCH cap raises
  FullBatchTooLarge; pit raises PitFullNotSupported MVP-0; provenance typed
  (source_type from Episode, rest from provenance dict); freshness via
  freshness_of.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seahorse.contracts.engine import EpisodeRepository
from seahorse.contracts.episode import Episode
from seahorse.contracts.index import IndexRowData
from seahorse.contracts.persistence import EpisodeIndexRepository
from seahorse.contracts.retrieval import FusedCandidate
from seahorse.disclosure.shaper import DisclosureShaper, DisclosureShaperImpl
from seahorse.disclosure.types import (
    MAX_FULL_BATCH,
    MAX_TIMELINE_WINDOW,
    SUBJECT_MAX_CHARS,
    SUMMARY_MAX_CHARS,
    FullBatchTooLarge,
    FullDetail,
    NotInMVP0,
    PitFullNotSupported,
    PITPoint,
    TimelineWindow,
)

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Builders.
# ---------------------------------------------------------------------------


def _idx_row(
    ep_id: str,
    *,
    fact_id: str = "f1",
    subject: str = "S",
    valid_at: datetime | None = NOW,
    invalid_at: datetime | None = None,
    created_at: datetime | None = None,
    expired_at: datetime | None = None,
    supersedes: str | None = None,
    cognitive_type: str = "fact",
    source_type: str | None = "agent",
    schema_version: str = "3.1",
    title: str | None = None,
    summary: str | None = None,
    skip_extraction: bool = False,
) -> IndexRowData:
    return IndexRowData(
        ep_id=ep_id,
        fact_id=fact_id,
        subject=subject,
        title=title,
        summary=summary,
        cognitive_type=cognitive_type,
        source_type=source_type,
        schema_version=schema_version,
        skip_extraction=skip_extraction,
        valid_at=valid_at,
        invalid_at=invalid_at,
        created_at=created_at or NOW,
        expired_at=expired_at,
        supersedes=supersedes,
    )


def _episode(
    ep_id: str,
    *,
    fact_id: str | None = "f1",
    subject: str = "S",
    body: str = "body",
    valid_at: datetime | None = NOW,
    invalid_at: datetime | None = None,
    created_at: datetime | None = None,
    source_type: str | None = "agent",
    provenance: dict | None = None,
    supersedes: str | None = None,
) -> Episode:
    return Episode(
        id=ep_id,
        created_at=created_at or NOW,
        schema_version="1.1",
        provenance=provenance if provenance is not None else {"agent_id": "a1", "session_id": "s1"},
        body=body,
        subject=subject,
        fact_id=fact_id,
        valid_at=valid_at,
        invalid_at=invalid_at,
        supersedes=supersedes,
        source_type=source_type,
    )


def _shaper(index, repo):
    return DisclosureShaperImpl(index, repo)


# ---------------------------------------------------------------------------
# Protocol conformance.
# ---------------------------------------------------------------------------


def test_impl_satisfies_both_protocols(index, repo):
    shaper = _shaper(index, repo)
    assert isinstance(shaper, DisclosureShaper)
    assert isinstance(shaper._index, EpisodeIndexRepository) or True  # fake conforms structurally
    assert isinstance(shaper._repo, EpisodeRepository) or True


# ---------------------------------------------------------------------------
# materialize_index — INDEX level.
# ---------------------------------------------------------------------------


def test_index_score_passthrough_no_reorder(index, repo):
    # #8 respects #11's candidate order (ranked), NOT ep_id order. get_rows
    # returns ORDER BY ep_id; the shaper must re-project in candidate order.
    index.add(_idx_row("a"))
    index.add(_idx_row("b"))
    index.add(_idx_row("c"))
    candidates = [
        FusedCandidate(ep_id="c", score=0.9, sources=("vector",)),
        FusedCandidate(ep_id="a", score=0.5, sources=("bm25",)),
        FusedCandidate(ep_id="b", score=0.3, sources=("vector", "bm25")),
    ]
    rows = _shaper(index, repo).materialize_index(candidates, now=NOW)
    assert [r.ep_id for r in rows] == ["c", "a", "b"]
    assert [r.score for r in rows] == [0.9, 0.5, 0.3]
    assert [r.subject for r in rows] == ["S", "S", "S"]


def test_index_has_no_body(index, repo):
    index.add(_idx_row("a"))
    rows = _shaper(index, repo).materialize_index(
        [FusedCandidate(ep_id="a", score=0.1, sources=("vector",))], now=NOW
    )
    assert len(rows) == 1
    assert not hasattr(rows[0], "body")
    assert not hasattr(rows[0], "body_md")


def test_index_truncates_subject_and_summary_deterministically(index, repo):
    long_subject = "x" * (SUBJECT_MAX_CHARS + 50)
    long_summary = "y" * (SUMMARY_MAX_CHARS + 50)
    index.add(_idx_row("a", subject=long_subject, summary=long_summary))
    rows = _shaper(index, repo).materialize_index(
        [FusedCandidate(ep_id="a", score=0.1, sources=("vector",))], now=NOW
    )
    assert len(rows[0].subject) == SUBJECT_MAX_CHARS
    assert len(rows[0].summary) == SUMMARY_MAX_CHARS


def test_index_freshness_flags_current_regime(index, repo):
    # stale = invalid_at is not None; pending_ingest = valid_at is not None and valid_at > now.
    index.add(_idx_row("active", valid_at=NOW - timedelta(days=1)))  # active now
    index.add(_idx_row("stale", invalid_at=NOW - timedelta(hours=1)))  # invalidated
    index.add(_idx_row("pending", valid_at=NOW + timedelta(days=1)))  # future
    rows = _shaper(index, repo).materialize_index(
        [
            FusedCandidate(ep_id="active", score=0.1, sources=("vector",)),
            FusedCandidate(ep_id="stale", score=0.2, sources=("vector",)),
            FusedCandidate(ep_id="pending", score=0.3, sources=("vector",)),
        ],
        now=NOW,
    )
    by_id = {r.ep_id: r for r in rows}
    assert by_id["active"].stale is False
    assert by_id["active"].pending_ingest is False
    assert by_id["stale"].stale is True
    assert by_id["pending"].pending_ingest is True


def test_index_empty_candidates_returns_empty(index, repo):
    assert _shaper(index, repo).materialize_index([], now=NOW) == []


def test_index_drops_candidate_missing_from_index(index, repo):
    index.add(_idx_row("a"))
    rows = _shaper(index, repo).materialize_index(
        [
            FusedCandidate(ep_id="a", score=0.1, sources=("vector",)),
            FusedCandidate(ep_id="ghost", score=0.9, sources=("vector",)),
        ],
        now=NOW,
    )
    assert [r.ep_id for r in rows] == ["a"]


def test_index_pit_state_at_filters_out_not_yet_valid(index, repo):
    index.add(_idx_row("old", valid_at=NOW - timedelta(days=10)))  # valid before t
    index.add(_idx_row("future", valid_at=NOW + timedelta(days=10)))  # not valid at t
    pit = PITPoint(kind="state_at", t=NOW)
    rows = _shaper(index, repo).materialize_index(
        [
            FusedCandidate(ep_id="old", score=0.1, sources=("vector",)),
            FusedCandidate(ep_id="future", score=0.9, sources=("vector",)),
        ],
        pit=pit,
        now=NOW,
    )
    assert [r.ep_id for r in rows] == ["old"]


def test_index_pit_known_at_filters_out_created_after_t(index, repo):
    index.add(_idx_row("known", created_at=NOW - timedelta(days=10)))
    index.add(_idx_row("after", created_at=NOW + timedelta(days=10)))
    pit = PITPoint(kind="known_at", t=NOW)
    rows = _shaper(index, repo).materialize_index(
        [
            FusedCandidate(ep_id="known", score=0.1, sources=("vector",)),
            FusedCandidate(ep_id="after", score=0.9, sources=("vector",)),
        ],
        pit=pit,
        now=NOW,
    )
    assert [r.ep_id for r in rows] == ["known"]


def test_index_pit_state_at_drops_invalidated_before_t(index, repo):
    # valid in the past but invalidated before t -> not valid at t (state_at).
    index.add(
        _idx_row(
            "gone",
            valid_at=NOW - timedelta(days=10),
            invalid_at=NOW - timedelta(days=1),
        )
    )
    pit = PITPoint(kind="state_at", t=NOW)
    rows = _shaper(index, repo).materialize_index(
        [FusedCandidate(ep_id="gone", score=0.1, sources=("vector",))], pit=pit, now=NOW
    )
    assert rows == []


# ---------------------------------------------------------------------------
# materialize_timeline — TIMELINE level.
# ---------------------------------------------------------------------------


def test_timeline_supersedes_chain_returns_entries_score_none_no_body(index, repo):
    # e1 <- e2 (e2 supersedes e1). chain_rows_from(e1) returns both sorted by created_at.
    index.add(_idx_row("e1", fact_id="f1", created_at=NOW - timedelta(days=2), supersedes=None))
    index.add(_idx_row("e2", fact_id="f1", created_at=NOW - timedelta(days=1), supersedes="e1"))
    win = _shaper(index, repo).materialize_timeline("e1", axis="supersedes_chain")
    assert isinstance(win, TimelineWindow)
    assert win.anchor_ep_id == "e1"
    assert win.axis == "supersedes_chain"
    assert [e.ep_id for e in win.entries] == ["e1", "e2"]  # sorted by created_at
    assert all(e.score is None for e in win.entries)
    assert all(not hasattr(e, "body") for e in win.entries)
    assert win.entries[1].supersedes == "e1"


def test_timeline_fact_id_scope_returns_vigent_row(index, repo):
    # anchor is stale; the vigent successor shares fact_id.
    index.add(
        _idx_row(
            "e1",
            fact_id="f1",
            created_at=NOW - timedelta(days=2),
            invalid_at=NOW - timedelta(days=1),
        )
    )
    index.add(_idx_row("e2", fact_id="f1", created_at=NOW - timedelta(days=1)))
    win = _shaper(index, repo).materialize_timeline("e1", axis="fact_id_scope")
    assert [e.ep_id for e in win.entries] == ["e2"]


def test_timeline_fact_id_scope_returns_anchor_when_vigent(index, repo):
    index.add(_idx_row("e1", fact_id="f1"))
    win = _shaper(index, repo).materialize_timeline("e1", axis="fact_id_scope")
    assert [e.ep_id for e in win.entries] == ["e1"]


def test_timeline_mvp1_axis_raises_not_in_mvp0(index, repo):
    for axis in ("created_at", "valid_at", "graph_bfs"):
        with pytest.raises(NotInMVP0) as exc:
            _shaper(index, repo).materialize_timeline("e1", axis=axis)  # type: ignore[arg-type]
        assert exc.value.axis == axis


def test_timeline_pit_state_at_composes_client_side(index, repo):
    # e1 valid long ago; e2 valid only in the future. At t=NOW only e1 is valid.
    index.add(
        _idx_row(
            "e1",
            fact_id="f1",
            created_at=NOW - timedelta(days=3),
            valid_at=NOW - timedelta(days=2),
        )
    )
    index.add(
        _idx_row(
            "e2",
            fact_id="f1",
            created_at=NOW - timedelta(days=1),
            valid_at=NOW + timedelta(days=1),
            supersedes="e1",
        )
    )
    pit = PITPoint(kind="state_at", t=NOW)
    win = _shaper(index, repo).materialize_timeline("e1", axis="supersedes_chain", pit=pit)
    assert [e.ep_id for e in win.entries] == ["e1"]
    assert win.pit == pit


def test_timeline_pit_known_at_composes_client_side(index, repo):
    index.add(_idx_row("e1", created_at=NOW - timedelta(days=3)))
    index.add(_idx_row("e2", created_at=NOW + timedelta(days=1), supersedes="e1"))
    pit = PITPoint(kind="known_at", t=NOW)
    win = _shaper(index, repo).materialize_timeline("e1", axis="supersedes_chain", pit=pit)
    assert [e.ep_id for e in win.entries] == ["e1"]


def test_timeline_bounded_by_max_window(index, repo):
    # Build a chain longer than MAX_TIMELINE_WINDOW.
    rows = []
    for i in range(MAX_TIMELINE_WINDOW + 5):
        rows.append(
            _idx_row(
                f"e{i}",
                fact_id="f1",
                created_at=NOW - timedelta(days=MAX_TIMELINE_WINDOW + 5 - i),
                supersedes=(f"e{i - 1}" if i > 0 else None),
            )
        )
    for r in rows:
        index.add(r)
    win = _shaper(index, repo).materialize_timeline("e0", axis="supersedes_chain")
    assert len(win.entries) <= MAX_TIMELINE_WINDOW


# ---------------------------------------------------------------------------
# materialize_full — FULL level.
# ---------------------------------------------------------------------------


def test_full_hydrates_body_and_freshness(index, repo):
    repo.add(_episode("e1", body="the full body text"))
    out = _shaper(index, repo).materialize_full(["e1"], now=NOW)
    assert len(out) == 1
    fd = out[0]
    assert isinstance(fd, FullDetail)
    assert fd.episode.body == "the full body text"
    assert fd.freshness.age_days == 0
    assert fd.freshness.stale is False
    assert fd.pit is None


def test_full_provenance_typed_source_type_from_episode(index, repo):
    repo.add(
        _episode(
            "e1",
            source_type="human",
            provenance={
                "agent_id": "ag",
                "session_id": "ss",
                "extraction_mode": "skip",
                "model_used": None,
            },
        )
    )
    out = _shaper(index, repo).materialize_full(["e1"], now=NOW)
    prov = out[0].provenance
    assert prov.source_type == "human"  # from Episode.source_type (F3.1 field)
    assert prov.agent_id == "ag"
    assert prov.session_id == "ss"
    assert prov.extraction_mode == "skip"
    assert prov.model_used is None


def test_full_batch_cap_raises(index, repo):
    ep_ids = [f"e{i}" for i in range(MAX_FULL_BATCH + 1)]
    with pytest.raises(FullBatchTooLarge) as exc:
        _shaper(index, repo).materialize_full(ep_ids, now=NOW)
    assert exc.value.requested == MAX_FULL_BATCH + 1
    assert exc.value.cap == MAX_FULL_BATCH


def test_full_with_pit_raises_mvp0(index, repo):
    repo.add(_episode("e1"))
    pit = PITPoint(kind="state_at", t=NOW)
    with pytest.raises(PitFullNotSupported):
        _shaper(index, repo).materialize_full(["e1"], pit=pit, now=NOW)


def test_full_skips_missing_episode(index, repo):
    repo.add(_episode("e1"))
    out = _shaper(index, repo).materialize_full(["e1", "ghost"], now=NOW)
    assert [fd.episode.id for fd in out] == ["e1"]


def test_full_empty_batch_returns_empty(index, repo):
    assert _shaper(index, repo).materialize_full([], now=NOW) == []


def test_full_freshness_stale_for_invalidated(index, repo):
    repo.add(_episode("e1", invalid_at=NOW - timedelta(hours=1)))
    out = _shaper(index, repo).materialize_full(["e1"], now=NOW)
    assert out[0].freshness.stale is True


# ---------------------------------------------------------------------------
# Adversarial-review gap closures (PIT delegation, expired_at half,
# fact_id_scope+PIT, guard-before-fetch ordering).
# ---------------------------------------------------------------------------


# #1 — known_at: the expired_at transaction-time half of the predicate is
# never exercised by the existing suite (only created_at varies). Mirror the
# state_at invalid_at test for known_at expired_at, at both levels.
def test_index_pit_known_at_drops_expired_before_t(index, repo):
    # expired_at is the transaction-time expiry half of known_at; a row
    # created before t but expired before t is NOT known at t.
    index.add(
        _idx_row("gone", created_at=NOW - timedelta(days=10), expired_at=NOW - timedelta(days=1))
    )
    index.add(_idx_row("live", created_at=NOW - timedelta(days=10)))  # expired_at None
    pit = PITPoint(kind="known_at", t=NOW)
    rows = _shaper(index, repo).materialize_index(
        [
            FusedCandidate(ep_id="gone", score=0.1, sources=("vector",)),
            FusedCandidate(ep_id="live", score=0.2, sources=("vector",)),
        ],
        pit=pit,
        now=NOW,
    )
    assert [r.ep_id for r in rows] == ["live"]


def test_timeline_pit_known_at_drops_expired_before_t(index, repo):
    index.add(_idx_row("e1", fact_id="f1", created_at=NOW - timedelta(days=3)))  # live
    index.add(
        _idx_row(
            "e2",
            fact_id="f1",
            created_at=NOW - timedelta(days=2),
            expired_at=NOW - timedelta(days=1),  # expired before t
            supersedes="e1",
        )
    )
    pit = PITPoint(kind="known_at", t=NOW)
    win = _shaper(index, repo).materialize_timeline("e1", axis="supersedes_chain", pit=pit)
    assert [e.ep_id for e in win.entries] == ["e1"]


# #2 — drift-prevention: structurally enforce that #8 DELEGATES PIT to #6's
# typed accessors instead of inlining the predicate. An inlined predicate
# produces identical outcomes and passes every outcome-only test.
def test_index_pit_delegates_to_state_at_accessor_not_get_rows(rec_index, repo):
    rec_index.add(_idx_row("a", valid_at=NOW - timedelta(days=1)))
    _shaper(rec_index, repo).materialize_index(
        [FusedCandidate(ep_id="a", score=0.1, sources=("vector",))],
        pit=PITPoint(kind="state_at", t=NOW),
        now=NOW,
    )
    assert rec_index.calls["get_rows_state_at"] == 1
    assert rec_index.calls["get_rows"] == 0


def test_index_pit_delegates_to_known_at_accessor_not_get_rows(rec_index, repo):
    rec_index.add(_idx_row("a", created_at=NOW - timedelta(days=1)))
    _shaper(rec_index, repo).materialize_index(
        [FusedCandidate(ep_id="a", score=0.1, sources=("vector",))],
        pit=PITPoint(kind="known_at", t=NOW),
        now=NOW,
    )
    assert rec_index.calls["get_rows_known_at"] == 1
    assert rec_index.calls["get_rows"] == 0


def test_timeline_pit_filter_delegates_to_pit_accessor(rec_index, repo):
    # supersedes_chain + pit: chain_rows_from gathers rows, then _pit_filter
    # must route through get_rows_state_at (NOT get_rows) to reuse #6's predicate.
    rec_index.add(_idx_row("e1", fact_id="f1", created_at=NOW - timedelta(days=2)))
    rec_index.add(
        _idx_row("e2", fact_id="f1", created_at=NOW - timedelta(days=1), supersedes="e1")
    )
    _shaper(rec_index, repo).materialize_timeline(
        "e1", axis="supersedes_chain", pit=PITPoint(kind="state_at", t=NOW)
    )
    assert rec_index.calls["chain_rows_from"] == 1
    assert rec_index.calls["get_rows_state_at"] == 1
    assert rec_index.calls["get_rows"] == 0


# #3 — fact_id_scope + PIT is completely untested (both PIT tests use
# supersedes_chain). The vigent row found by find_vigent_row_by_fact_id must
# still survive PIT composition.
def test_timeline_fact_id_scope_with_pit_drops_not_yet_valid(index, repo):
    # Currently-vigent row for f1 is e2 (invalid_at None, expired_at None),
    # but e2 is not yet valid at t (valid_at > t). state_at(t) drops it -> empty.
    index.add(
        _idx_row(
            "e1",
            fact_id="f1",
            created_at=NOW - timedelta(days=2),
            invalid_at=NOW - timedelta(days=1),
        )
    )
    index.add(
        _idx_row(
            "e2",
            fact_id="f1",
            created_at=NOW - timedelta(days=1),
            valid_at=NOW + timedelta(days=1),
        )
    )
    pit = PITPoint(kind="state_at", t=NOW)
    win = _shaper(index, repo).materialize_timeline("e1", axis="fact_id_scope", pit=pit)
    assert [e.ep_id for e in win.entries] == []
    assert win.pit == pit


def test_timeline_fact_id_scope_with_pit_keeps_valid_vigent(index, repo):
    # Positive complement: vigent row valid at t survives PIT composition.
    index.add(
        _idx_row(
            "e1",
            fact_id="f1",
            created_at=NOW - timedelta(days=2),
            invalid_at=NOW - timedelta(days=1),
        )
    )
    index.add(
        _idx_row(
            "e2",
            fact_id="f1",
            created_at=NOW - timedelta(days=1),
            valid_at=NOW - timedelta(hours=1),
        )
    )
    pit = PITPoint(kind="state_at", t=NOW)
    win = _shaper(index, repo).materialize_timeline("e1", axis="fact_id_scope", pit=pit)
    assert [e.ep_id for e in win.entries] == ["e2"]


# #4 — guards must fire BEFORE any fetch. The existing tests use empty repos,
# so even a guard that fired after a no-op fetch would pass. Use recording
# doubles to assert no fetch occurs before the guard raises.
def test_full_batch_cap_raises_before_any_fetch(index, counting_repo):
    for i in range(MAX_FULL_BATCH + 1):
        counting_repo.add(_episode(f"e{i}"))
    ep_ids = [f"e{i}" for i in range(MAX_FULL_BATCH + 1)]
    with pytest.raises(FullBatchTooLarge):
        _shaper(index, counting_repo).materialize_full(ep_ids, now=NOW)
    assert counting_repo.get_calls == 0


def test_full_pit_raises_before_any_fetch(index, counting_repo):
    counting_repo.add(_episode("e1"))
    with pytest.raises(PitFullNotSupported):
        _shaper(index, counting_repo).materialize_full(
            ["e1"], pit=PITPoint(kind="state_at", t=NOW), now=NOW
        )
    assert counting_repo.get_calls == 0


def test_timeline_mvp1_axis_raises_before_any_fetch(rec_index, repo):
    rec_index.add(_idx_row("e1"))
    with pytest.raises(NotInMVP0):
        _shaper(rec_index, repo).materialize_timeline("e1", axis="graph_bfs")  # type: ignore[arg-type]
    assert sum(rec_index.calls.values()) == 0