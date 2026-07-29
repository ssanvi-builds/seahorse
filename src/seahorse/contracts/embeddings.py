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

References:
- f5-07 §3.1 (Embedder Protocol — async + numpy, the full contract)
- f5-06 §7a.2 (VectorIndexRepository.knn takes ``query: Any``)
- f6-signoffs.md SO-6 (#11 owns RRF + ranking; storage/embedder are separate)
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class QueryEmbedder(Protocol):
    """Minimal sync query-embedding seam for #11's hot path.

    ``embed_query`` computes the query vector for one query string. The return
    value is opaque (``Any``): it is whatever the downstream
    ``VectorIndexRepository.knn`` accepts (numpy array, ``bytes``, tuple — #11
    does not care). #11 calls this exactly ONCE per recall, BEFORE stage 1.

    #7's real Embedder is async+batch (``embed(texts, role)``); a sync adapter
    satisfying this Protocol runs the single-query case (``role='query'``,
    L2-unit-normalized) and returns the vector in the shape #6 expects.
    """

    def embed_query(self, query: str) -> Any: ...


__all__ = ["QueryEmbedder"]
