"""SqliteChunkedVectorIndexRepository — chunk-level vec0 backend (P3.2 seam).

Materializes the SAME signed ``VectorIndexRepository`` Protocol over the
``vec_episode_chunks`` vec0 virtual table created by migration 013 (parallel
to ``vec_episodes``: one row per CHUNK, ``chunk_id = "{parent_ep_id}#{i}"``).
Wired ONLY at the composition root with ``build_facade(chunk_mode="chunked")``
— with the flag off (the default) the table stays empty and the classic
repository serves every path (blast radius zero, flag-off bit-identical).

Notes:
- **Upsert fold-into**: one atomic call replaces ALL of a parent's chunks
  (``upsert_chunks``: DELETE by parent + ordered INSERT per chunk + the
  lateral ``vec_chunks_meta`` stamps, no split write window). The signed
  Protocol ``upsert(ep_id, vector, ...)`` is the single-chunk case —
  ``"{ep_id}#0"`` — so the Protocol surface stays coherent for any other
  writer. ``fact_id`` / ``cognitive_type`` / ``created_at`` derive from the
  PARENT's ``episode_index`` row (chunks inherit the parent's identity).
- **kNN**: vec0 forbids auxiliary-column WHERE constraints inside a kNN scan
  (verified against 0.1.9 and 0.1.10-alpha.4), so the aux filters apply in the
  outer SELECT — AFTER the kNN, BEFORE the fold. It ALSO forbids projecting
  an auxiliary column out of the ``k = ?`` kNN form (``parent_ep_id`` is
  auxiliary here — the PK is chunk_id, unlike ``vec_episodes`` where ep_id is
  the PK), so every kNN subquery uses the ``LIMIT ?`` form, which allows the
  aux projection. The fold collapses chunks
  to their PARENT first-occurrence-wins (rows arrive distance-ordered, so a
  parent's best chunk defines its hit), and the re-cap to ``k`` happens AFTER
  the fold — never before (folding can only shrink the list; an early cap
  would artificially lower recall). A ``chunk_id`` never escapes: the
  surface stays parent ep_ids (``VectorHit.ep_id``).
- **Over-fetch sizing**: ``k × KNN_OVERFETCH_FACTOR × MAX_CHUNKS_PER_EPISODE``
  chunks. The write side caps chunks per parent, so the scan covers
  ``≥ k`` DISTINCT parents even after the aux drops — the fold can never
  starve the re-cap (the flagged risk). The constant derives from the
  chunker's cap (single source); the persistence module otherwise stays
  embeddings-free.
- **PIT**: ``state_at`` / ``known_at`` JOIN the PARENT's ``episode_index``
  row with the canonical ``_pit_predicate`` AFTER the kNN — chunks inherit
  the parent's bi-temporal validity, chunk-level validity does not exist.
- ``rebuild()`` is an honest no-op (the actual backfill is the
  ``RetrievalIndexer`` / ``index rebuild``).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from seahorse.contracts.index import PITKind
from seahorse.contracts.persistence import VectorHit
from seahorse.embeddings.chunker import MAX_CHUNKS_PER_EPISODE
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.sqlite_episode_index import _pit_predicate
from seahorse.persistence.vector_index import (
    KNN_OVERFETCH_FACTOR,
    _as_bytes,
    _row_to_hit,
)

# Chunk-level over-fetch: k parents × the parent-repo factor (aux/PIT drops)
# × the write-side cap per parent (fold guarantee). See the module notes.
CHUNK_KNN_OVERFETCH_FACTOR = KNN_OVERFETCH_FACTOR * MAX_CHUNKS_PER_EPISODE


class SqliteChunkedVectorIndexRepository:
    """Chunk-level SQLite vec0 implementation of the ``VectorIndexRepository``
    Protocol (the surface stays PARENT ep_ids)."""

    def __init__(self, cm: ConnectionManager) -> None:
        self._cm = cm

    def upsert_chunks(
        self,
        parent_ep_id: str,
        vectors: list[bytes],
        *,
        dim: int,
        model_identity: str,
        content_hashes: list[str],
        embedded_at: str,
    ) -> None:
        """Replace ALL of ``parent_ep_id``'s chunks in one atomic (fold-into).

        ``chunk_id = "{parent_ep_id}#{i}"`` (deterministic, ordered);
        ``content_hashes`` carries one content hash PER CHUNK (each chunk is a
        distinct embedded text — re-indexing under a new chunker or mode
        re-embeds honestly via the cache key).
        """
        if len(vectors) != len(content_hashes):
            raise ValueError(
                f"upsert_chunks: {len(vectors)} vectors but "
                f"{len(content_hashes)} content hashes (one hash per chunk)"
            )
        blobs = [_as_bytes(v) for v in vectors]
        with self._cm.atomic() as w:
            self._delete_parent(w, parent_ep_id)
            for i, blob in enumerate(blobs):
                w.execute(
                    "INSERT INTO vec_episode_chunks "
                    "(chunk_id, embedding, parent_ep_id, fact_id, invalid_at, "
                    "cognitive_type, created_at) "
                    "VALUES (?, ?, ?, "
                    "(SELECT fact_id FROM episode_index WHERE ep_id = ?), "
                    "(SELECT invalid_at FROM episode_index WHERE ep_id = ?), "
                    "(SELECT cognitive_type FROM episode_index WHERE ep_id = ?), "
                    "(SELECT created_at FROM episode_index WHERE ep_id = ?))",
                    (
                        f"{parent_ep_id}#{i}",
                        blob,
                        parent_ep_id,
                        parent_ep_id,
                        parent_ep_id,
                        parent_ep_id,
                        parent_ep_id,
                    ),
                )
                w.execute(
                    "INSERT INTO vec_chunks_meta "
                    "(chunk_id, parent_ep_id, model_identity, content_hash, "
                    "embedded_at, dim) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        f"{parent_ep_id}#{i}",
                        parent_ep_id,
                        model_identity,
                        content_hashes[i],
                        embedded_at,
                        dim,
                    ),
                )

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
        # The signed Protocol surface: one vector = the parent's single chunk
        # ("{ep_id}#0"), fold-into by parent — coherent with upsert_chunks.
        self.upsert_chunks(
            ep_id,
            [vector],
            dim=dim,
            model_identity=model_identity,
            content_hashes=[content_hash],
            embedded_at=embedded_at,
        )

    @staticmethod
    def _delete_parent(w: Any, parent_ep_id: str) -> None:
        w.execute("DELETE FROM vec_episode_chunks WHERE parent_ep_id = ?", (parent_ep_id,))
        w.execute("DELETE FROM vec_chunks_meta WHERE parent_ep_id = ?", (parent_ep_id,))

    def distinct_model_identities(self) -> list[str]:
        with self._cm.reader() as r:
            rows = r.execute(
                "SELECT DISTINCT model_identity FROM vec_chunks_meta "
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
        # Same shape as the parent repo's knn, but the outer LIMIT is the
        # CHUNK over-fetch (the aux filters run post-kNN, pre-fold) — the
        # parent fold + re-cap to k happens in Python.
        blob = _as_bytes(query)
        overfetch = k * CHUNK_KNN_OVERFETCH_FACTOR
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
        params.append(overfetch)
        sql = (
            "SELECT v.parent_ep_id AS ep_id, v.distance, 1/(1+v.distance) AS score "
            "FROM (SELECT parent_ep_id, distance, invalid_at, fact_id, cognitive_type "
            "FROM vec_episode_chunks WHERE embedding MATCH ? LIMIT ?) v "
            f"WHERE {' AND '.join(where)} ORDER BY v.distance LIMIT ?"
        )
        with self._cm.reader() as r:
            rows = r.execute(sql, params).fetchall()
        return _fold_to_parents(rows, k)

    def knn_state_at(self, query: Any, k: int, t: datetime) -> list[VectorHit]:
        return self._knn_pit(query, k, "state_at", t)

    def knn_known_at(self, query: Any, k: int, t: datetime) -> list[VectorHit]:
        return self._knn_pit(query, k, "known_at", t)

    def _knn_pit(
        self, query: Any, k: int, pit_kind: PITKind, t: datetime
    ) -> list[VectorHit]:
        blob = _as_bytes(query)
        overfetch = k * CHUNK_KNN_OVERFETCH_FACTOR
        pred_sql, (t1, t2) = _pit_predicate(pit_kind, t)
        # The subquery MUST use the LIMIT form (not ``AND k = ?``): vec0
        # forbids projecting an auxiliary column out of a ``k = ?`` kNN — and
        # ``parent_ep_id`` IS auxiliary here (the PK is chunk_id, unlike
        # vec_episodes where ep_id is the PK). The LIMIT form allows the aux
        # projection, same as the non-PIT knn above.
        sql = (
            "SELECT c.parent_ep_id AS ep_id, c.distance, 1/(1+c.distance) AS score "
            "FROM (SELECT parent_ep_id, distance FROM vec_episode_chunks "
            "WHERE embedding MATCH ? LIMIT ?) c "
            f"JOIN episode_index ix ON ix.ep_id = c.parent_ep_id AND {pred_sql} "
            "ORDER BY c.distance LIMIT ?"
        )
        with self._cm.reader() as r:
            rows = r.execute(sql, (blob, overfetch, t1, t2, overfetch)).fetchall()
        return _fold_to_parents(rows, k)

    def remove_for_rebuild(self) -> None:
        with self._cm.atomic() as w:
            w.execute("DELETE FROM vec_episode_chunks")
            w.execute("DELETE FROM vec_chunks_meta")

    def rebuild(self) -> None:
        # Honest no-op: the signed contract takes no args; the real backfill is
        # the RetrievalIndexer / `index rebuild`.
        return None

    def count(self) -> int:
        # Parent-level count (distinct parents) — the Protocol's semantic is
        # "indexed episodes"; the raw chunk-row count is chunk_count().
        with self._cm.reader() as r:
            return r.execute(
                "SELECT count(DISTINCT parent_ep_id) FROM vec_episode_chunks"
            ).fetchone()[0]

    def chunk_count(self) -> int:
        """The raw chunk-row count (diagnostic for the chunk experiment)."""
        with self._cm.reader() as r:
            return r.execute("SELECT count(*) FROM vec_episode_chunks").fetchone()[0]


def _fold_to_parents(rows: list[Any], k: int) -> list[VectorHit]:
    """Fold distance-ordered chunk rows to PARENT hits, first-occurrence-wins.

    The re-cap to ``k`` applies AFTER the fold (on distinct parents) — never
    before: the rows are chunk-level, and capping them at ``k`` would drop
    distinct parents whose best chunks arrive after the first ``k`` rows.
    """
    seen: dict[str, VectorHit] = {}
    order: list[str] = []
    for row in rows:
        parent = row["ep_id"]
        if parent in seen:
            continue
        seen[parent] = _row_to_hit(row)
        order.append(parent)
        if len(order) >= k:
            break
    return [seen[parent] for parent in order]


def vec_chunks_wipe(conn: sqlite3.Connection) -> None:
    """Secondary-index wipe for the sidecar rebuild (the ``vec_wipe`` pattern).

    Clears the chunk table + the lateral model stamp so a vault rebuild leaves
    no ghost chunks pointing at ep_ids deleted from ``episode_index``. Runs
    inside the rebuild ``atomic()`` (clear phase).
    """
    conn.execute("DELETE FROM vec_episode_chunks")
    conn.execute("DELETE FROM vec_chunks_meta")


__all__ = [
    "SqliteChunkedVectorIndexRepository",
    "CHUNK_KNN_OVERFETCH_FACTOR",
    "vec_chunks_wipe",
]