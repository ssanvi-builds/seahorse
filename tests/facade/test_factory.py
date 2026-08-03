"""Tests for ``seahorse.facade.factory.build_facade`` (pre-work for #13/#14)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seahorse.contracts.embeddings import QueryEmbedder
from seahorse.engine.errors import E_NOT_IN_MVP_0
from seahorse.facade import build_facade
from seahorse.facade.errors import SeahorseError
from seahorse.facade.facade import MemoryFacade
from seahorse.facade.stub_embedder import StubQueryEmbedder
from seahorse.facade.types import RememberPayload
from seahorse.persistence.storage import Storage


class _RecordingEmbedder:
    """``QueryEmbedder`` double that records calls (C8.4 seam tests)."""

    embedding_dim: int = 8

    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed_query(self, query: str) -> list[float]:
        self.calls.append(query)
        return [0.0] * self.embedding_dim

    def embed_queries(self, texts) -> list[list[float]]:  # type: ignore[no-untyped-def]
        self.calls.extend(texts)
        return [[0.0] * self.embedding_dim for _ in texts]


def _advancing_clock(start: datetime, step: timedelta):
    state = {"t": start}

    def _now() -> datetime:
        t = state["t"]
        state["t"] = t + step
        return t

    return _now


def _agent_by() -> dict:
    return {"source_type": "agent", "agent_id": "a1", "session_id": "s1"}


class TestBuildFacade:
    def test_returns_memory_facade(self, tmp_path) -> None:
        facade, storage = build_facade(tmp_path / "f.db")
        try:
            assert isinstance(facade, MemoryFacade)
        finally:
            storage.close()

    def test_remember_then_recall_round_trip(self, tmp_path) -> None:
        facade, storage = build_facade(tmp_path / "f.db")
        try:
            r = facade.remember(
                RememberPayload(body="Sergio lives in Madrid", by=_agent_by())
            )
            assert r.status == "ACTIVE"
            assert r.ep_id is not None
            rows = facade.recall("madrid")
            assert r.ep_id in [row.ep_id for row in rows]
        finally:
            storage.close()

    def test_injected_clock_drives_engine_timestamps(self, tmp_path) -> None:
        clock = _advancing_clock(
            datetime(2026, 7, 16, 12, 0, tzinfo=UTC), timedelta(seconds=10)
        )
        facade, storage = build_facade(tmp_path / "f.db", clock=clock)
        try:
            r = facade.remember(
                RememberPayload(body="first episode", by=_agent_by())
            )
            full = facade.recall_full([r.ep_id])
            assert len(full) == 1
            # The clock's first tick (12:00:00) is the created_at.
            assert full[0].episode.created_at == datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
        finally:
            storage.close()

    def test_default_clock_is_utc_now(self, tmp_path) -> None:
        before = datetime.now(UTC)
        facade, storage = build_facade(tmp_path / "f.db")
        try:
            r = facade.remember(
                RememberPayload(body="default clock episode", by=_agent_by())
            )
            after = datetime.now(UTC)
            full = facade.recall_full([r.ep_id])
            created = full[0].episode.created_at
            assert before <= created <= after
            assert created.tzinfo is not None
        finally:
            storage.close()

    def test_reuses_existing_storage(self, tmp_path) -> None:
        storage = Storage(tmp_path / "shared.db")
        try:
            facade, storage_out = build_facade(tmp_path / "ignored.db", storage=storage)
            assert storage_out is storage
            facade.remember(RememberPayload(body="reused storage", by=_agent_by()))
            rows = facade.recall("reused")
            assert any(r.ep_id for r in rows)
        finally:
            storage.close()

    def test_config_is_propagated(self, tmp_path) -> None:
        from seahorse.facade.types import FacadeConfig

        config = FacadeConfig(default_extraction_mode="skip")
        facade, storage = build_facade(tmp_path / "f.db", config=config)
        try:
            assert facade._config is config
        finally:
            storage.close()


class TestEmbedderSlot:
    """C8.4 — ``build_facade`` gains an ``embedder`` slot (composition-root seam).

    MVP-0 recall is the vigente listing (``VigenteListingRetriever``) and never
    embeds; the slot defaults to ``StubQueryEmbedder`` so the seam EXISTS at the
    composition root (single-point swap when #7 lands), NOT so it runs. Invoking
    the default stub raises ``E_NOT_IN_MVP_0`` (the skip-path guard). #7 is NOT
    wired here — the slot is the point.
    """

    def test_default_wires_stub_embedder(self, tmp_path) -> None:
        facade, storage = build_facade(tmp_path / "f.db")
        try:
            assert isinstance(facade._embedder, StubQueryEmbedder)
            assert isinstance(facade._embedder, QueryEmbedder)
        finally:
            storage.close()

    def test_accepts_custom_embedder(self, tmp_path) -> None:
        embedder = _RecordingEmbedder()
        facade, storage = build_facade(tmp_path / "f.db", embedder=embedder)
        try:
            assert facade._embedder is embedder
            assert isinstance(facade._embedder, QueryEmbedder)
        finally:
            storage.close()

    def test_default_stub_raises_not_in_mvp_0_if_invoked(self, tmp_path) -> None:
        facade, storage = build_facade(tmp_path / "f.db")
        try:
            with pytest.raises(SeahorseError) as excinfo:
                facade._embedder.embed_query("x")
            assert excinfo.value.code == E_NOT_IN_MVP_0
        finally:
            storage.close()

    def test_recall_does_not_invoke_embedder_in_mvp0(self, tmp_path) -> None:
        # The MVP-0 vigente-listing recall ignores the query for ranking, so the
        # embedder slot is NEVER consulted. This pins "no cablea #7": the seam is
        # present but inert. MVP-1 swaps the retriever to the hybrid adapter that
        # DOES call it (single-point change at this composition root).
        embedder = _RecordingEmbedder()
        facade, storage = build_facade(tmp_path / "f.db", embedder=embedder)
        try:
            facade.remember(RememberPayload(body="Sergio lives in Madrid", by=_agent_by()))
            facade.recall("madrid")
            assert embedder.calls == []
        finally:
            storage.close()


class _FakeAsyncEmbedder:
    """Async #7 Embedder double for the retrieval-regime wiring tests."""

    dim = 384

    async def embed(self, texts, role):
        import numpy as np

        return np.ones((len(texts), 384), dtype=np.float32)

    def model_identity(self):
        from seahorse.embeddings.types import ModelIdentity

        return ModelIdentity(
            backend="test", model_name="m", revision="r",
            dim=384, quantization="fp32", normalized=True,
        )


class TestRetrievalRegime:
    """M1-C.3 — build_facade swaps the hybrid retriever + indexer when retrieval
    is available, and stays G2 (VigenteListingRetriever, no vector access)
    otherwise."""

    def test_default_g2_when_passage_embedder_unavailable(self, monkeypatch, tmp_path) -> None:
        import seahorse.facade.factory as factory
        from seahorse.facade.vigente_retriever import VigenteListingRetriever

        monkeypatch.setattr(factory, "_build_passage_embedder", lambda: None)
        facade, storage = build_facade(tmp_path / "f.db")
        try:
            assert isinstance(facade._retriever, VigenteListingRetriever)
            assert isinstance(facade._embedder, StubQueryEmbedder)
        finally:
            storage.close()

    def test_retrieval_mode_wires_hybrid_and_indexer(self, monkeypatch, tmp_path) -> None:
        import seahorse.facade.factory as factory
        from seahorse.facade.hybrid_retriever import HybridRetriever

        monkeypatch.setattr(factory, "_build_passage_embedder", lambda: _FakeAsyncEmbedder())
        embedder = _RecordingEmbedder()
        facade, storage = build_facade(
            tmp_path / "f.db", embedder=embedder, retrieval_available=True
        )
        try:
            assert isinstance(facade._retriever, HybridRetriever)
            # the query seam receives the injected embedder...
            assert facade._retriever._embedder is embedder  # noqa: SLF001
            # ...and the write path carries the indexer.
            assert facade._write_path._indexer is not None  # noqa: SLF001
        finally:
            storage.close()