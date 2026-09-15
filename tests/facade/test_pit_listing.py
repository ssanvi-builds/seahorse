"""PIT listing recall — ``VigenteListingRetriever`` serves the PIT axes.

The listing retriever gains an optional ``pit_source`` (the repository slice
``query_state_at`` / ``query_known_at``). With one injected, the retriever
declares ``supports_pit`` (instance attribute) and ``recall(pit=...)`` routes
by axis: ``state_at`` -> ``query_state_at``, ``known_at`` -> ``query_known_at``
(the repo slice owns the bi-temporal predicate — never synthesized here). The
pit ``t`` is forwarded verbatim. Everything downstream is the same listing
contract: client-side ``cognitive_type`` filter, deterministic order
(``created_at`` desc, ``ep_id`` asc tie-break), truncate to ``k``, synthetic
``FusedCandidate(score=0.0, sources=())`` (no ranking). An unknown pit kind
fails loud. ``session_boost`` stays inert under PIT (no re-rank in the listing
regime). Without a ``pit_source`` nothing changes: the facade still refuses a
caller pit before delegating.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from seahorse.disclosure.types import TOP_K, PITPoint
from seahorse.facade.errors import InvalidPITKind
from seahorse.facade.types import FacadeConfig
from seahorse.facade.vigente_retriever import VigenteListingRetriever
from tests.facade.conftest import RecordingEngine, make_episode

T = datetime(2026, 6, 1, tzinfo=UTC)


class _PitRepo:
    """Repository double exposing only the PIT slice (the ``_PitSource`` shape)."""

    def __init__(
        self,
        *,
        state_at: list | None = None,
        known_at: list | None = None,
    ) -> None:
        self.state_calls: list[dict[str, Any]] = []
        self.known_calls: list[dict[str, Any]] = []
        self._state_at = state_at if state_at is not None else []
        self._known_at = known_at if known_at is not None else []

    def query_state_at(self, t: datetime, subject: str | None = None) -> list:
        self.state_calls.append({"t": t, "subject": subject})
        return list(self._state_at)

    def query_known_at(self, t: datetime, subject: str | None = None) -> list:
        self.known_calls.append({"t": t, "subject": subject})
        return list(self._known_at)


def _retriever(pit_repo: _PitRepo) -> VigenteListingRetriever:
    return VigenteListingRetriever(
        engine=RecordingEngine(),
        clock=lambda: T,
        config=FacadeConfig(),
        pit_source=pit_repo,
    )


class TestSupportsPit:
    def test_without_pit_source_is_false(self) -> None:
        ret = VigenteListingRetriever(
            engine=RecordingEngine(), clock=lambda: T, config=FacadeConfig()
        )
        assert ret.supports_pit is False

    def test_with_pit_source_is_true(self) -> None:
        assert _retriever(_PitRepo()).supports_pit is True


class TestPitRouting:
    def test_state_at_routes_to_query_state_at(self) -> None:
        repo = _PitRepo(state_at=[make_episode("e1")])
        ret = _retriever(repo)
        pit = PITPoint(kind="state_at", t=T)
        result = ret.recall("sergio", pit=pit)
        assert len(repo.state_calls) == 1
        assert repo.state_calls[0]["t"] == T
        assert repo.known_calls == []
        assert [c.ep_id for c in result] == ["e1"]

    def test_known_at_routes_to_query_known_at(self) -> None:
        repo = _PitRepo(known_at=[make_episode("e2")])
        ret = _retriever(repo)
        pit = PITPoint(kind="known_at", t=T)
        result = ret.recall("sergio", pit=pit)
        assert len(repo.known_calls) == 1
        assert repo.known_calls[0]["t"] == T
        assert repo.state_calls == []
        assert [c.ep_id for c in result] == ["e2"]

    def test_pit_t_forwarded_verbatim_never_synthetized(self) -> None:
        repo = _PitRepo(state_at=[make_episode("e1")])
        ret = _retriever(repo)
        t = datetime(2021, 3, 4, 5, 6, 7, tzinfo=UTC)
        ret.recall("sergio", pit=PITPoint(kind="state_at", t=t))
        assert repo.state_calls[0]["t"] == t

    def test_subject_filter_forwarded_to_the_slice(self) -> None:
        repo = _PitRepo(state_at=[make_episode("e1")])
        ret = _retriever(repo)
        ret.recall("sergio", pit=PITPoint(kind="state_at", t=T), subject_filter="S")
        assert repo.state_calls[0]["subject"] == "S"

    def test_no_pit_still_uses_get_vigente(self) -> None:
        # pit=None keeps the current-state listing even when a pit_source exists.
        engine = RecordingEngine()
        engine.vigente = [make_episode("e0")]
        repo = _PitRepo(state_at=[make_episode("e1")])
        ret = VigenteListingRetriever(
            engine=engine, clock=lambda: T, config=FacadeConfig(), pit_source=repo
        )
        result = ret.recall("sergio", pit=None)
        assert repo.state_calls == [] and repo.known_calls == []
        assert [c.ep_id for c in result] == ["e0"]

    def test_unknown_kind_fails_loud(self) -> None:
        repo = _PitRepo()
        ret = _retriever(repo)
        with pytest.raises(InvalidPITKind):
            ret.recall("sergio", pit=PITPoint(kind="mixed", t=T))  # type: ignore[arg-type]
        assert repo.state_calls == [] and repo.known_calls == []

    def test_unknown_kind_fails_before_any_query(self) -> None:
        # Fail loud before any read: no slice call, no get_vigente call.
        engine = RecordingEngine()
        ret = VigenteListingRetriever(
            engine=engine,
            clock=lambda: T,
            config=FacadeConfig(),
            pit_source=_PitRepo(),
        )
        with pytest.raises(InvalidPITKind):
            ret.recall("sergio", pit=PITPoint(kind="state", t=T))  # type: ignore[arg-type]
        assert engine.get_vigente_calls == []


class TestListingContractUnderPit:
    """The PIT-resolved set flows through the SAME listing contract."""

    def _eps(self) -> list:
        # e0 newest, e2/e1 share created_at (ep_id asc tie-break), e3 oldest.
        return [
            make_episode("e0", created_at=datetime(2026, 5, 1, tzinfo=UTC)),
            make_episode("e2", created_at=datetime(2026, 4, 1, tzinfo=UTC)),
            make_episode("e1", created_at=datetime(2026, 4, 1, tzinfo=UTC)),
            make_episode("e3", created_at=datetime(2026, 1, 1, tzinfo=UTC)),
        ]

    def test_deterministic_order_created_desc_ep_asc(self) -> None:
        repo = _PitRepo(state_at=list(reversed(self._eps())))
        result = _retriever(repo).recall("sergio", pit=PITPoint(kind="state_at", t=T))
        assert [c.ep_id for c in result] == ["e0", "e1", "e2", "e3"]

    def test_truncates_to_k(self) -> None:
        repo = _PitRepo(state_at=self._eps())
        result = _retriever(repo).recall("sergio", pit=PITPoint(kind="state_at", t=T), k=2)
        assert [c.ep_id for c in result] == ["e0", "e1"]

    def test_k_clamped_to_config_top_k(self) -> None:
        repo = _PitRepo(state_at=self._eps())
        ret = VigenteListingRetriever(
            engine=RecordingEngine(),
            clock=lambda: T,
            config=FacadeConfig(top_k=3),
            pit_source=repo,
        )
        result = ret.recall(
            "sergio", pit=PITPoint(kind="state_at", t=T), k=TOP_K * 10
        )
        assert len(result) == 3

    def test_cognitive_type_filter_client_side(self) -> None:
        eps = [
            make_episode("e0", cognitive_type="project_doc"),
            make_episode("e1", cognitive_type="episodic"),
        ]
        repo = _PitRepo(state_at=eps)
        result = _retriever(repo).recall(
            "sergio", pit=PITPoint(kind="state_at", t=T), cognitive_type="project_doc"
        )
        assert [c.ep_id for c in result] == ["e0"]

    def test_synthetic_score_zero_no_sources(self) -> None:
        repo = _PitRepo(state_at=[make_episode("e1")])
        (cand,) = _retriever(repo).recall("sergio", pit=PITPoint(kind="state_at", t=T))
        assert cand.score == 0.0
        assert cand.sources == ()

    def test_session_boost_inert_under_pit(self) -> None:
        eps = self._eps()
        repo = _PitRepo(state_at=eps)
        plain = _retriever(repo).recall("sergio", pit=PITPoint(kind="state_at", t=T))
        boosted = _retriever(repo).recall(
            "sergio", pit=PITPoint(kind="state_at", t=T), session_boost=True
        )
        assert [c.ep_id for c in plain] == [c.ep_id for c in boosted]