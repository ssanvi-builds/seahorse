"""Bridge test: ``WriteResult.fact_id == IndexRow.fact_id``.

Guaranteed by construction (``fact_id_for`` shared between the engine
and the index — ``engine/engine.py`` derives ``fact_id_of(subject)``,
``disclosure/shaper.py`` reads ``row.fact_id`` from the index). This test pins
the bridge the benchmark's Recall@k/nDCG@k depend on: the
``fact_id → session_id`` map populated from ``WriteResult.fact_id`` must match
the ``IndexRow.fact_id`` returned by ``recall``.
"""

from __future__ import annotations

import pytest

from seahorse.facade import build_facade
from tests.benchmark.conftest import remember_episode


@pytest.fixture
def facade(tmp_path):
    facade, storage = build_facade(tmp_path / "bench.db")
    yield facade
    storage.close()


def test_write_result_fact_id_equals_index_row_fact_id(facade):
    """remember → WriteResult.fact_id → recall → IndexRow.fact_id → equal."""
    wr = remember_episode(facade, "# France\n\nThe capital of France is Paris.", title="France")
    assert wr.fact_id is not None

    rows = facade.recall("France", k=10)
    assert rows, "recall must return the ingested episode"

    for row in rows:
        if row.fact_id == wr.fact_id:
            return  # bridge holds
    pytest.fail(
        f"WriteResult.fact_id={wr.fact_id!r} not found among IndexRow.fact_ids="
        f"{[r.fact_id for r in rows]!r}"
    )


def test_bridge_holds_across_multiple_episodes(facade):
    """The bridge holds for every ingested episode, not just the first."""
    bodies = [
        ("# France\n\nThe capital of France is Paris.", "France"),
        ("# Spain\n\nThe capital of Spain is Madrid.", "Spain"),
        ("# Italy\n\nThe capital of Italy is Rome.", "Italy"),
    ]
    fact_ids = set()
    for body, title in bodies:
        wr = remember_episode(facade, body, title=title)
        assert wr.fact_id is not None
        fact_ids.add(wr.fact_id)

    rows = facade.recall("capital", k=10)
    row_fact_ids = {r.fact_id for r in rows}
    assert fact_ids <= row_fact_ids, (
        f"all WriteResult.fact_ids {fact_ids} must appear in IndexRow.fact_ids {row_fact_ids}"
    )


def test_fact_id_to_session_bridge_is_consistent(facade):
    """The fact_id→session_id map the CorpusBuilder builds is consistent with
    the IndexRow.fact_id returned by recall."""
    body = "# France\n\nThe capital of France is Paris."
    wr = remember_episode(facade, body, session_id="s1", title="France")
    assert wr.fact_id is not None
    fact_id_to_session = {wr.fact_id: "s1"}

    rows = facade.recall("France", k=10)
    recovered = {fact_id_to_session.get(r.fact_id) for r in rows}
    assert "s1" in recovered
