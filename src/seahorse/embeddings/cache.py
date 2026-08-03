"""Query cache (M1-B.4, f5-07 §3.5).

``CachedQueryEmbedder`` wraps a sync ``QueryEmbedder`` with a two-level cache:
an in-memory LRU (cap ``_LRU_CAP``) and the SQLite ``EmbeddingsCacheRepository``
(migration 007, owned by #6). The cache key is
``(model_identity.cache_key(), role, content_hash)`` where ``content_hash`` is
the SHA-256 of the normalized text + role — repeated recall queries re-embed
once, and cached vectors survive process restarts.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Sequence
from typing import Any

from seahorse.contracts.embeddings import QueryEmbedder
from seahorse.contracts.persistence import EmbeddingsCacheRepository
from seahorse.embeddings.types import ModelIdentity

_LRU_CAP = 4096


def _content_hash(text: str, role: str) -> str:
    """SHA-256 of the normalized text + role (f5-07 §3.5)."""
    return hashlib.sha256(
        (" ".join(text.strip().split()) + "|" + role).encode("utf-8")
    ).hexdigest()


class CachedQueryEmbedder:
    """Sync ``QueryEmbedder`` with LRU + SQLite content-hash caching."""

    def __init__(
        self,
        inner: QueryEmbedder,
        cache_repo: EmbeddingsCacheRepository,
        model_identity: ModelIdentity,
    ) -> None:
        self._inner = inner
        self._cache_repo = cache_repo
        self._identity = model_identity
        self._lru: OrderedDict[str, bytes] = OrderedDict()

    @property
    def embedding_dim(self) -> int:
        return self._inner.embedding_dim

    def embed_query(self, query: str) -> Any:
        h = _content_hash(query, "query")
        hit = self._cache_get(h)
        if hit is not None:
            return hit
        blob = self._inner.embed_query(query)
        self._cache_put(h, blob)
        return blob

    def embed_queries(self, texts: Sequence[str]) -> Any:
        hashes = [_content_hash(t, "query") for t in texts]
        found = self._cache_repo.batch_lookup(
            self._identity.cache_key(), "query", hashes
        )
        blobs: dict[int, bytes] = {}
        missing: list[int] = []
        for i, h in enumerate(hashes):
            cached = self._lru_get(h)
            if cached is not None:
                blobs[i] = cached
            elif h in found:
                blobs[i] = found[h]
                self._lru_set(h, found[h])
            else:
                missing.append(i)
        if missing:
            raw = self._inner.embed_queries([texts[i] for i in missing])
            vec_size = self.embedding_dim * 4
            for j, i in enumerate(missing):
                piece = raw[j * vec_size : (j + 1) * vec_size]
                blobs[i] = piece
                self._cache_put(hashes[i], piece)
        return b"".join(blobs[i] for i in range(len(texts)))

    # -- cache internals -----------------------------------------------------

    def _lru_key(self, content_hash: str) -> str:
        return f"{self._identity.cache_key()}|{content_hash}"

    def _lru_get(self, content_hash: str) -> bytes | None:
        key = self._lru_key(content_hash)
        if key not in self._lru:
            return None
        self._lru.move_to_end(key)
        return self._lru[key]

    def _lru_set(self, content_hash: str, blob: bytes) -> None:
        key = self._lru_key(content_hash)
        self._lru[key] = blob
        self._lru.move_to_end(key)
        while len(self._lru) > _LRU_CAP:
            self._lru.popitem(last=False)

    def _cache_get(self, content_hash: str) -> bytes | None:
        hit = self._lru_get(content_hash)
        if hit is not None:
            return hit
        found = self._cache_repo.batch_lookup(
            self._identity.cache_key(), "query", [content_hash]
        )
        if content_hash not in found:
            return None
        self._lru_set(content_hash, found[content_hash])
        return found[content_hash]

    def _cache_put(self, content_hash: str, blob: bytes) -> None:
        self._lru_set(content_hash, blob)
        self._cache_repo.batch_insert(
            self._identity.cache_key(), "query", [content_hash], [blob]
        )


__all__ = ["CachedQueryEmbedder", "_content_hash", "_LRU_CAP"]
