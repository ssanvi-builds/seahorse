"""Recency tests — pure ``apply_recency_boost`` + ``recall`` integration.

Signals:
- ``score' = score · (1 + γ·exp(-ln2·age_days/half_life))``, factor in [1, 1+γ].
- Deterministic given ``now`` (the injectable clock of the engine).
- Default-OFF: ``recency=None`` keeps the pure-RRF bit-comparable fingerprint.
- Gate ``pit is None``: PIT queries reproduce state as-of-``t`` with pure RRF.
- ``created_at`` read in batch via ``index_repo.get_rows`` (one IN query, no N+1).
- The boost is folded INTO ``FusedCandidate.score`` (never an external reorder).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from seahorse.contracts.persistence import FullTextHit, VectorHit
from seahorse.contracts.retrieval import FusedCandidate
from seahorse.disclosure.types import PITPoint
from seahorse.retrieval import recall
from seahorse.retrieval.constants import RRF_K
from seahorse.retrieval.recency import RecencyConfig, apply_recency_boost

from .conftest import _row

NOW = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
GAMMA = 0.5
HALF_LIFE = 30.0


def _rrf(rank: int) -> float:
    return 1.0 / (RRF_K + rank)


def _cand(ep_id: str, score: float) -> FusedCandidate:
    return FusedCandidate(ep_id=ep_id, score=score, sources=("vector",))


def _factor(age_days: float) -> float:
    return 1.0 + GAMMA * math.exp(-math.log(2) * age_days / HALF_LIFE)


class TestApplyRecencyBoost:
    def test_fresh_candidate_gets_max_boost(self):
        # age 0 -> factor = 1 + γ·exp(0) = 1.5.
        out = apply_recency_boost(
            [_cand("e1", 1.0)],
            {"e1": NOW},
            NOW,
            RecencyConfig(gamma=GAMMA, half_life_days=HALF_LIFE),
            k=10,
        )
        assert out[0].score == pytest.approx(1.0 * (1 + GAMMA))

    def test_old_candidate_gets_no_boost(self):
        # age >> half_life -> factor ~ 1 (bounded, never below 1).
        old = NOW - timedelta(days=300)
        out = apply_recency_boost(
            [_cand("e1", 1.0)],
            {"e1": old},
            NOW,
            RecencyConfig(gamma=GAMMA, half_life_days=HALF_LIFE),
            k=10,
        )
        assert out[0].score == pytest.approx(1.0 * _factor(300), rel=1e-6)
        assert out[0].score > 1.0  # bounded: still slightly above 1

    def test_factor_bounded_in_1_1_plus_gamma(self):
        # For any age, factor ∈ [1, 1+γ] — a fresh-but-irrelevant candidate
        # cannot outrank a relevant one by more than 1+γ.
        for age_days in (0, 1, 7, 30, 90, 365, 3650):
            f = _factor(age_days)
            assert 1.0 <= f <= 1.0 + GAMMA

    def test_reorders_by_boosted_score(self):
        # e1: RRF 0.1, age 0 (factor 1.5) -> 0.15. e2: RRF 0.12, age 300d
        # (factor ~1.0005) -> ~0.12006. The fresh candidate outranks the older
        # higher-RRF one — the boost is folded into score, not an external reorder.
        old = NOW - timedelta(days=300)
        out = apply_recency_boost(
            [_cand("e2", 0.12), _cand("e1", 0.10)],
            {"e1": NOW, "e2": old},
            NOW,
            RecencyConfig(gamma=GAMMA, half_life_days=HALF_LIFE),
            k=10,
        )
        assert [c.ep_id for c in out] == ["e1", "e2"]
        assert out[0].score == pytest.approx(0.10 * (1 + GAMMA))
        assert out[1].score == pytest.approx(0.12 * _factor(300), rel=1e-6)

    def test_truncates_to_k(self):
        out = apply_recency_boost(
            [_cand("e1", 1.0), _cand("e2", 0.9), _cand("e3", 0.8)],
            {"e1": NOW, "e2": NOW, "e3": NOW},
            NOW,
            RecencyConfig(gamma=GAMMA, half_life_days=HALF_LIFE),
            k=2,
        )
        assert [c.ep_id for c in out] == ["e1", "e2"]

    def test_missing_created_at_unboosted(self):
        # A candidate absent from the map is left unboosted (honest — never
        # invent a boost for a row the index does not expose).
        out = apply_recency_boost(
            [_cand("e1", 1.0), _cand("e2", 0.9)],
            {"e1": NOW},
            NOW,
            RecencyConfig(gamma=GAMMA, half_life_days=HALF_LIFE),
            k=10,
        )
        by_id = {c.ep_id: c for c in out}
        assert by_id["e1"].score == pytest.approx(1.0 * (1 + GAMMA))
        assert by_id["e2"].score == pytest.approx(0.9)

    def test_deterministic_given_now(self):
        created = {"e1": NOW - timedelta(days=5), "e2": NOW - timedelta(days=40)}
        cfg = RecencyConfig(gamma=GAMMA, half_life_days=HALF_LIFE)
        r1 = apply_recency_boost(
            [_cand("e1", 1.0), _cand("e2", 0.9)], created, NOW, cfg, k=10
        )
        r2 = apply_recency_boost(
            [_cand("e1", 1.0), _cand("e2", 0.9)], created, NOW, cfg, k=10
        )
        assert r1 == r2

    def test_invalid_config_rejected(self):
        with pytest.raises(ValueError):
            RecencyConfig(gamma=-0.1)
        with pytest.raises(ValueError):
            RecencyConfig(half_life_days=0.0)
        with pytest.raises(ValueError):
            RecencyConfig(gamma=float("nan"))
        with pytest.raises(ValueError):
            RecencyConfig(gamma=float("inf"))
        with pytest.raises(ValueError):
            RecencyConfig(half_life_days=float("nan"))

    def test_naive_created_at_unboosted(self):
        # A naive created_at (which the index should never hold) is left
        # unboosted — never crashes the optional signal, never invents a boost.
        out = apply_recency_boost(
            [_cand("e1", 1.0)],
            {"e1": datetime(2024, 5, 1, 12, 0)},  # naive
            NOW,
            RecencyConfig(gamma=GAMMA, half_life_days=HALF_LIFE),
            k=10,
        )
        assert out[0].score == pytest.approx(1.0)


class TestRecallRecencyIntegration:
    def test_default_off_pure_rrf(self, embedder, vector_repo, fts_repo, episode_repo):
        # recency=None (the default) keeps the pure-RRF bit-comparable scores.
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9), VectorHit("e2", 0.2, 0.83)]
        fts_repo.search_hits = [FullTextHit("e1", 1.0, 0.37)]
        result = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
        )
        by_id = {c.ep_id: c for c in result}
        assert by_id["e1"].score == pytest.approx(_rrf(1) * 2)
        assert by_id["e2"].score == pytest.approx(_rrf(2))

    def test_recency_applied_when_pit_none(
        self, embedder, vector_repo, fts_repo, episode_repo, index_repo, fixed_clock
    ):
        # e1 fresh (age 0) -> factor 1+γ; e2 old (300d) -> factor ~1.
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9), VectorHit("e2", 0.2, 0.83)]
        fts_repo.search_hits = [FullTextHit("e1", 1.0, 0.37)]
        index_repo.add(_row("e1", created_at=NOW))
        index_repo.add(_row("e2", created_at=NOW - timedelta(days=300)))
        result = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            index_repo=index_repo,
            k=10,
            clock=fixed_clock,
            recency=RecencyConfig(gamma=GAMMA, half_life_days=HALF_LIFE),
        )
        by_id = {c.ep_id: c for c in result}
        assert by_id["e1"].score == pytest.approx(_rrf(1) * 2 * (1 + GAMMA))
        assert by_id["e2"].score == pytest.approx(_rrf(2) * _factor(300), rel=1e-6)

    def test_recency_not_applied_to_pit(
        self, embedder, vector_repo, fts_repo, episode_repo, index_repo, fixed_clock
    ):
        # Gate: PIT queries reproduce state as-of-t with pure RRF — never boosted.
        vector_repo.knn_state_at_hits = [VectorHit("e1", 0.1, 0.9)]
        fts_repo.search_state_at_hits = [FullTextHit("e1", 1.0, 0.37)]
        index_repo.add(_row("e1", created_at=NOW))
        result = recall(
            "q",
            pit=PITPoint(kind="state_at", t=NOW),
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            index_repo=index_repo,
            k=10,
            clock=fixed_clock,
            recency=RecencyConfig(gamma=GAMMA, half_life_days=HALF_LIFE),
        )
        assert result[0].score == pytest.approx(_rrf(1) * 2)
        assert index_repo.get_rows_calls == []  # never read created_at for PIT

    def test_get_rows_called_once_batch(
        self, embedder, vector_repo, fts_repo, episode_repo, index_repo, fixed_clock
    ):
        # One IN query for ≤k candidates, no N+1.
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9), VectorHit("e2", 0.2, 0.83)]
        fts_repo.search_hits = [FullTextHit("e1", 1.0, 0.37)]
        index_repo.add(_row("e1", created_at=NOW))
        index_repo.add(_row("e2", created_at=NOW))
        recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            index_repo=index_repo,
            k=10,
            clock=fixed_clock,
            recency=RecencyConfig(gamma=GAMMA, half_life_days=HALF_LIFE),
        )
        # One batch IN query for recency's created_at read, no N+1 (the session
        # boost is default-OFF — the authoritative LMEB-S run proved it
        # net-harmful — so it adds no get_rows call).
        assert len(index_repo.get_rows_calls) == 1
        assert set(index_repo.get_rows_calls[0]) == {"e1", "e2"}

    def test_recency_requires_index_repo(
        self, embedder, vector_repo, fts_repo, episode_repo, fixed_clock
    ):
        # recency set but no index_repo -> boost skipped (honest, never invented).
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9)]
        fts_repo.search_hits = [FullTextHit("e1", 1.0, 0.37)]
        result = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            k=10,
            clock=fixed_clock,
            recency=RecencyConfig(gamma=GAMMA, half_life_days=HALF_LIFE),
        )
        assert result[0].score == pytest.approx(_rrf(1) * 2)

    def test_get_rows_failure_keeps_pure_rrf(
        self, embedder, vector_repo, fts_repo, episode_repo, index_repo, fixed_clock
    ):
        # A failure in the OPTIONAL recency signal must not kill the whole
        # ranking (which would degrade the hybrid path to the listing regime) —
        # keep pure RRF.
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9)]
        fts_repo.search_hits = [FullTextHit("e1", 1.0, 0.37)]

        def _boom(ep_ids):
            raise RuntimeError("transient db error")

        index_repo.get_rows = _boom
        result = recall(
            "q",
            pit=None,
            embedder=embedder,
            vector_repo=vector_repo,
            fts_repo=fts_repo,
            episode_repo=episode_repo,
            index_repo=index_repo,
            k=10,
            clock=fixed_clock,
            recency=RecencyConfig(gamma=GAMMA, half_life_days=HALF_LIFE),
        )
        assert result[0].score == pytest.approx(_rrf(1) * 2)
