"""End-to-end smoke test for the MVP-0 engine surface (Phase 11, owned #2).

Exercises the full lifecycle of one fact through every MVP-0 primitive that
an agent/MCP caller would chain, against the real #6 persistence stack (no
mocks): ``remember`` -> ``get_vigente`` -> ``improve`` -> ``forget`` ->
``audit_log`` -> ``follow_supersedes_chain``. This is a smoke test, not a
behavior matrix: each primitive's branches are covered by its own module.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seahorse.engine.engine import BiTemporalEngine

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=2)
EVEN_LATER = NOW + timedelta(hours=4)


@pytest.fixture()
def engine(storage):
    repo, audit = storage
    return BiTemporalEngine(repo, audit), repo, audit


def test_full_lifecycle_smoke(engine):
    eng, repo, audit = engine

    # 1. remember a fact (agent path, UUIDv7).
    wr = eng.remember(
        body="# Madrid is the capital of Spain\n",
        by={"source_type": "agent", "agent_id": "a1"},
        now=NOW,
    )
    assert wr.status == "ACTIVE"
    assert wr.fact_id is not None
    ep_id = wr.ep_id
    assert repo.get(ep_id) is not None

    # 2. get_vigente sees it (activo ahora).
    vigent = eng.get_vigente()
    assert [e.id for e in vigent] == [ep_id]

    # 3. improve: human correction -> new episode, old invalidated (I8 atomic).
    new_ep = eng.improve(
        ep_id,
        "# Madrid is the capital of Spain (corrected)\n",
        by={"source_type": "human", "agent_id": "sergio"},
        reason="typo",
        now=LATER,
    )
    assert new_ep.supersedes == ep_id
    assert repo.get(ep_id).invalid_at == LATER
    # exactly one vigente now (the successor), old excluded.
    assert {e.id for e in eng.get_vigente()} == {new_ep.id}

    # 4. forget the successor (soft-delete).
    forgotten = eng.forget(
        new_ep.id, reason="obsolete", by={"agent_id": "sergio"}, now=EVEN_LATER
    )
    assert forgotten.invalid_at == EVEN_LATER
    assert eng.get_vigente() == []

    # 5. audit_log has the full history (apply + improve + forget).
    #    The improve audit targets the OLD episode (the one being replaced).
    events = eng.audit_log(ep_id)
    primitives = [e.primitive for e in events]
    assert primitives == ["apply", "improve"]
    new_events = eng.audit_log(new_ep.id)
    assert [e.primitive for e in new_events] == ["forget"]

    # 6. follow_supersedes_chain reconstructs the full replacement history.
    chain_ids = {e.id for e in eng.follow_supersedes_chain(ep_id)}
    assert chain_ids == {ep_id, new_ep.id}

    # 7. freshness_view on the forgotten successor is stale.
    fv = eng.freshness_view(new_ep.id, now=EVEN_LATER + timedelta(hours=1))
    assert fv.stale is True
    assert fv.fact_id is not None  # successor has a derivable subject