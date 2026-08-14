"""SqliteReindexJobRepository tests. Setters + list(status). No guards."""

from __future__ import annotations

import pytest

from seahorse.contracts.persistence import ReindexJobRepository
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_reindex_jobs import SqliteReindexJobRepository


@pytest.fixture()
def jobs(tmp_path) -> SqliteReindexJobRepository:
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=4, extensions=("vec0",))
    mgr.open()
    apply_migrations(mgr.writer)
    repo = SqliteReindexJobRepository(mgr)
    yield repo
    mgr.close()


def test_structurally_satisfies_protocol(jobs: SqliteReindexJobRepository) -> None:
    assert isinstance(jobs, ReindexJobRepository)
    assert not hasattr(jobs, "atomic")


def test_create_returns_id_and_lists_as_running(
    jobs: SqliteReindexJobRepository,
) -> None:
    jid = jobs.create(model_from="st-m@v1", model_to="st-m@v2", total=100)
    assert jid > 0
    all_jobs = jobs.list()
    assert len(all_jobs) == 1
    assert all_jobs[0].status == "running"
    assert all_jobs[0].total == 100
    assert all_jobs[0].started_at is not None
    assert all_jobs[0].finished_at is None


def test_status_setters(jobs: SqliteReindexJobRepository) -> None:
    jid = jobs.create(model_from="a", model_to="b", total=10)
    jobs.pause(jid)
    assert jobs.list(status="paused")[0].status == "paused"
    jobs.start(jid)
    assert jobs.list(status="running")[0].status == "running"
    jobs.finish(jid)
    done = jobs.list(status="done")[0]
    assert done.status == "done"
    assert done.finished_at is not None


def test_fail_sets_finished_at(jobs: SqliteReindexJobRepository) -> None:
    jid = jobs.create(model_from="a", model_to="b", total=10)
    jobs.fail(jid)
    failed = jobs.list(status="failed")[0]
    assert failed.status == "failed"
    assert failed.finished_at is not None


def test_list_status_filter(jobs: SqliteReindexJobRepository) -> None:
    j1 = jobs.create(model_from="a", model_to="b", total=1)
    j2 = jobs.create(model_from="a", model_to="c", total=1)
    jobs.finish(j1)
    assert [j.job_id for j in jobs.list(status="done")] == [j1]
    assert [j.job_id for j in jobs.list(status="running")] == [j2]


def test_setters_have_no_transition_guards(jobs: SqliteReindexJobRepository) -> None:
    # The first release: setters; pausing a done job is allowed (no guard raises).
    jid = jobs.create(model_from="a", model_to="b", total=1)
    jobs.finish(jid)
    jobs.pause(jid)  # no exception
    assert jobs.list(status="paused")[0].job_id == jid
