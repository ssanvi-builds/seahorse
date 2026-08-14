"""Tests for ``recall_timeline`` / ``recall_full`` — delegation to the
disclosure shaper.

``recall_timeline`` delegates to ``materialize_timeline(anchor, axis, pit)``
(no ``now``). ``recall_full`` delegates to ``materialize_full(ep_ids, pit,
now)``; the ``MAX_FULL_BATCH`` cap is the disclosure shaper's domain guard
(the facade does NOT replicate it — it delegates verbatim and surfaces the
disclosure shaper's ``FullBatchTooLarge``); PIT in FULL surfaces as the
disclosure shaper's ``PitFullNotSupported``. PIT kinds are validated at the
border (the facade's only FULL-level check), and that border check fires before
the disclosure shaper's batch guard.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.disclosure.types import (
    MAX_FULL_BATCH,
    FullBatchTooLarge,
    PitFullNotSupported,
    PITPoint,
    TimelineWindow,
)
from seahorse.facade.errors import InvalidPITKind


class TestRecallTimeline:
    def test_delegates_to_materialize_timeline(self, facade, shaper) -> None:
        shaper.timeline_result = TimelineWindow(
            anchor_ep_id="e1", axis="supersedes_chain", entries=(), pit=None
        )
        facade.recall_timeline("e1")
        assert len(shaper.timeline_calls) == 1
        call = shaper.timeline_calls[0]
        assert call["anchor"] == "e1"
        assert call["axis"] == "supersedes_chain"
        assert call["pit"] is None

    def test_custom_axis_forwarded(self, facade, shaper) -> None:
        shaper.timeline_result = TimelineWindow(
            anchor_ep_id="e1", axis="fact_id_scope", entries=(), pit=None
        )
        facade.recall_timeline("e1", axis="fact_id_scope")
        assert shaper.timeline_calls[0]["axis"] == "fact_id_scope"

    def test_pit_forwarded(self, facade, shaper) -> None:
        pit = PITPoint(kind="state_at", t=datetime(2026, 1, 1, tzinfo=UTC))
        shaper.timeline_result = TimelineWindow(
            anchor_ep_id="e1", axis="supersedes_chain", entries=(), pit=pit
        )
        facade.recall_timeline("e1", pit=pit)
        assert shaper.timeline_calls[0]["pit"] is pit

    def test_now_forwarded_from_clock(self, facade, shaper, clock) -> None:
        # materialize_timeline gains `now` so graph_bfs can resolve pit=None →
        # state_at(now) (no silent known_at). The facade forwards its clock; the
        # first-release axes ignore it.
        shaper.timeline_result = TimelineWindow(
            anchor_ep_id="e1", axis="supersedes_chain", entries=(), pit=None
        )
        facade.recall_timeline("e1")
        assert shaper.timeline_calls[0]["now"] == clock()

    def test_invalid_pit_kind_rejected(self, facade, shaper) -> None:
        pit = PITPoint(kind="future", t=datetime(2026, 1, 1, tzinfo=UTC))  # type: ignore[arg-type]
        with pytest.raises(InvalidPITKind):
            facade.recall_timeline("e1", pit=pit)
        assert shaper.timeline_calls == []


class TestRecallFull:
    def test_delegates_to_materialize_full(self, facade, shaper) -> None:
        facade.recall_full(["e1"])
        assert len(shaper.full_calls) == 1
        call = shaper.full_calls[0]
        assert call["ep_ids"] == ["e1"]
        assert call["pit"] is None

    def test_returns_shaper_result_verbatim(self, facade, shaper) -> None:
        facade.recall_full(["e1"])
        # RecordingShaper returns [] by default
        assert facade.recall_full(["e1"]) == []

    def test_batch_too_large_owned_by_shaper(self, facade, shaper) -> None:
        """The primitives facade must NOT pre-empt the disclosure shaper's
        MAX_FULL_BATCH check (owned by the disclosure shaper).

        The facade delegates verbatim and surfaces the disclosure shaper's
        FullBatchTooLarge. The call reaches the shaper — the primitives facade
        does not short-circuit the disclosure shaper's domain guard.
        """
        too_many = [f"e{i}" for i in range(MAX_FULL_BATCH + 1)]
        shaper.full_raise = FullBatchTooLarge(len(too_many), MAX_FULL_BATCH)
        with pytest.raises(FullBatchTooLarge):
            facade.recall_full(too_many)
        assert len(shaper.full_calls) == 1
        assert shaper.full_calls[0]["ep_ids"] == too_many

    def test_batch_at_cap_ok(self, facade, shaper) -> None:
        at_cap = [f"e{i}" for i in range(MAX_FULL_BATCH)]
        facade.recall_full(at_cap)
        assert len(shaper.full_calls) == 1

    def test_batch_at_cap_forwards_ep_ids(self, facade, shaper) -> None:
        at_cap = [f"e{i}" for i in range(MAX_FULL_BATCH)]
        facade.recall_full(at_cap)
        assert shaper.full_calls[0]["ep_ids"] == at_cap

    def test_pit_full_surfaces_shaper_error(self, facade, shaper) -> None:
        # The disclosure shaper raises PitFullNotSupported when pit is provided
        # to FULL.
        shaper.full_raise = PitFullNotSupported()
        pit = PITPoint(kind="state_at", t=datetime(2026, 1, 1, tzinfo=UTC))
        with pytest.raises(PitFullNotSupported):
            facade.recall_full(["e1"], pit=pit)

    def test_pit_forwarded_to_full(self, facade, shaper) -> None:
        # Guard against a regression where the facade drops pit to None before
        # delegating. Inspect the recorded call, not just the raised exception.
        pit = PITPoint(kind="state_at", t=datetime(2026, 1, 1, tzinfo=UTC))
        shaper.full_raise = PitFullNotSupported()
        with pytest.raises(PitFullNotSupported):
            facade.recall_full(["e1"], pit=pit)
        assert len(shaper.full_calls) == 1
        assert shaper.full_calls[0]["pit"] is pit  # forwarded verbatim

    def test_invalid_pit_kind_rejected_before_shaper(self, facade, shaper) -> None:
        pit = PITPoint(kind="future", t=datetime(2026, 1, 1, tzinfo=UTC))  # type: ignore[arg-type]
        with pytest.raises(InvalidPITKind):
            facade.recall_full(["e1"], pit=pit)
        assert shaper.full_calls == []

    def test_invalid_pit_kind_wins_over_oversized_batch(self, facade, shaper) -> None:
        # Border validation (pit.kind, facade-owned) fires BEFORE delegation to
        # the disclosure shaper's domain batch guard: an invalid pit kind raises
        # InvalidPITKind and the disclosure shaper is never reached, even with
        # an oversized batch.
        too_many = [f"e{i}" for i in range(MAX_FULL_BATCH + 1)]
        pit = PITPoint(kind="future", t=datetime(2026, 1, 1, tzinfo=UTC))  # type: ignore[arg-type]
        with pytest.raises(InvalidPITKind):
            facade.recall_full(too_many, pit=pit)
        assert shaper.full_calls == []

    def test_now_from_clock(self, facade, shaper) -> None:
        facade.recall_full(["e1"])
        assert shaper.full_calls[0]["now"] is not None