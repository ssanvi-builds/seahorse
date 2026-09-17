"""``derived_from`` — the structured membership of a consolidated episode.

F3.2: ``distill_episodes`` records the cluster membership as structured
``x-seahorse-derived-from`` data on the consolidated episode's provenance
(the same data the Evidence body block shows, uncapped). Each edge is
``{id, edge_kind}`` with ``edge_kind`` in ``evidence | derived | supersedes``
(frozen at format promotion; unknown values preserved, never rejected).

On a fresh distill every source episode is an ``evidence`` edge. On
supersession (the note is UPDATED via ``engine.improve``) the new note
INHERITS the superseded note's edges (the membership that does not change),
adds the new cluster's episodes as ``evidence``, and closes the lineage with
the superseded note itself as a ``supersedes`` edge — the importer never
re-implements the clusterer to know the complete evidence set.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from seahorse.distill.distill import distill_episodes

NOW = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)


def _remember(engine, *, body: str, now: datetime) -> str:
    wr = engine.remember(
        body=body,
        by={"source_type": "agent", "agent_id": "a1", "session_id": "sess-1"},
        now=now,
    )
    return wr.ep_id


def _episode(engine, ep_id: str):
    return engine._repo.get(ep_id)  # noqa: SLF001


def _cluster(engine, tag: str, start: datetime, count: int = 3) -> list[str]:
    ids = []
    for i in range(count):
        ids.append(
            _remember(
                engine,
                body=f"# {tag} [s:{i}]\n\nTurn {i}.",
                now=start + timedelta(minutes=i),
            )
        )
    return ids


def _edges(*ep_ids: str) -> list[dict[str, str]]:
    return [{"id": ep_id, "edge_kind": "evidence"} for ep_id in ep_ids]


def test_fresh_distill_records_evidence_edges(engine) -> None:
    """Every source episode is an ``evidence`` edge, in source order."""
    eng, _repo, _audit = engine
    ids = _cluster(eng, "fix the flaky recall test", NOW)
    rep = _episode(eng, ids[-1])

    wr = distill_episodes(eng, ids, rep, "# fix\n\nBody.", {}, supersede_ep_id=None)

    ep = _episode(eng, wr.ep_id)
    assert ep.provenance["derived_from"] == [
        {"id": ep_id, "edge_kind": "evidence"} for ep_id in ids
    ]


def test_fresh_distill_edges_survive_storage_round_trip(engine) -> None:
    """The membership lives in provenance (the persisted JSON column), so a
    re-fetch from the DB carries it — the materializer never re-derives it."""
    eng, _repo, _audit = engine
    ids = _cluster(eng, "fix the flaky recall test", NOW)
    rep = _episode(eng, ids[-1])
    wr = distill_episodes(eng, ids, rep, "# fix\n\nBody.", {})

    ep = eng.get(wr.ep_id)  # the DB round-trip path
    assert [e["id"] for e in ep.provenance["derived_from"]] == ids


def test_supersession_inherits_membership_and_closes_lineage(engine) -> None:
    """A superseded note contributes its OWN edges (the membership that does
    not change) plus the new cluster's evidence, and the superseded note
    itself lands as the ``supersedes`` edge — one complete list."""
    eng, _repo, _audit = engine
    first_ids = _cluster(eng, "fix the flaky recall test", NOW)
    rep1 = _episode(eng, first_ids[-1])
    wr1 = distill_episodes(eng, first_ids, rep1, "# fix\n\nv1.", {})

    # A new turn joins the topic; the note is superseded with the new cluster.
    new_id = _remember(
        eng, body="# fix the flaky recall test [s:9]\n\nNew.", now=NOW + timedelta(minutes=5)
    )
    second_ids = [new_id]
    wr2 = distill_episodes(
        eng,
        second_ids,
        _episode(eng, new_id),
        "# fix\n\nBody v2.",
        {},
        supersede_ep_id=wr1.ep_id,
    )

    ep2 = _episode(eng, wr2.ep_id)
    edges = ep2.provenance["derived_from"]
    assert edges[:1] == [{"id": new_id, "edge_kind": "evidence"}]
    # the superseded consolidated note closes the lineage
    assert edges[-1] == {"id": wr1.ep_id, "edge_kind": "supersedes"}
    # the inherited membership survives (the old evidence edges), without
    # re-listing the superseded note as evidence of itself
    inherited = [e for e in edges[1:-1] if e["id"] in first_ids]
    assert inherited == [{"id": ep_id, "edge_kind": "evidence"} for ep_id in first_ids]


def test_supersession_dedupes_shared_members(engine) -> None:
    """An episode that is BOTH inherited evidence and a new cluster member
    lands once, as ``evidence`` (never duplicated by the inheritance)."""
    eng, _repo, _audit = engine
    first_ids = _cluster(eng, "fix the flaky recall test", NOW)
    rep1 = _episode(eng, first_ids[-1])
    wr1 = distill_episodes(eng, first_ids, rep1, "# fix\n\nv1.", {})

    # The superseded note's LAST member is distilled again as the new cluster's
    # representative (a real re-consolidation overlaps memberships).
    wr2 = distill_episodes(
        eng, [first_ids[-1]], rep1, "# fix\n\nv2.", {}, supersede_ep_id=wr1.ep_id
    )

    edges = _episode(eng, wr2.ep_id).provenance["derived_from"]
    id_list = [e["id"] for e in edges]
    assert id_list.count(first_ids[-1]) == 1
    assert edges[0] == {"id": first_ids[-1], "edge_kind": "evidence"}
    assert edges[-1] == {"id": wr1.ep_id, "edge_kind": "supersedes"}


def _rewrite_provenance(repo, ep_id: str, provenance: dict) -> None:
    """Persist a modified provenance dict in place (a legacy-row simulation:
    the ``episodes`` table predates the field, so no API path can un-write
    it). Direct UPDATE on the persisted JSON column."""
    repo._cm.writer.execute(  # noqa: SLF001 — the test owns the DB
        "UPDATE episodes SET provenance = ? WHERE id = ?",
        (json.dumps(provenance), ep_id),
    )


def test_supersession_of_a_legacy_note_has_no_inherited_edges(engine) -> None:
    """A pre-1.4.0 consolidated note carries no ``derived_from``; superseding
    it yields the new evidence plus the lineage edge, nothing invented."""
    eng, repo, _audit = engine
    ids = _cluster(eng, "fix the flaky recall test", NOW)
    rep = _episode(eng, ids[-1])
    wr1 = distill_episodes(eng, ids, rep, "# fix\n\nv1.", {})
    # Simulate legacy: strip the field from the persisted episode.
    legacy_provenance = {
        k: v for k, v in _episode(eng, wr1.ep_id).provenance.items() if k != "derived_from"
    }
    _rewrite_provenance(repo, wr1.ep_id, legacy_provenance)

    new_id = _remember(
        eng, body="# fix the flaky recall test [s:9]\n\nNew.", now=NOW + timedelta(minutes=5)
    )
    wr2 = distill_episodes(
        eng, [new_id], _episode(eng, new_id), "# fix\n\nv2.", {}, supersede_ep_id=wr1.ep_id
    )

    edges = _episode(eng, wr2.ep_id).provenance["derived_from"]
    assert edges == [
        {"id": new_id, "edge_kind": "evidence"},
        {"id": wr1.ep_id, "edge_kind": "supersedes"},
    ]


@pytest.mark.parametrize(
    ("edge", "expected_kind"),
    [("ep-future", "evidence"), ("ep-x", "unknown_new_kind")],
)
def test_unknown_edge_kinds_are_carried_not_rejected(engine, edge, expected_kind) -> None:
    """The enumeration freezes at format promotion; unknown ``edge_kind``
    values are PRESERVED, not rejected (the same policy as the ``x-*``)."""
    eng, repo, _audit = engine
    ids = _cluster(eng, "fix the flaky recall test", NOW)
    rep = _episode(eng, ids[-1])
    wr = distill_episodes(eng, ids, rep, "# fix\n\nBody.", {})
    edges = [{"id": edge, "edge_kind": expected_kind}]
    _rewrite_provenance(
        repo, wr.ep_id, {**_episode(eng, wr.ep_id).provenance, "derived_from": edges}
    )
    assert [e["edge_kind"] for e in eng.get(wr.ep_id).provenance["derived_from"]] == [expected_kind]


def test_supersession_tolerates_malformed_inherited_membership(engine) -> None:
    """A third-party provenance block may carry the minimal string shape (a
    real design option) and unusable entries — tolerated, never a crash: the
    string is promoted to ``evidence``, the unusable are skipped, valid edges
    pass through, the lineage still closes with the ``supersedes`` edge."""
    eng, repo, _audit = engine
    first_ids = _cluster(eng, "fix the flaky recall test", NOW)
    rep1 = _episode(eng, first_ids[-1])
    wr1 = distill_episodes(eng, first_ids, rep1, "# fix\n\nv1.", {})
    _rewrite_provenance(
        repo,
        wr1.ep_id,
        {
            **_episode(eng, wr1.ep_id).provenance,
            "derived_from": [
                "ep-x",
                {"edge_kind": "evidence"},
                {"id": 42},
                7,
                {"id": "ep-ok", "edge_kind": "custom"},
            ],
        },
    )

    new_id = _remember(
        eng, body="# fix the flaky recall test [s:9]\n\nNew.", now=NOW + timedelta(minutes=5)
    )
    wr2 = distill_episodes(
        eng,
        [new_id],
        _episode(eng, new_id),
        "# fix\n\nBody v2.",
        {},
        supersede_ep_id=wr1.ep_id,
    )

    edges = _episode(eng, wr2.ep_id).provenance["derived_from"]
    by_id = {e["id"]: e["edge_kind"] for e in edges}
    # the string edge is promoted, the valid edge passes through, the unusable
    # entries are gone, and the lineage closes with the superseded note
    assert by_id["ep-x"] == "evidence"
    assert by_id["ep-ok"] == "custom"
    assert 42 not in by_id and "7" not in by_id
    assert edges[-1] == {"id": wr1.ep_id, "edge_kind": "supersedes"}
