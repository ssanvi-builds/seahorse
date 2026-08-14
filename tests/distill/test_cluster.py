"""Tests for ``seahorse.distill.cluster`` — deterministic clustering.

The clustering key is DISTINCT from the stored subject: the observer's H1
carries a ``[session_tag:prompt_number]`` suffix, so the stored subject is
unique per turn — the N≥3 recurrence trigger would NEVER fire if it clustered on
the stored subject. The key strips the tag suffix, so episodes about the same
topic cluster together. Deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from seahorse.distill.cluster import cluster_episodes, cluster_key

NOW = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)


def _remember(engine, *, body: str, now: datetime) -> str:
    wr = engine.remember(
        body=body,
        by={"source_type": "agent", "agent_id": "a1", "session_id": "sess-1"},
        now=now,
    )
    return wr.ep_id


def _episodes(engine, ep_ids):
    return [engine._repo.get(eid) for eid in ep_ids]  # noqa: SLF001


# ---------------------------------------------------------------------------
# cluster_key — strips the [session_tag:prompt_number] suffix
# ---------------------------------------------------------------------------


def test_cluster_key_strips_session_tag() -> None:
    assert cluster_key("Fix the flaky recall test [sess-1234:1]") == "fix the flaky recall test"


def test_cluster_key_no_suffix_unchanged() -> None:
    assert cluster_key("sergio") == "sergio"


def test_cluster_key_normalizes_case() -> None:
    assert cluster_key("Fix the bug [sess-1:2]") == cluster_key("fix the bug [sess-1:3]")


# ---------------------------------------------------------------------------
# cluster_episodes — recurrence over the clustering key
# ---------------------------------------------------------------------------


def test_cluster_episodes_groups_by_key(engine) -> None:
    eng, repo, audit = engine
    ids = [
        _remember(
            eng, body="# Fix the flaky recall test [sess-1:1]\n\nDetails.", now=NOW
        ),
        _remember(
            eng,
            body="# Fix the flaky recall test [sess-1:2]\n\nMore.",
            now=NOW + timedelta(minutes=1),
        ),
        _remember(
            eng,
            body="# Fix the flaky recall test [sess-1:3]\n\nEven more.",
            now=NOW + timedelta(minutes=2),
        ),
        _remember(
            eng,
            body="# Unrelated topic [sess-1:4]\n\nOther.",
            now=NOW + timedelta(minutes=3),
        ),
    ]
    clusters = cluster_episodes(_episodes(eng, ids))
    assert len(clusters) == 1  # only the N>=3 cluster
    assert clusters[0].key == "fix the flaky recall test"
    assert len(clusters[0].episodes) == 3


def test_cluster_episodes_below_min_size_excluded(engine) -> None:
    eng, repo, audit = engine
    ids = [
        _remember(eng, body="# Topic A [sess-1:1]\n\nOne.", now=NOW),
        _remember(eng, body="# Topic A [sess-1:2]\n\nTwo.", now=NOW + timedelta(minutes=1)),
        _remember(eng, body="# Topic B [sess-1:3]\n\nOne.", now=NOW + timedelta(minutes=2)),
    ]
    clusters = cluster_episodes(_episodes(eng, ids))
    assert clusters == []  # no cluster reaches N>=3


def test_cluster_episodes_representative_is_most_recent(engine) -> None:
    eng, repo, audit = engine
    ids = [
        _remember(eng, body="# Topic [sess-1:1]\n\nOld.", now=NOW),
        _remember(eng, body="# Topic [sess-1:2]\n\nMid.", now=NOW + timedelta(minutes=1)),
        _remember(eng, body="# Topic [sess-1:3]\n\nNew.", now=NOW + timedelta(minutes=2)),
    ]
    clusters = cluster_episodes(_episodes(eng, ids))
    assert clusters[0].representative.id == ids[2]


def test_cluster_episodes_is_deterministic(engine) -> None:
    eng, repo, audit = engine
    ids = [
        _remember(eng, body="# Topic [sess-1:1]\n\nOne.", now=NOW),
        _remember(eng, body="# Topic [sess-1:2]\n\nTwo.", now=NOW + timedelta(minutes=1)),
        _remember(eng, body="# Topic [sess-1:3]\n\nThree.", now=NOW + timedelta(minutes=2)),
    ]
    eps = _episodes(eng, ids)
    a = cluster_episodes(eps)
    b = cluster_episodes(eps)
    assert [c.key for c in a] == [c.key for c in b]
    assert [c.representative.id for c in a] == [c.representative.id for c in b]
