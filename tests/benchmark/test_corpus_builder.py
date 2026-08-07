"""Tests for ``CorpusBuilder`` + ``AdvancingClock`` (f5-16 §3.3/§3.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from seahorse.benchmark.corpus_builder import (
    AdvancingClock,
    CorpusBuilder,
    earliest_session_date,
)
from seahorse.benchmark.sut.seahorse_sut import SeahorseSUT
from seahorse.facade import build_facade
from tests.benchmark.conftest import FakeReaderLLM, FakeTokenizer


def test_advancing_clock_is_deterministic_and_ordered():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    clock = AdvancingClock(base, delta_seconds=1.0)
    t1 = clock()
    t2 = clock()
    t3 = clock()
    assert t1 == base
    assert t2 == base + timedelta(seconds=1)
    assert t3 == base + timedelta(seconds=2)
    # Deterministic: a fresh clock reproduces the same sequence
    clock2 = AdvancingClock(base, delta_seconds=1.0)
    assert clock2() == t1
    assert clock2() == t2


def test_earliest_session_date(synthetic_dataset):
    base = earliest_session_date(synthetic_dataset)
    assert base == datetime(2026, 1, 1, tzinfo=UTC)


def test_corpus_builder_ingests_and_returns_bridge(tmp_path, synthetic_dataset):
    facade, storage = build_facade(tmp_path / "bench.db")
    sut = SeahorseSUT(
        facade,
        lambda: build_facade(tmp_path / "bench2.db")[0],
        reader_llm=FakeReaderLLM(),
        tokenizer=FakeTokenizer(),
        fact_id_to_session={},
    )
    builder = CorpusBuilder(sut)
    bridge = builder.ingest(synthetic_dataset)
    assert bridge, "the bridge must be populated"
    # The France fact (in s1) maps to s1
    assert "s1" in set(bridge.values())
    storage.close()


def test_corpus_builder_ingests_all_sessions(tmp_path, synthetic_dataset):
    facade, storage = build_facade(tmp_path / "bench.db")
    sut = SeahorseSUT(
        facade,
        lambda: build_facade(tmp_path / "bench2.db")[0],
        reader_llm=FakeReaderLLM(),
        tokenizer=FakeTokenizer(),
        fact_id_to_session={},
    )
    builder = CorpusBuilder(sut)
    builder.ingest(synthetic_dataset)
    # s1 (France) + s3 (Project) ingested; s2's France collides (same subject,
    # superseded later by the KnowledgeUpdateSimulator via improve)
    assert len(sut._ep_id_to_session) >= 2
    storage.close()
