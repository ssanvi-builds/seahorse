"""C8.4 — widened ``QueryEmbedder`` Protocol + composition-root stub.

#6's audit (#6 in the 2-pass review) flagged the query-embedding seam as too
thin: a single sync ``embed_query`` method, no batch surface, no dim/shape
metadata, and no slot at the composition root. #7's real Embedder is
``async embed(texts, role) -> np.ndarray`` (f5-07 §3.1) — batch + async + numpy.
Widening the Protocol now (without materializing #7) makes the MVP-1 swap a
single-point change: #7 ships an adapter that bridges async→sync and exposes
``embedding_dim`` + ``embed_queries``, wired at ``build_facade``.

These tests pin:
- the Protocol surface (``embed_query`` + ``embed_queries`` + ``embedding_dim``);
- a minimal single-query impl NO LONGER satisfies the Protocol (widening is
  load-bearing — it forces #7's adapter to declare dim + batch);
- the MVP-0 ``StubQueryEmbedder`` satisfies the Protocol and raises
  ``E_NOT_IN_MVP_0`` fail-loud if invoked (the skip-path guard — MVP-0 recall is
  the vigente listing and never embeds).
"""

from __future__ import annotations

from typing import Any

import pytest

from seahorse.contracts.embeddings import QueryEmbedder
from seahorse.engine.errors import E_NOT_IN_MVP_0
from seahorse.facade.errors import SeahorseError
from seahorse.facade.stub_embedder import StubQueryEmbedder


class _SingleQueryOnly:
    """Pre-C8.4 shape: only ``embed_query``. Must NOT satisfy the widened Protocol."""

    def embed_query(self, query: str) -> Any:  # pragma: no cover - shape probe
        return None


class _FullEmbedder:
    """Post-C8.4 shape: dim + single + batch. Must satisfy the Protocol."""

    embedding_dim: int = 768

    def embed_query(self, query: str) -> Any:
        return [0.0] * self.embedding_dim

    def embed_queries(self, texts: Any) -> Any:
        return [[0.0] * self.embedding_dim for _ in texts]


def test_protocol_widened_with_batch_and_dim() -> None:
    # The seam now carries batch + dim metadata — a single-query-only impl no
    # longer conforms (the widening is load-bearing: #7's adapter MUST declare them).
    assert not isinstance(_SingleQueryOnly(), QueryEmbedder)
    assert isinstance(_FullEmbedder(), QueryEmbedder)


def test_stub_query_embedder_satisfies_protocol() -> None:
    assert isinstance(StubQueryEmbedder(), QueryEmbedder)


def test_stub_embedding_dim_is_zero_sentinel() -> None:
    # MVP-0 sentinel: no real backend wired, so the dim is non-meaningful. No
    # consumer reads it in MVP-0; #6 validates against it at MVP-1 materialization.
    assert StubQueryEmbedder().embedding_dim == 0


def test_stub_embed_query_raises_not_in_mvp_0() -> None:
    # Fail-loud guard: invoking the embedder in MVP-0 means a non-skip recall path
    # reached the slot before #7 was wired. The stub never silently degrades.
    with pytest.raises(SeahorseError) as excinfo:
        StubQueryEmbedder().embed_query("anything")
    assert excinfo.value.code == E_NOT_IN_MVP_0


def test_stub_embed_queries_raises_not_in_mvp_0() -> None:
    with pytest.raises(SeahorseError) as excinfo:
        StubQueryEmbedder().embed_queries(["a", "b"])
    assert excinfo.value.code == E_NOT_IN_MVP_0