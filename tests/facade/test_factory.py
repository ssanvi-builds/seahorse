"""Tests for ``seahorse.facade.factory.build_facade`` (pre-work for the MCP
server and CLI)."""

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
from seahorse.llm import ExtractResult
from seahorse.persistence.storage import Storage


class _RecordingEmbedder:
    """``QueryEmbedder`` double that records calls (extension-point tests)."""

    embedding_dim: int = 8

    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed_query(self, query: str) -> list[float]:
        self.calls.append(query)
        return [0.0] * self.embedding_dim

    def embed_queries(self, texts) -> list[list[float]]:  # type: ignore[no-untyped-def]
        self.calls.extend(texts)
        return [[0.0] * self.embedding_dim for _ in texts]

    def similarity(self, query_vec, passages) -> list[float]:  # type: ignore[no-untyped-def]
        return [0.0] * len(passages)


def _advancing_clock(start: datetime, step: timedelta):
    state = {"t": start}

    def _now() -> datetime:
        t = state["t"]
        state["t"] = t + step
        return t

    return _now


def _controllable_clock(start: datetime):
    """A clock whose current time is externally settable via ``state["now"]``."""
    state = {"now": start}

    def _now() -> datetime:
        return state["now"]

    return _now, state


def _agent_by() -> dict:
    return {"source_type": "agent", "agent_id": "a1", "session_id": "s1"}


class _RecordingLLMClient:
    """``LLMClient`` double that records extract calls (extension-point tests)."""

    def __init__(self, result: ExtractResult | None = None, error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self._result = result
        self._error = error

    def extract(
        self,
        content: str,
        schema_hint: type,
        *,
        role: str = "extraction",
        budget=None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> ExtractResult:
        self.calls.append(content)
        if self._error is not None:
            raise self._error
        return self._result

    def complete(
        self,
        messages,
        *,
        role: str = "extraction",
        budget=None,
        max_tokens=None,
        timeout_s=None,
    ):
        raise NotImplementedError


class TestLlmClientSlot:
    """``build_facade`` gains an ``llm_client`` slot (write-path extension point).

    The default (None) preserves the first release's honest llm→skip degrade.
    When a real client is wired, ``remember(extraction_mode='llm')`` routes
    through it and stores the effective LLM provenance. Skip mode never touches
    it.
    """

    def test_default_no_client_degrades_llm_to_skip(self, tmp_path) -> None:
        facade, storage = build_facade(tmp_path / "f.db")
        try:
            res = facade.remember(
                RememberPayload(body="Sergio lives in Madrid", by=_agent_by()),
                extraction_mode="llm",
            )
            ep = storage.episodes.get(res.ep_id)
            assert ep.provenance["extraction_mode"] == "skip"
            assert ep.provenance["degraded_from"] == "llm"
            assert ep.provenance["degrade_reason"] == "llm_not_implemented_mvp0"
        finally:
            storage.close()

    def test_wired_client_used_for_llm(self, tmp_path) -> None:
        client = _RecordingLLMClient(
            result=ExtractResult(
                data={"subject": "llm subject"},
                prompt_hash="h" * 64,
                model_used="ollama/qwen3:1.7b",
                confidence=0.9,
            )
        )
        facade, storage = build_facade(tmp_path / "f.db", llm_client=client)
        try:
            res = facade.remember(
                RememberPayload(body="Sergio lives in Madrid", by=_agent_by()),
                extraction_mode="llm",
            )
            assert client.calls == ["Sergio lives in Madrid"]
            ep = storage.episodes.get(res.ep_id)
            assert ep.provenance["extraction_mode"] == "llm"
            assert ep.provenance["model_used"] == "ollama/qwen3:1.7b"
            assert ep.provenance["prompt_hash"] == "h" * 64
            assert ep.subject == "llm subject"
        finally:
            storage.close()

    def test_skip_mode_does_not_touch_client(self, tmp_path) -> None:
        client = _RecordingLLMClient(
            result=ExtractResult(data={"subject": "s"}, prompt_hash="h" * 64)
        )
        facade, storage = build_facade(tmp_path / "f.db", llm_client=client)
        try:
            facade.remember(
                RememberPayload(body="Sergio lives in Madrid", by=_agent_by()),
                extraction_mode="skip",
            )
            assert client.calls == []
        finally:
            storage.close()


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


class TestEmbedModeSlot:
    """``build_facade`` gains an ``embed_mode`` slot.

    Propagated to the ``RetrievalIndexer`` (composition root, single-point swap)
    so the vectorial experiment can re-index with ``body+summary`` without
    touching the write path. Default ``body+summary`` is the vectorial
    experiment's product default.
    """

    def _hybrid(self, monkeypatch, tmp_path, *, embed_mode="body+summary"):
        import seahorse.facade.factory as factory

        monkeypatch.setattr(factory, "_build_passage_embedder", lambda: _FakeAsyncEmbedder())
        return build_facade(
            tmp_path / "f.db",
            embedder=_QueryEmbedder384(),
            retrieval_available=True,
            embed_mode=embed_mode,
        )

    def test_default_embed_mode_body_summary(self, monkeypatch, tmp_path) -> None:
        # The flip is at the composition root: build_facade without embed_mode
        # wires body+summary (no explicit pass-through masks the default).
        import seahorse.facade.factory as factory

        monkeypatch.setattr(factory, "_build_passage_embedder", lambda: _FakeAsyncEmbedder())
        facade, storage = build_facade(
            tmp_path / "f.db",
            embedder=_QueryEmbedder384(),
            retrieval_available=True,
        )
        try:
            assert facade._write_path._indexer._embed_mode == "body+summary"  # noqa: SLF001
        finally:
            storage.close()

    def test_embed_mode_propagated_to_indexer(self, monkeypatch, tmp_path) -> None:
        facade, storage = self._hybrid(monkeypatch, tmp_path, embed_mode="body+summary")
        try:
            assert facade._write_path._indexer._embed_mode == "body+summary"  # noqa: SLF001
        finally:
            storage.close()

    def test_invalid_embed_mode_rejected(self, monkeypatch, tmp_path) -> None:
        with pytest.raises(ValueError, match="embed_mode"):
            self._hybrid(monkeypatch, tmp_path, embed_mode="bogus")


class TestPassageEmbedderSlot:
    """Experiment extension point — ``build_facade`` accepts a ``passage_embedder``
    override.

    The synthetic experiment (mechanical CI verification) needs a deterministic
    passage embedder; the composition-root extension point keeps the wiring
    honest (no monkeypatching). Default None keeps the auto-resolved fastembed
    path. The indexer stores the override; the retriever's query extension point
    is derived over it (same async→sync adapter as the real path).
    """

    def test_passage_embedder_override_reaches_indexer_and_query_seam(
        self, tmp_path
    ) -> None:
        from seahorse.embeddings.query_adapter import AsyncToSyncQueryEmbedder
        from seahorse.facade.hybrid_retriever import HybridRetriever

        facade, storage = build_facade(
            tmp_path / "f.db",
            retrieval_available=True,
            passage_embedder=_FakeAsyncEmbedder(),
        )
        try:
            assert isinstance(facade._retriever, HybridRetriever)
            # The write-path indexer embeds with the injected passage embedder.
            assert facade._write_path._indexer._embedder is not None  # noqa: SLF001
            # The query extension point is the async→sync adapter over the same
            # embedder.
            assert isinstance(facade._retriever._embedder, AsyncToSyncQueryEmbedder)  # noqa: SLF001
        finally:
            storage.close()

    def test_default_resolves_fastembed_path(self, monkeypatch, tmp_path) -> None:
        # passage_embedder=None keeps the auto-resolution (fastembed extra) —
        # the existing `_build_passage_embedder` extension point.
        import seahorse.facade.factory as factory

        captured: dict = {}
        monkeypatch.setattr(
            factory,
            "_build_passage_embedder",
            lambda: captured.setdefault("called", True) or _FakeAsyncEmbedder(),
        )
        build_facade(
            tmp_path / "f.db", retrieval_available=True, embedder=_QueryEmbedder384()
        )
        assert captured.get("called") is True


class TestImproveIndexesSuccessor:
    """Experiment enabler — the hybrid composition root indexes the successor.

    ``improve`` writes the new version via ``engine.improve`` (NOT the write
    path); without an index hook the successor never reaches vec0/FTS, so hybrid
    recall cannot recover it and ``knowledge_update_accuracy`` would be 0. The
    factory wires ``on_episode_indexed`` to the write-path indexer.
    """

    def test_hybrid_facade_indexes_improve_successor(self, monkeypatch, tmp_path) -> None:
        import seahorse.facade.factory as factory

        monkeypatch.setattr(factory, "_build_passage_embedder", lambda: _FakeAsyncEmbedder())
        clock, _ = _controllable_clock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
        facade, storage = build_facade(
            tmp_path / "f.db",
            embedder=_QueryEmbedder384(),
            retrieval_available=True,
            clock=clock,
        )
        try:
            old = facade.remember(
                RememberPayload(body="The capital of France is Paris", by=_agent_by())
            )
            new_ep = facade.improve(
                old.ep_id, "The capital of France is now Lyon", by=_agent_by()
            )
            # The successor is now retrievable via the hybrid path (indexed).
            rows = facade.recall("capital of France", k=5)
            assert any(r.ep_id == new_ep.id for r in rows), (
                "the improve successor must be retrievable post-index"
            )
        finally:
            storage.close()


class TestDistillIndexesConsolidatedNote:
    """D1 — the consolidated note must be retrievable by hybrid recall.

    ``facade.distill`` writes via ``distill_episodes`` → ``engine.remember``
    directly (NOT the write path), so without an index hook the consolidated
    note — the most valuable memory the distillation exists to produce — never
    reaches vec0/FTS and the hybrid recall cannot recover it. The factory wires
    ``on_episode_indexed`` to the write-path indexer.
    """

    def test_hybrid_facade_indexes_distilled_note(self, monkeypatch, tmp_path) -> None:
        import seahorse.facade.factory as factory

        monkeypatch.setattr(factory, "_build_passage_embedder", lambda: _FakeAsyncEmbedder())
        clock, _ = _controllable_clock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
        facade, storage = build_facade(
            tmp_path / "f.db",
            embedder=_QueryEmbedder384(),
            retrieval_available=True,
            clock=clock,
        )
        try:
            ids = [
                facade.remember(
                    RememberPayload(
                        body=f"# The capital of France [s1:{i}]\n\nParis is the capital.",
                        by=_agent_by(),
                    )
                ).ep_id
                for i in range(1, 4)
            ]
            rep = storage.episodes.get(ids[-1])
            wr = facade.distill(
                source_ep_ids=ids,
                representative=rep,
                consolidated_body="# The capital of France\n\nThe capital of France is Paris.",
                by={"source_type": "system", "agent_id": "consolidator"},
            )
            # The consolidated note is now retrievable via the hybrid path (indexed).
            rows = facade.recall("capital of France", k=5)
            assert any(r.ep_id == wr.ep_id for r in rows), (
                "the consolidated note must be retrievable post-index"
            )
        finally:
            storage.close()


class TestEmbedderSlot:
    """``build_facade`` gains an ``embedder`` slot (composition-root extension
    point).

    First-release recall is the current-state listing (``VigenteListingRetriever``)
    and never embeds; the slot defaults to ``StubQueryEmbedder`` so the extension
    point EXISTS at the composition root (single-point swap when the embedder
    lands), NOT so it runs. Invoking the default stub raises ``E_NOT_IN_MVP_0``
    (the skip-path guard). The embedder is NOT wired here — the slot is the
    point.
    """

    def test_default_wires_stub_embedder(self, tmp_path) -> None:
        # The listing regime forced explicitly: with the ``embeddings`` extra
        # installed the default auto-resolves the hybrid retrieval (real
        # embedder), so the first-release stub behavior is pinned via
        # ``retrieval_available=False``.
        facade, storage = build_facade(tmp_path / "f.db", retrieval_available=False)
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
        facade, storage = build_facade(tmp_path / "f.db", retrieval_available=False)
        try:
            with pytest.raises(SeahorseError) as excinfo:
                facade._embedder.embed_query("x")
            assert excinfo.value.code == E_NOT_IN_MVP_0
        finally:
            storage.close()

    def test_recall_does_not_invoke_embedder_in_mvp0(self, tmp_path) -> None:
        # The first-release current-state-listing recall ignores the query for
        # ranking, so the embedder slot is NEVER consulted. This pins that the
        # embedder is not wired: the extension point is present but inert. A
        # later release swaps the retriever to the hybrid adapter that DOES call
        # it (single-point change at this composition root).
        embedder = _RecordingEmbedder()
        facade, storage = build_facade(
            tmp_path / "f.db", embedder=embedder, retrieval_available=False
        )
        try:
            facade.remember(RememberPayload(body="Sergio lives in Madrid", by=_agent_by()))
            facade.recall("madrid")
            assert embedder.calls == []
        finally:
            storage.close()


class _FakeAsyncEmbedder:
    """Async embedder double for the retrieval-regime wiring tests."""

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
    """build_facade swaps the hybrid retriever + indexer when retrieval is
    available, and stays in the listing regime (VigenteListingRetriever, no
    vector access) otherwise."""

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
            # the query extension point receives the injected embedder...
            assert facade._retriever._embedder is embedder  # noqa: SLF001
            # ...and the write path carries the indexer.
            assert facade._write_path._indexer is not None  # noqa: SLF001
        finally:
            storage.close()


class _QueryEmbedder384:
    """``QueryEmbedder`` double whose dim matches ``_FakeAsyncEmbedder`` (384).

    Returns a deterministic float32 BLOB (the vec0 ``knn`` contract) with a
    non-zero vector so the kNN path serves (a zero vector has no norm).
    """

    embedding_dim = 384

    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed_query(self, query: str) -> bytes:
        self.calls.append(query)
        import numpy as np

        return np.full(384, 0.5, dtype=np.float32).tobytes()

    def embed_queries(self, texts) -> list[bytes]:  # type: ignore[no-untyped-def]
        self.calls.extend(texts)
        import numpy as np

        return [np.full(384, 0.5, dtype=np.float32).tobytes() for _ in texts]


class TestRecencySlot:
    """``build_facade`` gains a ``recency`` slot (composition root).

    The ``HybridRetriever`` already propagates ``RecencyConfig | None``; the
    composition root must expose it so the benchmark SUT and CLI can wire the
    recency experiment without touching the facade internals (single-point
    swap). Default None keeps the pure-RRF fingerprint (honest, deterministic).
    """

    def _hybrid(self, monkeypatch, tmp_path, db_name, *, clock, recency=None):
        import seahorse.facade.factory as factory

        monkeypatch.setattr(factory, "_build_passage_embedder", lambda: _FakeAsyncEmbedder())
        return build_facade(
            tmp_path / db_name,
            embedder=_QueryEmbedder384(),
            retrieval_available=True,
            clock=clock,
            recency=recency,
        )

    def test_default_recency_is_none(self, monkeypatch, tmp_path) -> None:
        from seahorse.facade.hybrid_retriever import HybridRetriever

        clock, _ = _controllable_clock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
        facade, storage = self._hybrid(monkeypatch, tmp_path, "f.db", clock=clock)
        try:
            assert isinstance(facade._retriever, HybridRetriever)
            assert facade._retriever._recency is None  # noqa: SLF001
        finally:
            storage.close()

    def test_recency_config_is_propagated(self, monkeypatch, tmp_path) -> None:
        from seahorse.facade.hybrid_retriever import HybridRetriever
        from seahorse.retrieval.recency import RecencyConfig

        cfg = RecencyConfig(gamma=0.7, half_life_days=21.0)
        clock, _ = _controllable_clock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
        facade, storage = self._hybrid(monkeypatch, tmp_path, "f.db", clock=clock, recency=cfg)
        try:
            assert isinstance(facade._retriever, HybridRetriever)
            assert facade._retriever._recency is cfg  # noqa: SLF001
        finally:
            storage.close()

    def test_recall_boosted_with_recency_pure_rrf_without(
        self, monkeypatch, tmp_path
    ) -> None:
        """With recency the candidate scores are boosted vs the pure-RRF run.

        The same facade serves both recalls (same DB, same ep_id, same
        ``created_at``, same ``now``): first with ``recency=None`` (pure RRF,
        bit-identical baseline), then with a recency config re-wired on the
        retriever. A long half-life makes the factor ≈ (1+γ) strictly > 1.
        """
        from seahorse.retrieval.recency import RecencyConfig

        start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        later = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
        clock, state = _controllable_clock(start)
        facade, storage = self._hybrid(monkeypatch, tmp_path, "f.db", clock=clock)
        try:
            facade.remember(
                RememberPayload(body="The capital of France is Paris", by=_agent_by())
            )
            state["now"] = later
            baseline = facade.recall("capital of France", k=5)
            assert baseline and baseline[0].score > 0.0  # hybrid path served
            base_by_id = {r.ep_id: r.score for r in baseline}
            # Wire the recency signal on the SAME retriever (test hook — the
            # build-time propagation is covered by test_recency_config_is_propagated).
            facade._retriever._recency = RecencyConfig(  # noqa: SLF001
                gamma=1.0, half_life_days=365.0
            )
            boosted_rows = facade.recall("capital of France", k=5)
            assert boosted_rows
            for r in boosted_rows:
                assert r.ep_id in base_by_id
                assert r.score > base_by_id[r.ep_id], (
                    "recency boost must strictly increase the score of a present candidate"
                )
        finally:
            storage.close()


class _FakeReranker:
    """Deterministic ``QueryReranker`` double: scores docs by query-token overlap."""

    def rerank(self, query, docs):
        q_tokens = set(query.lower().split())
        return [float(sum(1 for t in d.lower().split() if t in q_tokens)) for d in docs]


class TestRerankSlot:
    """``build_facade`` gains a ``reranker`` slot (composition root).

    The ``HybridRetriever`` propagates ``QueryReranker | None``; the composition
    root must expose it so the benchmark SUT and CLI can wire the rerank A/B
    experiment without touching the facade internals (single-point swap). Default
    None keeps the pure-RRF fingerprint (honest, deterministic). Query-time pure:
    wiring a reranker never requires a reindex.
    """

    def _hybrid(self, monkeypatch, tmp_path, db_name, *, clock, reranker=None):
        import seahorse.facade.factory as factory

        monkeypatch.setattr(factory, "_build_passage_embedder", lambda: _FakeAsyncEmbedder())
        return build_facade(
            tmp_path / db_name,
            embedder=_QueryEmbedder384(),
            retrieval_available=True,
            clock=clock,
            reranker=reranker,
        )

    def test_default_reranker_is_none(self, monkeypatch, tmp_path) -> None:
        from seahorse.facade.hybrid_retriever import HybridRetriever

        clock, _ = _controllable_clock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
        facade, storage = self._hybrid(monkeypatch, tmp_path, "f.db", clock=clock)
        try:
            assert isinstance(facade._retriever, HybridRetriever)
            assert facade._retriever._reranker is None  # noqa: SLF001
        finally:
            storage.close()

    def test_reranker_is_propagated(self, monkeypatch, tmp_path) -> None:
        from seahorse.facade.hybrid_retriever import HybridRetriever

        reranker = _FakeReranker()
        clock, _ = _controllable_clock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
        facade, storage = self._hybrid(
            monkeypatch, tmp_path, "f.db", clock=clock, reranker=reranker
        )
        try:
            assert isinstance(facade._retriever, HybridRetriever)
            assert facade._retriever._reranker is reranker  # noqa: SLF001
        finally:
            storage.close()

    def test_recall_rerank_reorders_by_cross_encoder_score(
        self, monkeypatch, tmp_path
    ) -> None:
        """With a reranker the candidate order follows the cross-encoder score.

        Two episodes about France: the one whose summary/subject shares more
        query tokens scores higher and ranks first (the RRF order is replaced by
        the rerank order — score_source=rrf_rerank).
        """
        clock, _ = _controllable_clock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
        facade, storage = self._hybrid(
            monkeypatch, tmp_path, "f.db", clock=clock, reranker=_FakeReranker()
        )
        try:
            facade.remember(
                RememberPayload(
                    body="# France\n\nThe capital of France is Paris.",
                    by=_agent_by(),
                    summary="The capital of France is Paris.",
                )
            )
            facade.remember(
                RememberPayload(
                    body="# Weather\n\nIt is sunny in Madrid.",
                    by=_agent_by(),
                    summary="It is sunny in Madrid.",
                )
            )
            rows = facade.recall("capital of France", k=5)
            assert rows
            assert rows[0].subject == "france"  # the relevant episode ranks first
            assert rows[0].score != 0.0  # hybrid path served (not the listing regime)
        finally:
            storage.close()