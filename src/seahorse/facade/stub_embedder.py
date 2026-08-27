"""Composition-root stub for the ``embedder`` slot.

``build_facade`` wires ``StubQueryEmbedder`` as the default ``embedder`` so the
``QueryEmbedder`` extension point EXISTS at the composition root (a single-point
swap when the real embedder lands), NOT so it runs. Current ``recall`` is the
current-state listing (``VigenteListingRetriever``) and never embeds; the stub
raises ``E_NOT_IN_MVP_0`` fail-loud if a non-skip recall path invokes it before
the real embedder is wired (honesty — never silently degrade). A later release
swaps this slot for the real embedder adapter (async→sync wrapper over
``embed(texts, role='query')``; see the contract in
``seahorse.contracts.embeddings``).

``embedding_dim`` is ``0`` (sentinel): no consumer reads it in the current
release. The vector index validates the query shape against it at a later
materialization.

The stub raises a facade ``SeahorseError`` carrying the engine-owned
``E_NOT_IN_MVP_0`` marker code (the cross-cutting "not available in this
release" code, already in the shared error catalog for both the MCP server and
the CLI). The facade composition root is the right component-of-origin
attribution: it is the facade saying "no embedder is wired here." This reuses an
existing stable code rather than minting a new one, so no error-catalog /
exit-code / drift-guard churn.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from seahorse.engine.errors import E_NOT_IN_MVP_0
from seahorse.facade.errors import SeahorseError

_DETAIL = (
    "the query embedder is not wired in this release; a non-skip recall path "
    "invoked the embedder slot, which requires a real embedder backend — wire "
    "one via build_facade or swap the retriever to the hybrid adapter"
)


class StubQueryEmbedder:
    """Composition-root default for the ``embedder`` slot (fail-loud).

    Satisfies the ``QueryEmbedder`` Protocol (``embedding_dim`` +
    ``embed_query`` + ``embed_queries``) but raises ``E_NOT_IN_MVP_0`` on any
    embed call. In the current release the slot is wired but inert:
    ``VigenteListingRetriever`` never calls it. The guard fires only on misuse —
    an early later-release recall path wired before the real embedder adapter
    lands.
    """

    embedding_dim: int = 0

    def embed_query(self, query: str) -> Any:
        raise SeahorseError(code=E_NOT_IN_MVP_0, detail=_DETAIL)

    def embed_queries(self, texts: Sequence[str]) -> Any:
        raise SeahorseError(code=E_NOT_IN_MVP_0, detail=_DETAIL)

    def similarity(self, query_vec: Any, passages: Sequence[str]) -> Sequence[float]:
        raise SeahorseError(code=E_NOT_IN_MVP_0, detail=_DETAIL)


__all__ = ["StubQueryEmbedder"]