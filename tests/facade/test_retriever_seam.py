"""Recall-policy extension-point test — ``recall`` policy is delegated to an
injected ``Retriever``.

The facade's first-release recall (current-state listing, no ranking, no PIT)
used to live inline in ``_recall_mvp0``. The change extracts it behind a
``Retriever`` extension point so swapping to the later-release hybrid regime
(``seahorse.retrieval.recall``) is a single-point change at the composition
root, not a 6+ touch-point edit across facade/MCP/CLI. These tests pin the
extension point: the facade delegates to the injected retriever, forwards its
``FusedCandidate`` output to the disclosure shaper's ``materialize_index``,
and returns the disclosure shaper's result verbatim. The retriever owns
ranking/filter/truncate; the facade owns boundary validation + the shaper call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from seahorse.contracts.retrieval import FusedCandidate
from seahorse.disclosure.types import IndexRow, PITPoint
from seahorse.facade.errors import EmptyQueryError, PitRecallNotSupportedMVP0
from tests.facade.conftest import make_facade


class RecordingRetriever:
    """Fake ``Retriever`` that records calls and returns configurable candidates."""

    def __init__(self, candidates: tuple[FusedCandidate, ...] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._candidates = candidates if candidates is not None else ()

    def recall(
        self,
        query: str,
        *,
        pit: PITPoint | None,
        k: int,
        cognitive_type: str | None,
        subject_filter: str | None,
        session_boost: bool = False,
    ) -> tuple[FusedCandidate, ...]:
        self.calls.append(
            {
                "query": query,
                "pit": pit,
                "k": k,
                "cognitive_type": cognitive_type,
                "subject_filter": subject_filter,
                "session_boost": session_boost,
            }
        )
        return self._candidates


class PitCapableRetriever(RecordingRetriever):
    """Retriever that declares PIT capability."""

    supports_pit = True


@pytest.fixture()
def fake_retriever() -> RecordingRetriever:
    return RecordingRetriever(
        candidates=(
            FusedCandidate(ep_id="e1", score=0.0, sources=()),
            FusedCandidate(ep_id="e2", score=0.0, sources=()),
        )
    )


@pytest.fixture()
def seam_facade(fake_retriever, shaper, clock):
    f, _log = make_facade(
        engine=None,  # unused on the recall path once a retriever is injected
        write_path=None,
        shaper=shaper,
        retriever=fake_retriever,
        clock=clock,
    )
    return f, fake_retriever


class TestRetrieverDelegation:
    def test_facade_calls_retriever_recall_once(self, seam_facade) -> None:
        f, retriever = seam_facade
        f.recall("sergio")
        assert len(retriever.calls) == 1

    def test_facade_forwards_query_k_cognitive_subject(self, seam_facade) -> None:
        f, retriever = seam_facade
        f.recall("sergio", k=7, cognitive_type="semantic", subject_filter="Sergio")
        call = retriever.calls[0]
        assert call["query"] == "sergio"
        assert call["k"] == 7
        assert call["cognitive_type"] == "semantic"
        assert call["subject_filter"] == "Sergio"

    def test_facade_forwards_pit_none_to_retriever(self, seam_facade) -> None:
        # The facade refuses a caller-supplied pit before delegating; when there
        # is no pit, it forwards pit=None to the retriever (the first release has
        # no PIT axis).
        f, retriever = seam_facade
        f.recall("sergio")
        assert retriever.calls[0]["pit"] is None

    def test_facade_passes_retriever_candidates_to_shaper(self, seam_facade, shaper) -> None:
        f, retriever = seam_facade
        f.recall("sergio")
        # The shaper receives EXACTLY the candidates the retriever returned.
        assert [c.ep_id for c in shaper.index_calls[0]["candidates"]] == ["e1", "e2"]
        assert shaper.index_calls[0]["pit"] is None

    def test_facade_returns_shaper_result_verbatim(self, seam_facade, shaper) -> None:
        f, retriever = seam_facade
        row = IndexRow(
            ep_id="e1",
            fact_id="f1",
            subject="S",
            title=None,
            summary=None,
            cognitive_type="semantic",
            skip_extraction=True,
            valid_at=None,
            invalid_at=None,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            score=0.0,
            stale=False,
            pending_ingest=False,
        )
        shaper.index_result = [row]
        result = f.recall("sergio")
        assert result == [row]
        assert result[0] is row

    def test_pit_still_refused_before_delegation(self, seam_facade) -> None:
        # The extension point does not weaken the PIT refusal: pit is rejected
        # at the facade boundary BEFORE the retriever is consulted.
        f, retriever = seam_facade
        pit = PITPoint(kind="state_at", t=datetime(2026, 1, 1, tzinfo=UTC))
        with pytest.raises(PitRecallNotSupportedMVP0):
            f.recall("sergio", pit=pit)
        assert retriever.calls == []

    def test_empty_query_still_refused_before_delegation(self, seam_facade) -> None:
        f, retriever = seam_facade
        with pytest.raises(EmptyQueryError):
            f.recall("")
        assert retriever.calls == []


class TestPitForwardWhenCapable:
    """A retriever that declares ``supports_pit`` receives the pit."""

    def test_pit_forwarded_to_retriever_and_shaper(self, shaper, clock) -> None:
        retriever = PitCapableRetriever(
            candidates=(
                FusedCandidate(ep_id="e1", score=1.0, sources=("vector",)),
            )
        )
        f, _log = make_facade(
            engine=None,
            write_path=None,
            shaper=shaper,
            retriever=retriever,
            clock=clock,
        )
        pit = PITPoint(kind="state_at", t=datetime(2026, 1, 1, tzinfo=UTC))
        f.recall("sergio", pit=pit)
        # the retriever received the pit verbatim...
        assert retriever.calls[0]["pit"] == pit
        # ...and the disclosure shaper's materialize_index received the SAME pit
        # (not None).
        assert shaper.index_calls[0]["pit"] == pit

    def test_retriever_without_capability_still_refuses_pit(self, seam_facade) -> None:
        # The default RecordingRetriever has no supports_pit -> the facade still
        # refuses before delegating (the existing pin, kept green).
        f, retriever = seam_facade
        pit = PITPoint(kind="state_at", t=datetime(2026, 1, 1, tzinfo=UTC))
        with pytest.raises(PitRecallNotSupportedMVP0):
            f.recall("sergio", pit=pit)
        assert retriever.calls == []


class TestRetrieverDefaultWiring:
    """When no retriever is injected, the facade uses VigenteListingRetriever.

    The existing ``test_recall.py`` suite already pins the first-release
    current-state-listing behavior end-to-end; this just asserts the
    default-construct path compiles and delegates (no retriever arg => the
    composition root supplies the first-release impl, preserving the original
    behavior).
    """

    def test_make_facade_without_retriever_uses_default(self, engine, shaper, clock) -> None:
        from seahorse.facade.vigente_retriever import VigenteListingRetriever

        f, _log = make_facade(engine=engine, shaper=shaper, clock=clock)
        # The default retriever wraps the recording engine, so a recall still
        # drives engine.get_vigente (the original behavior).
        f.recall("sergio")
        assert len(engine.get_vigente_calls) == 1
        # And the facade's retriever is the first-release impl, not a stub.
        assert isinstance(f._retriever, VigenteListingRetriever)