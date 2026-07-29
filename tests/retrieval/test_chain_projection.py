"""Chain PIT projection tests (f5-11 §7.5; ADR-03 axis separation).

The ``supersedes`` chain is projected read-only under PIT. The two axes NEVER
mix (ADR-03): ``state_at`` reads ONLY ``valid_at``/``invalid_at`` (valid_time);
``known_at`` reads ONLY ``created_at``/``expired_at`` (transaction_time). The
predicates mirror ``tests/disclosure/conftest._pit_ok`` — NOT spec §7.5, which
mixes axes (it includes ``expired_at`` in the state_at predicate — an ADR-03
violation). These tests lock the corrected behavior.

Signals:
- ``_chain_active_now``: last episode that is not invalidated, not expired, and
  valid (``valid_at is None or valid_at <= now``).
- ``_chain_vigent_at``: state_at — ``expired_at`` (transaction_time) does NOT
  affect the result; ``invalid_at`` (valid_time) does.
- ``_chain_known_at``: known_at — ``invalid_at`` (valid_time) does NOT affect the
  result; ``expired_at`` (transaction_time) does.
- ``cognitive_type`` filters the projected chain client-side.
- anchor = stage-1 top-1 (or explicit ``anchor_ep_id``); empty stage 1 + no
  anchor -> stage 2 skipped.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.contracts.persistence import VectorHit
from seahorse.retrieval import recall
from seahorse.retrieval.engine import (
    _chain_active_now,
    _chain_known_at,
    _chain_vigent_at,
)

from .conftest import _ep

NOW = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
T0 = datetime(2024, 1, 1, tzinfo=UTC)
T1 = datetime(2024, 3, 1, tzinfo=UTC)
T2 = datetime(2024, 5, 1, tzinfo=UTC)
T3 = datetime(2024, 7, 1, tzinfo=UTC)


def _chain(*eps):
    return list(eps)


class TestChainActiveNow:
    def test_returns_last_active(self):
        # v1 invalidated, v2 expired, v3 active -> v3.
        v1 = _ep("v1", created_at=T0, valid_at=T0, invalid_at=T1)
        v2 = _ep("v2", created_at=T1, valid_at=T1, expired_at=T2)
        v3 = _ep("v3", created_at=T2, valid_at=T2)
        assert _chain_active_now(_chain(v1, v2, v3), NOW) is v3

    def test_valid_at_in_future_not_active(self):
        # valid_at > now -> not active yet.
        future = _ep("vF", created_at=NOW, valid_at=T3)
        assert _chain_active_now(_chain(future), NOW) is None

    def test_empty_chain(self):
        assert _chain_active_now([], NOW) is None


class TestChainVigentAtStateAxisOnly:
    def test_expired_at_does_not_affect_state_at(self):
        # v has expired_at set (transaction_time) but is vigent on the valid_time
        # axis -> STILL vigent_at t. ADR-03: state_at ignores expired_at.
        v = _ep("v", created_at=T0, valid_at=T0, expired_at=T2)
        assert _chain_vigent_at(_chain(v), T1) is v

    def test_invalid_at_excludes(self):
        v = _ep("v", created_at=T0, valid_at=T0, invalid_at=T1)
        assert _chain_vigent_at(_chain(v), T2) is None  # invalid_at <= t2

    def test_valid_at_in_future_excludes(self):
        v = _ep("v", created_at=T0, valid_at=T2)
        assert _chain_vigent_at(_chain(v), T1) is None  # valid_at > t1

    def test_valid_at_none_excludes(self):
        # Predicate requires valid_at is not None (conftest).
        v = _ep("v", created_at=T0)  # valid_at None
        assert _chain_vigent_at(_chain(v), T1) is None

    def test_last_vigent_wins(self):
        v1 = _ep("v1", created_at=T0, valid_at=T0, invalid_at=T2)
        v2 = _ep("v2", created_at=T1, valid_at=T1)  # still vigent at T1.5
        assert _chain_vigent_at(_chain(v1, v2), T1) is v2


class TestChainKnownAtTransactionAxisOnly:
    def test_invalid_at_does_not_affect_known_at(self):
        # v has invalid_at set (valid_time) but is known on the transaction_time
        # axis -> STILL known_at t. ADR-03: known_at ignores invalid_at.
        v = _ep("v", created_at=T0, invalid_at=T1)
        assert _chain_known_at(_chain(v), T2) == [v]

    def test_expired_at_excludes(self):
        v = _ep("v", created_at=T0, expired_at=T1)
        assert _chain_known_at(_chain(v), T2) == []  # expired_at <= t2

    def test_created_after_t_excludes(self):
        v = _ep("v", created_at=T2)
        assert _chain_known_at(_chain(v), T1) == []  # created_at > t1

    def test_returns_all_known(self):
        # known_at returns a LIST (not single); both v1 and v2 known at t.
        v1 = _ep("v1", created_at=T0, invalid_at=T1)  # valid_time only
        v2 = _ep("v2", created_at=T1, valid_at=T2)  # future-valid still known
        assert _chain_known_at(_chain(v1, v2), T1) == [v1, v2]


class TestEngineUsesChain:
    def test_stage1_top1_is_anchor(self, embedder, vector_repo, fts_repo, episode_repo, clock_now):
        # vector returns e1 first (rank1) -> anchor=e1 -> chain_from(e1) called.
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9), VectorHit("e2", 0.2, 0.83)]
        episode_repo.add(_ep("e1", created_at=clock_now))
        recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        assert episode_repo.chain_calls == ["e1"]

    def test_explicit_anchor_overrides_stage1(
        self, embedder, vector_repo, fts_repo, episode_repo, clock_now
    ):
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9)]
        episode_repo.add(_ep("e1", created_at=clock_now))
        episode_repo.add(_ep("anchorX", created_at=clock_now))
        recall(
            "q",
            pit=None,
            anchor_ep_id="anchorX",
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        assert episode_repo.chain_calls == ["anchorX"]

    def test_empty_stage1_no_anchor_skips_stage2(
        self, embedder, vector_repo, fts_repo, episode_repo
    ):
        # Both sources empty -> no anchor -> chain_from never called.
        recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        assert episode_repo.chain_calls == []
        assert (
            recall(
                "q",
                pit=None,
                embedder=embedder,
                vector_repo=vector_repo,
                fts_repo=fts_repo,
                episode_repo=episode_repo,
                k=10,
            )
            == []
        )


class TestChainCognitiveFilter:
    def test_chain_filtered_by_cognitive_type(
        self, embedder, vector_repo, fts_repo, episode_repo, clock_now
    ):
        # known_at returns a LIST of all known episodes; cognitive_type=fact drops
        # the preference member from the chain contribution. An explicit anchor
        # drives the chain regardless of empty stage-1 sources.
        from seahorse.disclosure.types import PITPoint

        # chain_from(e1) -> [eOld, e1] sorted by created_at asc (e1.supersedes=eOld).
        e_old = _ep(
            "eOld", created_at=datetime(2024, 1, 1, tzinfo=UTC), cognitive_type="preference"
        )
        e1 = _ep("e1", created_at=clock_now, cognitive_type="fact", supersedes="eOld")
        episode_repo.add(e_old)
        episode_repo.add(e1)
        result = recall(
            "q",
            pit=PITPoint(kind="known_at", t=clock_now),
            cognitive_type="fact",
            anchor_ep_id="e1",
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        ids = {c.ep_id for c in result}
        # e1 kept (fact); eOld dropped (preference) from the chain contribution.
        assert ids == {"e1"}
