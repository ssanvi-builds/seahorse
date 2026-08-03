"""SqliteEpisodeIndexRepository load-bearing tests (Phase 6 — the critical one).

Guards the 7 SO-1 accessors + bfs_neighbors_state_at (SO-8b): PIT axes never
mixed (state_at filters valid_at/invalid_at, known_at filters created_at/expired_at,
NULL-safe), bridge equality on find_vigent_row_by_fact_id, HopsCapExceeded at
hops > 2, NotImplementedError for include_tags_soft, and cycle-safety in BFS.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seahorse.contracts.index import MAX_HOPS_MVP1, HopsCapExceeded
from seahorse.contracts.persistence import EpisodeIndexRepository
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_episode_index import SqliteEpisodeIndexRepository


@pytest.fixture()
def index(tmp_path) -> SqliteEpisodeIndexRepository:
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=4, extensions=("vec0",))
    mgr.open()
    apply_migrations(mgr.writer)
    repo = SqliteEpisodeIndexRepository(mgr)
    yield repo
    mgr.close()


def _row(
    mgr: ConnectionManager,
    ep_id: str,
    *,
    fact_id: str = "f1",
    subject: str = "S",
    valid_at: datetime | None = None,
    invalid_at: datetime | None = None,
    created_at: datetime | None = None,
    expired_at: datetime | None = None,
    supersedes: str | None = None,
    cognitive_type: str = "fact",
    source_type: str = "agent",
    schema_version: str = "3.1",
    title: str | None = None,
    summary: str | None = None,
    skip_extraction: int = 0,
) -> None:
    """Insert a raw episode_index row for deterministic test data."""
    mgr.writer.execute(
        "INSERT INTO episode_index (ep_id, subject, fact_id, valid_at, invalid_at, "
        "created_at, expired_at, supersedes, cognitive_type, source_type, schema_version, "
        "skip_extraction, title, summary) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            ep_id,
            subject,
            fact_id,
            valid_at.isoformat() if valid_at else None,
            invalid_at.isoformat() if invalid_at else None,
            (created_at or datetime(2026, 1, 1, tzinfo=UTC)).isoformat(),
            expired_at.isoformat() if expired_at else None,
            supersedes,
            cognitive_type,
            source_type,
            schema_version,
            skip_extraction,
            title,
            summary,
        ),
    )
    # the caller wraps this in `with mgr.atomic():` — no explicit COMMIT here.


# --- structural conformance ------------------------------------------------


def test_structurally_satisfies_protocol(index: SqliteEpisodeIndexRepository) -> None:
    assert isinstance(index, EpisodeIndexRepository)
    assert not hasattr(index, "atomic")  # SO-7a.6


# --- get_rows ---------------------------------------------------------------


def test_get_rows_returns_index_rows_without_body(
    index: SqliteEpisodeIndexRepository,
) -> None:
    mgr = index._cm  # noqa: SLF001 — seed via the manager's writer
    with mgr.atomic():
        _row(mgr, "e1", title="T", summary="Sum")
        _row(mgr, "e2", fact_id="f2")
    rows = index.get_rows(["e1", "e2", "missing"])
    ids = {r.ep_id for r in rows}
    assert ids == {"e1", "e2"}
    e1 = next(r for r in rows if r.ep_id == "e1")
    assert e1.title == "T"
    assert e1.summary == "Sum"
    assert not hasattr(e1, "body") and not hasattr(e1, "body_md")


def test_get_rows_empty_input(index: SqliteEpisodeIndexRepository) -> None:
    assert index.get_rows([]) == []


# --- PIT axes never mixed, NULL-safe ---------------------------------------


def test_pit_axes_never_mixed_on_discrepant_row(
    index: SqliteEpisodeIndexRepository,
) -> None:
    # e1: valid_at Jan 1 (state_at window), created_at Jan 5 (discrepant)
    mgr = index._cm  # noqa: SLF001
    with mgr.atomic():
        _row(
            mgr,
            "e1",
            valid_at=datetime(2026, 1, 1, tzinfo=UTC),
            created_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
    at = datetime(2026, 1, 3, tzinfo=UTC)
    # state_at at Jan 3: e1 valid_at=Jan1 <= Jan3, invalid_at NULL -> INCLUDED
    assert {r.ep_id for r in index.get_rows_state_at(["e1"], at)} == {"e1"}
    # known_at at Jan 3: e1 created_at=Jan5 > Jan3 -> EXCLUDED (axes differ)
    assert index.get_rows_known_at(["e1"], at) == []


def test_state_at_excludes_real_pending_and_includes_from_forever(
    index: SqliteEpisodeIndexRepository,
) -> None:
    # CC-2 (C8.6): ``valid_at IS NULL`` means "from forever" (f5-02 §2 line 85) —
    # valid at ANY ``t``, so a ``state_at(t)`` PIT query MUST include it. The
    # previous predicate (``valid_at IS NOT NULL AND valid_at <= ?``) excluded
    # NULL, mis-treating "from forever" as PENDING. Real PENDING is
    # ``valid_at`` in the FUTURE (not yet valid at ``t``), which IS excluded.
    mgr = index._cm  # noqa: SLF001
    with mgr.atomic():
        _row(mgr, "e_forever", fact_id="ff", valid_at=None)  # "from forever"
        # future valid_at = real PENDING (not yet valid at Jan 3)
        _row(mgr, "e_pending", fact_id="fp", valid_at=datetime(2026, 1, 10, tzinfo=UTC))
    at = datetime(2026, 1, 3, tzinfo=UTC)
    # state_at at Jan 3: e_forever survives (valid_at NULL = valid anytime);
    # e_pending is excluded (valid_at Jan 10 > Jan 3, not yet valid).
    assert {r.ep_id for r in index.get_rows_state_at(["e_forever", "e_pending"], at)} == {
        "e_forever"
    }
    # known_at includes both by creation time (axes are independent — ADR-03).
    assert {r.ep_id for r in index.get_rows_known_at(["e_forever", "e_pending"], at)} == {
        "e_forever",
        "e_pending",
    }


def test_state_at_respects_invalidation_window(
    index: SqliteEpisodeIndexRepository,
) -> None:
    mgr = index._cm  # noqa: SLF001
    with mgr.atomic():
        _row(
            mgr,
            "e1",
            valid_at=datetime(2026, 1, 1, tzinfo=UTC),
            invalid_at=datetime(2026, 1, 10, tzinfo=UTC),
        )
    assert index.get_rows_state_at(["e1"], datetime(2025, 12, 31, tzinfo=UTC)) == []
    assert {r.ep_id for r in index.get_rows_state_at(["e1"], datetime(2026, 1, 5, tzinfo=UTC))} == {
        "e1"
    }
    assert index.get_rows_state_at(["e1"], datetime(2026, 1, 11, tzinfo=UTC)) == []


# --- find_vigent_row_by_fact_id (bridge equality) --------------------------


def test_find_vigent_row_bridge_equality(index: SqliteEpisodeIndexRepository) -> None:
    mgr = index._cm  # noqa: SLF001
    with mgr.atomic():
        _row(mgr, "e1", fact_id="fact-abc")
    vigent = index.find_vigent_row_by_fact_id("fact-abc")
    assert vigent is not None
    assert vigent.fact_id == "fact-abc"  # SO-8c bridge equality, by construction


def test_find_vigent_row_excludes_invalidated(index: SqliteEpisodeIndexRepository) -> None:
    mgr = index._cm  # noqa: SLF001
    with mgr.atomic():
        _row(mgr, "e1", fact_id="f1", invalid_at=datetime(2026, 1, 5, tzinfo=UTC))
    assert index.find_vigent_row_by_fact_id("f1") is None


def test_find_vigent_row_exclude_param(index: SqliteEpisodeIndexRepository) -> None:
    mgr = index._cm  # noqa: SLF001
    with mgr.atomic():
        _row(mgr, "e1", fact_id="f1")
    assert index.find_vigent_row_by_fact_id("f1", exclude="e1") is None
    assert index.find_vigent_row_by_fact_id("f1", exclude="other") is not None


# --- chain_rows_from transitive closure -------------------------------------


def test_chain_rows_from_full_lineage(index: SqliteEpisodeIndexRepository) -> None:
    mgr = index._cm  # noqa: SLF001
    base = datetime(2026, 1, 1, tzinfo=UTC)
    # a real supersession chain: e1 and e2 are invalidated, only e3 is vigente
    # (the I11 partial unique forbids two vigente rows with the same fact_id).
    with mgr.atomic():
        _row(
            mgr,
            "e1",
            fact_id="f1",
            created_at=base,
            invalid_at=base + timedelta(days=1),
        )
        _row(
            mgr,
            "e2",
            fact_id="f1",
            supersedes="e1",
            created_at=base + timedelta(days=1),
            invalid_at=base + timedelta(days=2),
        )
        _row(
            mgr,
            "e3",
            fact_id="f1",
            supersedes="e2",
            created_at=base + timedelta(days=2),
        )
    chain = index.chain_rows_from("e3")
    assert [r.ep_id for r in chain] == ["e1", "e2", "e3"]


# --- range_rows_* (MVP-1 axes, revisable) -----------------------------------


@pytest.mark.mvp1_axis
def test_range_rows_state_at_window(index: SqliteEpisodeIndexRepository) -> None:
    mgr = index._cm  # noqa: SLF001
    # distinct fact_ids so all three can be vigente simultaneously (I11).
    with mgr.atomic():
        _row(mgr, "e1", fact_id="f1", valid_at=datetime(2026, 1, 1, tzinfo=UTC))
        _row(mgr, "e2", fact_id="f2", valid_at=datetime(2026, 1, 5, tzinfo=UTC))
        _row(mgr, "e3", fact_id="f3", valid_at=datetime(2026, 1, 10, tzinfo=UTC))
    rows = index.range_rows_state_at(
        datetime(2026, 1, 3, tzinfo=UTC), datetime(2026, 1, 7, tzinfo=UTC)
    )
    assert {r.ep_id for r in rows} == {"e2"}


@pytest.mark.mvp1_axis
def test_range_rows_known_at_subject_filter(
    index: SqliteEpisodeIndexRepository,
) -> None:
    mgr = index._cm  # noqa: SLF001
    # distinct fact_ids so both can be vigente.
    with mgr.atomic():
        _row(mgr, "e1", fact_id="fa", subject="A", created_at=datetime(2026, 1, 1, tzinfo=UTC))
        _row(mgr, "e2", fact_id="fb", subject="B", created_at=datetime(2026, 1, 2, tzinfo=UTC))
    rows = index.range_rows_known_at(
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 3, tzinfo=UTC),
        subject="A",
    )
    assert {r.ep_id for r in rows} == {"e1"}


# --- bfs_neighbors_state_at (SO-8b) -----------------------------------------


def _seed_chain(index: SqliteEpisodeIndexRepository) -> None:
    mgr = index._cm  # noqa: SLF001
    base = datetime(2026, 1, 1, tzinfo=UTC)
    # a supersession chain with only e3 vigente (e1, e2 invalidated). The BFS
    # traversal explores the supersedes graph; the PIT filter selects returned rows.
    with mgr.atomic():
        _row(
            mgr,
            "e1",
            fact_id="f1",
            valid_at=base,
            invalid_at=base + timedelta(days=1),
            created_at=base,
        )
        _row(
            mgr,
            "e2",
            fact_id="f1",
            supersedes="e1",
            valid_at=base + timedelta(days=1),
            invalid_at=base + timedelta(days=2),
            created_at=base + timedelta(days=1),
        )
        _row(
            mgr,
            "e3",
            fact_id="f1",
            supersedes="e2",
            valid_at=base + timedelta(days=2),
            created_at=base + timedelta(days=2),
        )


def test_bfs_state_at_returns_neighbors_within_hops(
    index: SqliteEpisodeIndexRepository,
) -> None:
    _seed_chain(index)
    # state_at at Jan 2 12:00: e1 invalid_at=Jan2 (00:00) < pit -> excluded;
    # e2 valid [Jan2..Jan3] -> included; e3 valid_at=Jan3 > pit -> excluded.
    pit = datetime(2026, 1, 2, 12, tzinfo=UTC)
    rows = index.bfs_neighbors_state_at(
        "e3", pit, pit_kind="state_at", hops=2, include_tags_soft=False
    )
    assert {r.ep_id for r in rows} == {"e2"}


def test_bfs_traverses_two_hops_when_pit_includes_all(
    index: SqliteEpisodeIndexRepository,
) -> None:
    # Guards the multi-hop traversal: a 1-hop-only impl (that still validates the
    # hops cap) would return {e1, e2} and miss e3. Distinct fact_ids so all three
    # are vigente (I11); known_at PIT after every created_at includes them all.
    mgr = index._cm  # noqa: SLF001
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with mgr.atomic():
        _row(mgr, "e1", fact_id="f1", created_at=base)
        _row(mgr, "e2", fact_id="f2", supersedes="e1", created_at=base + timedelta(days=1))
        _row(mgr, "e3", fact_id="f3", supersedes="e2", created_at=base + timedelta(days=2))
    pit = base + timedelta(days=3)
    one_hop = index.bfs_neighbors_state_at(
        "e1", pit, pit_kind="known_at", hops=1, include_tags_soft=False
    )
    two_hop = index.bfs_neighbors_state_at(
        "e1", pit, pit_kind="known_at", hops=2, include_tags_soft=False
    )
    assert {r.ep_id for r in one_hop} == {"e1", "e2"}
    assert {r.ep_id for r in two_hop} == {"e1", "e2", "e3"}


def test_bfs_hops_cap_exceeded(index: SqliteEpisodeIndexRepository) -> None:
    _seed_chain(index)
    with pytest.raises(HopsCapExceeded) as exc:
        index.bfs_neighbors_state_at(
            "e3",
            datetime(2026, 1, 2, tzinfo=UTC),
            pit_kind="state_at",
            hops=MAX_HOPS_MVP1 + 1,
            include_tags_soft=False,
        )
    assert exc.value.hops == MAX_HOPS_MVP1 + 1
    assert exc.value.cap == MAX_HOPS_MVP1


def test_bfs_include_tags_soft_raises_not_implemented(
    index: SqliteEpisodeIndexRepository,
) -> None:
    with pytest.raises(NotImplementedError):
        index.bfs_neighbors_state_at(
            "e3",
            datetime(2026, 1, 2, tzinfo=UTC),
            pit_kind="state_at",
            hops=1,
            include_tags_soft=True,
        )


def test_bfs_known_at_filters_by_created_at(index: SqliteEpisodeIndexRepository) -> None:
    # known_at at Jan 1: only e1 (created Jan1) is known; e2/e3 created later.
    _seed_chain(index)
    pit = datetime(2026, 1, 1, 12, tzinfo=UTC)
    rows = index.bfs_neighbors_state_at(
        "e1", pit, pit_kind="known_at", hops=2, include_tags_soft=False
    )
    assert {r.ep_id for r in rows} == {"e1"}


def test_bfs_cycle_does_not_infinite_loop(index: SqliteEpisodeIndexRepository) -> None:
    mgr = index._cm  # noqa: SLF001
    with mgr.atomic():
        _row(mgr, "e1", fact_id="f1", supersedes="e1")  # self-loop
    rows = index.bfs_neighbors_state_at(
        "e1",
        datetime(2026, 1, 1, tzinfo=UTC),
        pit_kind="known_at",
        hops=2,
        include_tags_soft=False,
    )
    assert {r.ep_id for r in rows} == {"e1"}
