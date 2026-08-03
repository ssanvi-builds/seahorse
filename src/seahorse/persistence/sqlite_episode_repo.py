"""SqliteEpisodeRepository — MVP-0 implementation of the #2 Protocol.

Implements ``seahorse.contracts.engine.EpisodeRepository`` over the
``ConnectionManager``. There is NO ``delete``, NO ``update_body``,
NO ``update_valid_at`` (ADR-07 retain-not-delete). Each mutator wraps itself in
``ConnectionManager.atomic()`` (reentrant), so a bare mutator outside an explicit
``atomic()`` is still atomic, and the ``improve`` pattern
``with repo.atomic(): repo.append(new); repo.set_invalid_at(old, now)`` issues a
single ``BEGIN IMMEDIATE`` / ``COMMIT``.

Reads use ``ConnectionManager.read()`` (the writer under the reentrant lock, no
tx). The hot INDEX-level read path is served by ``EpisodeIndexRepository`` via the
reader pool, not here; this repo returns FULL ``Episode`` objects with body.

Bi-temporal axes (ADR-03): ``query_state_at`` filters on ``valid_at``/``invalid_at``,
``query_known_at`` on ``created_at``/``expired_at``. The two axes are never mixed.
NULL-safe: a row with ``valid_at IS NULL`` (PENDING_INGEST) is excluded from
``state_at`` (not valid at any time); ``known_at`` keeps ``expired_at IS NULL`` rows.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from seahorse.contracts.engine import InvalidationConflictError, NotFound
from seahorse.contracts.episode import Episode
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.episode_index_columns import (
    EPISODE_INDEX_CORE_COLUMNS,
    index_insert_sql,
)


def _fmt_dt(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _req_dt(value: str) -> datetime:
    """Parse a NOT NULL timestamp column (episodes.created_at is NOT NULL)."""
    return datetime.fromisoformat(value)


_EPISODES_INSERT = (
    "INSERT INTO episodes (id, subject, fact_id, body_md, valid_at, invalid_at, "
    "created_at, expired_at, supersedes, supersedes_reason, cognitive_type, "
    "source_type, schema_version, provenance) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
# C8.3 [36]: the episode_index INSERT column list is derived from the single
# source in ``episode_index_columns`` (the hot path writes the 15 core columns,
# leaving file metadata NULL). The VALUES tuple below must stay in
# EPISODE_INDEX_CORE_COLUMNS order.
_INDEX_INSERT = index_insert_sql(EPISODE_INDEX_CORE_COLUMNS)
_SELECT_WITH_INDEX = (
    "SELECT e.*, ix.title AS ix_title, ix.summary AS ix_summary "
    "FROM episodes e LEFT JOIN episode_index ix ON ix.ep_id = e.id"
)


class SqliteEpisodeRepository:
    """SQLite implementation of the ``EpisodeRepository`` Protocol (#2)."""

    def __init__(self, cm: ConnectionManager) -> None:
        self._cm = cm

    @contextmanager
    def atomic(self) -> Iterator[None]:
        with self._cm.atomic():
            yield

    # -- mutation -------------------------------------------------------------

    def append(self, episode: Episode) -> None:
        """Insert the episode + propagate to ``episode_index``.

        A second vigente row for the same ``fact_id`` raises ``IntegrityError``
        (mirrors the ``episodes`` I11 partial unique index); the Engine must
        invalidate the predecessor first via ``set_invalid_at``.
        """
        with self._cm.atomic() as w:
            w.execute(
                _EPISODES_INSERT,
                (
                    episode.id,
                    episode.subject,
                    episode.fact_id,
                    episode.body,
                    _fmt_dt(episode.valid_at),
                    _fmt_dt(episode.invalid_at),
                    _fmt_dt(episode.created_at),
                    _fmt_dt(episode.expired_at),
                    episode.supersedes,
                    episode.supersedes_reason,
                    episode.cognitive_type,
                    episode.source_type,
                    episode.schema_version,
                    episode.provenance_json(),
                ),
            )
            w.execute(
                _INDEX_INSERT,
                (
                    episode.id,
                    episode.subject,
                    episode.fact_id,
                    _fmt_dt(episode.valid_at),
                    _fmt_dt(episode.invalid_at),
                    _fmt_dt(episode.created_at),
                    _fmt_dt(episode.expired_at),
                    episode.supersedes,
                    episode.supersedes_reason,
                    episode.cognitive_type,
                    episode.source_type,
                    episode.schema_version,
                    0,
                    episode.title,
                    episode.summary,
                ),
            )

    def set_invalid_at(self, ep_id: str, now: datetime) -> None:
        """Idempotent invalidation (SO-3): ``invalid_at`` set once.

        Raises ``NotFound`` if ``ep_id`` never existed; ``InvalidationConflictError``
        if it is already invalidated (the ``WHERE invalid_at IS NULL`` guard matched
        0 rows). Propagates to ``episode_index`` and, since M1-A.5, to the vec0
        auxiliary column ``vec_episodes.invalid_at`` (spec §4.4 load-bearing) so
        the vigent-only kNN pushdown excludes invalidated episodes. The FTS5
        index needs no UPDATE (search filters via the ``episode_index`` JOIN).
        """
        with self._cm.atomic() as w:
            exists = w.execute("SELECT 1 FROM episodes WHERE id = ?", (ep_id,)).fetchone()
            if exists is None:
                raise NotFound(ep_id)
            cur = w.execute(
                "UPDATE episodes SET invalid_at = ? WHERE id = ? AND invalid_at IS NULL",
                (_fmt_dt(now), ep_id),
            )
            if cur.rowcount == 0:
                raise InvalidationConflictError(ep_id)
            w.execute(
                "UPDATE episode_index SET invalid_at = ? WHERE ep_id = ? AND invalid_at IS NULL",
                (_fmt_dt(now), ep_id),
            )
            w.execute(
                "UPDATE vec_episodes SET invalid_at = ? WHERE ep_id = ? AND invalid_at IS NULL",
                (_fmt_dt(now), ep_id),
            )

    # -- reads ----------------------------------------------------------------

    def _row_to_episode(self, row: sqlite3.Row) -> Episode:
        return Episode(
            id=row["id"],
            created_at=_req_dt(row["created_at"]),
            schema_version=row["schema_version"],
            provenance=json.loads(row["provenance"]),
            body=row["body_md"],
            subject=row["subject"],
            fact_id=row["fact_id"],
            valid_at=_parse_dt(row["valid_at"]),
            invalid_at=_parse_dt(row["invalid_at"]),
            expired_at=_parse_dt(row["expired_at"]),
            supersedes=row["supersedes"],
            supersedes_reason=row["supersedes_reason"],
            cognitive_type=row["cognitive_type"],
            source_type=row["source_type"],
            title=row["ix_title"],
            summary=row["ix_summary"],
            tags=[],
        )

    def _fetch_one(self, where: str, params: tuple[object, ...]) -> Episode | None:
        with self._cm.read() as w:
            row = w.execute(f"{_SELECT_WITH_INDEX} WHERE {where}", params).fetchone()
            return self._row_to_episode(row) if row is not None else None

    def _fetch_many(self, where: str, params: tuple[object, ...]) -> list[Episode]:
        with self._cm.read() as w:
            rows = w.execute(f"{_SELECT_WITH_INDEX} WHERE {where}", params).fetchall()
            return [self._row_to_episode(r) for r in rows]

    def get(self, ep_id: str) -> Episode | None:
        return self._fetch_one("e.id = ?", (ep_id,))

    def find_vigent_by_fact_id(self, fact_id: str, exclude: str | None = None) -> Episode | None:
        where = "e.fact_id = ? AND e.invalid_at IS NULL AND e.expired_at IS NULL"
        if exclude is not None:
            where += " AND e.id != ?"
            return self._fetch_one(where, (fact_id, exclude))
        return self._fetch_one(where, (fact_id,))

    def chain_from(self, ep_id: str) -> list[Episode]:
        """Transitive closure over ``supersedes`` (both directions), sorted by created_at."""
        with self._cm.read() as w:
            seen: set[str] = set()
            rows: list[sqlite3.Row] = []
            # walk backward (toward older) via supersedes
            current: str | None = ep_id
            while current is not None and current not in seen:
                seen.add(current)
                row = w.execute(f"{_SELECT_WITH_INDEX} WHERE e.id = ?", (current,)).fetchone()
                if row is None:
                    break
                rows.append(row)
                current = row["supersedes"]
            # walk forward (toward newer): episodes whose supersedes points into seen
            frontier: set[str] = {ep_id}
            while frontier:
                placeholders = ",".join("?" * len(frontier))
                forward = w.execute(
                    f"{_SELECT_WITH_INDEX} WHERE e.supersedes IN ({placeholders})",
                    tuple(frontier),
                ).fetchall()
                next_frontier: set[str] = set()
                for r in forward:
                    if r["id"] not in seen:
                        seen.add(r["id"])
                        rows.append(r)
                        next_frontier.add(r["id"])
                frontier = next_frontier
        episodes = [self._row_to_episode(r) for r in rows]
        episodes.sort(key=lambda e: e.created_at)
        return episodes

    def query_vigent(self, subject: str | None = None) -> list[Episode]:
        where = "e.invalid_at IS NULL AND e.expired_at IS NULL"
        if subject is not None:
            where += " AND e.subject = ?"
            return self._fetch_many(where, (subject,))
        return self._fetch_many(where, ())

    def query_state_at(self, t: datetime, subject: str | None = None) -> list[Episode]:
        # state_at axis: (valid_at IS NULL OR valid_at <= t) AND (invalid_at IS NULL
        # OR invalid_at > t). CC-2 (C8.6): valid_at IS NULL = "from forever"
        # (f5-02 §2 line 85) — valid at ANY t, so it is INCLUDED (mirrors the
        # canonical engine predicates get_vigente / is_valid_at, which already
        # include NULL). Real PENDING is valid_at in the FUTURE, excluded by
        # ``valid_at <= t``.
        where = (
            "(e.valid_at IS NULL OR e.valid_at <= ?) "
            "AND (e.invalid_at IS NULL OR e.invalid_at > ?)"
        )
        params: list[object] = [_fmt_dt(t), _fmt_dt(t)]
        if subject is not None:
            where += " AND e.subject = ?"
            params.append(subject)
        return self._fetch_many(where, tuple(params))

    def query_known_at(self, t: datetime, subject: str | None = None) -> list[Episode]:
        # known_at axis: created_at <= t AND (expired_at IS NULL OR expired_at > t).
        where = "e.created_at <= ? AND (e.expired_at IS NULL OR e.expired_at > ?)"
        params: list[object] = [_fmt_dt(t), _fmt_dt(t)]
        if subject is not None:
            where += " AND e.subject = ?"
            params.append(subject)
        return self._fetch_many(where, tuple(params))


__all__ = ["SqliteEpisodeRepository"]
