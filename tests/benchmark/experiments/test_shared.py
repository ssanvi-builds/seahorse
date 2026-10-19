"""Tests for the shared experiment helpers — the unified episode ingest loop.

``ingest_episodes`` replaces the four byte-identical loop copies (session map,
stored-with-ids, stored, end_to_end's fused D+C) plus decay's ``now``-biased
loop. The behavioral parity guards live in the per-experiment suites; these
tests pin the helper's own contract: collision exclusion, id remapping, provenance
maps, first-wins fact_key, and the per-episode ``now`` selector.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from seahorse.benchmark.experiments._shared import (
    BENCH_EPOCH,
    EpisodeIngestResult,
    ingest_episodes,
    session_to_ep_ids,
)
from seahorse.contracts.episode import Episode
from seahorse.facade import build_facade


def _ep(
    eid: str,
    body: str,
    *,
    session: str = "s1",
    fact_key: str | None = None,
    created: datetime | None = None,
    source_record_id: str | None = None,
    body_text: str | None = None,
) -> Episode:
    provenance: dict[str, str] = {
        "source_type": "importer",
        "importer_vendor": "claude-mem",
        "extraction_mode": "skip",
        "session_id": session,
        # the deterministic ep_id = uuid5(vendor:source_record_id:body_hash)
        "source_record_id": source_record_id if source_record_id is not None else eid,
    }
    if fact_key is not None:
        provenance["fact_key"] = fact_key
    return Episode(
        id=eid,
        created_at=created or BENCH_EPOCH,
        schema_version="1.1",
        provenance=provenance,
        body=body_text or f"# {body}\n\n{body} narrative for the deterministic corpus.",
        title=body,
        valid_at=created or BENCH_EPOCH,
        cognitive_type="semantic",
        source_type="importer",
    )


def test_ingest_episodes_stores_collision_free_and_remaps_ids(tmp_path):
    """COLLISION status = same SUBJECT, different deterministic identity →
    ``WriteResult.ep_id`` is None and the episode is EXCLUDED from stored +
    id_map."""
    facade, storage = build_facade(tmp_path / "bench.db", retrieval_available=False)
    result = ingest_episodes(
        facade,
        [
            _ep("a", "France", source_record_id="dup-1"),
            _ep("b", "France", source_record_id="dup-2", body_text="# France alt"),
        ],
    )
    assert len(result.stored) == 1
    assert len(result.id_map) == 1
    assert result.id_map == {"a": result.stored[0].id}
    assert result.stored[0].id != "a"  # the engine's derived UUIDv5
    storage.close()


def test_ingest_episodes_maps_session_and_fact_key_from_provenance(tmp_path):
    facade, storage = build_facade(tmp_path / "bench.db", retrieval_available=False)
    result = ingest_episodes(
        facade,
        [
            _ep("a", "France", session="s1", fact_key="france-capital"),
            _ep("b", "Lyon", session="s2", fact_key="france-rhone"),
        ],
    )
    assert set(result.ep_id_to_session.values()) == {"s1", "s2"}
    assert set(result.fact_key_to_ep_id) == {"france-capital", "france-rhone"}
    for ep in result.stored:
        assert result.ep_id_to_session[ep.id]  # every stored ep has a session
    storage.close()


def test_ingest_episodes_fact_key_map_is_first_wins(tmp_path):
    """SUT parity: the SUT captures the FIRST version per fact_key (setdefault);
    decay's corpus keys are unique per old/new version, so first-wins is the
    unified policy."""
    facade, storage = build_facade(tmp_path / "bench.db", retrieval_available=False)
    result = ingest_episodes(
        facade,
        [
            _ep("old", "France", fact_key="france-capital"),
            _ep("new", "Lyon", fact_key="france-capital"),
        ],
    )
    stored_ids = {ep.title for ep in result.stored}  # titles France / Lyon
    assert stored_ids == {"France", "Lyon"}  # both stored (different bodies)
    # first-wins: the OLD episode's stored id is the registered one
    assert result.fact_key_to_ep_id["france-capital"] == result.id_map["old"]
    storage.close()


def test_ingest_episodes_per_episode_now(tmp_path):
    """``now_of`` forces each remember's created_at (decay's age-spread
    contract: the bias reads the fixture's own timestamps, not the clock)."""
    t0 = datetime(2026, 3, 1, tzinfo=UTC)
    t1 = t0 + timedelta(days=40)
    facade, storage = build_facade(tmp_path / "bench.db", retrieval_available=False)
    episodes = [
        _ep("old", "France", created=t0),
        _ep("new", "Lyon", created=t1),
    ]
    result = ingest_episodes(facade, episodes, now_of=lambda ep: ep.created_at)
    details = facade.recall_full([ep.id for ep in result.stored])
    created_by_title = {d.episode.title: d.episode.created_at for d in details}
    assert created_by_title["France"] == t0
    assert created_by_title["Lyon"] == t1
    storage.close()


def test_ingest_episodes_result_is_frozen():
    from dataclasses import FrozenInstanceError

    import pytest

    result = EpisodeIngestResult(stored=[], id_map={}, ep_id_to_session={}, fact_key_to_ep_id={})
    with pytest.raises(FrozenInstanceError):
        result.stored = []  # type: ignore[misc]


def test_session_to_ep_ids_inverse_groups_in_ep_order():
    """One ep_id→session entry per stored episode; the inverse groups by session
    preserving the ep insertion order inside each session (the three consumers'
    byte-identical setdefault reduction)."""
    inverse = session_to_ep_ids({"e1": "s1", "e2": "s1", "e3": "s2", "e4": "s1"})
    assert inverse == {"s1": ["e1", "e2", "e4"], "s2": ["e3"]}