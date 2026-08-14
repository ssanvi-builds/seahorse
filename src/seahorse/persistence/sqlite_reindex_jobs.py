"""SqliteReindexJobRepository — resumable backfill job state.

Implements ``seahorse.contracts.persistence.ReindexJobRepository`` over
``reindex_jobs``. Current-release methods are SETTERS: there are no
state-transition guards (a ``pause`` on a ``done`` job is allowed and just sets the
column). No own ``atomic()``.

Timestamps: ``started_at`` is stamped at ``create`` time; ``finished_at`` at
``finish`` / ``fail``. These are operational (not query-path) so wall-clock
``datetime.now`` is acceptable here — reproducibility applies to the query
path, not to job bookkeeping.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from seahorse.contracts.persistence import ReindexJob
from seahorse.persistence.connection import ConnectionManager


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SqliteReindexJobRepository:
    """SQLite implementation of the ``ReindexJobRepository`` Protocol."""

    def __init__(self, cm: ConnectionManager) -> None:
        self._cm = cm

    def create(self, *, model_from: str, model_to: str, total: int) -> int:
        with self._cm.atomic() as w:
            cur = w.execute(
                "INSERT INTO reindex_jobs (model_from, model_to, total, status, started_at) "
                "VALUES (?,?,?,'running',?)",
                (model_from, model_to, total, _now_iso()),
            )
            lastrowid = cur.lastrowid
            assert lastrowid is not None  # AUTOINCREMENT PK always returns a rowid
            return lastrowid

    def start(self, job_id: int) -> None:
        self._set_status(job_id, "running")

    def pause(self, job_id: int) -> None:
        self._set_status(job_id, "paused")

    def finish(self, job_id: int) -> None:
        with self._cm.atomic() as w:
            w.execute(
                "UPDATE reindex_jobs SET status='done', finished_at=? WHERE job_id=?",
                (_now_iso(), job_id),
            )

    def fail(self, job_id: int) -> None:
        with self._cm.atomic() as w:
            w.execute(
                "UPDATE reindex_jobs SET status='failed', finished_at=? WHERE job_id=?",
                (_now_iso(), job_id),
            )

    def list(self, *, status: str | None = None) -> list[ReindexJob]:
        if status is not None:
            sql = "SELECT * FROM reindex_jobs WHERE status=? ORDER BY job_id"
            params: tuple[object, ...] = (status,)
        else:
            sql = "SELECT * FROM reindex_jobs ORDER BY job_id"
            params = ()
        with self._cm.read() as w:
            rows = w.execute(sql, params).fetchall()
        return [self._row_to_job(r) for r in rows]

    def _set_status(self, job_id: int, status: str) -> None:
        with self._cm.atomic() as w:
            w.execute("UPDATE reindex_jobs SET status=? WHERE job_id=?", (status, job_id))

    def _row_to_job(self, row: sqlite3.Row) -> ReindexJob:
        return ReindexJob(
            job_id=row["job_id"],
            model_from=row["model_from"],
            model_to=row["model_to"],
            total=row["total"],
            done=row["done"],
            status=row["status"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )


__all__ = ["SqliteReindexJobRepository"]
