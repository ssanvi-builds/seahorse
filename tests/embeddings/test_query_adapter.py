"""Sync QueryEmbedder adapter (extension point materialization).

``AsyncToSyncQueryEmbedder`` satisfies the sync ``QueryEmbedder`` extension
point (contracts/embeddings) that hybrid retrieval calls in its hot path,
bridging the async ``Embedder`` Protocol via a dedicated event loop in a daemon
thread (``asyncio.run_coroutine_threadsafe``). It returns the vector as a
float32 BLOB (the shape vec0 ``knn(query: Any)`` expects), keeping numpy out of
the core path.
"""

from __future__ import annotations

import numpy as np
import pytest

from seahorse.embeddings.query_adapter import AsyncToSyncQueryEmbedder
from seahorse.embeddings.types import ModelIdentity


class _FakeEmbedder:
    """Async Embedder double: records calls, returns deterministic vectors."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim
        self.calls: list[tuple[tuple[str, ...], str]] = []

    async def embed(self, texts, role):
        self.calls.append((tuple(texts), role))
        vecs = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i in range(len(texts)):
            vecs[i, i % self.dim] = 1.0
        return vecs

    def model_identity(self) -> ModelIdentity:
        return ModelIdentity(
            backend="test", model_name="m", revision="r",
            dim=self.dim, quantization="fp32", normalized=True,
        )


def test_adapter_satisfies_query_embedder_protocol() -> None:
    from seahorse.contracts.embeddings import QueryEmbedder

    adapter = AsyncToSyncQueryEmbedder(_FakeEmbedder())
    assert isinstance(adapter, QueryEmbedder)
    assert adapter.embedding_dim == 384


def test_embed_query_runs_single_query_embed_and_returns_float32_blob() -> None:
    embedder = _FakeEmbedder()
    adapter = AsyncToSyncQueryEmbedder(embedder)
    blob = adapter.embed_query("madrid")
    assert isinstance(blob, bytes)
    assert len(blob) == 384 * 4  # dim * float32
    assert embedder.calls == [(("madrid",), "query")]  # single text, role=query


def test_embed_queries_batch_returns_concatenated_blob() -> None:
    embedder = _FakeEmbedder()
    adapter = AsyncToSyncQueryEmbedder(embedder)
    blob = adapter.embed_queries(["madrid", "paris"])
    assert isinstance(blob, bytes)
    assert len(blob) == 2 * 384 * 4
    assert embedder.calls == [(("madrid", "paris"), "query")]  # batch, role=query


def test_embed_query_is_deterministic_for_same_query() -> None:
    adapter = AsyncToSyncQueryEmbedder(_FakeEmbedder())
    a = adapter.embed_query("madrid")
    b = adapter.embed_query("madrid")
    assert a == b


def test_similarity_returns_cosine_per_passage() -> None:
    # The two-stage session→episode seam: the embedder computes cosine
    # (query-vs-passage, passages embedded with role='passage' — the e5 role
    # prefix). The fake's vecs[i, i % dim] = 1.0 makes passage 0 identical to
    # the query (cosine 1.0) and passage 1 orthogonal (cosine 0.0).
    embedder = _FakeEmbedder()
    adapter = AsyncToSyncQueryEmbedder(embedder)
    query_blob = adapter.embed_query("madrid")
    sims = adapter.similarity(query_blob, ["same as query", "orthogonal"])
    assert len(sims) == 2
    assert sims[0] == pytest.approx(1.0)
    assert sims[1] == pytest.approx(0.0)
    # Passages are embedded with role='passage' (the e5 role prefix).
    assert embedder.calls[-1] == (("same as query", "orthogonal"), "passage")
