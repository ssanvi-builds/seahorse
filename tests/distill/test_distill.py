"""Tests for ``seahorse.distill.distill`` — the ``distill_episodes`` primitive.

The distillation is a write-path operation over existing extension points:
``consolidated`` is schema-valid, ``cognitive_type=semantic`` exists, and the
consolidated episode references its representative source via ``supersedes``
WITHOUT invalidating it — the sources stay current-state (they are the
evidence). The provenance carries ``extraction_mode=consolidated``. The subject
is the stable clustering key (distinct from the per-turn stored subjects).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seahorse.distill.cluster import cluster_key
from seahorse.distill.distill import distill_episodes

NOW = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)


def _remember(engine, *, body: str, now: datetime) -> str:
    wr = engine.remember(
        body=body,
        by={"source_type": "agent", "agent_id": "a1", "session_id": "sess-1"},
        now=now,
    )
    return wr.ep_id


def _episode(engine, ep_id):
    return engine._repo.get(ep_id)  # noqa: SLF001


def _cluster(engine):
    """Build a 3-episode cluster about the same topic; return (ids, representative)."""
    ids = [
        _remember(
            engine, body="# Fix the flaky recall test [sess-1:1]\n\nOld.", now=NOW
        ),
        _remember(
            engine,
            body="# Fix the flaky recall test [sess-1:2]\n\nMid.",
            now=NOW + timedelta(minutes=1),
        ),
        _remember(
            engine,
            body="# Fix the flaky recall test [sess-1:3]\n\nNew.",
            now=NOW + timedelta(minutes=2),
        ),
    ]
    return ids, _episode(engine, ids[2])


def test_distill_writes_consolidated_semantic_episode(engine) -> None:
    eng, repo, audit = engine
    ids, rep = _cluster(eng)
    wr = distill_episodes(
        eng,
        source_ep_ids=ids,
        representative=rep,
        consolidated_body="# Fix the flaky recall test\n\nConsolidated knowledge.",
        by={"source_type": "system", "agent_id": "consolidator"},
    )
    assert wr.status == "ACTIVE"
    ep = repo.get(wr.ep_id)
    assert ep.cognitive_type == "semantic"
    assert ep.provenance["extraction_mode"] == "consolidated"
    assert ep.provenance["source_type"] == "system"


def test_distill_supersedes_representative(engine) -> None:
    eng, repo, audit = engine
    ids, rep = _cluster(eng)
    wr = distill_episodes(
        eng,
        source_ep_ids=ids,
        representative=rep,
        consolidated_body="# Fix the flaky recall test\n\nConsolidated.",
        by={"source_type": "system", "agent_id": "consolidator"},
    )
    ep = repo.get(wr.ep_id)
    assert ep.supersedes == rep.id
    assert ep.supersedes_reason == "merge"


def test_distill_sources_stay_vigente(engine) -> None:
    eng, repo, audit = engine
    ids, rep = _cluster(eng)
    distill_episodes(
        eng,
        source_ep_ids=ids,
        representative=rep,
        consolidated_body="# Fix the flaky recall test\n\nConsolidated.",
        by={"source_type": "system", "agent_id": "consolidator"},
    )
    # The sources are the evidence — they are NOT invalidated.
    for eid in ids:
        assert repo.get(eid).invalid_at is None


def test_distill_subject_is_stable_clustering_key(engine) -> None:
    eng, repo, audit = engine
    ids, rep = _cluster(eng)
    wr = distill_episodes(
        eng,
        source_ep_ids=ids,
        representative=rep,
        consolidated_body="# Fix the flaky recall test\n\nConsolidated.",
        by={"source_type": "system", "agent_id": "consolidator"},
    )
    ep = repo.get(wr.ep_id)
    # The subject is the clustering key (no [session_tag:n] suffix) — stable
    # across turns, distinct from the per-turn stored subjects.
    assert ep.subject == cluster_key(rep.subject)
    assert "[sess" not in (ep.subject or "")


def test_distill_synthetic_consolidator_session(engine) -> None:
    eng, repo, audit = engine
    ids, rep = _cluster(eng)
    wr = distill_episodes(
        eng,
        source_ep_ids=ids,
        representative=rep,
        consolidated_body="# Fix the flaky recall test\n\nConsolidated.",
        by={"source_type": "system", "agent_id": "consolidator"},
    )
    ep = repo.get(wr.ep_id)
    assert ep.provenance["session_id"].startswith("consolidate-")


def test_distill_representative_must_be_in_sources(engine) -> None:
    eng, repo, audit = engine
    ids, rep = _cluster(eng)
    with pytest.raises(ValueError):
        distill_episodes(
            eng,
            source_ep_ids=ids[:2],  # representative NOT in sources
            representative=rep,
            consolidated_body="# X\n\nY.",
            by={"source_type": "system", "agent_id": "consolidator"},
        )


def test_distill_respects_llm_provenance(engine) -> None:
    eng, repo, audit = engine
    ids, rep = _cluster(eng)
    wr = distill_episodes(
        eng,
        source_ep_ids=ids,
        representative=rep,
        consolidated_body="# Fix the flaky recall test\n\nSynthesized.",
        by={
            "source_type": "system",
            "agent_id": "consolidator",
            "model_used": "ollama/qwen3:1.7b",
            "prompt_hash": "h" * 64,
            "confidence": 0.9,
        },
    )
    ep = repo.get(wr.ep_id)
    # The LLM provenance from `by` is respected (not forced to None/1.0).
    assert ep.provenance["extraction_mode"] == "consolidated"
    assert ep.provenance["model_used"] == "ollama/qwen3:1.7b"
    assert ep.provenance["prompt_hash"] == "h" * 64
    assert ep.provenance["confidence"] == 0.9


def test_distill_respects_degrade_marker(engine) -> None:
    eng, repo, audit = engine
    ids, rep = _cluster(eng)
    wr = distill_episodes(
        eng,
        source_ep_ids=ids,
        representative=rep,
        consolidated_body="# Fix the flaky recall test\n\nFallback.",
        by={
            "source_type": "system",
            "agent_id": "consolidator",
            "model_used": None,
            "prompt_hash": None,
            "confidence": 1.0,
            "degraded_from": "llm",
            "degrade_reason": "llm_degraded",
        },
    )
    ep = repo.get(wr.ep_id)
    # The honest degrade marker (C8.7) passes through from `by`.
    assert ep.provenance["degraded_from"] == "llm"
    assert ep.provenance["degrade_reason"] == "llm_degraded"


def test_distill_supersedes_existing_note(engine) -> None:
    # F7+ supersession: when a cluster whose key already has a consolidated note
    # gains NEW valid episodes, the note is UPDATED via improve (invalidate +
    # atomic append) instead of duplicating.
    eng, repo, audit = engine
    ids, rep = _cluster(eng)
    wr1 = distill_episodes(
        eng,
        source_ep_ids=ids,
        representative=rep,
        consolidated_body="# Fix the flaky recall test\n\nConsolidated v1.",
        by={"source_type": "system", "agent_id": "consolidator"},
    )
    # A new episode arrives (the representative changes).
    new_id = _remember(
        eng,
        body="# Fix the flaky recall test [sess-1:4]\n\nNewest.",
        now=NOW + timedelta(minutes=3),
    )
    new_rep = _episode(eng, new_id)
    wr2 = distill_episodes(
        eng,
        source_ep_ids=ids + [new_id],
        representative=new_rep,
        consolidated_body="# Fix the flaky recall test\n\nConsolidated v2.",
        by={"source_type": "system", "agent_id": "consolidator"},
        supersede_ep_id=wr1.ep_id,
    )
    # The old note is invalidated; the new one supersedes it (CORRECTION).
    old = repo.get(wr1.ep_id)
    assert old.invalid_at is not None
    new = repo.get(wr2.ep_id)
    assert new.supersedes == wr1.ep_id
    assert new.supersedes_reason == "correction"
    assert new.cognitive_type == "semantic"
    assert new.provenance["extraction_mode"] == "consolidated"


def test_distill_summary_skips_h1(engine) -> None:
    eng, repo, audit = engine
    ids, rep = _cluster(eng)
    wr = distill_episodes(
        eng,
        source_ep_ids=ids,
        representative=rep,
        consolidated_body="# Fix the flaky recall test\n\nIt fails intermittently on CI.",
        by={"source_type": "system", "agent_id": "consolidator"},
    )
    ep = repo.get(wr.ep_id)
    summary = ep.summary or ""
    # The summary is the first sentence of the CONTENT (skipping the H1) —
    # never the tagged H1.
    assert "It fails intermittently on CI." in summary
    assert "[sess" not in summary
