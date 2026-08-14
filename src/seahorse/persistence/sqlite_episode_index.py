"""SqliteEpisodeIndexRepository — index accessors + bfs_neighbors_state_at.

Implements ``seahorse.contracts.persistence.EpisodeIndexRepository`` over the
``episode_index`` bridge table (NO body — returns ``IndexRowData``). This is the
hot INDEX/TIMELINE read path: reads use the WAL reader pool (no write-lock
contention). No own ``atomic()``.

Bi-temporal axes: ``*_state_at`` filters the valid_time axis
(``valid_at``/``invalid_at``); ``*_known_at`` filters the transaction_time axis
(``created_at``/``expired_at``). The two axes are NEVER mixed. NULL-safe:
``valid_at IS NULL`` (PENDING_INGEST) is excluded from ``state_at``; ``known_at``
keeps ``expired_at IS NULL`` rows. ``bfs_neighbors_state_at`` raises
``HopsCapExceeded`` for ``hops > MAX_HOPS_MVP1`` and ``NotImplementedError`` for
``include_tags_soft=True`` (a medium-term goal, not in the current release).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from typing import cast

from seahorse.contracts.index import MAX_HOPS_MVP1, HopsCapExceeded, IndexRowData, PITKind
from seahorse.persistence.connection import ConnectionManager


def _req_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _req_str(value: str | None) -> str:
    # IndexRowData declares these as non-null str; the DDL allows NULL (PENDING_INGEST).
    # Coerce NULL to "" so the frozen contract holds. The INDEX level shows an empty
    # subject/fact_id for not-yet-valid episodes; the bridge equality is only
    # exercised on episodes the Engine populated with a real fact_id.
    return value if value is not None else ""


def _row_to_index(row: sqlite3.Row) -> IndexRowData:
    return IndexRowData(
        ep_id=row["ep_id"],
        fact_id=_req_str(row["fact_id"]),
        subject=_req_str(row["subject"]),
        title=row["title"],
        summary=row["summary"],
        cognitive_type=_req_str(row["cognitive_type"]),
        source_type=row["source_type"],
        schema_version=row["schema_version"],
        skip_extraction=bool(row["skip_extraction"]),
        valid_at=_parse_dt(row["valid_at"]),
        invalid_at=_parse_dt(row["invalid_at"]),
        created_at=_req_dt(row["created_at"]),
        expired_at=_parse_dt(row["expired_at"]),
        supersedes=row["supersedes"],
    )


def _pit_predicate(pit_kind: PITKind, pit: datetime) -> tuple[str, tuple[str, str]]:
    """Return (SQL fragment, params) for the bi-temporal PIT filter (axes never mixed).

    The ``state_at`` branch includes ``valid_at IS NULL`` ("from forever" —
    valid at any ``t``), mirroring the canonical engine predicates
    ``get_vigente`` / ``is_valid_at``. Real PENDING is ``valid_at`` in the
    FUTURE, excluded by ``valid_at <= ?``.
    """
    iso = pit.isoformat()
    if pit_kind == "state_at":
        return (
            "(valid_at IS NULL OR valid_at <= ?) AND (invalid_at IS NULL OR invalid_at > ?)",
            (iso, iso),
        )
    if pit_kind == "known_at":
        return (
            "created_at <= ? AND (expired_at IS NULL OR expired_at > ?)",
            (iso, iso),
        )
    raise ValueError(f"pit_kind must be 'state_at' or 'known_at', got {pit_kind!r}")


class SqliteEpisodeIndexRepository:
    """SQLite implementation of the ``EpisodeIndexRepository`` Protocol."""

    def __init__(self, cm: ConnectionManager) -> None:
        self._cm = cm

    # -- INDEX level ----------------------------------------------------------

    def _rows_by_ep_ids(self, ep_ids: tuple[str, ...]) -> list[IndexRowData]:
        if not ep_ids:
            return []
        placeholders = ",".join("?" * len(ep_ids))
        with self._cm.reader() as r:
            rows = r.execute(
                f"SELECT * FROM episode_index WHERE ep_id IN ({placeholders}) ORDER BY ep_id",
                ep_ids,
            ).fetchall()
        return [_row_to_index(row) for row in rows]

    def get_rows(self, ep_ids: Sequence[str]) -> list[IndexRowData]:
        return self._rows_by_ep_ids(tuple(ep_ids))

    def get_rows_state_at(self, ep_ids: Sequence[str], t: datetime) -> list[IndexRowData]:
        ids = tuple(ep_ids)
        if not ids:
            return []
        pred, pred_params = _pit_predicate("state_at", t)
        placeholders = ",".join("?" * len(ids))
        with self._cm.reader() as r:
            rows = r.execute(
                f"SELECT * FROM episode_index WHERE ep_id IN ({placeholders}) AND {pred} "
                "ORDER BY ep_id",
                (*ids, *pred_params),
            ).fetchall()
        return [_row_to_index(row) for row in rows]

    def get_rows_known_at(self, ep_ids: Sequence[str], t: datetime) -> list[IndexRowData]:
        ids = tuple(ep_ids)
        if not ids:
            return []
        pred, pred_params = _pit_predicate("known_at", t)
        placeholders = ",".join("?" * len(ids))
        with self._cm.reader() as r:
            rows = r.execute(
                f"SELECT * FROM episode_index WHERE ep_id IN ({placeholders}) AND {pred} "
                "ORDER BY ep_id",
                (*ids, *pred_params),
            ).fetchall()
        return [_row_to_index(row) for row in rows]

    # -- TIMELINE level (current-release axes) -------------------------------

    def chain_rows_from(self, ep_id: str) -> list[IndexRowData]:
        """Transitive closure over ``supersedes`` on ``episode_index`` (both directions)."""
        with self._cm.reader() as r:
            seen: set[str] = set()
            rows: list[sqlite3.Row] = []
            current: str | None = ep_id
            while current is not None and current not in seen:
                seen.add(current)
                row = r.execute(
                    "SELECT * FROM episode_index WHERE ep_id = ?", (current,)
                ).fetchone()
                if row is None:
                    break
                rows.append(row)
                current = row["supersedes"]
            frontier: set[str] = {ep_id}
            while frontier:
                placeholders = ",".join("?" * len(frontier))
                forward = r.execute(
                    f"SELECT * FROM episode_index WHERE supersedes IN ({placeholders})",
                    tuple(frontier),
                ).fetchall()
                next_frontier: set[str] = set()
                for row in forward:
                    if row["ep_id"] not in seen:
                        seen.add(row["ep_id"])
                        rows.append(row)
                        next_frontier.add(row["ep_id"])
                frontier = next_frontier
        result = [_row_to_index(row) for row in rows]
        result.sort(key=lambda x: x.created_at)
        return result

    def find_vigent_row_by_fact_id(
        self, fact_id: str, exclude: str | None = None
    ) -> IndexRowData | None:
        where = "fact_id = ? AND invalid_at IS NULL AND expired_at IS NULL"
        params: list[object] = [fact_id]
        if exclude is not None:
            where += " AND ep_id != ?"
            params.append(exclude)
        with self._cm.reader() as r:
            row = r.execute(f"SELECT * FROM episode_index WHERE {where}", tuple(params)).fetchone()
        return _row_to_index(row) if row is not None else None

    # -- TIMELINE level (later-release axes) ---------------------------------

    def _range_rows(
        self,
        axis: PITKind,
        t_start: datetime,
        t_end: datetime,
        subject: str | None,
    ) -> list[IndexRowData]:
        col = "valid_at" if axis == "state_at" else "created_at"
        sql = f"SELECT * FROM episode_index WHERE {col} IS NOT NULL AND {col} >= ? AND {col} <= ?"
        params: list[object] = [t_start.isoformat(), t_end.isoformat()]
        if subject is not None:
            sql += " AND subject = ?"
            params.append(subject)
        sql += f" ORDER BY {col}"
        with self._cm.reader() as r:
            rows = r.execute(sql, tuple(params)).fetchall()
        return [_row_to_index(row) for row in rows]

    def range_rows_state_at(
        self,
        t_start: datetime,
        t_end: datetime,
        *,
        subject: str | None = None,
    ) -> list[IndexRowData]:
        return self._range_rows("state_at", t_start, t_end, subject)

    def range_rows_known_at(
        self,
        t_start: datetime,
        t_end: datetime,
        *,
        subject: str | None = None,
    ) -> list[IndexRowData]:
        return self._range_rows("known_at", t_start, t_end, subject)

    # -- BFS extension -------------------------------------------------------

    def bfs_neighbors_state_at(
        self,
        ep_id: str,
        pit: datetime,
        *,
        pit_kind: PITKind,
        hops: int,
        include_tags_soft: bool,
    ) -> list[IndexRowData]:
        if include_tags_soft:
            raise NotImplementedError(
                "include_tags_soft is a medium-term goal (not in the current release)"
            )
        if hops > MAX_HOPS_MVP1:
            raise HopsCapExceeded(hops, MAX_HOPS_MVP1)
        pred, pred_params = _pit_predicate(cast(PITKind, pit_kind), pit)
        with self._cm.reader() as r:
            seen: set[str] = {ep_id}
            current_layer: set[str] = {ep_id}
            collected: list[sqlite3.Row] = []
            for _depth in range(hops + 1):
                if not current_layer:
                    break
                placeholders = ",".join("?" * len(current_layer))
                # rows at this layer that satisfy the PIT predicate
                matching = r.execute(
                    f"SELECT * FROM episode_index WHERE ep_id IN ({placeholders}) AND {pred}",
                    (*current_layer, *pred_params),
                ).fetchall()
                collected.extend(matching)
                # neighbors via supersedes (both directions): rows pointing INTO
                # the current layer, and rows the current layer points to.
                newer = r.execute(
                    f"SELECT ep_id FROM episode_index WHERE supersedes IN ({placeholders})",
                    tuple(current_layer),
                ).fetchall()
                older = r.execute(
                    f"SELECT supersedes FROM episode_index "
                    f"WHERE ep_id IN ({placeholders}) AND supersedes IS NOT NULL",
                    tuple(current_layer),
                ).fetchall()
                next_layer = {row["ep_id"] for row in newer} | {row["supersedes"] for row in older}
                next_layer -= seen
                seen |= next_layer
                current_layer = next_layer
        result = [_row_to_index(row) for row in collected]
        result.sort(key=lambda x: x.ep_id)
        return result


__all__ = ["SqliteEpisodeIndexRepository"]
