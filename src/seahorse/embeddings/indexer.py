"""Retrieval indexer — write-path + backfill population of vec0/FTS.

``RetrievalIndexer`` embeds the episode body (role='passage') and upserts the
vec0 vector + the FTS5 doc in ONE ``atomic()`` (no split index). With
``chunk_mode='chunked'`` (P3.2 seam) the SAME effective text is split into
fixed windows and the indexer writes one embedding per CHUNK through the
chunked repository (still one atomic; FTS stays one doc per episode). Driven
by the write path (``StubWritePath.ingest``) and by ``seahorse index rebuild``
(backfill). Best-effort: an embedder failure is logged and swallowed — the
episode write never fails because the index is derived.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import numpy as np

from seahorse.contracts.persistence import (
    FtsDoc,
    FullTextIndexRepository,
    VectorIndexRepository,
)
from seahorse.embeddings.cache import _content_hash
from seahorse.embeddings.chunker import chunk_text
from seahorse.embeddings.query_adapter import run_coroutine
from seahorse.embeddings.types import CHUNK_MODES, EMBED_MODES, Embedder
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.sqlite_episode_repo import SqliteEpisodeRepository
from seahorse.persistence.vector_index_chunked import SqliteChunkedVectorIndexRepository

_logger = logging.getLogger("seahorse.embeddings.indexer")


class RetrievalIndexer:
    """Embed + index a single episode into vec0/FTS (best-effort).

    ``embed_mode`` selects the embedded text: ``body`` (baseline) or
    ``body+summary`` (summary leads, then the body — the FTS doc is unchanged).
    A retrieval experiment showed +2.7% recall@10 when the summary leads, making
    ``body+summary`` the default; ``body`` stays selectable for comparison. The
    content hash reflects the EFFECTIVE embedded text, so re-indexing under a
    new mode re-embeds (cache miss) honestly.

    ``chunk_mode`` (P3.2 seam) selects the vector surface: ``off`` (default)
    embeds the whole effective text as ONE vector per episode — bit-identical
    to the pre-seam path; ``chunked`` splits the SAME effective text with
    ``embeddings.chunker`` and writes one embedding per CHUNK through the
    chunked repository (fail-fast: the classic repo cannot carry a multi-chunk
    write — the signed Protocol has no ``upsert_chunks``). Composable with
    ``embed_mode`` by design: the chunker splits whatever the effective text
    is. FTS stays one doc per episode under both modes (documented caveat —
    chunking changes only the vector surface).
    """

    def __init__(
        self,
        embedder: Embedder,
        vector_repo: VectorIndexRepository,
        fts_repo: FullTextIndexRepository,
        episode_repo: SqliteEpisodeRepository,
        cm: ConnectionManager,
        *,
        embed_mode: str = "body+summary",
        chunk_mode: str = "off",
    ) -> None:
        if embed_mode not in EMBED_MODES:
            raise ValueError(
                f"embed_mode must be one of {EMBED_MODES!r}, got {embed_mode!r}"
            )
        if chunk_mode not in CHUNK_MODES:
            raise ValueError(
                f"chunk_mode must be one of {CHUNK_MODES!r}, got {chunk_mode!r}"
            )
        chunk_repo: SqliteChunkedVectorIndexRepository | None = None
        if chunk_mode == "chunked":
            # Fail-fast: the chunked write path needs upsert_chunks, which the
            # signed Protocol does not carry — a classic repo here would raise
            # AttributeError on the first index write, far from the cause.
            if not isinstance(vector_repo, SqliteChunkedVectorIndexRepository):
                raise ValueError(
                    "chunk_mode='chunked' requires the chunked vector repository "
                    "(SqliteChunkedVectorIndexRepository) — the signed Protocol "
                    "carries no multi-chunk write"
                )
            chunk_repo = vector_repo
        self._embedder = embedder
        self._vector_repo = vector_repo
        self._fts_repo = fts_repo
        self._episode_repo = episode_repo
        self._cm = cm
        self._embed_mode = embed_mode
        self._chunk_mode = chunk_mode
        self._chunk_repo = chunk_repo

    def index_episode(self, ep_id: str) -> None:
        """Embed ``ep_id``'s body and upsert vec0 + FTS in one atomic.

        Reads the episode from the repository (write-path driver). Skips
        episodes without a non-empty body. Best-effort: an embedder failure is
        logged and swallowed — the index is derived, the episode write already
        succeeded.
        """
        ep = self._episode_repo.get(ep_id)
        if ep is None or not ep.body or not ep.body.strip():
            return
        self._index(ep.id, ep.body, ep.title, ep.summary, ep.subject)

    def index_episode_from_note(self, ep: Any, body: str) -> None:
        """Embed a parsed vault ``Episode`` + its markdown body (backfill).

        The vault rebuild populates ``episode_index`` only (not the ``episodes``
        table) and ``parse_file`` keeps the body separate from the ``Episode``,
        so the backfill passes both explicitly instead of re-reading via
        ``episode_repo.get``.
        """
        if not body or not body.strip():
            return
        self._index(ep.id, body, ep.title, ep.summary, ep.subject)

    def _embed_text(self, body: str, summary: str | None) -> str:
        """The effective text the passage embedder receives.

        ``body+summary`` folds the summary in front (``summary\\n\\nbody``) so the
        vector captures the distilled editorial signal; a missing/blank summary
        honestly falls back to the body alone (never a fabricated text).
        """
        if self._embed_mode == "body+summary" and summary and summary.strip():
            return f"{summary.strip()}\n\n{body}"
        return body

    def _index(
        self,
        ep_id: str,
        body: str,
        title: str | None,
        summary: str | None,
        subject: str | None,
    ) -> None:
        text = self._embed_text(body, summary)
        if self._chunk_mode == "chunked":
            self._index_chunked(ep_id, text, body, title, summary, subject)
            return
        vecs = self._embed_safe(ep_id, [text])
        if vecs is None:
            return
        blob = np.asarray(vecs[0], dtype=np.float32).tobytes()
        identity = self._embedder.model_identity()
        now = datetime.now(UTC).isoformat()
        with self._cm.atomic():
            self._vector_repo.upsert(
                ep_id,
                blob,
                dim=identity.dim,
                model_identity=identity.cache_key(),
                content_hash=_content_hash(text, "passage"),
                embedded_at=now,
            )
            self._fts_repo.upsert(
                FtsDoc(
                    ep_id=ep_id,
                    body_md=body,
                    title=title,
                    summary=summary,
                    subject=subject,
                )
            )

    def _index_chunked(
        self,
        ep_id: str,
        text: str,
        body: str,
        title: str | None,
        summary: str | None,
        subject: str | None,
    ) -> None:
        """Chunked write path: one embedding per CHUNK, fold-into per parent.

        The chunker splits the SAME effective text ``embed_mode`` built (the
        seam's composability), all chunks are embedded in ONE batched call,
        and the chunked repository replaces the parent's rows atomically.
        FTS stays one doc per episode (the off-path upsert, unchanged).
        """
        chunk_repo = self._chunk_repo
        if chunk_repo is None:  # unreachable: construction-guarded
            return
        chunks = chunk_text(text)
        if not chunks:  # blank effective text — unreachable via the body guards
            return
        vecs = self._embed_safe(ep_id, chunks)
        if vecs is None:
            return
        if len(vecs) != len(chunks):
            # Embedder contract violation (one vector per input) — treat as an
            # embedder failure: best-effort skip, the episode write never fails.
            _logger.warning(
                "indexer.embed_count_mismatch ep_id=%s: %s vectors for %s chunks; "
                "episode stays unindexed (derived index, best-effort)",
                ep_id,
                len(vecs),
                len(chunks),
            )
            return
        identity = self._embedder.model_identity()
        blobs = [np.asarray(v, dtype=np.float32).tobytes() for v in vecs]
        now = datetime.now(UTC).isoformat()
        with self._cm.atomic():
            chunk_repo.upsert_chunks(
                ep_id,
                blobs,
                dim=identity.dim,
                model_identity=identity.cache_key(),
                content_hashes=[_content_hash(c, "passage") for c in chunks],
                embedded_at=now,
            )
            self._fts_repo.upsert(
                FtsDoc(
                    ep_id=ep_id,
                    body_md=body,
                    title=title,
                    summary=summary,
                    subject=subject,
                )
            )

    def _embed_safe(self, ep_id: str, texts: list[str]) -> Any | None:
        try:
            return run_coroutine(self._embedder.embed(texts, "passage"))
        except Exception:  # noqa: BLE001 — best-effort (index is derived)
            _logger.warning(
                "indexer.embed_failed ep_id=%s; episode stays unindexed "
                "(derived index, best-effort)",
                ep_id,
                exc_info=True,
            )
            return None


__all__ = ["RetrievalIndexer"]
