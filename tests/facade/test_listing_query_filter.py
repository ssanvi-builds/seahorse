"""Listing recall filters by the query substring (the 2026-10-17 UX fix).

In the listing regime the query used to be accepted and ignored: an agent
"searching" got an unfiltered newest-first listing and old episodes stayed
buried under the ``k`` newest (feni's K0-K8 under the herald capture). Now a
non-empty query filters case-insensitively — the whole query as one needle
(no tokenization) over ``subject``/``title``/``body`` — in BOTH the
current-state listing and the PIT slices. Deterministic order and the ``k``
truncation are untouched; the hybrid regime is untouched (it ranks with the
query through real retrieval).
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.disclosure.types import PITPoint
from seahorse.facade.types import FacadeConfig
from seahorse.facade.vigente_retriever import VigenteListingRetriever
from tests.facade.conftest import RecordingEngine, make_episode

T = datetime(2026, 6, 1, tzinfo=UTC)


def _retriever(eps: list) -> VigenteListingRetriever:
    engine = RecordingEngine()
    engine.vigente = eps
    return VigenteListingRetriever(
        engine=engine, clock=lambda: T, config=FacadeConfig(top_k=50)
    )


def _ep(ep_id: str, **kw) :
    return make_episode(ep_id, **kw)


class TestCurrentStateFilter:
    def test_query_filters_by_subject_substring(self) -> None:
        eps = [
            _ep("e2", created_at=datetime(2026, 1, 3, tzinfo=UTC), subject="beta note"),
            _ep("e1", created_at=datetime(2026, 1, 2, tzinfo=UTC), subject="alpha note"),
            _ep("e0", created_at=datetime(2026, 1, 1, tzinfo=UTC), subject="alphabet"),
        ]
        result = _retriever(eps).recall("alpha", pit=None)
        # Substring semantics: "alpha" matches both; newest-first kept.
        assert [c.ep_id for c in result] == ["e1", "e0"]

    def test_query_matches_body_when_subject_absent(self) -> None:
        eps = [
            _ep("e1", subject=None, body="the limine fix lives here"),
        ]
        result = _retriever(eps).recall("limine", pit=None)
        assert [c.ep_id for c in result] == ["e1"]

    def test_query_matches_title(self) -> None:
        eps = [
            _ep("e1", subject=None, title="Deployment Guide"),
        ]
        result = _retriever(eps).recall("deployment", pit=None)
        assert [c.ep_id for c in result] == ["e1"]

    def test_query_is_case_insensitive(self) -> None:
        eps = [_ep("e1", subject="Alpha Note")]
        result = _retriever(eps).recall("ALPHA", pit=None)
        assert [c.ep_id for c in result] == ["e1"]

    def test_no_match_returns_empty_not_full_listing(self) -> None:
        eps = [_ep("e1", subject="feni deploy")]
        result = _retriever(eps).recall("zzz-nothing", pit=None)
        assert result == ()

    def test_multi_word_query_is_one_needle(self) -> None:
        """The whole query is the needle: no tokenization, no AND-splitting."""
        eps = [_ep("e1", subject="feni k5 closed")]
        assert [c.ep_id for c in _retriever(eps).recall("feni k5", pit=None)] == ["e1"]
        assert _retriever(eps).recall("k5 feni", pit=None) == ()


class TestPitSliceFilter:
    def test_state_at_slice_filters_too(self) -> None:
        class _PitRepo:
            def __init__(self, eps: list) -> None:
                self._eps = eps

            def query_state_at(self, t, subject=None):
                return list(self._eps)

            def query_known_at(self, t, subject=None):
                return []

        eps = [
            _ep("e1", created_at=datetime(2026, 1, 2, tzinfo=UTC), subject="alpha"),
            _ep("e0", created_at=datetime(2026, 1, 1, tzinfo=UTC), subject="beta"),
        ]
        ret = VigenteListingRetriever(
            engine=RecordingEngine(),
            clock=lambda: T,
            config=FacadeConfig(top_k=50),
            pit_source=_PitRepo(eps),
        )
        result = ret.recall("beta", pit=PITPoint(kind="state_at", t=T))
        assert [c.ep_id for c in result] == ["e0"]


class TestOrderPreserved:
    def test_filtered_subset_keeps_deterministic_order(self) -> None:
        eps = [
            _ep("e-a", created_at=datetime(2026, 1, 3, tzinfo=UTC), subject="alpha one"),
            _ep("e-b", created_at=datetime(2026, 1, 2, tzinfo=UTC), subject="alpha two"),
            _ep("e-c", created_at=datetime(2026, 1, 1, tzinfo=UTC), subject="other"),
        ]
        # Same created_at across the matched pair -> ep_id asc tie-break.
        result = _retriever(eps).recall("alpha", pit=None)
        assert [c.ep_id for c in result] == ["e-a", "e-b"]