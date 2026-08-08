"""Retrieval indexer (M1-B.5) — write-path + backfill population of vec0/FTS.

``RetrievalIndexer`` embeds the episode body (role='passage') and upserts the
vec0 vector + the FTS5 doc in ONE ``atomic()`` (no split index). Driven by the
write path (``StubWritePath.ingest``) and by ``seahorse index rebuild``
(backfill). Best-effort (ADR-10): an embedder failure is logged and swallowed —
the episode write never fails because the index is derived.
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
from seahorse.embeddings.query_adapter import run_coroutine
from seahorse.embeddings.types import EMBED_MODES, Embedder
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.sqlite_episode_repo import SqliteEpisodeRepository

_logger = logging.getLogger("seahorse.embeddings.indexer")


class RetrievalIndexer:
    """Embed + index a single episode into vec0/FTS (best-effort).

    ``embed_mode`` selects the embedded text (f7 §5c): ``body`` (baseline) or
    ``body+summary`` (summary leads, then the body — the FTS doc is unchanged).
    The F3 flip makes ``body+summary`` the default (f7-experiment-embed §decide,
    +2.7% recall@10); ``body`` stays selectable for the F3 A/B. The content hash
    reflects the EFFECTIVE embedded text, so re-indexing under a new mode
    re-embeds (cache miss) honestly.
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
    ) -> None:
        if embed_mode not in EMBED_MODES:
            raise ValueError(
                f"embed_mode must be one of {EMBED_MODES!r}, got {embed_mode!r}"
            )
        self._embedder = embedder
        self._vector_repo = vector_repo
        self._fts_repo = fts_repo
        self._episode_repo = episode_repo
        self._cm = cm
        self._embed_mode = embed_mode

    def index_episode(self, ep_id: str) -> None:
        """Embed ``ep_id``'s body and upsert vec0 + FTS in one atomic.

        Reads the episode from the repository (write-path driver). Skips
        episodes without a non-empty body. Best-effort: an embedder failure is
        logged and swallowed (ADR-10 — the index is derived, the episode write
        already succeeded).
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
        """The effective text the passage embedder receives (f7 §5c).

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
        vecs = self._embed_safe(ep_id, text)
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

    def _embed_safe(self, ep_id: str, text: str) -> Any | None:
        try:
            return run_coroutine(self._embedder.embed([text], "passage"))
        except Exception:  # noqa: BLE001 — best-effort (ADR-10)
            _logger.warning(
                "indexer.embed_failed ep_id=%s; episode stays unindexed "
                "(derived index, best-effort)",
                ep_id,
                exc_info=True,
            )
            return None


__all__ = ["RetrievalIndexer"]
