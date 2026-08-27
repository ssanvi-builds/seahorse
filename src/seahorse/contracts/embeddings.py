"""Query-embedding seam — owned by hybrid retrieval, materialized by the embedder.

Hybrid Retrieval needs a query vector to feed the vector index's ``knn``. The
FULL embedder is ``async`` + returns ``np.ndarray`` — it is NOT materialized in
the stdlib-only core, and pulling numpy/async into hybrid retrieval would
violate the stdlib-only-core contract. By the established frontier pattern
(``FusedCandidate`` is owned by hybrid retrieval and materialized by progressive
disclosure in ``contracts.retrieval``; the 9 repository Protocols are owned by
the persistence layer in ``contracts.persistence``), this minimal query-path
seam is OWNED by hybrid retrieval (the component that needs it) and will be
MATERIALIZED by the embedder (the component that provides the real embedding
backend). It crosses the hybrid-retrieval -> embedder boundary, so it lives in
``contracts/`` — not in ``retrieval/engine.py``, which would invert the
dependency direction (the embedder would have to import from hybrid retrieval's
engine module).

The output is ``Any`` and is OPAQUE to hybrid retrieval: it never inspects or
normalizes the vector. It passes it verbatim to
``VectorIndexRepository.knn(query=..., ...)`` whose signed contract takes
``query: Any``. This keeps hybrid retrieval free of numpy/ONNX. When the
embedder ships its real implementation, it adapts to this Protocol via a sync
wrapper (``role='query'``, L2-unit-normalized — the normalization contract stays
in the embedder). The adapter is additive: this seam does not move.

The seam was widened after an audit (it was too thin — single sync method, no
batch, no dim/shape metadata, no composition-root slot). The Protocol now
carries:

- ``embed_query(query)`` — the single-query hot path called ONCE per recall,
  before stage 1. Unchanged behavior; still the only method called in the
  current release.
- ``embed_queries(texts)`` — the batch surface the real embedder exposes
  natively (``async embed(texts, role)``). Not called today; it exists so the
  embedder's adapter exposes its native batch shape and so future multi-query
  recall amortizes the embedding call without widening the seam again.
- ``embedding_dim`` — the dimensionality of the vectors this embedder produces.
  The vector index validates the query vector's shape against it at
  materialization (the vec0 column dimension). Current stubs return a ``0``
  sentinel; no consumer reads it until a real embedder backend is wired.

Async→sync adapter contract (the embedder -> this seam), documented so the
embedder's adapter is unambiguous when it materializes:

- The real embedder is ``async embed(texts: Sequence[str], *, role) -> np.ndarray``,
  L2-unit-normalized for ``role='query'``.
- The adapter runs the coroutine to completion (``asyncio.run`` per call, a
  shared event loop, or ``loop.run_until_complete`` — the embedder's choice) and
  returns the ndarray. Normalization STAYS in the embedder: the adapter hands
  hybrid retrieval an already-L2-unit-normalized query vector.
- ``embed_query(query)`` dispatches to ``embed([query], role='query')[0]``;
  ``embed_queries(texts)`` dispatches to ``embed(texts, role='query')`` verbatim.
- This seam is SYNC by design: pulling ``async``/``await`` into hybrid retrieval
  would violate the stdlib-only-core contract. The bridge is the embedder's
  responsibility.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class QueryEmbedder(Protocol):
    """Sync query-embedding seam for the hybrid retrieval hot path.

    ``embed_query`` computes the query vector for one query string — hybrid
    retrieval calls it exactly ONCE per recall, BEFORE stage 1. The return value
    is opaque (``Any``): it is whatever the downstream
    ``VectorIndexRepository.knn`` accepts (numpy array, ``bytes``, tuple — hybrid
    retrieval does not care).

    ``embed_queries`` is the batch forward-compat surface (see module docstring
    for the async→sync adapter contract). ``embedding_dim`` is the vector
    dimensionality metadata (``0`` sentinel in the current release; consulted
    at a later materialization). The real embedder is async+batch; a sync
    adapter satisfying this Protocol runs the single-query case
    (``role='query'``, L2-unit-normalized) and returns the vector in the shape
    the vector index expects.

    ``similarity`` is the two-stage session→episode seam: the engine is
    stdlib-only and never decodes the opaque vectors, so the embedder (which
    knows the vector format) computes the query-vs-passage cosine similarities.
    ``query_vec`` is the opaque vector from ``embed_query``; ``passages`` are
    embedded with ``role='passage'`` (the e5 role prefix — the same semantics
    the two-stage experiment's within-session re-rank uses). Returns one cosine
    per passage, in order.
    """

    embedding_dim: int

    def embed_query(self, query: str) -> Any: ...

    def embed_queries(self, texts: Sequence[str]) -> Any: ...

    def similarity(self, query_vec: Any, passages: Sequence[str]) -> Sequence[float]: ...


__all__ = ["QueryEmbedder"]