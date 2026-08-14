"""SqliteEpisodeRepository load-bearing tests.

Guards: structural conformance to the EpisodeRepository Protocol, append +
episode_index propagation, set_invalid_at idempotency with NotFound vs
InvalidationConflictError disambiguation, the bi-temporal PIT axes (never mixed,
NULL-safe), chain_from transitive closure over supersedes, atomic reentrant
rollback, and the fact_id bridge equality (fact_id stored == requested).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from seahorse.contracts.engine import (
    EpisodeRepository,
    InvalidationConflictError,
    NotFound,
)
from seahorse.contracts.episode import Episode
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_episode_repo import SqliteEpisodeRepository


@pytest.fixture()
def repo(tmp_path) -> SqliteEpisodeRepository:
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=4, extensions=("vec0",))
    mgr.open()
    apply_migrations(mgr.writer)
    r = SqliteEpisodeRepository(mgr)
    yield r
    mgr.close()


def _episode(
    ep_id: str = "e1",
    *,
    subject: str | None = "Sergio",
    fact_id: str | None = "fact-1",
    valid_at: datetime | None = None,
    invalid_at: datetime | None = None,
    expired_at: datetime | None = None,
    supersedes: str | None = None,
    supersedes_reason: str | None = None,
    body: str = "body",
    title: str | None = "Title",
    summary: str | None = "Summary",
    cognitive_type: str | None = "fact",
    source_type: str | None = "agent",
    created_at: datetime | None = None,
    schema_version: str = "3.1",
) -> Episode:
    return Episode(
        id=ep_id,
        created_at=created_at or datetime(2026, 1, 1, tzinfo=UTC),
        schema_version=schema_version,
        provenance={"agent": "test"},
        body=body,
        subject=subject,
        fact_id=fact_id,
        valid_at=valid_at,
        invalid_at=invalid_at,
        expired_at=expired_at,
        supersedes=supersedes,
        supersedes_reason=supersedes_reason,
        cognitive_type=cognitive_type,
        source_type=source_type,
        title=title,
        summary=summary,
    )


# --- structural conformance ------------------------------------------------


def test_structurally_satisfies_episode_repository_protocol(
    repo: SqliteEpisodeRepository,
) -> None:
    assert isinstance(repo, EpisodeRepository)


def test_has_no_forbidden_methods(repo: SqliteEpisodeRepository) -> None:
    for forbidden in ("delete", "update_body", "update_valid_at"):
        assert not hasattr(repo, forbidden)


# --- append + episode_index propagation -----------------------------------


def test_append_persists_episode_and_propagates_index(
    repo: SqliteEpisodeRepository,
) -> None:
    ep = _episode(title="Hello", summary="A summary")
    repo.append(ep)
    fetched = repo.get("e1")
    assert fetched is not None
    assert fetched.body == "body"
    assert fetched.subject == "Sergio"
    assert fetched.fact_id == "fact-1"
    # title/summary propagated to episode_index and hydrated via the LEFT JOIN.
    assert fetched.title == "Hello"
    assert fetched.summary == "A summary"


def test_append_two_vigente_same_fact_id_raises_integrity(
    repo: SqliteEpisodeRepository,
) -> None:
    repo.append(_episode("e1", fact_id="fact-1"))
    with pytest.raises(sqlite3.IntegrityError):  # uq_one_active_per_subject partial unique
        repo.append(_episode("e2", fact_id="fact-1"))


def test_append_after_invalidation_allowed(repo: SqliteEpisodeRepository) -> None:
    repo.append(_episode("e1", fact_id="fact-1"))
    repo.set_invalid_at("e1", datetime(2026, 1, 5, tzinfo=UTC))
    # a new currently valid row for the same fact_id is now legitimate (supersession)
    repo.append(_episode("e2", fact_id="fact-1", supersedes="e1"))
    assert repo.get("e2") is not None


# --- set_invalid_at idempotency --------------------------------------------


def test_set_invalid_at_marks_row_invalid(repo: SqliteEpisodeRepository) -> None:
    repo.append(_episode("e1"))
    repo.set_invalid_at("e1", datetime(2026, 1, 5, tzinfo=UTC))
    fetched = repo.get("e1")
    assert fetched is not None
    assert fetched.invalid_at == datetime(2026, 1, 5, tzinfo=UTC)


def test_set_invalid_at_not_found_for_missing_ep(repo: SqliteEpisodeRepository) -> None:
    with pytest.raises(NotFound):
        repo.set_invalid_at("nope", datetime(2026, 1, 5, tzinfo=UTC))


def test_set_invalid_at_conflict_on_already_invalidated(
    repo: SqliteEpisodeRepository,
) -> None:
    repo.append(_episode("e1"))
    repo.set_invalid_at("e1", datetime(2026, 1, 5, tzinfo=UTC))
    # second invalidation -> InvalidationConflictError, NOT NotFound
    with pytest.raises(InvalidationConflictError):
        repo.set_invalid_at("e1", datetime(2026, 1, 6, tzinfo=UTC))


# --- bi-temporal PIT axes (never mixed, NULL-safe) --------------------------


def _seed_timeline(repo: SqliteEpisodeRepository) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    # e1: valid [Jan 1 .. Jan 10], created Jan 1
    repo.append(
        _episode("e1", fact_id="f1", valid_at=t0, invalid_at=datetime(2026, 1, 10, tzinfo=UTC))
    )
    # e2: valid_at NULL (PENDING_INGEST), created Jan 2 (discrepant axes)
    repo.append(
        _episode(
            "e2",
            fact_id="f2",
            valid_at=None,
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
    )


def test_state_at_includes_from_forever_excludes_real_pending(
    repo: SqliteEpisodeRepository,
) -> None:
    # valid_at IS NULL = "from forever" — valid at
    # ANY t, so state_at INCLUDES it (mirrors get_vigente / is_valid_at, which
    # already include NULL). Real PENDING is valid_at in the FUTURE (not yet valid
    # at t), which state_at EXCLUDES. The previous predicate excluded NULL,
    # mis-treating "from forever" as PENDING.
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    repo.append(_episode("e_active", fact_id="fa", valid_at=t0))
    repo.append(_episode("e_forever", fact_id="ff", valid_at=None))
    repo.append(_episode("e_pending", fact_id="fp", valid_at=datetime(2026, 1, 20, tzinfo=UTC)))
    at = datetime(2026, 1, 5, tzinfo=UTC)
    ids = {e.id for e in repo.query_state_at(at)}
    assert ids == {"e_active", "e_forever"}  # e_pending (future valid_at) excluded


def test_known_at_includes_by_creation_regardless_of_valid_at(
    repo: SqliteEpisodeRepository,
) -> None:
    # known_at is the transaction_time axis (created_at/expired_at) —
    # valid_at is irrelevant (the axes are independent). Both "from forever"
    # (valid_at NULL) and real PENDING (valid_at future) are known_at-included by
    # creation time. Distinct fact_ids so all three can be currently valid simultaneously.
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    repo.append(_episode("e_active", fact_id="fa", valid_at=t0, created_at=t0))
    repo.append(_episode("e_forever", fact_id="ff", valid_at=None, created_at=t0))
    repo.append(
        _episode(
            "e_pending",
            fact_id="fp",
            valid_at=datetime(2026, 1, 20, tzinfo=UTC),
            created_at=t0,
        )
    )
    at = datetime(2026, 1, 3, tzinfo=UTC)
    state_ids = {e.id for e in repo.query_state_at(at)}
    known_ids = {e.id for e in repo.query_known_at(at)}
    assert state_ids == {"e_active", "e_forever"}  # e_pending excluded from state_at
    assert known_ids == {"e_active", "e_forever", "e_pending"}  # all created <= Jan 3


def test_state_at_respects_invalidation_window(repo: SqliteEpisodeRepository) -> None:
    # Use a concrete valid_at window — valid_at=None is "from forever"
    # (valid at ANY t) and would not demonstrate the window edges.
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    repo.append(
        _episode("e1", fact_id="f1", valid_at=t0, invalid_at=datetime(2026, 1, 10, tzinfo=UTC))
    )
    # before valid_at -> not in state_at
    assert {e.id for e in repo.query_state_at(datetime(2025, 12, 31, tzinfo=UTC))} == set()
    # within [valid_at, invalid_at) -> in state_at
    assert {e.id for e in repo.query_state_at(datetime(2026, 1, 5, tzinfo=UTC))} == {"e1"}
    # after invalid_at -> not in state_at
    assert {e.id for e in repo.query_state_at(datetime(2026, 1, 11, tzinfo=UTC))} == set()


def test_query_vigent_returns_only_vigente(repo: SqliteEpisodeRepository) -> None:
    _seed_timeline(repo)
    ids = {e.id for e in repo.query_vigent()}
    assert ids == {"e2"}  # e1 has invalid_at set, e2 is currently valid (invalid_at NULL)


def test_query_vigent_subject_filter(repo: SqliteEpisodeRepository) -> None:
    repo.append(_episode("e1", subject="A", fact_id="f1"))
    repo.append(_episode("e2", subject="B", fact_id="f2"))
    assert {e.id for e in repo.query_vigent(subject="A")} == {"e1"}


# --- chain_from transitive closure -----------------------------------------


def test_chain_from_returns_full_lineage(repo: SqliteEpisodeRepository) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    repo.append(_episode("e1", fact_id="f1", created_at=base))
    repo.set_invalid_at("e1", base + timedelta(days=1))
    repo.append(_episode("e2", fact_id="f1", supersedes="e1", created_at=base + timedelta(days=2)))
    repo.set_invalid_at("e2", base + timedelta(days=3))
    repo.append(_episode("e3", fact_id="f1", supersedes="e2", created_at=base + timedelta(days=4)))
    chain = repo.chain_from("e3")
    assert {e.id for e in chain} == {"e1", "e2", "e3"}
    # sorted by created_at
    assert [e.id for e in chain] == ["e1", "e2", "e3"]


def test_chain_from_cycle_does_not_loop(repo: SqliteEpisodeRepository) -> None:
    # defensive: a self-supersedes row must not infinite-loop (guarded by `seen`)
    repo.append(_episode("e1", fact_id="f1", supersedes="e1"))
    chain = repo.chain_from("e1")
    assert [e.id for e in chain] == ["e1"]


# --- atomic reentrant rollback ---------------------------------------------


def test_atomic_rolls_back_on_exception(repo: SqliteEpisodeRepository) -> None:
    class _Boom(Exception):
        pass

    with pytest.raises(_Boom), repo.atomic():
        repo.append(_episode("e1"))
        raise _Boom
    assert repo.get("e1") is None


def test_atomic_reentrant_single_tx_for_improve_pattern(
    repo: SqliteEpisodeRepository,
) -> None:
    # improve = invalidate(old) + append(new) inside ONE atomic. The partial
    # unique on fact_id WHERE invalid_at IS NULL forces this order: the successor
    # cannot coexist with a still-currently-valid predecessor.
    repo.append(_episode("e1", fact_id="f1"))
    with repo.atomic():
        repo.set_invalid_at("e1", datetime(2026, 1, 5, tzinfo=UTC))
        repo.append(_episode("e2", fact_id="f1", supersedes="e1"))
    assert repo.get("e2") is not None
    assert repo.get("e1").invalid_at == datetime(2026, 1, 5, tzinfo=UTC)


# --- supersedes_reason round-trip (migration 009) --------------------------


def test_supersedes_reason_round_trip(repo: SqliteEpisodeRepository) -> None:
    # The portable supersedes_reason lands in storage via migration 009
    # and round-trips through append → get. The successor carries the taxonomy of
    # the replacement; the predecessor only receives invalid_at.
    repo.append(_episode("e0", fact_id="f0"))
    repo.set_invalid_at("e0", datetime(2026, 1, 5, tzinfo=UTC))
    repo.append(
        _episode("e1", fact_id="f0", supersedes="e0", supersedes_reason="correction")
    )
    fetched = repo.get("e1")
    assert fetched is not None
    assert fetched.supersedes_reason == "correction"


def test_supersedes_reason_none_round_trips(repo: SqliteEpisodeRepository) -> None:
    # A note with no supersedes_reason persists NULL (not a sentinel string).
    repo.append(_episode("e1", fact_id="f1"))
    fetched = repo.get("e1")
    assert fetched is not None
    assert fetched.supersedes_reason is None


def test_append_still_raises_on_duplicate_vigent_fact_id_after_009(
    repo: SqliteEpisodeRepository,
) -> None:
    # Regression: the new column must not break the partial unique index.
    repo.append(_episode("e1", fact_id="fact-1"))
    with pytest.raises(sqlite3.IntegrityError):
        repo.append(_episode("e2", fact_id="fact-1"))


# --- fact_id bridge equality ---------------------------------------


def test_fact_id_stored_equals_requested(repo: SqliteEpisodeRepository) -> None:
    repo.append(_episode("e1", fact_id="fact-abc"))
    vigent = repo.find_vigent_by_fact_id("fact-abc")
    assert vigent is not None
    assert vigent.fact_id == "fact-abc"  # bridge equality, by construction


def test_find_vigent_by_fact_id_exclude(repo: SqliteEpisodeRepository) -> None:
    repo.append(_episode("e1", fact_id="f1"))
    assert repo.find_vigent_by_fact_id("f1", exclude="e1") is None
    assert repo.find_vigent_by_fact_id("f1", exclude="e2") is not None
