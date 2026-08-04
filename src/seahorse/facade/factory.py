"""Composition root for the real MVP-0 stack (``build_facade``).

#12 ships the facade + primitives but leaves wiring to the caller. #13 (MCP
server startup) and #14 (CLI bootstrap) both need a single, testable seam that
builds a real ``MemoryFacade`` over real ``Storage`` (SQLite) + real
``BiTemporalEngine`` + real ``DisclosureShaperImpl`` + real ``StubWritePath``
with an injectable clock (ADR-10 reproducibility — one shared seam, the same
clock drives the engine and the facade).

This is additive: it does not change #12's surface. It is the function
f5-13/f5-14 reference as ``build_facade`` of #12.

The caller owns the ``Storage`` lifecycle (it must ``close()`` it to release
the SQLite connection pool); ``build_facade`` does not close on the caller's
behalf. Tests use ``tmp_path`` and a context-manager pattern.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from seahorse.contracts.embeddings import QueryEmbedder
from seahorse.disclosure.shaper import DisclosureShaperImpl
from seahorse.engine.engine import BiTemporalEngine
from seahorse.facade.facade import MemoryFacade
from seahorse.facade.types import FacadeConfig
from seahorse.facade.vigente_retriever import VigenteListingRetriever
from seahorse.llm import LLMClient
from seahorse.persistence.storage import Storage
from seahorse.write_path.stub import StubWritePath


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
) -> tuple[MemoryFacade, Storage]:
    """Build a real ``MemoryFacade`` over SQLite + #2 + #8 + #5-stub.

    Returns ``(facade, storage)`` so the caller can ``storage.close()`` when
    done (the server keeps the storage open for the process lifetime; tests
    close it in a fixture teardown). Pass an existing ``storage`` to reuse a
    connection pool — otherwise one is created from ``db_path``.

    The recall regime is wired at this composition root (C8.1 seam). The MVP-1
    hybrid regime (``HybridRetriever`` over ``seahorse.retrieval.recall`` +
    the write-path indexer) is wired when ``retrieval_available`` resolves True:
    an injected ``embedder``, or the ``embeddings`` extra importable. Otherwise
    the honest G2 regime (``VigenteListingRetriever``, no ranking/PIT, and the
    vector/FTS repos are NEVER touched) is the default — ``uv sync --extra dev``
    stays G2/offline. ``retrieval_available`` overrides the auto-resolution
    (False forces G2; True forces the hybrid wiring, used by tests). The same
    ``clock`` drives the engine, retriever, and facade (ADR-10).

    The ``embedder`` slot (C8.4 seam) defaults to ``StubQueryEmbedder`` in the
    G2 regime (inert, ``E_NOT_IN_MVP_0`` on invocation); in the hybrid regime it
    is the sync query adapter the retriever calls.

    The ``llm_client`` slot (M4-C.3 seam) is the write-path LLM extractor. When
    None (the default) the write path keeps its MVP-0 honest llm→skip degrade;
    the CLI builds a real ``LiteLLMBackend`` from the ``[llm]`` config
    (``seahorse init --llm``) and passes it here. ``MemoryFacade`` does not
    change — the client is a write-path concern.
    """
    own_storage = storage if storage is not None else Storage(db_path)
    engine = BiTemporalEngine(repo=own_storage.episodes, audit=own_storage.audit)
    shaper = DisclosureShaperImpl(
        index_repo=own_storage.episode_index, episode_repo=own_storage.episodes
    )
    clk = clock or _default_clock
    cfg = config or FacadeConfig()
    retriever: Any  # HybridRetriever (retrieval) or VigenteListingRetriever (G2)
    retrieval = _resolve_retrieval(embedder, retrieval_available)
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
            graph_repo=own_storage.episode_index,
            clock=clk,
            config=cfg,
            fallback=fallback,
        )
        indexer = RetrievalIndexer(
            passage_embedder,
            vector,
            fts,
            own_storage.episodes,
            own_storage._cm,  # noqa: SLF001 — composition root owns Storage
        )
        write_path = StubWritePath(engine=engine, indexer=indexer, llm_client=llm_client)
        facade_embedder: QueryEmbedder | None = query_embedder
    else:
        retriever = VigenteListingRetriever(engine=engine, clock=clk, config=cfg)
        write_path = StubWritePath(engine=engine, llm_client=llm_client)
        facade_embedder = embedder
    facade = MemoryFacade(
        engine=engine,
        write_path=write_path,
        shaper=shaper,
        retriever=retriever,
        clock=clk,
        config=cfg,
        embedder=facade_embedder,
    )
    return facade, own_storage


def _resolve_retrieval(
    embedder: QueryEmbedder | None, retrieval_available: bool | None
) -> tuple[Any, Any] | None:
    """Resolve the hybrid regime wiring, or None (honest G2).

    Returns ``(query_embedder, passage_embedder)``: the sync query seam the
    retriever calls (the injected one, or an adapter over the async #7
    embedder) plus the async passage embedder for the write-path indexer.
    """
    if retrieval_available is False:
        return None
    if os.environ.get("SEAHORSE_EMBEDDING_BACKEND") == "stub":
        return None
    passage = _build_passage_embedder()
    if passage is None:
        return None  # the 'embeddings' extra is not installed -> G2
    from seahorse.embeddings.query_adapter import AsyncToSyncQueryEmbedder  # lazy

    query = embedder if embedder is not None else AsyncToSyncQueryEmbedder(passage)
    return query, passage


def _build_passage_embedder() -> Any | None:
    """Build the async #7 embedder if the ``embeddings`` extra is present."""
    try:
        from seahorse.embeddings.fastembed_backend import build_fastembed_embedder

        return build_fastembed_embedder()
    except Exception:  # noqa: BLE001 — embedder absence is an honest G2
        return None


__all__ = ["build_facade"]