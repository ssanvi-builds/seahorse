"""HybridRetriever — the later-release recall regime over ``seahorse.retrieval.recall``.

Materializes the ``_RetrieverLike`` extension point (the recall policy slot)
with the real hybrid engine. It serves the hybrid path when there is something
to serve (vec0/FTS data + a real embedder) and honestly degrades to the
injected listing ``VigenteListingRetriever`` otherwise (the motor keeps working
without ranking). ``supports_pit`` is True — PIT routing is hybrid retrieval's
job; if the degrade path receives a pit it refuses (the listing regime has no
PIT axis).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime

from seahorse.contracts.embeddings import QueryEmbedder
from seahorse.contracts.engine import EpisodeRepository
from seahorse.contracts.persistence import (
    EpisodeIndexRepository,
    FullTextIndexRepository,
    VectorIndexRepository,
)
from seahorse.contracts.rerank import QueryReranker
from seahorse.contracts.retrieval import FusedCandidate
from seahorse.disclosure.types import TOP_K, PITPoint
from seahorse.facade.errors import PitRecallNotSupportedMVP0
from seahorse.facade.types import FacadeConfig
from seahorse.facade.vigente_retriever import VigenteListingRetriever
from seahorse.retrieval.decay import DecayConfig
from seahorse.retrieval.recency import RecencyConfig

_logger = logging.getLogger("seahorse.facade.hybrid_retriever")


class HybridRetriever:
    """Later-release ``_RetrieverLike`` over ``seahorse.retrieval.recall``."""

    supports_pit = True

    def __init__(
        self,
        *,
        embedder: QueryEmbedder,
        vector_repo: VectorIndexRepository,
        fts_repo: FullTextIndexRepository,
        episode_repo: EpisodeRepository,
        index_repo: EpisodeIndexRepository | None,
        clock: Callable[[], datetime],
        config: FacadeConfig,
        fallback: VigenteListingRetriever,
        recency: RecencyConfig | None = None,
        decay: DecayConfig | None = None,
        reranker: QueryReranker | None = None,
    ) -> None:
        self._embedder = embedder
        self._vector_repo = vector_repo
        self._fts_repo = fts_repo
        self._episode_repo = episode_repo
        self._index_repo = index_repo
        self._clock = clock
        self._config = config
        self._fallback = fallback
        # Recency (default-OFF): None keeps the pure-RRF fingerprint.
        self._recency = recency
        # Decay (Sprint D, default-OFF): None keeps the pure-RRF fingerprint.
        self._decay = decay
        # Rerank (default-OFF): None keeps the pure-RRF fingerprint.
        # The composition root wires the cross-encoder here (single-point swap).
        self._reranker = reranker

    def recall(
        self,
        query: str,
        *,
        pit: PITPoint | None = None,
        k: int = TOP_K,
        cognitive_type: str | None = None,
        subject_filter: str | None = None,
    ) -> Sequence[FusedCandidate]:
        # Parity with the listing retriever (``k_eff = min(k, config.top_k)``):
        # the config's ``top_k`` (e.g. the MCP ``seahorse.toml``) caps the
        # hybrid path too — surfaced when the embeddings extra wired the hybrid
        # regime.
        k_eff = min(k, self._config.top_k)
        if self._can_serve():
            try:
                return self._hybrid(query, pit, k_eff, cognitive_type, subject_filter)
            except PitRecallNotSupportedMVP0:
                raise
            except Exception:
                # Honest degrade: the hybrid path could not serve
                # (embedder/runtime error); fall back to the current-state listing.
                _logger.warning(
                    "hybrid recall degraded to the current-state listing (query=%r)",
                    query,
                    exc_info=True,
                )
                return self._g2(query, pit, k_eff, cognitive_type, subject_filter)
        return self._g2(query, pit, k_eff, cognitive_type, subject_filter)

    def _can_serve(self) -> bool:
        try:
            if self._embedder.embedding_dim <= 0:
                return False  # StubQueryEmbedder sentinel (not wired)
            if self._vector_repo.count() + self._fts_repo.count() == 0:
                return False
        except Exception:  # noqa: BLE001 — a broken repo/embedder is an honest no
            return False
        return True

    def _hybrid(
        self,
        query: str,
        pit: PITPoint | None,
        k: int,
        cognitive_type: str | None,
        subject_filter: str | None,
    ) -> Sequence[FusedCandidate]:
        from seahorse.retrieval.engine import recall  # lazy (import-laziness)

        return recall(
            query,
            pit=pit,
            embedder=self._embedder,
            vector_repo=self._vector_repo,
            fts_repo=self._fts_repo,
            episode_repo=self._episode_repo,
            index_repo=self._index_repo,  # episode_index repo (batch created_at)
            k=k,
            cognitive_type=cognitive_type,
            subject_filter=subject_filter,
            anchor_ep_id=None,
            clock=self._clock,
            recency=self._recency,
            decay=self._decay,
            reranker=self._reranker,
        )

    def _g2(
        self,
        query: str,
        pit: PITPoint | None,
        k: int,
        cognitive_type: str | None,
        subject_filter: str | None,
    ) -> Sequence[FusedCandidate]:
        if pit is not None:
            raise PitRecallNotSupportedMVP0()  # the listing regime has no PIT axis
        return self._fallback.recall(
            query, pit=None, k=k, cognitive_type=cognitive_type, subject_filter=subject_filter
        )


__all__ = ["HybridRetriever"]
