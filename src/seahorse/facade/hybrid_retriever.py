"""HybridRetriever — the later-release recall regime over ``seahorse.retrieval.recall``.

Materializes the ``_RetrieverLike`` extension point (the recall policy slot)
with the real hybrid engine. It serves the hybrid path when there is something
to serve (vec0/FTS data + a real embedder) and honestly degrades to the
injected listing ``VigenteListingRetriever`` otherwise (the motor keeps working
without ranking). ``supports_pit`` is True — PIT routing is hybrid retrieval's
job. Since v1.3.0 the degrade serves a caller pit too: the factory wires the
repository slice into the fallback, so ``_g2`` delegates the pit verbatim to a
PIT-capable fallback (a hybrid install with an empty index keeps serving
bi-temporal listings); the ``E_PIT_RECALL_MVP_0`` refusal remains only for a
genuinely PIT-less fallback.
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
        session_boost: bool = False,
    ) -> Sequence[FusedCandidate]:
        # Parity with the listing retriever (``k_eff = min(k, config.top_k)``):
        # the config's ``top_k`` (e.g. the MCP ``seahorse.toml``) caps the
        # hybrid path too — surfaced when the embeddings extra wired the hybrid
        # regime.
        k_eff = min(k, self._config.top_k)
        if self._can_serve():
            try:
                return self._hybrid(
                    query, pit, k_eff, cognitive_type, subject_filter, session_boost
                )
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
        session_boost: bool = False,
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
            session_boost=session_boost,
        )

    def _g2(
        self,
        query: str,
        pit: PITPoint | None,
        k: int,
        cognitive_type: str | None,
        subject_filter: str | None,
    ) -> Sequence[FusedCandidate]:
        # v1.3.0: the factory wires the repository slice into the fallback, so a
        # degrade with a caller pit serves the PIT listing instead of refusing
        # it (the same pain the listing regime had, one level up — a hybrid
        # install with an empty index). The raise remains only for a genuinely
        # PIT-less fallback (E_PIT_RECALL_MVP_0 keeps its fail-loud contract).
        if pit is not None and not getattr(self._fallback, "supports_pit", False):
            raise PitRecallNotSupportedMVP0()
        return self._fallback.recall(
            query, pit=pit, k=k, cognitive_type=cognitive_type, subject_filter=subject_filter
        )


__all__ = ["HybridRetriever"]
