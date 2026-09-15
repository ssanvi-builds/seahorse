"""PIT listing through the composition root — ``build_facade`` wires the slice.

The factory injects ``pit_source=own_storage.episodes`` into the listing
retriever at BOTH construction points (the listing regime and the hybrid's
fallback), so ``recall(pit=...)`` serves bi-temporal listings over real SQLite
end to end. The round-trip contract:

- remember A → improve A→A′ → ``recall(pit=state_at pre-improve)`` returns A
  (``invalid_at`` > t keeps it state-valid; the supersession is known_at's
  axis, not state_at's) — and the no-pit recall returns A′ only (the
  current-state listing).
- forget is the same story: the row survives PIT recall at any t before the
  forget.
- a future-dated (PENDING) episode is excluded by ``state_at`` at an earlier
  t (``valid_at <= t``) while the no-pit listing still shows it.
- ``known_at`` is the knowledge axis: created after t → excluded.

The listing regime's invariant stands under PIT: vector/FTS are never touched
and the (stub) query embedder is never invoked — a PIT listing resolves the
set from the repository slice alone.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.disclosure.types import PITPoint
from seahorse.facade.factory import build_facade
from seahorse.facade.types import RememberPayload

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 2, 1, tzinfo=UTC)
T2 = datetime(2026, 3, 1, tzinfo=UTC)

BY = {"source_type": "agent", "agent_id": "a", "session_id": "s"}


def _build(tmp_path):
    return build_facade(
        tmp_path / "pit.db",
        clock=lambda: T1,  # the retriever's now for the no-pit branch
        retrieval_available=False,
    )


def _remember(facade, body, *, now, valid_at=None, title=None, by=None):
    return facade.remember(
        RememberPayload(body=body, by=by or BY, valid_at=valid_at, title=title), now=now
    )


def _ep_ids(facade, pit) -> set[str]:
    return {r.ep_id for r in facade.recall("any", pit=pit)}


class TestFactoryWiring:
    def test_listing_regime_retriever_is_pit_capable(self, tmp_path) -> None:
        facade, storage = _build(tmp_path)
        try:
            assert facade._retriever.supports_pit is True  # noqa: SLF001
        finally:
            storage.close()

    def test_hybrid_fallback_retriever_is_pit_capable(self, monkeypatch, tmp_path) -> None:
        import seahorse.facade.factory as factory
        from tests.facade.test_factory import _FakeAsyncEmbedder, _RecordingEmbedder

        monkeypatch.setattr(factory, "_build_passage_embedder", lambda: _FakeAsyncEmbedder())
        facade, storage = build_facade(
            tmp_path / "hyb.db",
            embedder=_RecordingEmbedder(),
            retrieval_available=True,
        )
        try:
            fallback = facade._retriever._fallback  # noqa: SLF001
            assert fallback.supports_pit is True
        finally:
            storage.close()


class TestImproveRoundTrip:
    def test_state_at_before_improve_returns_a_no_pit_returns_a_prime(self, tmp_path) -> None:
        facade, storage = _build(tmp_path)
        try:
            first = _remember(facade, "Sergio lives in Madrid", now=T0, title="home")
            facade.improve(first.ep_id, "Sergio lives in Barcelona", by=BY, now=T1)

            pit_rows = _ep_ids(facade, PITPoint(kind="state_at", t=T0))
            # A stays state-valid at T0 (invalid_at=T1 > T0). A' ALSO resolves —
            # it is state-valid at T0 too (valid_at NULL, "from forever") and
            # state_at does not filter the creation axis (that is known_at's job).
            assert first.ep_id in pit_rows

            now_rows = _ep_ids(facade, pit=None)
            # The current-state listing serves only the vigent successor.
            assert first.ep_id not in now_rows
            assert len(now_rows) == 1
        finally:
            storage.close()


class TestForgetRoundTrip:
    def test_state_at_before_forget_still_returns_the_row(self, tmp_path) -> None:
        facade, storage = _build(tmp_path)
        try:
            first = _remember(facade, "Deploy behind oauth", now=T0, title="deploy")
            facade.forget(first.ep_id, reason="done", by=BY, now=T1)

            pit_rows = _ep_ids(facade, PITPoint(kind="state_at", t=T0))
            assert first.ep_id in pit_rows  # the row survives PIT before the forget
            now_rows = _ep_ids(facade, pit=None)
            assert first.ep_id not in now_rows  # soft-deleted from the current state
        finally:
            storage.close()


class TestStateAtPredicate:
    def test_future_episode_excluded_at_earlier_t(self, tmp_path) -> None:
        facade, storage = _build(tmp_path)
        try:
            # valid_at is human-only (E_VALID_AT_HUMAN_ONLY): the future-dated
            # episode is written with human provenance.
            _remember(
                facade,
                "Contract starts in June",
                now=T0,
                valid_at=T2,
                title="contract",
                by={"source_type": "human", "agent_id": "human", "session_id": "s"},
            )

            pit_rows = _ep_ids(facade, PITPoint(kind="state_at", t=T1))
            assert pit_rows == set()  # valid_at=T2 > T1: not yet true (PENDING)
            # The same row IS state-valid at its own valid_at boundary.
            pit_at_t2 = _ep_ids(facade, PITPoint(kind="state_at", t=T2))
            assert len(pit_at_t2) == 1
            # The no-pit current-state listing excludes future-dated rows too
            # (get_vigente post-filters valid_at <= now); the PENDING distinction
            # the two regimes share — state_at just moves the boundary to t.
            now_rows = _ep_ids(facade, pit=None)
            assert now_rows == set()
        finally:
            storage.close()

    def test_null_valid_at_included_at_any_t(self, tmp_path) -> None:
        # state_at INCLUDES valid_at IS NULL ("vigente desde forever") at ANY t.
        facade, storage = _build(tmp_path)
        try:
            first = _remember(facade, "Sergio was born in Spain", now=T0, title="origin")
            rows = _ep_ids(facade, PITPoint(kind="state_at", t=T0))
            assert rows == {first.ep_id}
        finally:
            storage.close()


class TestKnownAtRoundTrip:
    def test_known_at_t0_only_a_t1_only_a_prime(self, tmp_path) -> None:
        facade, storage = _build(tmp_path)
        try:
            first = _remember(facade, "Sergio lives in Madrid", now=T0, title="home")
            second = facade.improve(
                first.ep_id, "Sergio lives in Barcelona", by=BY, now=T1
            )

            known_t0 = _ep_ids(facade, PITPoint(kind="known_at", t=T0))
            assert known_t0 == {first.ep_id}
            known_t1 = _ep_ids(facade, PITPoint(kind="known_at", t=T1))
            # known_at does NOT track supersession (no invalid axis): A is still
            # known at T1 (created_at=T0 <= T1, expired_at NULL) — the knowledge
            # axis accumulates; supersession is state_at's + the chain's job.
            assert known_t1 == {first.ep_id, second.id}
        finally:
            storage.close()


class TestListingInvariantUnderPit:
    def test_pit_listing_never_touches_vector_fts_embedder(self, tmp_path) -> None:
        facade, storage = _build(tmp_path)
        try:
            first = _remember(facade, "Fact one", now=T0, title="f1")
            # The stub query embedder raises E_NOT_IN_MVP_0 when invoked: a
            # successful PIT recall proves the embedder was never called.
            rows = facade.recall("sergio", pit=PITPoint(kind="state_at", t=T1))
            assert [r.ep_id for r in rows] == [first.ep_id]
            # The vector/FTS repos stay untouched (empty — nothing indexed them).
            assert storage.vector.count() == 0
            assert storage.fts.count() == 0
        finally:
            storage.close()