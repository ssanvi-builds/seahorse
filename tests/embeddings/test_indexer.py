"""Retrieval indexer write-path (M1-B.5).

``RetrievalIndexer`` embeds the passage body and upserts vec0 + FTS in one
atomic, driven by the write path (``StubWritePath.ingest`` → best-effort) and
by ``seahorse index rebuild`` (backfill). Best-effort: an embedder failure must
never fail the episode write (ADR-10 — the index is derived).
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from seahorse.contracts.episode import Episode
from seahorse.embeddings.cache import _content_hash
from seahorse.embeddings.indexer import RetrievalIndexer
from seahorse.embeddings.types import ModelIdentity
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.fts_index import SqliteFullTextIndexRepository
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_audit import SqliteAuditEventRepository
from seahorse.persistence.sqlite_episode_repo import SqliteEpisodeRepository
from seahorse.persistence.vector_index import SqliteVectorIndexRepository


@pytest.fixture()
def mgr(tmp_path) -> ConnectionManager:
    m = ConnectionManager(tmp_path / "seahorse.db", pool_size=2, extensions=("vec0",))
    m.open()
    apply_migrations(m.writer)
    yield m
    m.close()


class _FakePassageEmbedder:
    dim = 384

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []

    async def embed(self, texts, role):
        self.calls.append((list(texts), role))
        return np.ones((len(texts), self.dim), dtype=np.float32)

    def model_identity(self) -> ModelIdentity:
        return ModelIdentity(
            backend="test", model_name="m", revision="r",
            dim=384, quantization="fp32", normalized=True,
        )


def _episode(ep_id: str, body: str) -> Episode:
    return Episode(
        id=ep_id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version="3.1",
        provenance={},
        body=body,
        fact_id=f"fact-{ep_id}",
        cognitive_type="fact",
        source_type="agent",
        title=ep_id,
    )


def _stack(
    mgr: ConnectionManager,
) -> tuple[
    RetrievalIndexer,
    _FakePassageEmbedder,
    SqliteVectorIndexRepository,
    SqliteFullTextIndexRepository,
    SqliteEpisodeRepository,
]:
    episodes = SqliteEpisodeRepository(mgr)
    vector = SqliteVectorIndexRepository(mgr)
    fts = SqliteFullTextIndexRepository(mgr)
    embedder = _FakePassageEmbedder()
    indexer = RetrievalIndexer(embedder, vector, fts, episodes, mgr)
    return indexer, embedder, vector, fts, episodes


def test_index_episode_upserts_vector_and_fts_in_one_atomic(mgr) -> None:
    indexer, embedder, vector, fts, episodes = _stack(mgr)
    episodes.append(_episode("e1", "madrid spain"))
    indexer.index_episode("e1")
    assert vector.count() == 1
    assert fts.count() == 1
    assert embedder.calls == [(["madrid spain"], "passage")]  # role=passage
    row = mgr.writer.execute(
        "SELECT content_hash, model_identity, dim FROM vec_episodes_meta WHERE ep_id='e1'"
    ).fetchone()
    assert row[0] == _content_hash("madrid spain", "passage")
    assert row[1] == "test:m:r:384:fp32"  # embedder cache_key
    assert row[2] == 384


def test_index_episode_skips_empty_body_and_missing_episode(mgr) -> None:
    indexer, embedder, vector, fts, episodes = _stack(mgr)
    episodes.append(_episode("e1", "   "))  # whitespace-only body
    indexer.index_episode("e1")
    indexer.index_episode("missing")
    assert vector.count() == 0
    assert fts.count() == 0
    assert embedder.calls == []


def test_index_episode_best_effort_on_embedder_failure(mgr) -> None:
    # ADR-10: an embedder failure must NOT raise out of the write path — the
    # index is derived; the episode write already succeeded.
    from seahorse.embeddings.indexer import RetrievalIndexer as RI

    class _BrokenEmbedder:
        dim = 384

        async def embed(self, texts, role):
            raise RuntimeError("onnx session unavailable")

        def model_identity(self) -> ModelIdentity:
            return ModelIdentity(
                backend="test", model_name="m", revision="r",
                dim=384, quantization="fp32", normalized=True,
            )

    episodes = SqliteEpisodeRepository(mgr)
    vector = SqliteVectorIndexRepository(mgr)
    fts = SqliteFullTextIndexRepository(mgr)
    episodes.append(_episode("e1", "madrid spain"))
    indexer = RI(_BrokenEmbedder(), vector, fts, episodes, mgr)
    indexer.index_episode("e1")  # swallows the embed error
    assert vector.count() == 0
    assert fts.count() == 0


def test_stub_write_path_indexes_after_ingest(mgr) -> None:
    # M1-B.5: remember (skip path) drives the indexer when wired.
    from seahorse.engine.engine import BiTemporalEngine
    from seahorse.facade.types import RememberPayload
    from seahorse.write_path.stub import StubWritePath

    episodes = SqliteEpisodeRepository(mgr)
    engine = BiTemporalEngine(repo=episodes, audit=SqliteAuditEventRepository(mgr))
    indexer, _embedder, vector, fts, _episodes = _stack(mgr)
    wp = StubWritePath(engine, indexer=indexer)
    result = wp.ingest(
        RememberPayload(body="madrid spain", by={"source_type": "agent"}),
        "skip",
    )
    assert result.status == "ACTIVE"
    assert result.ep_id is not None
    assert vector.count() == 1
    assert fts.count() == 1
