"""Decay tests — pure ``apply_decay_bias`` + ``recall`` integration.

Signals (Sprint D, Ebbinghaus forgetting curve, default-OFF):
- ``score' = score · 2^(-age_days/half_life[type])``, factor in (0, 1].
- ``half_life[type]`` = S₀ prior by ``cognitive_type`` (R3, memlocal priors:
  episodic 139d, semantic 347d, social 231d, procedural 347d); unknown/None
  types fall back to ``DecayConfig.default_half_life_days``.
- Deterministic given ``now`` (the injectable clock of the engine).
- Default-OFF: ``decay=None`` keeps the pure-RRF bit-comparable fingerprint.
- Gate ``pit is None``: PIT queries reproduce state as-of-``t`` with pure RRF.
- ``created_at`` + ``cognitive_type`` read in batch via ``index_repo.get_rows``
  (one IN query, no N+1 — the D3 fix).
- The bias is folded INTO ``FusedCandidate.score`` (never an external reorder).
- No writes (R2): the read path never writes; ``expired_at`` stays NULL.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seahorse.contracts.persistence import FullTextHit, VectorHit
from seahorse.contracts.retrieval import FusedCandidate
from seahorse.disclosure.types import PITPoint
from seahorse.retrieval import recall
from seahorse.retrieval.constants import RRF_K
from seahorse.retrieval.decay import DecayConfig, apply_decay_bias

from .conftest import _row

NOW = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
HALF_LIFE = 30.0
DEFAULT_HALF_LIFE = 347.0
# The custom decay config the downweight/reorder/gate tests share.
CFG = DecayConfig(
    half_lives={"semantic": HALF_LIFE}, default_half_life_days=DEFAULT_HALF_LIFE
)


def _rrf(rank: int) -> float:
    return 1.0 / (RRF_K + rank)


def _cand(ep_id: str, score: float) -> FusedCandidate:
    return FusedCandidate(ep_id=ep_id, score=score, sources=("vector",))


def _factor(age_days: float, half_life: float) -> float:
    return 2.0 ** (-age_days / half_life)


class TestApplyDecayBias:
    def test_fresh_candidate_gets_no_decay(self):
        # age 0 -> factor = 2^0 = 1 (a freshly-created episode is never decayed).
        out = apply_decay_bias(
            [_cand("e1", 1.0)],
            {"e1": NOW},
            {"e1": "semantic"},
            NOW,
            DecayConfig(),
            k=10,
        )
        assert out[0].score == pytest.approx(1.0)

    def test_old_candidate_downweighted(self):
        # age = half_life -> factor 0.5; age >> half_life -> factor -> 0.
        old = NOW - timedelta(days=300)
        out = apply_decay_bias(
            [_cand("e1", 1.0)],
            {"e1": old},
            {"e1": "semantic"},
            NOW,
            CFG,
            k=10,
        )
        assert out[0].score == pytest.approx(_factor(300, HALF_LIFE), rel=1e-6)
        assert out[0].score < 0.01  # 2^-10 ≈ 0.00098: old knowledge fades out

    def test_factor_in_0_1(self):
        # For any age, factor ∈ (0, 1] — decay downweights, never boosts.
        for age_days in (0, 1, 7, 30, 90, 365, 3650):
            f = _factor(age_days, HALF_LIFE)
            assert 0.0 < f <= 1.0

    def test_reorders_by_decayed_score(self):
        # e1: RRF 0.10, age 0 (factor 1) -> 0.10. e2: RRF 0.12, age 300d
        # (factor ~0.001) -> ~0.0001. The fresh candidate outranks the older
        # higher-RRF one — the FAMA fix: the obsolete old version drops.
        old = NOW - timedelta(days=300)
        out = apply_decay_bias(
            [_cand("e2", 0.12), _cand("e1", 0.10)],
            {"e1": NOW, "e2": old},
            {"e1": "semantic", "e2": "semantic"},
            NOW,
            CFG,
            k=10,
        )
        assert [c.ep_id for c in out] == ["e1", "e2"]
        assert out[0].score == pytest.approx(0.10)
        assert out[1].score == pytest.approx(0.12 * _factor(300, HALF_LIFE), rel=1e-6)

    def test_per_type_half_life_priors(self):
        # R3 priors: episodic (139d) decays FASTER than semantic (347d) at the
        # same age — the per-type S₀ is real, not a constant.
        age = NOW - timedelta(days=200)
        out = apply_decay_bias(
            [_cand("ep", 1.0), _cand("sem", 1.0)],
            {"ep": age, "sem": age},
            {"ep": "episodic", "sem": "semantic"},
            NOW,
            DecayConfig(),
            k=10,
        )
        by_id = {c.ep_id: c for c in out}
        f_episodic = _factor(200, 139.0)
        f_semantic = _factor(200, 347.0)
        assert by_id["ep"].score == pytest.approx(f_episodic, rel=1e-6)
        assert by_id["sem"].score == pytest.approx(f_semantic, rel=1e-6)
        assert f_episodic < f_semantic  # episodic forgets faster (smaller half-life)

    def test_unknown_type_uses_default_half_life(self):
        # cognitive_type missing from the map (or unknown) -> default half-life.
        age = NOW - timedelta(days=100)
        cfg = DecayConfig(default_half_life_days=DEFAULT_HALF_LIFE)
        out = apply_decay_bias(
            [_cand("e1", 1.0)],
            {"e1": age},
            {"e1": "project_doc"},  # not in the R3 priors map
            NOW,
            cfg,
            k=10,
        )
        assert out[0].score == pytest.approx(_factor(100, DEFAULT_HALF_LIFE), rel=1e-6)
        # A missing cognitive_type entry behaves identically (honest default).
        out2 = apply_decay_bias(
            [_cand("e1", 1.0)],
            {"e1": age},
            {},
            NOW,
            cfg,
            k=10,
        )
        assert out2[0].score == pytest.approx(_factor(100, DEFAULT_HALF_LIFE), rel=1e-6)

    def test_missing_created_at_undecayed(self):
        # A candidate absent from the map is left undecayed (honest — never
        # invent a decay for a row the index does not expose).
        out = apply_decay_bias(
            [_cand("e1", 1.0), _cand("e2", 0.9)],
            {"e1": NOW},
            {"e1": "semantic", "e2": "semantic"},
            NOW,
            DecayConfig(),
            k=10,
        )
        by_id = {c.ep_id: c for c in out}
        assert by_id["e1"].score == pytest.approx(1.0)
        assert by_id["e2"].score == pytest.approx(0.9)

    def test_naive_created_at_undecayed(self):
        # A naive created_at (which the index should never hold) is left
        # undecayed — never crashes the optional signal, never invents a decay.
        out = apply_decay_bias(
            [_cand("e1", 1.0)],
            {"e1": datetime(2024, 5, 1, 12, 0)},  # naive
            {"e1": "semantic"},
            NOW,
            DecayConfig(),
            k=10,
        )
        assert out[0].score == pytest.approx(1.0)

    def test_future_created_at_clamped_to_zero_age(self):
        # A created_at in the future (clock skew) clamps to age 0 -> factor 1.
        future = NOW + timedelta(days=10)
        out = apply_decay_bias(
            [_cand("e1", 1.0)],
            {"e1": future},
            {"e1": "semantic"},
            NOW,
            DecayConfig(),
            k=10,
        )
        assert out[0].score == pytest.approx(1.0)

    def test_truncates_to_k(self):
        out = apply_decay_bias(
            [_cand("e1", 1.0), _cand("e2", 0.9), _cand("e3", 0.8)],
            {"e1": NOW, "e2": NOW, "e3": NOW},
            {"e1": "semantic", "e2": "semantic", "e3": "semantic"},
            NOW,
            DecayConfig(),
            k=2,
        )
        assert [c.ep_id for c in out] == ["e1", "e2"]

    def test_deterministic_given_now(self):
        created = {"e1": NOW - timedelta(days=5), "e2": NOW - timedelta(days=40)}
        types = {"e1": "episodic", "e2": "semantic"}
        cfg = DecayConfig()
        r1 = apply_decay_bias([_cand("e1", 1.0), _cand("e2", 0.9)], created, types, NOW, cfg, k=10)
        r2 = apply_decay_bias([_cand("e1", 1.0), _cand("e2", 0.9)], created, types, NOW, cfg, k=10)
        assert r1 == r2

    def test_invalid_config_rejected(self):
        with pytest.raises(ValueError):
            DecayConfig(default_half_life_days=0.0)
        with pytest.raises(ValueError):
            DecayConfig(default_half_life_days=-1.0)
        with pytest.raises(ValueError):
            DecayConfig(default_half_life_days=float("nan"))
        with pytest.raises(ValueError):
            DecayConfig(default_half_life_days=float("inf"))
        with pytest.raises(ValueError):
            DecayConfig(half_lives={"semantic": 0.0})
        with pytest.raises(ValueError):
            DecayConfig(half_lives={"semantic": float("nan")})


class TestRecallDecayIntegration:
    def test_default_off_pure_rrf(self, embedder, vector_repo, fts_repo, episode_repo):
        # decay=None (the default) keeps the pure-RRF bit-comparable scores.
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

    def test_decay_applied_when_pit_none(
        self, embedder, vector_repo, fts_repo, episode_repo, index_repo, fixed_clock
    ):
        # e1 fresh (age 0) -> factor 1; e2 old (300d, semantic) -> factor 2^-10.
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9), VectorHit("e2", 0.2, 0.83)]
        fts_repo.search_hits = [FullTextHit("e1", 1.0, 0.37)]
        index_repo.add(_row("e1", created_at=NOW, cognitive_type="semantic"))
        index_repo.add(_row("e2", created_at=NOW - timedelta(days=300), cognitive_type="semantic"))
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
            decay=CFG,
        )
        by_id = {c.ep_id: c for c in result}
        assert by_id["e1"].score == pytest.approx(_rrf(1) * 2)
        assert by_id["e2"].score == pytest.approx(_rrf(2) * _factor(300, HALF_LIFE), rel=1e-6)

    def test_decay_not_applied_to_pit(
        self, embedder, vector_repo, fts_repo, episode_repo, index_repo, fixed_clock
    ):
        # Gate: PIT queries reproduce state as-of-t with pure RRF — never decayed.
        vector_repo.knn_state_at_hits = [VectorHit("e1", 0.1, 0.9)]
        fts_repo.search_state_at_hits = [FullTextHit("e1", 1.0, 0.37)]
        index_repo.add(_row("e1", created_at=NOW, cognitive_type="semantic"))
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
            decay=DecayConfig(),
        )
        assert result[0].score == pytest.approx(_rrf(1) * 2)
        assert index_repo.get_rows_calls == []  # never read rows for PIT

    def test_get_rows_called_once_batch(
        self, embedder, vector_repo, fts_repo, episode_repo, index_repo, fixed_clock
    ):
        # One IN query for ≤k candidates, no N+1 (the D3 fix).
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9), VectorHit("e2", 0.2, 0.83)]
        fts_repo.search_hits = [FullTextHit("e1", 1.0, 0.37)]
        index_repo.add(_row("e1", created_at=NOW, cognitive_type="semantic"))
        index_repo.add(_row("e2", created_at=NOW, cognitive_type="semantic"))
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
            decay=DecayConfig(),
        )
        # One batch IN query for decay's created_at read, no N+1 (the session
        # boost is default-OFF — the authoritative LMEB-S run proved it
        # net-harmful — so it adds no get_rows call).
        assert len(index_repo.get_rows_calls) == 1
        assert set(index_repo.get_rows_calls[0]) == {"e1", "e2"}

    def test_decay_requires_index_repo(
        self, embedder, vector_repo, fts_repo, episode_repo, fixed_clock
    ):
        # decay set but no index_repo -> bias skipped (honest, never invented).
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
            decay=DecayConfig(),
        )
        assert result[0].score == pytest.approx(_rrf(1) * 2)

    def test_get_rows_failure_keeps_pure_rrf(
        self, embedder, vector_repo, fts_repo, episode_repo, index_repo, fixed_clock
    ):
        # A failure in the OPTIONAL decay signal must not kill the whole ranking
        # (which would degrade the hybrid path to the listing regime) — keep RRF.
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
            decay=DecayConfig(),
        )
        assert result[0].score == pytest.approx(_rrf(1) * 2)

    def test_recency_then_decay_compound(
        self, embedder, vector_repo, fts_repo, episode_repo, index_repo, fixed_clock
    ):
        # Both optional signals on: recency boost folds first, decay downweight
        # second — multiplicative compound, deterministic.
        vector_repo.knn_hits = [VectorHit("e1", 0.1, 0.9)]
        fts_repo.search_hits = [FullTextHit("e1", 1.0, 0.37)]
        index_repo.add(_row("e1", created_at=NOW - timedelta(days=10), cognitive_type="semantic"))
        from seahorse.retrieval.recency import RecencyConfig

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
            recency=RecencyConfig(gamma=0.5, half_life_days=30.0),
            decay=CFG,
        )
        recency_factor = 1.0 + 0.5 * 2.0 ** (-10.0 / 30.0)
        decay_factor = _factor(10, HALF_LIFE)
        assert result[0].score == pytest.approx(
            _rrf(1) * 2 * recency_factor * decay_factor, rel=1e-6
        )
