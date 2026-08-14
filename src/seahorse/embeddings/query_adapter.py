"""Sync ``QueryEmbedder`` adapter over the async ``Embedder`` Protocol.

Bridges the async ``Embedder`` Protocol to the sync ``QueryEmbedder`` extension
point that the retrieval engine calls once per recall. Uses a dedicated event
loop in a daemon thread (singleton) + ``asyncio.run_coroutine_threadsafe`` —
NOT ``asyncio.run`` per call, which would re-create a loop each time and fail
when invoked from inside an existing loop.

Returns vectors as float32 BLOBs (``np.float32.tobytes()``) — the shape vec0
``knn(query: Any)`` expects, so numpy stays out of the core retrieval path
(the extension-point output is opaque ``Any``).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence
from typing import Any

import numpy as np

from seahorse.embeddings.types import Embedder

_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_THREAD: threading.Thread | None = None
_LOOP_LOCK = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    """Process-wide singleton loop thread (daemon) for the async→sync bridge."""
    global _LOOP, _LOOP_THREAD
    with _LOOP_LOCK:
        if _LOOP is None:
            _LOOP = asyncio.new_event_loop()
            _LOOP_THREAD = threading.Thread(target=_LOOP.run_forever, daemon=True)
            _LOOP_THREAD.start()
        return _LOOP


def run_coroutine(coro: Any) -> Any:
    """Run an async coroutine to completion from sync code (shared bridge loop).

    Used by the RetrievalIndexer to embed passages with the async ``Embedder``
    Protocol from the sync write path.
    """
    return asyncio.run_coroutine_threadsafe(coro, _get_loop()).result()


class AsyncToSyncQueryEmbedder:
    """Sync ``QueryEmbedder`` over an async ``Embedder``."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._loop = _get_loop()

    @property
    def embedding_dim(self) -> int:
        return self._embedder.dim

    def embed_query(self, query: str) -> Any:
        vecs = self._run(self._embedder.embed([query], "query"))
        return np.asarray(vecs[0], dtype=np.float32).tobytes()

    def embed_queries(self, texts: Sequence[str]) -> Any:
        vecs = self._run(self._embedder.embed(texts, "query"))
        return np.asarray(vecs, dtype=np.float32).tobytes()

    def _run(self, coro: Any) -> np.ndarray:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()


__all__ = ["AsyncToSyncQueryEmbedder"]
