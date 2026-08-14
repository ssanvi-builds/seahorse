"""SqliteEmbeddingsCacheRepository — content-hash embedding cache.

Implements ``seahorse.contracts.persistence.EmbeddingsCacheRepository`` over
``embeddings_cache``. Keyed by ``(content_hash, model_identity, role)``;
``batch_insert`` is ``INSERT OR REPLACE`` (a content-hash collision with a new
vector for the same key replaces the old — model identity change implies a new
key, so a REPLACE on the same key means re-embedding the same text with a new
vector, which is the intended backfill behavior). ``dim`` is derived from the
blob length (float32 = 4 bytes/vector element). ``trim`` keeps the newest
``max_rows`` rows by ``created_at`` (LRU). No own ``atomic()``.
"""

from __future__ import annotations

from collections.abc import Sequence

from seahorse.persistence.connection import ConnectionManager


class SqliteEmbeddingsCacheRepository:
    """SQLite implementation of the ``EmbeddingsCacheRepository`` Protocol."""

    def __init__(self, cm: ConnectionManager) -> None:
        self._cm = cm

    def batch_lookup(
        self, model_identity: str, role: str, content_hashes: Sequence[str]
    ) -> dict[str, bytes]:
        if not content_hashes:
            return {}
        placeholders = ",".join("?" * len(content_hashes))
        sql = (
            f"SELECT content_hash, vector FROM embeddings_cache "
            f"WHERE model_identity=? AND role=? AND content_hash IN ({placeholders})"
        )
        with self._cm.read() as w:
            rows = w.execute(sql, (model_identity, role, *content_hashes)).fetchall()
        return {row["content_hash"]: row["vector"] for row in rows}

    def batch_insert(
        self,
        model_identity: str,
        role: str,
        content_hashes: Sequence[str],
        vectors: Sequence[bytes],
    ) -> None:
        if len(content_hashes) != len(vectors):
            raise ValueError("content_hashes and vectors must have equal length")
        if not content_hashes:
            return
        rows = [
            (content_hash, model_identity, role, vector, len(vector) // 4)
            for content_hash, vector in zip(content_hashes, vectors, strict=True)
        ]
        with self._cm.atomic() as w:
            w.executemany(
                "INSERT OR REPLACE INTO embeddings_cache "
                "(content_hash, model_identity, role, vector, dim) VALUES (?,?,?,?,?)",
                rows,
            )

    def count(self) -> int:
        with self._cm.read() as w:
            return int(w.execute("SELECT COUNT(*) FROM embeddings_cache").fetchone()[0])

    def trim(self, max_rows: int) -> None:
        # LRU by created_at: keep the newest max_rows, delete the rest. ``rowid``
        # is the insertion-order tiebreaker (datetime('now') has 1s resolution, so
        # rows inserted within the same second need a deterministic order).
        with self._cm.atomic() as w:
            w.execute(
                "DELETE FROM embeddings_cache WHERE rowid NOT IN "
                "(SELECT rowid FROM embeddings_cache "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?)",
                (max_rows,),
            )


__all__ = ["SqliteEmbeddingsCacheRepository"]
