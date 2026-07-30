"""Query-embedding seam — owned by #11, materialized by #7 (frontier pattern).

#11 Hybrid Retrieval needs a query vector to feed #6's ``knn``. The FULL #7
Embedder (f5-07 §3.1) is ``async`` + returns ``np.ndarray`` — it is NOT
materialized in the stdlib-only core, and pulling numpy/async into #11 would
violate the stdlib-only-core contract. By the established frontier pattern
(``FusedCandidate`` is owned by #11 and materialized by #8 in
``contracts.retrieval``; the 9 repository Protocols are owned by #6 in
``contracts.persistence``), this minimal query-path seam is OWNED by #11 (the
component that needs it) and will be MATERIALIZED by #7 (the component that
provides the real embedding backend). It crosses the #11 -> #7 boundary, so it
lives in ``contracts/`` — not in ``retrieval/engine.py``, which would invert the
dependency direction (#7 would have to import from #11's engine module).

The output is ``Any`` and is OPAQUE to #11: #11 never inspects or normalizes
the vector. It passes it verbatim to ``VectorIndexRepository.knn(query=..., ...)``
whose signed contract takes ``query: Any`` (f5-06 §7a.2). This keeps #11 free of
numpy/ONNX. When #7 ships its real Embedder, it adapts to this Protocol via a
sync wrapper (``role='query'``, L2-unit-normalized — the normalization contract
stays in #7, not #11). The adapter is additive: this seam does not move.

C8.4 widened the seam (audit #6: it was too thin — single sync method, no
batch, no dim/shape metadata, no composition-root slot). The Protocol now
carries:

- ``embed_query(query)`` — the single-query hot path #11 calls ONCE per recall,
  before stage 1. Unchanged behavior; still the only method #11 calls in MVP-1.
- ``embed_queries(texts)`` — the batch surface #7's real Embedder exposes
  natively (``async embed(texts, role)``). #11 does not call it today; it exists
  so #7's adapter exposes its native batch shape and so future multi-query
  recall amortizes the embedding call without widening the seam again.
- ``embedding_dim`` — the dimensionality of the vectors this embedder produces.
  #6's vector index validates the query vector's shape against it at MVP-1
  materialization (the vec0 column dimension). MVP-0 stubs return a ``0``
  sentinel; no consumer reads it until a real #7 backend is wired.

Async→sync adapter contract (#7 → this seam), documented so #7's adapter is
unambiguous when it materializes:

- #7's real Embedder is ``async embed(texts: Sequence[str], *, role) -> np.ndarray``
  (f5-07 §3.1), L2-unit-normalized for ``role='query'``.
- The adapter runs the coroutine to completion (``asyncio.run`` per call, a
  shared event loop, or ``loop.run_until_complete`` — #7's choice) and returns
  the ndarray. Normalization STAYS in #7 (not #11): the adapter hands #11 an
  already-L2-unit-normalized query vector.
- ``embed_query(query)`` dispatches to ``embed([query], role='query')[0]``;
  ``embed_queries(texts)`` dispatches to ``embed(texts, role='query')`` verbatim.
- This seam is SYNC by design: pulling ``async``/``await`` into #11 would
  violate the stdlib-only-core contract. The bridge is #7's responsibility.

References:
- f5-07 §3.1 (Embedder Protocol — async + numpy, the full contract)
- f5-06 §7a.2 (VectorIndexRepository.knn takes ``query: Any``)
- f6-signoffs.md SO-6 (#11 owns RRF + ranking; storage/embedder are separate)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class QueryEmbedder(Protocol):
    """Sync query-embedding seam for #11's hot path (widened C8.4: batch + dim).

    ``embed_query`` computes the query vector for one query string — #11 calls it
    exactly ONCE per recall, BEFORE stage 1. The return value is opaque
    (``Any``): it is whatever the downstream ``VectorIndexRepository.knn``
    accepts (numpy array, ``bytes``, tuple — #11 does not care).

    ``embed_queries`` is the batch forward-compat surface (see module docstring
    for the async→sync adapter contract). ``embedding_dim`` is the vector
    dimensionality metadata (``0`` sentinel in MVP-0; consulted at MVP-1
    materialization). #7's real Embedder is async+batch; a sync adapter
    satisfying this Protocol runs the single-query case (``role='query'``,
    L2-unit-normalized) and returns the vector in the shape #6 expects.
    """

    embedding_dim: int

    def embed_query(self, query: str) -> Any: ...

    def embed_queries(self, texts: Sequence[str]) -> Any: ...


__all__ = ["QueryEmbedder"]