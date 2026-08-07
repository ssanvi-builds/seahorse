"""Tests for ``KnowledgeUpdateSimulator`` (f5-16 §4.6, OQ-16-13)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.benchmark.knowledge_update_simulator import KnowledgeUpdateSimulator
from seahorse.benchmark.sut.seahorse_sut import SeahorseSUT
from seahorse.facade import build_facade
from tests.benchmark.conftest import FakeReaderLLM, FakeTokenizer


@pytest.fixture
def sut(tmp_path):
    facade, storage = build_facade(tmp_path / "bench.db")
    sut = SeahorseSUT(
        facade,
        lambda: build_facade(tmp_path / "bench2.db")[0],
        reader_llm=FakeReaderLLM(),
        tokenizer=FakeTokenizer(),
        fact_id_to_session={},
    )
    yield sut
    storage.close()


def test_derive_updates_from_explicit_pairs(sut, synthetic_dataset):
    # Ingest the haystack first so fact_key_to_ep_id is populated
    sut.ingest([s for i in synthetic_dataset.instances for s in i.haystack])
    kus = KnowledgeUpdateSimulator(sut)
    updates = kus.derive_updates(synthetic_dataset)
    assert "q2" in updates
    pair = updates["q2"][0]
    assert pair["fact_key"] == "france-capital"
    assert pair["old_ep_id"] is not None  # resolved from fact_key_to_ep_id


def test_derive_updates_from_haystack(sut):
    """OQ-16-13: derive pairs from turns sharing a fact_key across sessions."""
    d1 = datetime(2026, 1, 1, tzinfo=UTC)
    d2 = datetime(2026, 1, 2, tzinfo=UTC)
    from seahorse.benchmark.contracts import BenchmarkDataset, BenchmarkInstance

    inst = BenchmarkInstance(
        instance_id="q1",
        question="What is the new capital?",
        golden_answer="Lyon",
        golden_session_ids=("s2",),
        golden_evidence=(),
        question_type="knowledge-update",
        capabilities=("knowledge-update",),
        cognitive_category="semantic",
        question_date=None,
        haystack=(
            {
                "session_id": "s1",
                "date": d1,
                "turns": [
                    {"body": "old", "title": "France", "fact_key": "fc"},
                ],
            },
            {
                "session_id": "s2",
                "date": d2,
                "turns": [
                    {"body": "new", "title": "France", "fact_key": "fc"},
                ],
            },
        ),
    )
    dataset = BenchmarkDataset(
        name="synthetic", version="1", config="s", split_hash="h", loader_code_sha256="c",
        instances=(inst,), metadata={},
    )
    # Ingest the haystack first so fact_key_to_ep_id is populated
    sut.ingest([s for i in dataset.instances for s in i.haystack])
    kus = KnowledgeUpdateSimulator(sut)
    updates = kus.derive_updates(dataset)
    assert "q1" in updates
    pair = updates["q1"][0]
    assert pair["old_body"] == "old"
    assert pair["new_body"] == "new"
    assert pair["old_ep_id"] is not None


def test_apply_creates_supersedes_chain(sut, synthetic_dataset):
    kus = KnowledgeUpdateSimulator(sut)
    updates = kus.derive_updates(synthetic_dataset)
    new_ep_ids = kus.apply(sut, updates)
    assert "q2" in new_ep_ids
    assert len(new_ep_ids["q2"]) == 1
    # The new version is in the ep_id bridge under session s2
    assert sut._ep_id_to_session[new_ep_ids["q2"][0]] == "s2"


def test_apply_ingests_old_version_when_missing(sut):
    """When old_ep_id is None and not in the corpus, ingest it fresh first."""
    d2 = datetime(2026, 1, 2, tzinfo=UTC)
    from seahorse.benchmark.contracts import BenchmarkDataset, BenchmarkInstance

    inst = BenchmarkInstance(
        instance_id="q1",
        question="Q?",
        golden_answer="Lyon",
        golden_session_ids=("s2",),
        golden_evidence=(),
        question_type="knowledge-update",
        capabilities=("knowledge-update",),
        cognitive_category="semantic",
        question_date=None,
        haystack=(),
        knowledge_updates=(
            {
                "fact_key": "fc",
                "old_ep_id": None,
                "old_body": "# France\n\nThe capital is Paris.",
                "new_body": "# France\n\nThe capital is now Lyon.",
                "session_id": "s2",
                "date": d2,
            },
        ),
    )
    dataset = BenchmarkDataset(
        name="synthetic", version="1", config="s", split_hash="h", loader_code_sha256="c",
        instances=(inst,), metadata={},
    )
    kus = KnowledgeUpdateSimulator(sut)
    updates = kus.derive_updates(dataset)
    new_ep_ids = kus.apply(sut, updates)
    assert len(new_ep_ids["q1"]) == 1


def test_derive_updates_skips_non_ku(sut, synthetic_dataset):
    kus = KnowledgeUpdateSimulator(sut)
    updates = kus.derive_updates(synthetic_dataset)
    assert "q1" not in updates  # information-extraction, not knowledge-update
    assert "q5" not in updates  # abstention
