"""SqliteFullTextIndexRepository — real FTS5 external-content backend (M1-A.4).

Materializes the ``FullTextIndexRepository`` Protocol over the FTS5
``episode_fts`` external-content pair (``episode_content`` + ``episode_fts``,
migration 010). External content = no triggers: every write into
``episode_content`` is mirrored by an explicit INSERT into ``episode_fts`` with
the same rowid, and deletes by the ``'delete'`` command (f5-06 §7a.1); the
``'rebuild'`` command resyncs the index from ``episode_content``.

- ``score = exp(-bm25(episode_fts))`` — bm25 is lower=better, exp(-x) makes it
  higher=better in (0,1] (ADR-10 reproducible, no min-max).
- Baseline tokenizer ``unicode61 remove_diacritics 2`` (accent/case-insensitive,
  no stemming — Snowball Spanish is mediano, f5-06 §5.2/§5.5).
- Query escaping (f5-06 §5.7): default phrase-literal (double-quote the whole
  query); the signed contract has no advanced-syntax flag.
- The vigent / PIT variants JOIN ``episode_index`` (CC-2 ``_pit_predicate``);
  the BM25 scan is capped at ``FTS_OVERFETCH_FACTOR * k`` so the JOIN drop and
  re-cap to ``k`` stay cheap.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from seahorse.contracts.index import PITKind
from seahorse.contracts.persistence import FtsDoc, FullTextHit
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.sqlite_episode_index import _pit_predicate

# The BM25 subquery is capped above k because the PIT/vigent JOIN may drop hits.
FTS_OVERFETCH_FACTOR = 5


def _escape_query(query: str) -> str:
    """Phrase-literal default (f5-06 §5.7): quote the whole query as a phrase."""
    return f'"{query}"'


def _tags(doc: FtsDoc) -> str:
    return " ".join(doc.tags)


class SqliteFullTextIndexRepository:
    """SQLite FTS5 external-content implementation of ``FullTextIndexRepository``."""

    def __init__(self, cm: ConnectionManager) -> None:
        self._cm = cm

    def upsert(self, doc: FtsDoc) -> None:
        tags = _tags(doc)
        with self._cm.atomic() as w:
            existing = w.execute(
                "SELECT rowid FROM episode_content WHERE ep_id = ?", (doc.ep_id,)
            ).fetchone()
            if existing is not None:
                # 'delete' must run while episode_content still holds the row
                # (FTS5 reads it to drop the tokens), then the row is removed.
                w.execute(
                    "INSERT INTO episode_fts(episode_fts, rowid) VALUES('delete', ?)",
                    (existing[0],),
                )
                w.execute("DELETE FROM episode_content WHERE ep_id = ?", (doc.ep_id,))
            cur = w.execute(
                "INSERT INTO episode_content "
                "(ep_id, body_md, title, tags, summary, subject) VALUES (?, ?, ?, ?, ?, ?)",
                (doc.ep_id, doc.body_md, doc.title, tags, doc.summary, doc.subject),
            )
            rowid = cur.lastrowid
            w.execute(
                "INSERT INTO episode_fts "
                "(rowid, ep_id, body_md, title, tags, summary, subject) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rowid, doc.ep_id, doc.body_md, doc.title, tags, doc.summary, doc.subject),
            )

    def search(
        self,
        query: str,
        k: int,
        *,
        vigent_only: bool = True,
        subject_filter: str | None = None,
    ) -> list[FullTextHit]:
        escaped = _escape_query(query)
        overfetch = k * FTS_OVERFETCH_FACTOR
        params: list[object] = [escaped, overfetch]
        where = ["ix.invalid_at IS NULL AND ix.expired_at IS NULL"]
        if subject_filter is not None:
            where.append("c.subject = ?")
            params.append(subject_filter)
        params.append(k)
        sql = (
            "SELECT c.ep_id, f.bm25_score, exp(-f.bm25_score) AS score "
            "FROM (SELECT rowid, bm25(episode_fts) AS bm25_score FROM episode_fts "
            "WHERE episode_fts MATCH ? ORDER BY bm25(episode_fts) LIMIT ?) f "
            "JOIN episode_content c ON c.rowid = f.rowid "
            "JOIN episode_index ix ON ix.ep_id = c.ep_id "
            f"WHERE {' AND '.join(where)} ORDER BY f.bm25_score LIMIT ?"
        )
        with self._cm.reader() as r:
            rows = r.execute(sql, params).fetchall()
        return [_row_to_hit(row) for row in rows]

    def search_state_at(self, query: str, k: int, t: datetime) -> list[FullTextHit]:
        return self._search_pit(query, k, "state_at", t)

    def search_known_at(self, query: str, k: int, t: datetime) -> list[FullTextHit]:
        return self._search_pit(query, k, "known_at", t)

    def _search_pit(
        self, query: str, k: int, pit_kind: PITKind, t: datetime
    ) -> list[FullTextHit]:
        escaped = _escape_query(query)
        overfetch = k * FTS_OVERFETCH_FACTOR
        pred_sql, (t1, t2) = _pit_predicate(pit_kind, t)
        sql = (
            "SELECT c.ep_id, f.bm25_score, exp(-f.bm25_score) AS score "
            "FROM (SELECT rowid, bm25(episode_fts) AS bm25_score FROM episode_fts "
            "WHERE episode_fts MATCH ? ORDER BY bm25(episode_fts) LIMIT ?) f "
            "JOIN episode_content c ON c.rowid = f.rowid "
            f"JOIN episode_index ix ON ix.ep_id = c.ep_id AND {pred_sql} "
            "ORDER BY f.bm25_score LIMIT ?"
        )
        with self._cm.reader() as r:
            rows = r.execute(sql, (escaped, overfetch, t1, t2, k)).fetchall()
        return [_row_to_hit(row) for row in rows]

    def remove_for_rebuild(self, ep_id: str) -> None:
        with self._cm.atomic() as w:
            existing = w.execute(
                "SELECT rowid FROM episode_content WHERE ep_id = ?", (ep_id,)
            ).fetchone()
            if existing is None:
                return
            w.execute(
                "INSERT INTO episode_fts(episode_fts, rowid) VALUES('delete', ?)",
                (existing[0],),
            )
            w.execute("DELETE FROM episode_content WHERE ep_id = ?", (ep_id,))

    def rebuild(self, docs: list[FtsDoc]) -> None:
        with self._cm.atomic() as w:
            w.execute("DELETE FROM episode_content")
            # Resync the index with the (now empty) content table before repopulating.
            w.execute("INSERT INTO episode_fts(episode_fts) VALUES('rebuild')")
            for doc in docs:
                tags = _tags(doc)
                cur = w.execute(
                    "INSERT INTO episode_content "
                    "(ep_id, body_md, title, tags, summary, subject) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (doc.ep_id, doc.body_md, doc.title, tags, doc.summary, doc.subject),
                )
                rowid = cur.lastrowid
                w.execute(
                    "INSERT INTO episode_fts "
                    "(rowid, ep_id, body_md, title, tags, summary, subject) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (rowid, doc.ep_id, doc.body_md, doc.title, tags, doc.summary, doc.subject),
                )

    def count(self) -> int:
        with self._cm.reader() as r:
            return r.execute("SELECT count(*) FROM episode_fts").fetchone()[0]


def _row_to_hit(row: sqlite3.Row) -> FullTextHit:
    return FullTextHit(
        ep_id=row["ep_id"],
        bm25_score=float(row["bm25_score"]),
        score=float(row["score"]),
    )


__all__ = ["SqliteFullTextIndexRepository", "FTS_OVERFETCH_FACTOR"]
