"""Tests for the entity-centric recall experiment — (f) does the graph add value over clustering?

The synthetic corpus verifies the harness MECHANICS: coherent entities (the
entity name in every body) must yield high entity recall@k, scattered entities
(the entity name in only one body) low entity recall@k. The authoritative
decision comes from an LMEB-S run (``--corpus lmeb-s``, not yet wired).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.benchmark.experiments.entity_centric import (
    ENTITY_RECALL_THRESHOLD,
    ENTITY_TOP_K,
    EntityCentricResult,
    build_synthetic_corpus,
    compute_entity_clusters,
    decide_entity_centric,
    render_entity_centric_report,
    run_entity_centric_experiment,
)
from seahorse.contracts.episode import Episode

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _ep(i: int, entity: str | None, title: str, narrative: str) -> Episode:
    return Episode(
        id=f"ep-{i}",
        created_at=NOW,
        schema_version="1.1",
        provenance={
            "source_type": "importer",
            "importer_vendor": "claude-mem",
            "extraction_mode": "skip",
            "session_id": "claude-mem-import-test",
            "x-entity": entity,
        },
        body=f"# {title}\n\n{narrative}",
        title=title,
        valid_at=NOW,
        cognitive_type="semantic",
        source_type="importer",
    )


class TestComputeEntityClusters:
    def test_groups_by_entity(self) -> None:
        eps = [
            _ep(0, "Alice", "Alice work", "Alice works at Acme."),
            _ep(1, "Alice", "Alice home", "Alice lives in Madrid."),
            _ep(2, "Bob", "Bob job", "Bob is an engineer."),
        ]
        clusters = compute_entity_clusters(eps)
        assert len(clusters) == 2
        by_entity = {c.entity: c for c in clusters}
        assert set(by_entity) == {"Alice", "Bob"}
        assert len(by_entity["Alice"].ep_ids) == 2
        assert len(by_entity["Bob"].ep_ids) == 1

    def test_skips_episodes_without_entity_marker(self) -> None:
        eps = [
            _ep(0, "Alice", "Alice work", "Alice works at Acme."),
            _ep(1, None, "Quantum", "Qubits exploit superposition."),
        ]
        clusters = compute_entity_clusters(eps)
        assert len(clusters) == 1
        assert clusters[0].entity == "Alice"

    def test_subjects_are_the_h1(self) -> None:
        eps = [_ep(0, "Alice", "Alice work", "Alice works at Acme.")]
        clusters = compute_entity_clusters(eps)
        assert clusters[0].subjects[0] == "Alice work"


class TestRunEntityCentricExperimentSynthetic:
    def test_hybrid_regime_no_fallback(self) -> None:
        result = run_entity_centric_experiment(corpus="synthetic")
        assert result.regime == "hybrid"
        assert result.n_entities == 4  # Alice, Bob, Carol, Dave
        assert result.n_entity_episodes == 12  # 4 entities x 3 episodes
        assert result.n_queries == 4

    def test_coherent_entities_high_recall_scattered_low(self) -> None:
        """The synthetic corpus must be falsifiable: coherent entities recovered
        as clusters, scattered entities not (mechanics)."""
        result = run_entity_centric_experiment(corpus="synthetic")
        per_entity = dict(
            zip(("Alice", "Bob", "Carol", "Dave"), result.per_entity_recall, strict=True)
        )
        # Coherent: the entity name in every body -> the query recovers the cluster.
        assert per_entity["Alice"] >= ENTITY_RECALL_THRESHOLD
        assert per_entity["Bob"] >= ENTITY_RECALL_THRESHOLD
        # Scattered: only the naming episode is recovered -> below the threshold.
        assert per_entity["Carol"] < ENTITY_RECALL_THRESHOLD
        assert per_entity["Dave"] < ENTITY_RECALL_THRESHOLD
        # The mean sits between the two sides (not trivially 0 or 1).
        assert 0.0 < result.recall_at_k < 1.0

    def test_entity_recall_fraction_reported(self) -> None:
        result = run_entity_centric_experiment(corpus="synthetic")
        # 3+3+1+1 = 8 of 12 entity episodes recovered.
        assert result.entity_recall_fraction == pytest.approx(8 / 12, abs=1e-6)
        assert result.covered_entities == pytest.approx(0.5, abs=1e-6)

    def test_unknown_corpus_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown corpus"):
            run_entity_centric_experiment(corpus="nope")

    def test_lmeb_corpus_not_wired(self) -> None:
        with pytest.raises(NotImplementedError, match="LMEB-S"):
            run_entity_centric_experiment(corpus="lmeb-s")


class TestDecideEntityCentric:
    def test_no_rung3_when_clustering_covers(self) -> None:
        result = EntityCentricResult(
            recall_at_k=0.8,
            entity_recall_fraction=0.8,
            n_entities=2,
            n_entity_episodes=6,
            n_queries=2,
            per_entity_recall=(1.0, 0.6),
            covered_entities=1.0,
            regime="hybrid",
        )
        decision = decide_entity_centric(result)
        assert decision["decision"] == "no_rung3"
        assert decision["flip"] is False

    def test_consider_rung3_when_clustering_does_not_cover(self) -> None:
        result = EntityCentricResult(
            recall_at_k=0.2,
            entity_recall_fraction=0.2,
            n_entities=2,
            n_entity_episodes=6,
            n_queries=2,
            per_entity_recall=(0.3, 0.1),
            covered_entities=0.0,
            regime="hybrid",
        )
        decision = decide_entity_centric(result)
        assert decision["decision"] == "consider_rung3"
        assert decision["flip"] is True

    def test_invalid_regime_on_fallback_g2(self) -> None:
        result = EntityCentricResult(
            recall_at_k=0.0,
            entity_recall_fraction=0.0,
            n_entities=2,
            n_entity_episodes=6,
            n_queries=2,
            per_entity_recall=(0.0, 0.0),
            covered_entities=0.0,
            regime="fallback_g2",
        )
        decision = decide_entity_centric(result)
        assert decision["decision"] == "invalid_regime"
        assert decision["flip"] is False

    def test_threshold_boundary(self) -> None:
        """At exactly the threshold the clustering covers entity-centric (>=)."""
        result = EntityCentricResult(
            recall_at_k=ENTITY_RECALL_THRESHOLD,
            entity_recall_fraction=ENTITY_RECALL_THRESHOLD,
            n_entities=1,
            n_entity_episodes=3,
            n_queries=1,
            per_entity_recall=(ENTITY_RECALL_THRESHOLD,),
            covered_entities=1.0,
            regime="hybrid",
        )
        assert decide_entity_centric(result)["decision"] == "no_rung3"


class TestBuildSyntheticCorpus:
    def test_ingests_and_retrieves(self, tmp_path) -> None:
        facade, storage, episodes = build_synthetic_corpus(tmp_path / "bench.db")
        try:
            rows = facade.recall("what do we know about Alice", k=ENTITY_TOP_K)
            assert rows
            assert any(r.score > 0.0 for r in rows)  # hybrid, not the listing regime
        finally:
            storage.close()


class TestRenderEntityCentricReport:
    def test_render_includes_metrics_and_decision(self) -> None:
        result = run_entity_centric_experiment(corpus="synthetic")
        decision = decide_entity_centric(result)
        rendered = render_entity_centric_report(result, decision)
        assert "Entity-centric recall experiment" in rendered
        assert "recall@10 (entity-centric)" in rendered
        assert "entity episodes recovered" in rendered
        assert "decision:" in rendered
        assert "reason:" in rendered


class TestRunExperimentWiring:
    def test_entity_centric_via_run_experiment(self) -> None:
        """``run_experiment(experiment='entity_centric')`` delegates to the module."""
        from seahorse.benchmark.experiments.runner import (
            render_experiment_report,
            run_experiment,
        )

        report = run_experiment(experiment="entity_centric", corpus="synthetic")
        assert report.experiment == "entity_centric"
        assert report.batch_result is not None
        assert report.batch_result.regime == "hybrid"
        assert report.decision["decision"] in ("no_rung3", "consider_rung3")
        rendered = render_experiment_report(report)
        assert "Entity-centric recall experiment" in rendered
        assert "decision:" in rendered

    def test_entity_centric_rejects_unknown_corpus(self) -> None:
        from seahorse.benchmark.experiments.runner import run_experiment

        with pytest.raises(ValueError, match="entity_centric experiment corpus"):
            run_experiment(experiment="entity_centric", corpus="claude-mem")
