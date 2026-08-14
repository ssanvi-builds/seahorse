"""Tests for ``seahorse.distill.consolidate`` — the consolidate orchestration.

``consolidate(facade)`` reads the current-state set, clusters by subject
recurrence (N≥3), and distills each cluster into a consolidated semantic
episode via the facade. The consolidated body uses the stable clustering key as
its H1 (no ``[session_tag:n]`` suffix). The sources stay current-state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from seahorse.distill.consolidate import consolidate
from seahorse.facade.factory import build_facade
from seahorse.facade.types import RememberPayload

T0 = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)


def _remember(facade, *, body: str, now: datetime) -> None:
    facade.remember(
        RememberPayload(
            body=body,
            by={"source_type": "agent", "agent_id": "a1", "session_id": "sess-1"},
        ),
        now=now,
    )


def _facade(db_path):
    facade, storage = build_facade(db_path)
    return facade, storage


def test_consolidate_no_clusters(tmp_path) -> None:
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        report = consolidate(facade)
        assert report.clusters_found == 0
        assert report.items == []
    finally:
        storage.close()


def test_consolidate_distills_recurrent_cluster(tmp_path) -> None:
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        for i in range(3):
            _remember(
                facade,
                body=f"# Fix the flaky recall test [sess-1:{i + 1}]\n\nAttempt {i + 1}.",
                now=T0 + timedelta(minutes=i),
            )
        report = consolidate(facade)
        assert report.clusters_found == 1
        assert report.items[0].key == "fix the flaky recall test"
        assert report.items[0].source_count == 3
        assert report.items[0].status == "ACTIVE"
        # The consolidated episode is a semantic knowledge note (4 current-state:
        # 3 sources + 1 consolidated).
        eps = facade.get_vigente()
        assert len(eps) == 4
        assert any(e.cognitive_type == "semantic" for e in eps)
    finally:
        storage.close()


def test_consolidate_sources_stay_vigente(tmp_path) -> None:
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        for i in range(3):
            _remember(
                facade,
                body=f"# Topic [sess-1:{i + 1}]\n\nDetail {i + 1}.",
                now=T0 + timedelta(minutes=i),
            )
        consolidate(facade)
        eps = facade.get_vigente()
        # 4 current-state: 3 sources + 1 consolidated (none invalidated).
        assert len(eps) == 4
    finally:
        storage.close()


def test_consolidate_ignores_below_threshold(tmp_path) -> None:
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        for i in range(2):
            _remember(
                facade,
                body=f"# Rare topic [sess-1:{i + 1}]\n\nDetail {i + 1}.",
                now=T0 + timedelta(minutes=i),
            )
        report = consolidate(facade)
        assert report.clusters_found == 0  # N<3 → no distillation
    finally:
        storage.close()


def test_consolidate_is_deterministic(tmp_path) -> None:
    """Two fresh facades with the same data produce the same report."""
    reports = []
    for idx in range(2):
        db_dir = tmp_path / f"db-{idx}"
        db_dir.mkdir(parents=True, exist_ok=True)
        facade, storage = _facade(db_dir / "seahorse.db")
        try:
            for i in range(3):
                _remember(
                    facade,
                    body=f"# Topic [sess-1:{i + 1}]\n\nDetail {i + 1}.",
                    now=T0 + timedelta(minutes=i),
                )
            reports.append(consolidate(facade))
        finally:
            storage.close()
    assert [i.key for i in reports[0].items] == [i.key for i in reports[1].items]
    assert [i.source_count for i in reports[0].items] == [i.source_count for i in reports[1].items]


def test_consolidate_is_idempotent(tmp_path) -> None:
    """A cluster whose key already has a consolidated note is skipped —
    the second run does NOT create a duplicate knowledge note."""
    facade, storage = _facade(tmp_path / "seahorse.db")
    try:
        for i in range(3):
            _remember(
                facade,
                body=f"# Topic [sess-1:{i + 1}]\n\nDetail {i + 1}.",
                now=T0 + timedelta(minutes=i),
            )
        first = consolidate(facade)
        assert first.clusters_found == 1
        second = consolidate(facade)
        assert second.clusters_found == 1  # the cluster still exists
        assert second.items == []  # but nothing new was distilled
        eps = facade.get_vigente()
        consolidated = [e for e in eps if e.cognitive_type == "semantic"]
        assert len(consolidated) == 1  # no duplicate knowledge note
    finally:
        storage.close()
