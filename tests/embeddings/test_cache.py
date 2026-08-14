"""Query cache.

``CachedQueryEmbedder`` wraps a sync ``QueryEmbedder`` with an in-memory LRU
(cap 4096) + the SQLite ``EmbeddingsCacheRepository`` (migration 007). Key:
``(model_identity.cache_key(), role, content_hash)``. The second call with the
same normalized text must NOT re-embed (LRU) and must survive across instances
(SQLite).
"""

from __future__ import annotations

import hashlib

import pytest

from seahorse.embeddings.cache import CachedQueryEmbedder, _content_hash
from seahorse.embeddings.types import ModelIdentity
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_embeddings_cache import (
    SqliteEmbeddingsCacheRepository,
)


@pytest.fixture()
def cache_repo(tmp_path) -> SqliteEmbeddingsCacheRepository:
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=2, extensions=("vec0",))
    mgr.open()
    apply_migrations(mgr.writer)
    yield SqliteEmbeddingsCacheRepository(mgr)
    mgr.close()


def _identity() -> ModelIdentity:
    return ModelIdentity(
        backend="fastembed",
        model_name="intfloat/multilingual-e5-small",
        revision="a1b2c3d4e5f6",
        dim=384,
        quantization="fp32",
        normalized=True,
    )


class _FakeInner:
    """Sync QueryEmbedder double: returns a deterministic blob, counts calls."""

    embedding_dim = 384

    def __init__(self) -> None:
        self.embed_query_calls = 0

    def embed_query(self, query: str) -> bytes:
        self.embed_query_calls += 1
        return (query.encode() * 384)[: 384 * 4]

    def embed_queries(self, texts) -> bytes:
        return b"".join(self.embed_query(t) for t in texts)


def test_second_call_same_query_hits_lru_without_reembed(cache_repo) -> None:
    inner = _FakeInner()
    cached = CachedQueryEmbedder(inner, cache_repo, _identity())
    a = cached.embed_query("  Madrid  city ")
    b = cached.embed_query(" Madrid city ")  # same normalized text
    assert a == b
    assert inner.embed_query_calls == 1  # 2nd call served by LRU


def test_cache_persists_across_instances_via_sqlite(cache_repo) -> None:
    CachedQueryEmbedder(_FakeInner(), cache_repo, _identity()).embed_query("madrid")
    inner2 = _FakeInner()
    cached2 = CachedQueryEmbedder(inner2, cache_repo, _identity())
    blob = cached2.embed_query("madrid")
    assert inner2.embed_query_calls == 0  # SQLite hit
    assert len(blob) == 384 * 4


def test_lru_trims_to_cap(cache_repo) -> None:
    from seahorse.embeddings.cache import _LRU_CAP

    inner = _FakeInner()
    cached = CachedQueryEmbedder(inner, cache_repo, _identity())
    for i in range(_LRU_CAP + 20):
        cached.embed_query(f"query-{i}")
    assert len(cached._lru) <= _LRU_CAP  # noqa: SLF001 — LRU trimmed


def test_content_hash_normalizes_whitespace_and_roles() -> None:
    assert _content_hash("  Madrid  city ", "query") == hashlib.sha256(
        b"Madrid city|query"
    ).hexdigest()
    assert _content_hash("Madrid city", "passage") == hashlib.sha256(
        b"Madrid city|passage"
    ).hexdigest()
