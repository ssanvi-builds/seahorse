"""SqliteVectorIndexRepository — real vec0 backend (M1-A.3, SO-7b).

Materializes the ``VectorIndexRepository`` Protocol (fold-into-upsert +
``distinct_model_identities``) over the sqlite-vec ``vec_episodes`` virtual
table created by migration 010. Requires the sqlite-vec extension loaded on the
connection (``ConnectionManager.extensions=("vec0",)`` — Storage opts in).

Notes:
- **Upsert fold-into (SO-7b)**: one atomic call writes the vector into
  ``vec_episodes`` AND the model stamp into ``vec_episodes_meta`` (no split
  write window). ``fact_id`` / ``cognitive_type`` / ``created_at`` are derived
  from ``episode_index`` (aux columns for filter pushdown) so the impl stays
  within the signed ``upsert(ep_id, vector, *, dim, model_identity,
  content_hash, embedded_at)`` surface.
- **kNN (vigent / filtered)**: sqlite-vec v0.1.9 flat scan applies auxiliary
  filters DURING the scan, so ``k`` is the final cap — no over-fetch needed.
  ``score = 1/(1+distance)`` (ADR-10, L2 over unit vectors).
- **kNN PIT**: ``state_at`` / ``known_at`` span ``valid_at`` / ``expired_at``
  which vec0 does not carry, so they JOIN ``episode_index`` with the canonical
  ``_pit_predicate`` (CC-2, ``valid_at IS NULL`` = from-forever included) AFTER
  the kNN. Over-fetch by ``KNN_OVERFETCH_FACTOR`` and re-cap to ``k``.
- ``rebuild()`` is an honest no-op (signed signature takes no args; the actual
  backfill is #7's ``RetrievalIndexer`` / ``index rebuild``).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from seahorse.contracts.index import PITKind
from seahorse.contracts.persistence import VectorHit
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.sqlite_episode_index import _pit_predicate

# v0.1.9 flat: filters apply during the scan, so k is final for vigent knn; the
# PIT variants over-fetch because the JOIN predicate drops hits after kNN.
KNN_OVERFETCH_FACTOR = 5


def _as_bytes(query: Any) -> bytes:
    """Coerce the opaque ``query`` to a float32 BLOB (the shape vec0 expects).

    The #7 QueryEmbedder adapter returns ``bytes``; a non-bytes value is a
    contract violation surfaced loud (ADR-10) rather than silently mis-encoded.
    """
    if isinstance(query, bytes):
        return query
    raise TypeError(
        f"knn query must be a bytes float32 BLOB; got {type(query).__name__} "
        "(the #7 QueryEmbedder adapter returns bytes)"
    )


class SqliteVectorIndexRepository:
    """SQLite vec0 implementation of the ``VectorIndexRepository`` Protocol."""

    def __init__(self, cm: ConnectionManager) -> None:
        self._cm = cm

    def upsert(
        self,
        ep_id: str,
        vector: bytes,
        *,
        dim: int,
        model_identity: str,
        content_hash: str,
        embedded_at: str,
    ) -> None:
        blob = _as_bytes(vector)
        # v0.1.9 vec0 has no INSERT OR REPLACE / ON CONFLICT — upsert is
        # DELETE + INSERT in the same transaction (f5-06 §4.4). The lateral
        # vec_episodes_meta follows the same pattern (fold-into-upsert SO-7b).
        with self._cm.atomic() as w:
            w.execute("DELETE FROM vec_episodes WHERE ep_id = ?", (ep_id,))
            w.execute(
                "INSERT INTO vec_episodes "
                "(ep_id, embedding, fact_id, invalid_at, cognitive_type, created_at) "
                "VALUES (?, ?, "
                "(SELECT fact_id FROM episode_index WHERE ep_id = ?), NULL, "
                "(SELECT cognitive_type FROM episode_index WHERE ep_id = ?), "
                "(SELECT created_at FROM episode_index WHERE ep_id = ?))",
                (ep_id, blob, ep_id, ep_id, ep_id),
            )
            w.execute("DELETE FROM vec_episodes_meta WHERE ep_id = ?", (ep_id,))
            w.execute(
                "INSERT INTO vec_episodes_meta "
                "(ep_id, model_identity, content_hash, embedded_at, dim) "
                "VALUES (?, ?, ?, ?, ?)",
                (ep_id, model_identity, content_hash, embedded_at, dim),
            )

    def distinct_model_identities(self) -> list[str]:
        with self._cm.reader() as r:
            rows = r.execute(
                "SELECT DISTINCT model_identity FROM vec_episodes_meta "
                "ORDER BY model_identity"
            ).fetchall()
        return [row[0] for row in rows]

    def knn(
        self,
        query: Any,
        k: int,
        *,
        vigent_only: bool = True,
        fact_id_filter: str | None = None,
        cognitive_types: list[str] | None = None,
    ) -> list[VectorHit]:
        # sqlite-vec v0.1.9 forbids auxiliary-column WHERE constraints directly
        # in a kNN query ("illegal WHERE constraint on a vec0 auxiliary
        # column"), and forbids LIMIT alongside k=?. So: run the kNN scan
        # (over-fetch), filter the auxiliary columns in the outer SELECT, then
        # re-cap to k. Same pattern as the PIT variants.
        blob = _as_bytes(query)
        overfetch = k * KNN_OVERFETCH_FACTOR
        where: list[str] = []
        params: list[object] = [blob, overfetch]
        if vigent_only:
            where.append("v.invalid_at IS NULL")
        if fact_id_filter is not None:
            where.append("v.fact_id = ?")
            params.append(fact_id_filter)
        if cognitive_types:
            where.append(f"v.cognitive_type IN ({', '.join('?' * len(cognitive_types))})")
            params.extend(cognitive_types)
        params.append(k)
        sql = (
            "SELECT v.ep_id, v.distance, 1/(1+v.distance) AS score "
            "FROM (SELECT ep_id, distance, invalid_at, fact_id, cognitive_type "
            "FROM vec_episodes WHERE embedding MATCH ? LIMIT ?) v "
            f"WHERE {' AND '.join(where)} ORDER BY v.distance LIMIT ?"
        )
        with self._cm.reader() as r:
            rows = r.execute(sql, params).fetchall()
        return [_row_to_hit(row) for row in rows]

    def knn_state_at(self, query: Any, k: int, t: datetime) -> list[VectorHit]:
        return self._knn_pit(query, k, "state_at", t)

    def knn_known_at(self, query: Any, k: int, t: datetime) -> list[VectorHit]:
        return self._knn_pit(query, k, "known_at", t)

    def _knn_pit(
        self, query: Any, k: int, pit_kind: PITKind, t: datetime
    ) -> list[VectorHit]:
        blob = _as_bytes(query)
        overfetch = k * KNN_OVERFETCH_FACTOR
        pred_sql, (t1, t2) = _pit_predicate(pit_kind, t)
        sql = (
            "SELECT k.ep_id, k.distance, 1/(1+k.distance) AS score "
            "FROM (SELECT ep_id, distance FROM vec_episodes "
            "WHERE embedding MATCH ? AND k = ?) k "
            f"JOIN episode_index ix ON ix.ep_id = k.ep_id AND {pred_sql} "
            "ORDER BY k.distance LIMIT ?"
        )
        with self._cm.reader() as r:
            rows = r.execute(sql, (blob, overfetch, t1, t2, k)).fetchall()
        return [_row_to_hit(row) for row in rows]

    def remove_for_rebuild(self) -> None:
        with self._cm.atomic() as w:
            w.execute("DELETE FROM vec_episodes")
            w.execute("DELETE FROM vec_episodes_meta")

    def rebuild(self) -> None:
        # Honest no-op: the signed contract takes no args; the real backfill is
        # #7's RetrievalIndexer / `index rebuild` (M1-B.5).
        return None

    def count(self) -> int:
        with self._cm.reader() as r:
            return r.execute("SELECT count(*) FROM vec_episodes").fetchone()[0]


def _row_to_hit(row: Any) -> VectorHit:
    return VectorHit(
        ep_id=row["ep_id"],
        distance=float(row["distance"]),
        score=float(row["score"]),
    )


def vec_wipe(conn: sqlite3.Connection) -> None:
    """Secondary-index wipe for the sidecar rebuild (M1-A.6, C8.8 seam).

    Clears vec0 + the lateral model stamp so a vault rebuild leaves no ghost
    vectors pointing at ep_ids deleted from ``episode_index``. Runs inside the
    rebuild ``atomic()`` (clear phase), so a wipe failure rolls back the whole
    episode_index clear too.
    """
    conn.execute("DELETE FROM vec_episodes")
    conn.execute("DELETE FROM vec_episodes_meta")


__all__ = ["SqliteVectorIndexRepository", "KNN_OVERFETCH_FACTOR", "vec_wipe"]
