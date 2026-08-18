"""HybridRetriever adapter.

``HybridRetriever`` implements the facade ``_RetrieverLike`` extension point
over ``seahorse.retrieval.recall`` (the later-release regime), with an honest
degrade to the ``VigenteListingRetriever`` when there is nothing to serve (no
vectors/FTS, or the embedder is not wired) — the motor keeps working without
ranking.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seahorse.contracts.retrieval import FusedCandidate
from seahorse.facade.hybrid_retriever import HybridRetriever
from seahorse.facade.types import FacadeConfig


class _Vec:
    def __init__(self, count: int = 3) -> None:
        self._count = count

    def count(self) -> int:
        return self._count


class _Fts:
    def __init__(self, count: int = 3) -> None:
        self._count = count

    def count(self) -> int:
        return self._count


class _Emb:
    def __init__(self, dim: int = 384) -> None:
        self.embedding_dim = dim

    def embed_query(self, query: str) -> bytes:
        return b""

    def embed_queries(self, texts) -> bytes:
        return b""


class _Ep:
    def get(self, ep_id: str):
        return None

    def chain_from(self, ep_id: str) -> list:
        return []


class _Graph:
    def bfs_neighbors_state_at(self, ep_id, pit, *, pit_kind, hops, include_tags_soft):
        return []


class _Fallback:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def recall(self, query, *, pit=None, k=10, cognitive_type=None, subject_filter=None):
        self.calls.append((query, k))
        return []


def _make(*, vec_count=3, fts_count=3, dim=384, fallback=None, config=None) -> HybridRetriever:
    return HybridRetriever(
        embedder=_Emb(dim),
        vector_repo=_Vec(vec_count),
        fts_repo=_Fts(fts_count),
        episode_repo=_Ep(),
        index_repo=_Graph(),
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        config=config or FacadeConfig(),
        fallback=fallback or _Fallback(),
    )


def test_hybrid_retriever_satisfies_retriever_protocol() -> None:
    from seahorse.facade.facade import _RetrieverLike

    assert isinstance(_make(), _RetrieverLike)


def test_hybrid_retriever_supports_pit() -> None:
    assert _make().supports_pit is True


def test_hybrid_recall_delegates_to_retrieval_engine(monkeypatch) -> None:
    import seahorse.retrieval.engine as re_mod

    calls: list[tuple[str, dict]] = []

    def fake_recall(query: str, **kwargs):
        calls.append((query, kwargs))
        return [FusedCandidate(ep_id="e1", score=1.0, sources=("vector",))]

    monkeypatch.setattr(re_mod, "recall", fake_recall)
    hybrid = _make()
    result = hybrid.recall(
        "madrid", pit=None, k=5, cognitive_type="semantic", subject_filter="S"
    )
    assert [c.ep_id for c in result] == ["e1"]
    assert calls[0][0] == "madrid"
    kw = calls[0][1]
    assert kw["pit"] is None
    assert kw["k"] == 5
    assert kw["cognitive_type"] == "semantic"
    assert kw["subject_filter"] == "S"
    assert kw["anchor_ep_id"] is None


def test_hybrid_recall_caps_k_at_config_top_k(monkeypatch) -> None:
    """Parity with the listing-regime retriever: k is capped at ``config.top_k``
    (the MCP ``seahorse.toml`` top_k must be honored by the hybrid path too —
    surfaced when the embeddings extra wired the hybrid regime)."""
    import seahorse.retrieval.engine as re_mod

    calls: list[tuple[str, dict]] = []

    def fake_recall(query: str, **kwargs):
        calls.append((query, kwargs))
        return [FusedCandidate(ep_id="e1", score=1.0, sources=("vector",))]

    monkeypatch.setattr(re_mod, "recall", fake_recall)
    hybrid = _make(config=FacadeConfig(top_k=2))
    hybrid.recall("madrid", pit=None, k=10)
    assert calls[0][1]["k"] == 2


def test_hybrid_degrades_to_g2_when_no_index_data() -> None:
    fallback = _Fallback()
    hybrid = _make(vec_count=0, fts_count=0, fallback=fallback)
    result = hybrid.recall("madrid", pit=None, k=5)
    assert fallback.calls == [("madrid", 5)]  # delegated to the listing regime
    assert result == []


def test_hybrid_degrades_to_g2_when_embedder_not_wired() -> None:
    # embedding_dim=0 is the StubQueryEmbedder sentinel (E_NOT_IN_MVP_0) — not
    # a real embedder, so the hybrid path cannot serve.
    fallback = _Fallback()
    hybrid = _make(dim=0, fallback=fallback)
    hybrid.recall("madrid", pit=None, k=5)
    assert fallback.calls == [("madrid", 5)]


def test_hybrid_degrades_to_g2_on_embedder_runtime_failure(monkeypatch) -> None:
    # The index has data but the embedder blows up at runtime: honest degrade to
    # the listing regime rather than failing the recall.
    import seahorse.retrieval.engine as re_mod

    def boom(query: str, **kwargs):
        raise RuntimeError("onnx session unavailable")

    monkeypatch.setattr(re_mod, "recall", boom)
    fallback = _Fallback()
    hybrid = _make(fallback=fallback)
    result = hybrid.recall("madrid", pit=None, k=5)
    assert fallback.calls == [("madrid", 5)]
    assert result == []
