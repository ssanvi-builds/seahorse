"""Tests for ``SeahorseSUT`` — the #12 → SUT adapter (f5-16 §3.2)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seahorse.benchmark.sut.seahorse_sut import SeahorseSUT
from seahorse.facade import build_facade
from tests.benchmark.conftest import FakeReaderLLM, FakeTokenizer


@pytest.fixture
def sut(tmp_path, fake_reader, fake_tokenizer):
    """A SeahorseSUT over a real G2 facade (no embeddings needed).

    ``retrieval_available=False`` forces the G2 regime explicitly — with the
    ``embeddings`` extra installed the default auto-resolves the hybrid path.
    """
    db = tmp_path / "bench.db"
    facade, storage = build_facade(db, retrieval_available=False)
    sut = SeahorseSUT(
        facade,
        lambda: build_facade(tmp_path / "bench2.db", retrieval_available=False)[0],
        reader_llm=fake_reader,
        tokenizer=fake_tokenizer,
        fact_id_to_session={},
    )
    yield sut
    storage.close()


def _session(session_id, date, turns):
    return {"session_id": session_id, "date": date, "turns": turns}


def test_ingest_populates_bridge(sut):
    d = datetime(2026, 1, 1, tzinfo=UTC)
    ep_ids = sut.ingest(
        [_session("s1", d, [{"body": "# France\n\nThe capital is Paris.", "title": "France"}])]
    )
    assert len(ep_ids) == 1
    assert len(sut.fact_id_to_session) == 1
    assert len(sut._ep_id_to_session) == 1
    assert set(sut._ep_id_to_session.values()) == {"s1"}


def test_sut_accepts_prepopulated_bridge(tmp_path, fake_reader, fake_tokenizer):
    """Warm-DB: a variant SUT over a copied corpus DB receives the template's
    bridge (ep_id→session, fact_id→session, fact_key→ep_id) instead of
    re-ingesting (f7 §5a warm-DB — the recency variants share one ingest)."""
    facade, storage = build_facade(tmp_path / "bench.db")
    sut = SeahorseSUT(
        facade,
        lambda: build_facade(tmp_path / "bench2.db")[0],
        reader_llm=fake_reader,
        tokenizer=fake_tokenizer,
        fact_id_to_session={"f1": "s1"},
        ep_id_to_session={"e1": "s1"},
        fact_key_to_ep_id={"france-capital": "e1"},
    )
    assert sut.fact_id_to_session == {"f1": "s1"}
    assert sut._ep_id_to_session == {"e1": "s1"}
    assert sut.fact_key_to_ep_id == {"france-capital": "e1"}
    storage.close()


def test_ingest_temporal_mode_sets_valid_at(sut):
    d = datetime(2026, 1, 1, tzinfo=UTC)
    temporal = SeahorseSUT(
        sut._facade,
        sut._facade_factory,
        reader_llm=FakeReaderLLM(),
        tokenizer=FakeTokenizer(),
        fact_id_to_session={},
        temporal_mode=True,
    )
    ep_ids = temporal.ingest(
        [_session("s1", d, [{"body": "# France\n\nThe capital is Paris.", "title": "France"}])]
    )
    assert len(ep_ids) == 1
    # The episode's valid_at should be the session date (temporal mode)
    ep = sut._facade.recall_full([ep_ids[0]])[0].episode
    assert ep.valid_at == d


def test_query_returns_response_with_bridge(sut, fake_reader):
    d = datetime(2026, 1, 1, tzinfo=UTC)
    sut.ingest(
        [_session("s1", d, [{"body": "# France\n\nThe capital is Paris.", "title": "France"}])]
    )
    resp = sut.query("What is the capital of France?")
    assert resp.answer == "Paris"
    assert resp.retrieved_ep_ids
    assert resp.retrieved_session_ids == ("s1",)
    assert resp.tokens_consumed_measured > 0
    assert "index" in resp.latency_ms


def test_query_detects_fallback_g2(sut):
    """In the G2 regime all scores are 0.0 → honest score_source=fallback_g2."""
    d = datetime(2026, 1, 1, tzinfo=UTC)
    sut.ingest(
        [_session("s1", d, [{"body": "# France\n\nThe capital is Paris.", "title": "France"}])]
    )
    resp = sut.query("France")
    assert resp.sut_metadata["score_source"] == "fallback_g2"


def test_probe_level_index(sut):
    d = datetime(2026, 1, 1, tzinfo=UTC)
    sut.ingest(
        [_session("s1", d, [{"body": "# France\n\nThe capital is Paris.", "title": "France"}])]
    )
    result = sut.probe_level("France", "index")
    assert result["count"] == 1
    assert result["latency_ms"] >= 0


def test_probe_level_timeline_and_full(sut):
    d = datetime(2026, 1, 1, tzinfo=UTC)
    sut.ingest(
        [_session("s1", d, [{"body": "# France\n\nThe capital is Paris.", "title": "France"}])]
    )
    tl = sut.probe_level("France", "timeline")
    assert "latency_ms" in tl
    full = sut.probe_level("France", "full")
    assert "latency_ms" in full


def test_probe_level_unknown_raises(sut):
    with pytest.raises(ValueError, match="unknown probe level"):
        sut.probe_level("France", "bogus")


def test_apply_knowledge_updates_creates_supersedes(sut):
    d1 = datetime(2026, 1, 1, tzinfo=UTC)
    ep_ids = sut.ingest(
        [
            _session(
                "s1",
                d1,
                [
                    {
                        "body": "# France\n\nThe capital is Paris.",
                        "title": "France",
                        "fact_key": "fc",
                    }
                ],
            )
        ]
    )
    old_ep_id = ep_ids[0]
    new_ep_ids = sut.apply_knowledge_updates(
        [
            {
                "old_ep_id": old_ep_id,
                "new_body": "# France\n\nThe capital is now Lyon.",
                "session_id": "s2",
            }
        ]
    )
    assert len(new_ep_ids) == 1
    # The new version is in session s2 via the ep_id bridge
    assert sut._ep_id_to_session[new_ep_ids[0]] == "s2"
    # The old version is invalidated
    rows = sut._facade.recall("France", k=10)
    assert all(r.invalid_at is None for r in rows)  # recall returns vigente only


def test_reset_clears_bridges(sut):
    d = datetime(2026, 1, 1, tzinfo=UTC)
    sut.ingest(
        [_session("s1", d, [{"body": "# France\n\nThe capital is Paris.", "title": "France"}])]
    )
    assert sut.fact_id_to_session
    sut.reset()
    assert not sut.fact_id_to_session
    assert not sut._ep_id_to_session


def test_identity_reports_experiment_flags(sut):
    ident = sut.identity()
    assert ident["sut_type"] == "seahorse"
    assert ident["extraction_mode"] == "skip"
    assert ident["score_source"] == "mvp1_rrf"
    assert ident["recency_config"] is None
    assert ident["rerank_enabled"] is False
    assert ident["embed_mode"] == "body"


# --------------------------------------------------------- PIT temporal-reasoning

def test_query_passes_state_at_pit_in_temporal_mode(sut):
    """f5-16 §3.4: temporal-reasoning questions evaluate with ``state_at`` PIT."""
    from seahorse.disclosure.types import PITPoint

    calls: list = []

    class _SpyFacade:
        def recall(self, query, *, pit=None, k=10, **kwargs):
            calls.append(pit)
            return []

    temporal = SeahorseSUT(
        _SpyFacade(),
        lambda: _SpyFacade(),
        reader_llm=FakeReaderLLM(),
        tokenizer=FakeTokenizer(),
        fact_id_to_session={},
        temporal_mode=True,
    )
    d = datetime(2026, 1, 1, tzinfo=UTC)
    temporal.query("What was the capital on day 1?", question_date=d)
    assert calls == [PITPoint(kind="state_at", t=d)]


def test_query_pit_queries_false_queries_active_now(sut):
    """F7 §5a: the recency boost's gate is ``pit is None`` — the recency
    experiment queries the active-now regime (no PIT) even in temporal mode, so
    the boost is actually testable (all LMEB questions carry a question_date)."""
    calls: list = []

    class _SpyFacade:
        def recall(self, query, *, pit=None, k=10, **kwargs):
            calls.append(pit)
            return []

    temporal = SeahorseSUT(
        _SpyFacade(),
        lambda: _SpyFacade(),
        reader_llm=FakeReaderLLM(),
        tokenizer=FakeTokenizer(),
        fact_id_to_session={},
        temporal_mode=True,
        pit_queries=False,
    )
    d = datetime(2026, 1, 1, tzinfo=UTC)
    temporal.query("What was the capital on day 1?", question_date=d)
    assert calls == [None]  # active-now, no PIT → the recency boost can fire


def test_query_no_pit_without_question_date(sut):
    calls: list = []

    class _SpyFacade:
        def recall(self, query, *, pit=None, k=10, **kwargs):
            calls.append(pit)
            return []

    temporal = SeahorseSUT(
        _SpyFacade(),
        lambda: _SpyFacade(),
        reader_llm=FakeReaderLLM(),
        tokenizer=FakeTokenizer(),
        fact_id_to_session={},
        temporal_mode=True,
    )
    temporal.query("What is the capital?")  # no question_date
    assert calls == [None]


def test_query_falls_back_without_pit_when_unsupported(sut):
    """G2 (no PIT axis, ADR-03): honest degrade to active-now, no crash."""
    from seahorse.disclosure.types import PITPoint
    from seahorse.facade.errors import PitRecallNotSupportedMVP0

    calls: list = []

    class _G2Facade:
        supports_pit = False

        def recall(self, query, *, pit=None, k=10, **kwargs):
            calls.append(pit)
            if pit is not None:
                raise PitRecallNotSupportedMVP0()
            return []

    temporal = SeahorseSUT(
        _G2Facade(),
        lambda: _G2Facade(),
        reader_llm=FakeReaderLLM(),
        tokenizer=FakeTokenizer(),
        fact_id_to_session={},
        temporal_mode=True,
    )
    d = datetime(2026, 1, 1, tzinfo=UTC)
    temporal.query("What was the capital on day 1?", question_date=d)
    assert calls == [PITPoint(kind="state_at", t=d), None]  # pit attempt + fallback
