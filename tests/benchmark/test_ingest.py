"""Tests for ``benchmark/ingest`` — the shared session-shape ingest (C7).

``turn_payload``/``remember_sessions`` single-home the session→remember loop
the SeahorseSUT and ``lmeb_corpus.ingest_haystack`` once repeated. The SUT
keeps its own bridge bookkeeping (it owns the three maps); the standalone
session experiments never run the KnowledgeUpdateSimulator, so the helper
deliberately tracks only ``ep_id → session``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.benchmark.ingest import remember_sessions, turn_payload
from seahorse.benchmark.sut.seahorse_sut import SeahorseSUT
from seahorse.facade import build_facade
from tests.benchmark.conftest import FakeReaderLLM

_D0 = datetime(2026, 1, 1, tzinfo=UTC)
_D1 = datetime(2026, 2, 1, tzinfo=UTC)


def _session(session_id, date, turns):
    return {"session_id": session_id, "date": date, "turns": turns}


def test_turn_payload_matches_sut_shape():
    """The payload the SeahorseSUT.ingest builds, byte-identical: agent source
    + no valid_at outside temporal mode; human source + valid_at=date in
    temporal mode (a human source permits an arbitrary valid_at)."""
    turn = {"body": "# France\n\nParis.", "title": "France", "summary": "caps"}
    payload = turn_payload(turn, "s1", temporal=False, agent_id="bench")
    assert payload.body == "# France\n\nParis."
    assert payload.by["source_type"] == "agent"  # by rides as dict at this layer
    assert payload.by["agent_id"] == "bench"
    assert payload.by["session_id"] == "s1"
    assert payload.valid_at is None
    assert payload.title == "France"
    assert payload.summary == "caps"

    temporal = turn_payload({"body": "b"}, "s2", temporal=True, valid_at=_D0)
    assert temporal.by["source_type"] == "human"
    assert temporal.valid_at == _D0
    assert temporal.title is None  # absent keys are None, not errors
    assert temporal.summary is None


def test_remember_sessions_dedups_and_sorts_oldest_first(tmp_path):
    """Sessions repeat across questions; one remember per TURN, sessions
    ordered oldest-first; duplicate session_ids collapse to one ingest."""
    facade, storage = build_facade(tmp_path / "bench.db", retrieval_available=False)
    sessions = [
        _session("s2", _D1, [{"body": "# Lyon\n\nTwo.", "title": "Lyon"}]),
        _session("s1", _D0, [{"body": "# France\n\nOne.", "title": "France"}]),
        _session("s1", _D0, [{"body": "# Dupe\n\nDupe."}]),  # dup session_id
    ]
    ep_ids, ep_id_to_session = remember_sessions(facade, sessions)
    assert len(ep_ids) == 2
    assert set(ep_id_to_session.values()) == {"s1", "s2"}
    storage.close()


def test_remember_sessions_summary_passthrough_is_stored(tmp_path):
    """The summary field rides the payload into the store (the B→A delta:
    the old lmeb loop dropped it)."""
    facade, storage = build_facade(tmp_path / "bench.db", retrieval_available=False)
    ep_ids, _ = remember_sessions(
        facade,
        [
            _session(
                "s1",
                _D0,
                [{"body": "# France\n\nParis.", "title": "France", "summary": "cap"}],
            )
        ],
    )
    details = facade.recall_full(ep_ids)
    assert details[0].episode.summary == "cap"
    storage.close()


def test_remember_sessions_matches_sut_ingest_parity(tmp_path):
    """The helper and the SUT ingest produce the SAME ep/session landscape for
    the same sessions (the payload shape is single-homed -- the SUT delegates
    to ``turn_payload``)."""
    sessions = [
        _session(
            "s1",
            _D0,
            [{"body": "# France\n\nParis.", "title": "France", "summary": "cap"}],
        ),
        _session("s2", _D1, [{"body": "# Lyon\n\nTwo.", "title": "Lyon"}]),
    ]

    facade_a, storage_a = build_facade(tmp_path / "a.db", retrieval_available=False)
    ep_ids_a, map_a = remember_sessions(facade_a, sessions, temporal=True)

    facade_b, storage_b = build_facade(tmp_path / "b.db", retrieval_available=False)

    class _IdentityTokenizer:
        def count(self, text):
            return len(text)

    sut = SeahorseSUT(
        facade_b,
        lambda: facade_b,
        reader_llm=FakeReaderLLM(),
        tokenizer=_IdentityTokenizer(),
        temporal_mode=True,
    )
    ep_ids_b = sut.ingest(sessions)

    assert len(ep_ids_a) == len(ep_ids_b) == 2  # 1 turn s1 + 1 turn s2
    assert set(map_a.values()) == {"s1", "s2"}
    assert set(sut.bridge.ep_id_to_session.values()) == {"s1", "s2"}
    # the temporal payloads agree: human source, valid_at, summary passthrough
    d_a = facade_a.recall_full([ep_ids_a[0]])[0].episode
    d_b = facade_b.recall_full([ep_ids_b[0]])[0].episode
    # the helper's synthesized agent identity matches the SUT's default --
    # a drift between _BENCHMARK_AGENT_ID and the SUT ctor default must fail
    assert d_a.provenance["agent_id"] == d_b.provenance["agent_id"] == "seahorse-benchmark"
    assert d_a.provenance["source_type"] == d_b.provenance["source_type"] == "human"
    assert d_a.valid_at == d_b.valid_at == _D0
    assert d_a.summary == d_b.summary == "cap"
    storage_a.close()
    storage_b.close()


def test_remember_sessions_oldest_first_decides_the_survivor(tmp_path):
    """The oldest-first sort is CORRECTNESS, not convenience: with the engine's
    one-active-per-subject rule, the first-ingested version of a fact survives
    and its successor collides -- ingest the successor first and the survivor
    flips (LMEB knowledge-update payloads make this case real)."""
    facade, storage = build_facade(tmp_path / "bench.db", retrieval_available=False)
    sessions = [
        # the successor (same subject, newer) listed FIRST in the input
        _session("s2", _D1, [{"body": "# France\n\nSuccessor body.", "title": "France"}]),
        _session("s1", _D0, [{"body": "# France\n\nOriginal body.", "title": "France"}]),
    ]
    ep_ids, ep_id_to_session = remember_sessions(facade, sessions)
    assert len(ep_ids) == 1
    assert set(ep_id_to_session.values()) == {"s1"}  # the OLDER session won
    storage.close()
