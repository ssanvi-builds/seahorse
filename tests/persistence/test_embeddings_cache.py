"""SqliteEmbeddingsCacheRepository tests (Phase 5d).

Isolation by (model_identity, role), INSERT OR REPLACE on conflict, count, and
trim keeping the newest rows (LRU by created_at).
"""

from __future__ import annotations

import pytest

from seahorse.contracts.persistence import EmbeddingsCacheRepository
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_embeddings_cache import SqliteEmbeddingsCacheRepository


@pytest.fixture()
def cache(tmp_path) -> SqliteEmbeddingsCacheRepository:
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=4, extensions=("vec0",))
    mgr.open()
    apply_migrations(mgr.writer)
    repo = SqliteEmbeddingsCacheRepository(mgr)
    yield repo
    mgr.close()


def _vec(seed: int) -> bytes:
    # a 2-dim float32 vector as 8 bytes
    return bytes([seed, 0, 0, 0, seed, 0, 0, 0])


def test_structurally_satisfies_protocol(cache: SqliteEmbeddingsCacheRepository) -> None:
    assert isinstance(cache, EmbeddingsCacheRepository)
    assert not hasattr(cache, "atomic")  # SO-7a.6


def test_batch_insert_then_lookup(cache: SqliteEmbeddingsCacheRepository) -> None:
    cache.batch_insert("m1", "passage", ["h1", "h2"], [_vec(1), _vec(2)])
    found = cache.batch_lookup("m1", "passage", ["h1", "h2", "h3"])
    assert set(found) == {"h1", "h2"}
    assert found["h1"] == _vec(1)


def test_isolation_by_model_and_role(cache: SqliteEmbeddingsCacheRepository) -> None:
    cache.batch_insert("m1", "passage", ["h1"], [_vec(1)])
    cache.batch_insert("m1", "query", ["h1"], [_vec(2)])
    cache.batch_insert("m2", "passage", ["h1"], [_vec(3)])
    # same content_hash, different (model, role) -> independent vectors
    assert cache.batch_lookup("m1", "passage", ["h1"])["h1"] == _vec(1)
    assert cache.batch_lookup("m1", "query", ["h1"])["h1"] == _vec(2)
    assert cache.batch_lookup("m2", "passage", ["h1"])["h1"] == _vec(3)


def test_insert_replaces_on_conflict(cache: SqliteEmbeddingsCacheRepository) -> None:
    cache.batch_insert("m1", "passage", ["h1"], [_vec(1)])
    cache.batch_insert("m1", "passage", ["h1"], [_vec(9)])  # same key -> replace
    assert cache.batch_lookup("m1", "passage", ["h1"])["h1"] == _vec(9)
    assert cache.count() == 1  # one row, not two


def test_batch_insert_length_mismatch_raises(
    cache: SqliteEmbeddingsCacheRepository,
) -> None:
    with pytest.raises(ValueError):
        cache.batch_insert("m1", "passage", ["h1", "h2"], [_vec(1)])


def test_batch_lookup_empty_input(cache: SqliteEmbeddingsCacheRepository) -> None:
    assert cache.batch_lookup("m1", "passage", []) == {}


def test_count(cache: SqliteEmbeddingsCacheRepository) -> None:
    cache.batch_insert("m1", "passage", ["h1", "h2", "h3"], [_vec(1), _vec(2), _vec(3)])
    assert cache.count() == 3


def test_trim_keeps_newest(cache: SqliteEmbeddingsCacheRepository) -> None:
    # insert sequentially so created_at is monotonic (datetime('now') resolution: 1s).
    # Use distinct content hashes so each inserts a separate row.
    hashes = [f"h{i}" for i in range(4)]
    vecs = [_vec(i) for i in range(4)]
    cache.batch_insert("m1", "passage", hashes, vecs)
    cache.trim(2)
    assert cache.count() == 2
    # the newest two (h2, h3 — inserted last) survive
    remaining = cache.batch_lookup("m1", "passage", hashes)
    assert set(remaining) == {"h2", "h3"}
