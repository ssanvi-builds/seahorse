"""Composition root for the real current-release stack (``build_facade``).

The facade ships the primitives but leaves wiring to the caller. The MCP server
startup and the CLI bootstrap both need a single, testable extension point that
builds a real ``MemoryFacade`` over real ``Storage`` (SQLite) + real
``BiTemporalEngine`` + real ``DisclosureShaperImpl`` + real ``StubWritePath``
with an injectable clock (reproducibility — one shared seam, the same clock
drives the engine and the facade).

This is additive: it does not change the facade's surface.

The caller owns the ``Storage`` lifecycle (it must ``close()`` it to release
the SQLite connection pool); ``build_facade`` does not close on the caller's
behalf. Tests use ``tmp_path`` and a context-manager pattern.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from seahorse.contracts.embeddings import QueryEmbedder
from seahorse.contracts.rerank import QueryReranker
from seahorse.disclosure.shaper import DisclosureShaperImpl
from seahorse.embeddings.types import EMBED_MODES
from seahorse.engine.engine import BiTemporalEngine
from seahorse.facade.facade import MemoryFacade
from seahorse.facade.types import FacadeConfig
from seahorse.facade.vigente_retriever import VigenteListingRetriever
from seahorse.llm import LLMClient
from seahorse.persistence.storage import Storage
from seahorse.write_path.stub import StubWritePath

if TYPE_CHECKING:
    from seahorse.retrieval.recency import RecencyConfig


def _default_clock() -> datetime:
    return datetime.now(UTC)


def build_facade(
    db_path: Path | str,
    *,
    clock: Callable[[], datetime] | None = None,
    config: FacadeConfig | None = None,
    storage: Storage | None = None,
    embedder: QueryEmbedder | None = None,
    retrieval_available: bool | None = None,
    llm_client: LLMClient | None = None,
    recency: RecencyConfig | None = None,
    embed_mode: str = "body+summary",
    passage_embedder: Any | None = None,
    reranker: QueryReranker | None = None,
) -> tuple[MemoryFacade, Storage]:
    """Build a real ``MemoryFacade`` over SQLite + the engine + the shaper + the write-path stub.

    Returns ``(facade, storage)`` so the caller can ``storage.close()`` when
    done (the server keeps the storage open for the process lifetime; tests
    close it in a fixture teardown). Pass an existing ``storage`` to reuse a
    connection pool — otherwise one is created from ``db_path``.

    The recall regime is wired at this composition root. The hybrid regime
    (``HybridRetriever`` over ``seahorse.retrieval.recall`` + the write-path
    indexer) is wired when ``retrieval_available`` resolves True: an injected
    ``embedder``, or the ``embeddings`` extra importable. Otherwise the honest
    listing regime (``VigenteListingRetriever``, no ranking/PIT, and the
    vector/FTS repos are NEVER touched) is the default — ``uv sync --extra dev``
    stays offline. ``retrieval_available`` overrides the auto-resolution (False
    forces the listing; True forces the hybrid wiring, used by tests). The same
    ``clock`` drives the engine, retriever, and facade.

    The ``embedder`` slot defaults to ``StubQueryEmbedder`` in the listing
    regime (inert, ``E_NOT_IN_MVP_0`` on invocation); in the hybrid regime it is
    the sync query adapter the retriever calls.

    The ``llm_client`` slot is the write-path LLM extractor. When None (the
    default) the write path keeps its honest llm→skip degrade; the CLI builds a
    real ``LiteLLMBackend`` from the ``[llm]`` config (``seahorse init --llm``)
    and passes it here. ``MemoryFacade`` does not change — the client is a
    write-path concern.

    The ``recency`` slot is the recency configuration passed through to the
    ``HybridRetriever`` (composition root, single-point swap). The default
    ``None`` keeps the pure-RRF bit-comparable fingerprint; the benchmark SUT
    and CLI wire it to run the recency A/B + sweep experiment.

    The ``embed_mode`` slot selects the passage text the write-path indexer
    embeds. Default ``body+summary`` (summary leads the vector, +2.7%
    recall@10); ``body`` remains selectable for the A/B. Validated at the
    boundary (fail-fast); propagated to the ``RetrievalIndexer`` (single-point
    swap for the reindex experiment).

    The ``passage_embedder`` slot overrides the auto-resolved fastembed backend
    with a deterministic embedder (the synthetic mechanical verification in CI).
    When None, ``_build_passage_embedder`` resolves the real backend exactly as
    before.

    The ``reranker`` slot is the cross-encoder passed through to the
    ``HybridRetriever`` (composition root, single-point swap). The default
    ``None`` keeps the pure-RRF bit-comparable fingerprint; the benchmark SUT
    and CLI wire it to run the rerank A/B experiment. Query-time pure: wiring a
    reranker never requires a reindex.
    """
    if embed_mode not in EMBED_MODES:
        raise ValueError(
            f"embed_mode must be one of {EMBED_MODES!r}, got {embed_mode!r}"
        )
    own_storage = storage if storage is not None else Storage(db_path)
    engine = BiTemporalEngine(repo=own_storage.episodes, audit=own_storage.audit)
    shaper = DisclosureShaperImpl(
        index_repo=own_storage.episode_index, episode_repo=own_storage.episodes
    )
    clk = clock or _default_clock
    cfg = config or FacadeConfig()
    retriever: Any  # HybridRetriever (retrieval) or VigenteListingRetriever (listing)
    retrieval = _resolve_retrieval(embedder, retrieval_available, passage_embedder)
    if retrieval is not None:
        query_embedder, passage_embedder = retrieval
        from seahorse.embeddings.indexer import RetrievalIndexer  # lazy: numpy
        from seahorse.facade.hybrid_retriever import HybridRetriever

        vector = own_storage.vector  # lazy import: vec0 repo (sqlite-vec)
        fts = own_storage.fts  # lazy import: FTS repo
        fallback = VigenteListingRetriever(engine=engine, clock=clk, config=cfg)
        retriever = HybridRetriever(
            embedder=query_embedder,
            vector_repo=vector,
            fts_repo=fts,
            episode_repo=own_storage.episodes,
            index_repo=own_storage.episode_index,
            clock=clk,
            config=cfg,
            fallback=fallback,
            recency=recency,
            reranker=reranker,
        )
        indexer = RetrievalIndexer(
            passage_embedder,
            vector,
            fts,
            own_storage.episodes,
            own_storage.connection_manager,
            embed_mode=embed_mode,
        )
        write_path = StubWritePath(engine=engine, indexer=indexer, llm_client=llm_client)
        facade_embedder: QueryEmbedder | None = query_embedder
    else:
        retriever = VigenteListingRetriever(engine=engine, clock=clk, config=cfg)
        write_path = StubWritePath(engine=engine, llm_client=llm_client)
        facade_embedder = embedder
    # The hybrid composition root indexes the successors of writes that bypass
    # the write path (improve + distill). Without this hook the new versions
    # never reach vec0/FTS. The listing regime wires nothing.
    on_indexed: Callable[[str], None] | None = None
    if retrieval is not None:
        on_indexed = indexer.index_episode
    facade = MemoryFacade(
        engine=engine,
        write_path=write_path,
        shaper=shaper,
        retriever=retriever,
        clock=clk,
        config=cfg,
        embedder=facade_embedder,
        on_episode_indexed=on_indexed,
    )
    return facade, own_storage


def _resolve_retrieval(
    embedder: QueryEmbedder | None,
    retrieval_available: bool | None,
    passage_embedder: Any | None = None,
) -> tuple[Any, Any] | None:
    """Resolve the hybrid regime wiring, or None (honest listing).

    Returns ``(query_embedder, passage_embedder)``: the sync query seam the
    retriever calls (the injected one, or an adapter over the async embedder)
    plus the async passage embedder for the write-path indexer. An explicit
    ``passage_embedder`` override (deterministic synthetic embedder) replaces
    the auto-resolved fastembed backend.
    """
    if retrieval_available is False:
        return None
    if os.environ.get("SEAHORSE_EMBEDDING_BACKEND") == "stub":
        return None
    passage = passage_embedder if passage_embedder is not None else _build_passage_embedder()
    if passage is None:
        return None  # the 'embeddings' extra is not installed -> listing
    from seahorse.embeddings.query_adapter import AsyncToSyncQueryEmbedder  # lazy

    query = embedder if embedder is not None else AsyncToSyncQueryEmbedder(passage)
    return query, passage


def _build_passage_embedder() -> Any | None:
    """Build the async embedder if the ``embeddings`` extra is present.

    ``ImportError`` (the extra not installed) → ``None`` (honest listing
    regime). Any OTHER failure — a broken model bundle, a fastembed version
    break — is a REAL error and fails loud: swallowing it would silently degrade
    retrieval to the listing regime with zero observability.
    """
    try:
        from seahorse.embeddings.fastembed_backend import build_fastembed_embedder

        return build_fastembed_embedder()
    except ImportError:
        return None


__all__ = ["build_facade"]