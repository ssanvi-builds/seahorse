"""MVP-0 composition-root stub for the ``embedder`` slot (C8.4 seam).

``build_facade`` wires ``StubQueryEmbedder`` as the default ``embedder`` so the
``QueryEmbedder`` seam EXISTS at the composition root (a single-point swap when
#7 lands), NOT so it runs. MVP-0 ``recall`` is the vigente listing
(``VigenteListingRetriever``) and never embeds; the stub raises ``E_NOT_IN_MVP_0``
fail-loud if a non-skip recall path invokes it before #7 is wired (ADR-10
honesty — never silently degrade). MVP-1 swaps this slot for the real #7 adapter
(async→sync wrapper over ``embed(texts, role='query')``; see the contract in
``seahorse.contracts.embeddings``).

``embedding_dim`` is ``0`` (sentinel): no consumer reads it in MVP-0. #6's
vector index validates the query shape against it at MVP-1 materialization.

The stub raises a facade ``SeahorseError`` carrying the engine-owned
``E_NOT_IN_MVP_0`` marker code (the cross-cutting "not available in MVP-0"
code, already in ``CAT_A`` for both #13 and #14). The facade composition root is
the right component-of-origin attribution: it is #12 saying "no embedder is
wired here." This reuses an existing stable code rather than minting a new one,
so no CAT_A / exit-code / drift-guard churn.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from seahorse.engine.errors import E_NOT_IN_MVP_0
from seahorse.facade.errors import SeahorseError

_DETAIL = (
    "query embedder is not wired in MVP-0; a non-skip recall path invoked the "
    "embedder slot, which is an MVP-1 (#7) capability — wire a real embedder at "
    "build_facade or swap the retriever to the hybrid adapter"
)


class StubQueryEmbedder:
    """MVP-0 composition-root default for the ``embedder`` slot (fail-loud).

    Satisfies the widened ``QueryEmbedder`` Protocol (``embedding_dim`` +
    ``embed_query`` + ``embed_queries``) but raises ``E_NOT_IN_MVP_0`` on any
    embed call. In MVP-0 the slot is wired but inert: ``VigenteListingRetriever``
    never calls it. The guard fires only on misuse — an early MVP-1 recall path
    wired before #7's real adapter lands.
    """

    embedding_dim: int = 0

    def embed_query(self, query: str) -> Any:
        raise SeahorseError(code=E_NOT_IN_MVP_0, detail=_DETAIL)

    def embed_queries(self, texts: Sequence[str]) -> Any:
        raise SeahorseError(code=E_NOT_IN_MVP_0, detail=_DETAIL)


__all__ = ["StubQueryEmbedder"]