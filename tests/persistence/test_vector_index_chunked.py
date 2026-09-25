"""Chunked vector index backend tests.

Migration 013 creates the ``vec_episode_chunks`` vec0 virtual table (parallel
to ``vec_episodes`` — one row per CHUNK, ``chunk_id = "{parent_ep_id}#{i}"``)
plus the lateral ``vec_chunks_meta`` stamp;
``SqliteChunkedVectorIndexRepository`` materializes the SAME signed
``VectorIndexRepository`` Protocol over it: the surface stays PARENT ep_ids
(a chunk_id never escapes), the kNN over-fetches at the chunk level, folds
chunk→parent first-occurrence-wins, and re-caps to ``k`` AFTER the fold
(never before — folding can only shrink the list, so an early cap would
artificially lower recall). PIT variants JOIN the PARENT's ``episode_index``
row (chunks inherit the parent's bi-temporal validity).
"""

from __future__ import annotations

import inspect
import math
import struct
from datetime import UTC, datetime

import pytest

from seahorse.contracts.persistence import VectorIndexRepository
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.vector_index_chunked import (
    CHUNK_KNN_OVERFETCH_FACTOR,
    SqliteChunkedVectorIndexRepository,
    vec_chunks_wipe,
)


@pytest.fixture()
def mgr(tmp_path) -> ConnectionManager:
    m = ConnectionManager(tmp_path / "seahorse.db", pool_size=4, extensions=("vec0",))
    m.open()
    apply_migrations(m.writer)
    yield m
    m.close()


@pytest.fixture()
def chunked(mgr: ConnectionManager) -> SqliteChunkedVectorIndexRepository:
    return SqliteChunkedVectorIndexRepository(mgr)


_EMBED_DIM = 384  # migration 013 vec0 float[384]


def _v(*pos: float) -> bytes:
    """384-dim vector: pos[0] -> component 0, pos[1] -> component 1, rest 0."""
    vals = [0.0] * _EMBED_DIM
    for i, v in enumerate(pos):
        vals[i] = v
    return struct.pack(f"<{_EMBED_DIM}f", *vals)


def _insert_index_row(
    mgr: ConnectionManager,
    ep_id: str,
    *,
    valid_at: str | None = None,
    invalid_at: str | None = None,
    expired_at: str | None = None,
    created_at: str = "2026-01-01T00:00:00+00:00",
    fact_id: str | None = None,
    cognitive_type: str = "fact",
    subject: str = "S",
) -> None:
    if fact_id is None:
        fact_id = f"fact-{ep_id}"
    mgr.writer.execute(
        "INSERT INTO episode_index (ep_id, subject, fact_id, valid_at, invalid_at, "
        "created_at, expired_at, supersedes, cognitive_type, source_type, schema_version, "
        "skip_extraction, title, summary, supersedes_reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 'agent', '3.1', 0, '', '', NULL)",
        (
            ep_id,
            subject,
            fact_id,
            valid_at,
            invalid_at,
            created_at,
            expired_at,
            cognitive_type,
        ),
    )
    mgr.writer.commit()


def _upsert_chunks(
    repo: SqliteChunkedVectorIndexRepository,
    parent_ep_id: str,
    *vecs: bytes,
    model_identity: str = "fastembed:me5-small:abc123:384:fp32",
) -> None:
    repo.upsert_chunks(
        parent_ep_id,
        list(vecs),
        dim=_EMBED_DIM,
        model_identity=model_identity,
        content_hashes=[f"h-{parent_ep_id}#{i}" for i in range(len(vecs))],
        embedded_at="2026-01-01T00:00:00+00:00",
    )


# --- migration 013 -----------------------------------------------------------


def test_013_vec0_schema_has_parent_and_aux_columns(
    mgr: ConnectionManager,
) -> None:
    sql = mgr.writer.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='vec_episode_chunks'"
    ).fetchone()[0]
    assert "USING vec0" in sql
    assert "float[384]" in sql
    for col in ("parent_ep_id", "fact_id", "invalid_at", "cognitive_type", "created_at"):
        assert f"+{col}" in sql


# --- protocol conformance ------------------------------------------------------


def test_chunked_satisfies_vector_protocol(
    chunked: SqliteChunkedVectorIndexRepository,
) -> None:
    assert isinstance(chunked, VectorIndexRepository)


def test_chunked_method_signatures_match_contract() -> None:
    contract = VectorIndexRepository
    stub = SqliteChunkedVectorIndexRepository
    for name in (
        "upsert",
        "distinct_model_identities",
        "knn",
        "knn_state_at",
        "knn_known_at",
        "remove_for_rebuild",
        "rebuild",
        "count",
    ):
        c_sig = inspect.signature(getattr(contract, name))
        s_sig = inspect.signature(getattr(stub, name))
        assert c_sig == s_sig, f"signature drift on VectorIndexRepository.{name}"


# --- upsert fold-into ----------------------------------------------------------


def test_upsert_chunks_writes_one_row_per_chunk_and_meta(
    chunked: SqliteChunkedVectorIndexRepository,
    mgr: ConnectionManager,
) -> None:
    _insert_index_row(mgr, "e1")
    _upsert_chunks(chunked, "e1", _v(1.0, 0.0), _v(0.0, 1.0), _v(0.5, 0.5))
    rows = mgr.writer.execute(
        "SELECT chunk_id, parent_ep_id FROM vec_episode_chunks ORDER BY chunk_id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("e1#0", "e1"),
        ("e1#1", "e1"),
        ("e1#2", "e1"),
    ]
    assert chunked.chunk_count() == 3
    assert chunked.count() == 1  # parent-level count: distinct parents
    assert chunked.distinct_model_identities() == [
        "fastembed:me5-small:abc123:384:fp32"
    ]


def test_upsert_chunks_replaces_parents_chunks_fold_into(
    chunked: SqliteChunkedVectorIndexRepository,
    mgr: ConnectionManager,
) -> None:
    # Re-indexing an episode must leave no stale chunks: DELETE by parent +
    # INSERT ordered in ONE atomic call (the vec_episodes fold-into pattern).
    _insert_index_row(mgr, "e1")
    _upsert_chunks(chunked, "e1", _v(1.0, 0.0), _v(0.0, 1.0))
    _upsert_chunks(chunked, "e1", _v(0.5, 0.5))
    assert chunked.chunk_count() == 1
    rows = mgr.writer.execute(
        "SELECT chunk_id FROM vec_episode_chunks ORDER BY chunk_id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [("e1#0",)]
    # The lateral meta follows the same fold-into (no stale stamps).
    meta = mgr.writer.execute(
        "SELECT chunk_id FROM vec_chunks_meta ORDER BY chunk_id"
    ).fetchall()
    assert [tuple(r) for r in meta] == [("e1#0",)]


def test_upsert_chunks_derives_aux_columns_from_parent_index_row(
    chunked: SqliteChunkedVectorIndexRepository,
    mgr: ConnectionManager,
) -> None:
    _insert_index_row(mgr, "e1", fact_id="f1", cognitive_type="semantic")
    _insert_index_row(mgr, "e2", invalid_at="2026-02-01T00:00:00+00:00")
    _upsert_chunks(chunked, "e1", _v(1.0, 0.0))
    _upsert_chunks(chunked, "e2", _v(0.5, 0.5))
    rows = mgr.writer.execute(
        "SELECT parent_ep_id, fact_id, cognitive_type, invalid_at, created_at "
        "FROM vec_episode_chunks ORDER BY parent_ep_id"
    ).fetchall()
    # EVERY aux column — invalid_at included — inherits from the parent's
    # episode_index row (regression pin: an earlier INSERT hardcoded NULL for
    # invalid_at, silently breaking the vigent_only pushdown).
    assert [tuple(r) for r in rows] == [
        ("e1", "f1", "semantic", None, "2026-01-01T00:00:00+00:00"),
        ("e2", "fact-e2", "fact", "2026-02-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    ]


def test_upsert_single_vector_through_protocol(
    chunked: SqliteChunkedVectorIndexRepository,
    mgr: ConnectionManager,
) -> None:
    # The signed Protocol surface: one vector = the parent's single chunk
    # ("{ep_id}#0"), fold-into by parent.
    _insert_index_row(mgr, "e1")
    chunked.upsert(
        "e1",
        _v(1.0, 0.0),
        dim=_EMBED_DIM,
        model_identity="fastembed:me5-small:abc123:384:fp32",
        content_hash="h-e1",
        embedded_at="2026-01-01T00:00:00+00:00",
    )
    rows = mgr.writer.execute(
        "SELECT chunk_id, parent_ep_id FROM vec_episode_chunks"
    ).fetchall()
    assert [tuple(r) for r in rows] == [("e1#0", "e1")]


# --- kNN fold -------------------------------------------------------------------


def test_knn_folds_multi_chunk_parent_to_one_hit_first_occurrence_wins(
    chunked: SqliteChunkedVectorIndexRepository,
    mgr: ConnectionManager,
) -> None:
    # One parent with 3 chunks ALL near the query: a single hit (the best
    # chunk's distance), never three — the surface stays parent ep_ids.
    _insert_index_row(mgr, "e1")
    _upsert_chunks(chunked, "e1", _v(1.0, 0.0), _v(0.9, 0.1), _v(0.8, 0.2))
    hits = chunked.knn(_v(1.0, 0.0), 5)
    assert [h.ep_id for h in hits] == ["e1"]
    assert hits[0].distance == pytest.approx(0.0)  # best chunk wins


def test_knn_orders_parents_by_best_chunk_distance(
    chunked: SqliteChunkedVectorIndexRepository,
    mgr: ConnectionManager,
) -> None:
    _insert_index_row(mgr, "e1")
    _insert_index_row(mgr, "e2")
    # e1's best chunk (0.9, 0.1) is closer to the query (1.0, 0.0) than e2's
    # best chunk (0.8, 0.2): sqrt(0.02) < sqrt(0.08) — the parent's score is
    # its BEST chunk's distance, not an average or first chunk.
    _upsert_chunks(chunked, "e1", _v(0.5, 0.5), _v(0.9, 0.1))
    _upsert_chunks(chunked, "e2", _v(0.0, 1.0), _v(0.8, 0.2))
    hits = chunked.knn(_v(1.0, 0.0), 5)
    assert [h.ep_id for h in hits] == ["e1", "e2"]
    assert hits[0].distance == pytest.approx(math.sqrt(0.02))
    assert hits[0].score == pytest.approx(1 / (1 + math.sqrt(0.02)))
    assert hits[1].distance == pytest.approx(math.sqrt(0.08))
    assert hits[1].score == pytest.approx(1 / (1 + math.sqrt(0.08)))


def test_knn_chunk_ids_never_escape(
    chunked: SqliteChunkedVectorIndexRepository,
    mgr: ConnectionManager,
) -> None:
    # The contract surface is parent IndexRows: no hit ep_id may contain a
    # chunk suffix (explicit blast-radius pin).
    _insert_index_row(mgr, "e1")
    _upsert_chunks(chunked, "e1", _v(1.0, 0.0), _v(0.0, 1.0))
    hits = chunked.knn(_v(1.0, 0.0), 5)
    assert hits
    assert all("#" not in h.ep_id for h in hits)
    assert all(h.ep_id == "e1" for h in hits)


def test_knn_recaps_to_k_after_fold(
    chunked: SqliteChunkedVectorIndexRepository,
    mgr: ConnectionManager,
) -> None:
    # The re-cap applies AFTER the fold: 2 parents × many chunks, k=1 → one
    # parent hit (over-fetch protected the fold, the cap bounds the result).
    _insert_index_row(mgr, "e1")
    _insert_index_row(mgr, "e2")
    _upsert_chunks(chunked, "e1", _v(1.0, 0.0), _v(0.95, 0.05), _v(0.9, 0.1))
    _upsert_chunks(chunked, "e2", _v(0.85, 0.15), _v(0.8, 0.2))
    hits = chunked.knn(_v(1.0, 0.0), 1)
    assert len(hits) == 1
    assert hits[0].ep_id == "e1"


def test_knn_overfetch_factor_bounds_distinct_parents(
    chunked: SqliteChunkedVectorIndexRepository,
    mgr: ConnectionManager,
) -> None:
    # Sizing guarantee: the write side caps chunks per parent, so the chunk
    # over-fetch yields >= k DISTINCT parents whenever k parents exist —
    # the fold can never starve the re-cap (the flagged risk).
    from seahorse.embeddings.chunker import MAX_CHUNKS_PER_EPISODE

    _insert_index_row(mgr, "e1")
    _upsert_chunks(
        chunked,
        "e1",
        *(_v(1.0 - 0.01 * i, 0.01 * i) for i in range(MAX_CHUNKS_PER_EPISODE)),
    )
    assert CHUNK_KNN_OVERFETCH_FACTOR % MAX_CHUNKS_PER_EPISODE == 0
    # k=1 with the full cap of chunks on ONE parent: the fold returns the
    # parent (never zero parents), the cap bounds the list to k.
    hits = chunked.knn(_v(1.0, 0.0), 1)
    assert [h.ep_id for h in hits] == ["e1"]


def test_knn_vigent_only_excludes_invalidated_parent(
    chunked: SqliteChunkedVectorIndexRepository,
    mgr: ConnectionManager,
) -> None:
    _insert_index_row(mgr, "e1")
    _insert_index_row(mgr, "e2", invalid_at="2026-02-01T00:00:00+00:00")
    _upsert_chunks(chunked, "e1", _v(1.0, 0.0))
    _upsert_chunks(chunked, "e2", _v(0.5, 0.5))
    hits = chunked.knn(_v(1.0, 0.0), 5)
    assert [h.ep_id for h in hits] == ["e1"]


def test_knn_fact_id_filter_and_cognitive_types(
    chunked: SqliteChunkedVectorIndexRepository,
    mgr: ConnectionManager,
) -> None:
    _insert_index_row(mgr, "e1", fact_id="f1", cognitive_type="episodic")
    _insert_index_row(mgr, "e2", fact_id="f2", cognitive_type="semantic")
    _upsert_chunks(chunked, "e1", _v(1.0, 0.0), _v(0.9, 0.1))
    _upsert_chunks(chunked, "e2", _v(0.0, 1.0))
    assert [h.ep_id for h in chunked.knn(_v(1.0, 0.0), 5, fact_id_filter="f1")] == ["e1"]
    assert [h.ep_id for h in chunked.knn(_v(0.0, 1.0), 5, cognitive_types=["semantic"])] == [
        "e2"
    ]


# --- PIT: chunks inherit the parent's bi-temporal validity -----------------------


def test_knn_state_at_joins_parent_window(
    chunked: SqliteChunkedVectorIndexRepository,
    mgr: ConnectionManager,
) -> None:
    # valid_at IS NULL ("from forever") is valid at any t; the PENDING parent
    # (future valid_at) is excluded via the PARENT's episode_index row.
    _insert_index_row(mgr, "e1", valid_at=None)
    _insert_index_row(mgr, "e2", valid_at="2026-03-01T00:00:00+00:00")
    _upsert_chunks(chunked, "e1", _v(1.0, 0.0))
    _upsert_chunks(chunked, "e2", _v(0.5, 0.5))
    t = datetime(2026, 1, 1, tzinfo=UTC)
    assert [h.ep_id for h in chunked.knn_state_at(_v(1.0, 0.0), 5, t)] == ["e1"]


def test_knn_known_at_respects_parent_transaction_time(
    chunked: SqliteChunkedVectorIndexRepository,
    mgr: ConnectionManager,
) -> None:
    _insert_index_row(mgr, "e1", created_at="2026-01-01T00:00:00+00:00")
    _insert_index_row(mgr, "e2", created_at="2026-05-01T00:00:00+00:00")
    _upsert_chunks(chunked, "e1", _v(1.0, 0.0))
    _upsert_chunks(chunked, "e2", _v(0.5, 0.5))
    t = datetime(2026, 3, 1, tzinfo=UTC)
    assert [h.ep_id for h in chunked.knn_known_at(_v(1.0, 0.0), 5, t)] == ["e1"]


# --- rebuild / wipe ---------------------------------------------------------------


def test_remove_for_rebuild_clears_chunks_and_meta(
    chunked: SqliteChunkedVectorIndexRepository,
) -> None:
    _upsert_chunks(chunked, "e1", _v(1.0, 0.0), _v(0.0, 1.0))
    assert chunked.chunk_count() == 2
    chunked.remove_for_rebuild()
    assert chunked.chunk_count() == 0
    assert chunked.count() == 0
    assert chunked.distinct_model_identities() == []


def test_rebuild_is_honest_noop(
    chunked: SqliteChunkedVectorIndexRepository,
) -> None:
    _upsert_chunks(chunked, "e1", _v(1.0, 0.0))
    chunked.rebuild()
    assert chunked.chunk_count() == 1


def test_vec_chunks_wipe_clears_for_sidecar_rebuild(
    chunked: SqliteChunkedVectorIndexRepository,
    mgr: ConnectionManager,
) -> None:
    _upsert_chunks(chunked, "e1", _v(1.0, 0.0), _v(0.0, 1.0))
    assert chunked.chunk_count() == 2
    with mgr.atomic() as w:
        vec_chunks_wipe(w)
    assert chunked.chunk_count() == 0