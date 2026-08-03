"""Storage composition root + E2E smoke (Phase 8).

Verifies the single shared ``atomic()`` (SO-7a.6) wraps multi-repo writes, that
no repository but ``episodes`` exposes ``atomic()``, and a full happy-path +
stub smoke across the whole storage layer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seahorse.contracts.engine import AuditEvent, InvalidationConflictError, NotFound
from seahorse.contracts.episode import Episode
from seahorse.persistence.storage import Storage


@pytest.fixture()
def storage(tmp_path) -> Storage:
    s = Storage(tmp_path / "seahorse.db", pool_size=4)
    yield s
    s.close()


def _episode(
    ep_id: str = "e1",
    *,
    fact_id: str = "fact-1",
    subject: str = "S",
    valid_at: datetime | None = None,
    supersedes: str | None = None,
    title: str | None = None,
    summary: str | None = None,
) -> Episode:
    return Episode(
        id=ep_id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version="3.1",
        provenance={"src": "test"},
        body="# body",
        subject=subject,
        fact_id=fact_id,
        valid_at=valid_at,
        supersedes=supersedes,
        cognitive_type="fact",
        source_type="agent",
        title=title,
        summary=summary,
    )


# --- composition root shape --------------------------------------------------


def test_storage_exposes_all_repos(storage: Storage) -> None:
    for attr in (
        "episodes",
        "episode_index",
        "audit",
        "sidecar",
        "embeddings_cache",
        "reindex_jobs",
        "vector",
        "fts",
    ):
        assert getattr(storage, attr) is not None


def test_only_episodes_repo_has_atomic(storage: Storage) -> None:
    # SO-7a.6: the single shared atomic lives on Storage (and the episodes repo,
    # which delegates). Every other repo MUST NOT expose its own atomic().
    repos_without_atomic = (
        storage.episode_index,
        storage.audit,
        storage.sidecar,
        storage.embeddings_cache,
        storage.reindex_jobs,
        storage.vector,
        storage.fts,
    )
    for repo in repos_without_atomic:
        assert not hasattr(repo, "atomic"), f"{type(repo).__name__} has its own atomic()"


def test_storage_atomic_delegates_to_single_shared_atomic(storage: Storage) -> None:
    # nesting Storage.atomic inside itself must not deadlock and must not issue
    # a second BEGIN (the ConnectionManager tracks depth).
    with storage.atomic():
        assert storage._cm.depth == 1  # noqa: SLF001
        with storage.atomic():
            assert storage._cm.depth == 2  # noqa: SLF001
        assert storage._cm.depth == 1
    assert storage._cm.depth == 0  # noqa: SLF001


# --- single shared atomic across repos --------------------------------------


def test_single_atomic_persists_multi_repo_write(storage: Storage) -> None:
    ep = _episode("e1", valid_at=datetime(2026, 1, 1, tzinfo=UTC))
    ev = AuditEvent(
        primitive="apply",
        target_id="e1",
        transaction_time=datetime(2026, 1, 1, tzinfo=UTC),
        result="added",
        agent_id="a",
        session_id="s",
    )
    with storage.atomic():
        storage.episodes.append(ep)
        storage.audit.append(ev)
        storage.sidecar.put_path("e1", "notes/e1.md", 100, 42)
    # all three persisted outside the atomic block.
    assert storage.episodes.get("e1") is not None
    assert {e.target_id for e in storage.audit.query(target_id="e1")} == {"e1"}
    assert storage.sidecar.get_path("e1") == ("notes/e1.md", 100, 42)


def test_single_atomic_rolls_back_all_repos_on_exception(storage: Storage) -> None:
    ep = _episode("e9", valid_at=datetime(2026, 1, 1, tzinfo=UTC))

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom), storage.atomic():
        storage.episodes.append(ep)
        storage.sidecar.put_path("e9", "notes/e9.md", 1, 1)
        raise _Boom
    # nothing committed.
    assert storage.episodes.get("e9") is None
    assert storage.sidecar.get_path("e9") is None


# --- E2E smoke: full happy path + stubs -------------------------------------


def test_e2e_smoke_full_flow(storage: Storage) -> None:
    # 1. create + migrate already happened in __init__; verify schema_version.
    assert (
        storage._cm.writer.execute(  # noqa: SLF001
            "SELECT version FROM schema_version"
        ).fetchall()[-1][0]
        == 10
    )

    # 2. append an episode (with a real fact_id + title/summary).
    ep = _episode(
        "e1",
        fact_id="fact-abc",
        valid_at=datetime(2026, 1, 1, tzinfo=UTC),
        title="T",
        summary="Sum",
    )
    storage.episodes.append(ep)

    # 3. index.get_rows returns IndexRowData WITHOUT body, with title/summary.
    rows = storage.episode_index.get_rows(["e1"])
    assert len(rows) == 1
    assert rows[0].title == "T"
    assert rows[0].summary == "Sum"
    assert not hasattr(rows[0], "body") and not hasattr(rows[0], "body_md")

    # 4. find_vigent_by_fact_id bridge equality (SO-8c): the index row's fact_id
    #    matches the stored episode's fact_id.
    vigent_ep = storage.episodes.find_vigent_by_fact_id("fact-abc")
    vigent_row = storage.episode_index.find_vigent_row_by_fact_id("fact-abc")
    assert vigent_ep is not None and vigent_row is not None
    assert vigent_ep.fact_id == vigent_row.fact_id == "fact-abc"
    assert vigent_ep.id == vigent_row.ep_id == "e1"

    # 5. audit an apply.
    storage.audit.append(
        AuditEvent(
            primitive="apply",
            target_id="e1",
            transaction_time=datetime(2026, 1, 1, 12, tzinfo=UTC),
            result="added",
            agent_id="a",
            session_id="s",
        )
    )
    assert len(storage.audit.query(target_id="e1")) == 1

    # 6. set_invalid_at, then idempotency raises InvalidationConflictError.
    now = datetime(2026, 1, 5, tzinfo=UTC)
    storage.episodes.set_invalid_at("e1", now)
    with pytest.raises(InvalidationConflictError):
        storage.episodes.set_invalid_at("e1", now + timedelta(days=1))

    # 7. NotFound for a never-existing episode.
    with pytest.raises(NotFound):
        storage.episodes.set_invalid_at("ghost", now)

    # 8. BFS over the index (e1 alone -> {e1} via known_at, no infinite loop).
    bfs = storage.episode_index.bfs_neighbors_state_at(
        "e1",
        datetime(2026, 1, 3, tzinfo=UTC),
        pit_kind="known_at",
        hops=2,
        include_tags_soft=False,
    )
    assert {r.ep_id for r in bfs} == {"e1"}

    # 9. embeddings_cache round-trip + trim.
    storage.embeddings_cache.batch_insert(
        "model-x", "body", ["h1", "h2"], [b"\x00" * 16, b"\x01" * 16]
    )
    assert storage.embeddings_cache.count() == 2
    assert storage.embeddings_cache.batch_lookup("model-x", "body", ["h1"])["h1"] == (b"\x00" * 16)
    storage.embeddings_cache.trim(1)
    assert storage.embeddings_cache.count() == 1

    # 10. reindex_jobs lifecycle (setters, no transition guards in MVP-0).
    job_id = storage.reindex_jobs.create(model_from="model-x", model_to="model-y", total=100)
    storage.reindex_jobs.pause(job_id)
    storage.reindex_jobs.start(job_id)
    storage.reindex_jobs.finish(job_id)
    assert storage.reindex_jobs.list(status="done")[0].job_id == job_id

    # 11. M1-A.3: vector is a real vec0 repo (empty -> count 0); fts is still
    # a stub until M1-A.4 (raises NotImplementedError).
    assert storage.vector.count() == 0
    with pytest.raises(NotImplementedError):
        storage.fts.count()


# --- idempotent open() -------------------------------------------------------


def test_open_is_idempotent(storage: Storage) -> None:
    # re-applying migrations must not error and must keep schema_version stable.
    storage.open()
    storage.open()
    versions = [
        r[0]
        for r in storage._cm.writer.execute(  # noqa: SLF001
            "SELECT version FROM schema_version ORDER BY version"
        ).fetchall()
    ]
    assert versions == list(range(1, 11))


def test_storage_context_manager(tmp_path) -> None:
    with Storage(tmp_path / "ctx.db") as s:
        s.episodes.append(_episode("e1", valid_at=datetime(2026, 1, 1, tzinfo=UTC)))
    # after the with-block the manager is closed; a fresh Storage sees the row.
    with Storage(tmp_path / "ctx.db") as s2:
        assert s2.episodes.get("e1") is not None


def test_errors_reexport_path_matches_contracts() -> None:
    # f5-06 documents `from seahorse.persistence.errors import ...`; verify it.
    from seahorse.contracts.engine import (
        InvalidationConflictError as I,
    )
    from seahorse.contracts.engine import (
        NotFound as N,
    )
    from seahorse.contracts.index import HopsCapExceeded as H
    from seahorse.persistence.errors import (
        HopsCapExceeded,
        InvalidationConflictError,
        NotFound,
    )

    assert HopsCapExceeded is H
    assert InvalidationConflictError is I
    assert NotFound is N


def test_storage_init_closes_manager_if_migrations_raise(tmp_path, monkeypatch) -> None:
    # If apply_migrations fails, the Storage must close the ConnectionManager so
    # the writer + reader pool do not leak (no caller reference to .close()).
    import seahorse.persistence.storage as storage_mod

    def _boom(_conn: object) -> None:
        raise RuntimeError("migration failure")

    monkeypatch.setattr(storage_mod, "apply_migrations", _boom)
    with pytest.raises(RuntimeError, match="migration failure"):
        storage_mod.Storage(tmp_path / "leak.db")
    # the manager was torn down: a fresh Storage on the same path opens cleanly.
    monkeypatch.undo()
    s = Storage(tmp_path / "leak.db")
    assert s.episodes is not None
    s.close()
