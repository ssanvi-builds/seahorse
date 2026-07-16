"""Tests for ``recall_timeline`` / ``recall_full`` — delegation to #8 shaper.

``recall_timeline`` delegates to ``materialize_timeline(anchor, axis, pit)``
(no ``now``). ``recall_full`` delegates to ``materialize_full(ep_ids, pit,
now)``; ``len > MAX_FULL_BATCH`` raises ``FullBatchTooLarge`` BEFORE any fetch;
PIT in FULL surfaces as #8's ``PitFullNotSupported``. PIT kinds are validated
at the border.
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

    def test_no_now_forwarded(self, facade, shaper) -> None:
        # materialize_timeline has no `now` parameter — confirm it is not passed.
        shaper.timeline_result = TimelineWindow(
            anchor_ep_id="e1", axis="supersedes_chain", entries=(), pit=None
        )
        facade.recall_timeline("e1")
        assert "now" not in shaper.timeline_calls[0]

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

    def test_batch_too_large_before_fetch(self, facade, shaper) -> None:
        too_many = [f"e{i}" for i in range(MAX_FULL_BATCH + 1)]
        with pytest.raises(FullBatchTooLarge):
            facade.recall_full(too_many)
        assert shaper.full_calls == []

    def test_batch_at_cap_ok(self, facade, shaper) -> None:
        at_cap = [f"e{i}" for i in range(MAX_FULL_BATCH)]
        facade.recall_full(at_cap)
        assert len(shaper.full_calls) == 1

    def test_pit_full_surfaces_shaper_error(self, facade, shaper) -> None:
        # #8 raises PitFullNotSupported when pit is provided to FULL.
        shaper.full_raise = PitFullNotSupported()
        pit = PITPoint(kind="state_at", t=datetime(2026, 1, 1, tzinfo=UTC))
        with pytest.raises(PitFullNotSupported):
            facade.recall_full(["e1"], pit=pit)

    def test_invalid_pit_kind_rejected_before_shaper(self, facade, shaper) -> None:
        pit = PITPoint(kind="future", t=datetime(2026, 1, 1, tzinfo=UTC))  # type: ignore[arg-type]
        with pytest.raises(InvalidPITKind):
            facade.recall_full(["e1"], pit=pit)
        assert shaper.full_calls == []

    def test_now_from_clock(self, facade, shaper) -> None:
        facade.recall_full(["e1"])
        assert shaper.full_calls[0]["now"] is not None