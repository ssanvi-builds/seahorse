"""Retrieval indexer write-path.

``RetrievalIndexer`` embeds the passage body and upserts vec0 + FTS in one
atomic, driven by the write path (``StubWritePath.ingest`` → best-effort) and
by ``seahorse index rebuild`` (backfill). Best-effort: an embedder failure must
never fail the episode write (the index is derived).
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from seahorse.contracts.episode import Episode
from seahorse.embeddings.cache import _content_hash
from seahorse.embeddings.chunker import chunk_text
from seahorse.embeddings.indexer import RetrievalIndexer
from seahorse.embeddings.types import CHUNK_MODES, ModelIdentity
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.fts_index import SqliteFullTextIndexRepository
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_audit import SqliteAuditEventRepository
from seahorse.persistence.sqlite_episode_repo import SqliteEpisodeRepository
from seahorse.persistence.vector_index import SqliteVectorIndexRepository
from seahorse.persistence.vector_index_chunked import SqliteChunkedVectorIndexRepository


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


def _episode(ep_id: str, body: str, *, summary: str | None = None) -> Episode:
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
        summary=summary,
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


# ----------------------------------------------------------- embed_mode

def test_embed_mode_body_summary_combines_body_and_summary(mgr) -> None:
    # Vectorial candidate: embed body+summary so the vector captures
    # the editorial summary, not just the body. The effective embedded text is
    # ``summary\n\nbody`` (summary leads — the distilled signal).
    episodes = SqliteEpisodeRepository(mgr)
    vector = SqliteVectorIndexRepository(mgr)
    fts = SqliteFullTextIndexRepository(mgr)
    embedder = _FakePassageEmbedder()
    indexer = RetrievalIndexer(
        embedder, vector, fts, episodes, mgr, embed_mode="body+summary"
    )
    episodes.append(_episode("e1", "detailed body text", summary="The distilled gist"))
    indexer.index_episode("e1")
    assert vector.count() == 1
    assert fts.count() == 1  # FTS still indexes the body (unchanged)
    assert embedder.calls == [(["The distilled gist\n\ndetailed body text"], "passage")]
    row = mgr.writer.execute(
        "SELECT content_hash FROM vec_episodes_meta WHERE ep_id='e1'"
    ).fetchone()
    assert row[0] == _content_hash("The distilled gist\n\ndetailed body text", "passage")


def test_embed_mode_body_summary_without_summary_falls_back_to_body(mgr) -> None:
    # Honest fallback: no summary → embed the body alone (never a fabricated
    # text, never skip the episode).
    episodes = SqliteEpisodeRepository(mgr)
    vector = SqliteVectorIndexRepository(mgr)
    fts = SqliteFullTextIndexRepository(mgr)
    embedder = _FakePassageEmbedder()
    indexer = RetrievalIndexer(
        embedder, vector, fts, episodes, mgr, embed_mode="body+summary"
    )
    episodes.append(_episode("e1", "madrid spain"))
    indexer.index_episode("e1")
    assert embedder.calls == [(["madrid spain"], "passage")]


def test_embed_mode_body_summary_is_default(mgr) -> None:
    # body+summary is the product default — the summary leads the vector
    # (distilled signal first).
    indexer, embedder, vector, fts, episodes = _stack(mgr)  # embed_mode="body+summary"
    episodes.append(_episode("e1", "madrid spain", summary="a gist"))
    indexer.index_episode("e1")
    assert embedder.calls == [(["a gist\n\nmadrid spain"], "passage")]


def test_reindex_with_body_summary_produces_distinct_vectors(mgr) -> None:
    # Reindexing the SAME episode under body+summary re-embeds honestly — the
    # effective text changes (summary leads), so the content_hash over the
    # EFFECTIVE text differs → cache miss vs the body-only index.
    episodes = SqliteEpisodeRepository(mgr)
    vector = SqliteVectorIndexRepository(mgr)
    fts = SqliteFullTextIndexRepository(mgr)
    episodes.append(_episode("e1", "detailed body text", summary="The distilled gist"))

    body_embedder = _FakePassageEmbedder()
    body_indexer = RetrievalIndexer(
        body_embedder, vector, fts, episodes, mgr, embed_mode="body"
    )
    body_indexer.index_episode("e1")
    assert body_embedder.calls == [(["detailed body text"], "passage")]
    body_hash = mgr.writer.execute(
        "SELECT content_hash FROM vec_episodes_meta WHERE ep_id='e1'"
    ).fetchone()[0]
    assert body_hash == _content_hash("detailed body text", "passage")

    bs_embedder = _FakePassageEmbedder()
    bs_indexer = RetrievalIndexer(
        bs_embedder, vector, fts, episodes, mgr, embed_mode="body+summary"
    )
    bs_indexer.index_episode("e1")
    assert bs_embedder.calls == [(["The distilled gist\n\ndetailed body text"], "passage")]
    bs_hash = mgr.writer.execute(
        "SELECT content_hash FROM vec_episodes_meta WHERE ep_id='e1'"
    ).fetchone()[0]
    assert bs_hash == _content_hash("The distilled gist\n\ndetailed body text", "passage")
    # distinct effective text → distinct content_hash → honest cache miss + a
    # different vector than the body-only reindex
    assert body_hash != bs_hash


def test_invalid_embed_mode_rejected(mgr) -> None:
    episodes = SqliteEpisodeRepository(mgr)
    vector = SqliteVectorIndexRepository(mgr)
    fts = SqliteFullTextIndexRepository(mgr)
    with pytest.raises(ValueError, match="embed_mode"):
        RetrievalIndexer(
            _FakePassageEmbedder(), vector, fts, episodes, mgr, embed_mode="bogus"
        )


def test_index_episode_best_effort_on_embedder_failure(mgr) -> None:
    # An embedder failure must NOT raise out of the write path — the
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
    # remember (skip path) drives the indexer when wired.
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


# ----------------------------------------------------------- chunk_mode (P3.2)


def _chunk_stack(mgr):
    """The chunked-mode stack: the chunked vector repo + chunk_mode='chunked'."""
    episodes = SqliteEpisodeRepository(mgr)
    chunked = SqliteChunkedVectorIndexRepository(mgr)
    fts = SqliteFullTextIndexRepository(mgr)
    embedder = _FakePassageEmbedder()
    indexer = RetrievalIndexer(
        embedder, chunked, fts, episodes, mgr, chunk_mode="chunked"
    )
    return indexer, embedder, chunked, fts, episodes


_LONG_BODY = "chunk window filler sentence. " * 180  # spans several chunk windows


def test_chunk_modes_constant() -> None:
    # The flag surface: ``off`` (default, one embedding per episode) is the
    # baseline; ``chunked`` is the P3.2 seam. Single source in types.py.
    assert CHUNK_MODES == ("off", "chunked")


def test_invalid_chunk_mode_rejected(mgr) -> None:
    episodes = SqliteEpisodeRepository(mgr)
    chunked = SqliteChunkedVectorIndexRepository(mgr)
    fts = SqliteFullTextIndexRepository(mgr)
    with pytest.raises(ValueError, match="chunk_mode"):
        RetrievalIndexer(
            _FakePassageEmbedder(), chunked, fts, episodes, mgr, chunk_mode="bogus"
        )


def test_chunked_mode_requires_chunked_repository(mgr) -> None:
    # Fail-fast: chunk_mode='chunked' cannot write through the classic repo —
    # the signed Protocol carries no multi-chunk write (upsert_chunks is the
    # chunked repo's own surface).
    episodes = SqliteEpisodeRepository(mgr)
    vector = SqliteVectorIndexRepository(mgr)
    fts = SqliteFullTextIndexRepository(mgr)
    with pytest.raises(ValueError, match="chunked vector repository"):
        RetrievalIndexer(
            _FakePassageEmbedder(), vector, fts, episodes, mgr, chunk_mode="chunked"
        )


def test_chunk_mode_off_default_keeps_classic_path(mgr) -> None:
    # The default stack is unchanged: vec_episodes gets the row, the chunk
    # table stays EMPTY (the flag-off blast radius pinned at the indexer).
    indexer, _embedder, vector, _fts, episodes = _stack(mgr)
    chunked = SqliteChunkedVectorIndexRepository(mgr)
    episodes.append(_episode("e1", "madrid spain"))
    indexer.index_episode("e1")
    assert vector.count() == 1
    assert chunked.chunk_count() == 0


def test_chunked_mode_writes_one_row_per_chunk(mgr) -> None:
    indexer, embedder, chunked, fts, episodes = _chunk_stack(mgr)
    episodes.append(_episode("e1", _LONG_BODY))
    indexer.index_episode("e1")
    chunks = chunk_text(_LONG_BODY)  # the indexer feeds the chunker's exact output
    assert len(chunks) > 1
    assert chunked.chunk_count() == len(chunks)
    assert chunked.count() == 1  # one PARENT (distinct parents)
    # ONE batched embed call carrying ALL chunks (single roundtrip).
    assert len(embedder.calls) == 1
    assert embedder.calls[0] == (chunks, "passage")
    # Per-chunk content hashes: each chunk is a distinct embedded text, so a
    # chunker change re-embeds honestly (cache miss on the moved window).
    meta = mgr.writer.execute(
        "SELECT content_hash FROM vec_chunks_meta ORDER BY chunk_id"
    ).fetchall()
    assert [r[0] for r in meta] == [_content_hash(c, "passage") for c in chunks]


def test_chunked_mode_short_body_is_single_chunk(mgr) -> None:
    indexer, embedder, chunked, _fts, episodes = _chunk_stack(mgr)
    episodes.append(_episode("e1", "madrid spain"))
    indexer.index_episode("e1")
    assert embedder.calls == [(["madrid spain"], "passage")]
    assert chunked.chunk_count() == 1  # "{e1}#0" — the flag-off embedding surface


def test_chunked_mode_fts_stays_episode_level(mgr) -> None:
    # Documented caveat: FTS remains one doc per EPISODE — chunking changes
    # only the vector surface (BM25 windows need their own position-aware
    # eval; out of scope for P3.2).
    indexer, _embedder, chunked, fts, episodes = _chunk_stack(mgr)
    episodes.append(_episode("e1", _LONG_BODY))
    indexer.index_episode("e1")
    assert chunked.chunk_count() > 1
    assert fts.count() == 1


def test_chunked_mode_reindex_replaces_parents_chunks(mgr) -> None:
    # Re-indexing is a fold-into by parent: no stale chunks survive the new
    # (shorter) body, in either the chunk table or the lateral meta.
    indexer, _embedder, chunked, _fts, _episodes = _chunk_stack(mgr)
    ep = _episode("e1", _LONG_BODY)
    indexer.index_episode_from_note(ep, _LONG_BODY)
    assert chunked.chunk_count() > 1
    indexer.index_episode_from_note(ep, "one short body")
    assert chunked.chunk_count() == 1
    rows = mgr.writer.execute("SELECT chunk_id FROM vec_episode_chunks").fetchall()
    assert [r[0] for r in rows] == ["e1#0"]
    meta = mgr.writer.execute("SELECT chunk_id FROM vec_chunks_meta").fetchall()
    assert [r[0] for r in meta] == ["e1#0"]


def test_chunked_mode_composes_with_embed_mode(mgr) -> None:
    # chunking splits the SAME effective text embed_mode builds — the summary
    # leads the first chunk (composability is the seam's design point).
    episodes = SqliteEpisodeRepository(mgr)
    chunked = SqliteChunkedVectorIndexRepository(mgr)
    fts = SqliteFullTextIndexRepository(mgr)
    embedder = _FakePassageEmbedder()
    indexer = RetrievalIndexer(
        embedder,
        chunked,
        fts,
        episodes,
        mgr,
        embed_mode="body+summary",
        chunk_mode="chunked",
    )
    summary = "The distilled gist."
    episodes.append(_episode("e1", _LONG_BODY, summary=summary))
    indexer.index_episode("e1")
    effective = f"{summary}\n\n{_LONG_BODY}"
    assert embedder.calls == [(chunk_text(effective), "passage")]
    assert chunked.chunk_count() == len(chunk_text(effective))


def test_chunked_mode_best_effort_on_embedder_failure(mgr) -> None:
    # The best-effort contract is unchanged under chunking: an embedder
    # failure never raises out of the write path (the index is derived).

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
    chunked = SqliteChunkedVectorIndexRepository(mgr)
    fts = SqliteFullTextIndexRepository(mgr)
    episodes.append(_episode("e1", _LONG_BODY))
    indexer = RetrievalIndexer(
        _BrokenEmbedder(), chunked, fts, episodes, mgr, chunk_mode="chunked"
    )
    indexer.index_episode("e1")  # swallows the embed error
    assert chunked.chunk_count() == 0
    assert fts.count() == 0
