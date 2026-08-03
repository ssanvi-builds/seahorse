"""Vector/FTS MVP-1 stub tests (Phase 7).

M1-A.2: migration 010 now creates the ``vec_episodes`` vec0 virtual table and the
FTS5 ``episode_fts`` / ``episode_content`` tables, so the schema-side assertions
here verify their presence. The repository impls are still stubs that raise
``NotImplementedError`` (they flip to real behavior in M1-A.3 / M1-A.4).

C8.5: the ``mvp1_axis`` marker (SO-1 safeguard 2) keeps the whole file visible
to the runner without gating the MVP-0 green suite — the NotImplementedError
raises flip en masse when #6 materializes the real sqlite-vec + FTS5 backends.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from seahorse.contracts.persistence import (
    FtsDoc,
    FullTextIndexRepository,
    VectorIndexRepository,
)
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.fts_index import SqliteFullTextIndexRepository
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.vector_index import SqliteVectorIndexRepository

pytestmark = pytest.mark.mvp1_axis


@pytest.fixture()
def mgr(tmp_path) -> ConnectionManager:
    # M1-A.2: the vec0 extension is required once migration 010 creates the
    # virtual table (``USING vec0``); mirrors the composition root (Storage).
    m = ConnectionManager(tmp_path / "seahorse.db", pool_size=4, extensions=("vec0",))
    m.open()
    apply_migrations(m.writer)
    yield m
    m.close()


@pytest.fixture()
def vector(mgr: ConnectionManager) -> SqliteVectorIndexRepository:
    return SqliteVectorIndexRepository(mgr)


@pytest.fixture()
def fts(mgr: ConnectionManager) -> SqliteFullTextIndexRepository:
    return SqliteFullTextIndexRepository(mgr)


# --- structural conformance --------------------------------------------------


def test_vector_stub_satisfies_protocol(vector: SqliteVectorIndexRepository) -> None:
    assert isinstance(vector, VectorIndexRepository)
    assert not hasattr(vector, "atomic")  # SO-7a.6


def test_fts_stub_satisfies_protocol(fts: SqliteFullTextIndexRepository) -> None:
    assert isinstance(fts, FullTextIndexRepository)
    assert not hasattr(fts, "atomic")  # SO-7a.6


# --- every vector method raises NotImplementedError --------------------------


def test_vector_upsert_raises(vector: SqliteVectorIndexRepository) -> None:
    with pytest.raises(NotImplementedError):
        vector.upsert(
            "e1",
            b"\x00" * 8,
            dim=4,
            model_identity="m",
            content_hash="h",
            embedded_at="2026-01-01T00:00:00+00:00",
        )


def test_vector_distinct_model_identities_raises(
    vector: SqliteVectorIndexRepository,
) -> None:
    with pytest.raises(NotImplementedError):
        vector.distinct_model_identities()


def test_vector_knn_raises(vector: SqliteVectorIndexRepository) -> None:
    with pytest.raises(NotImplementedError):
        vector.knn(b"\x00" * 8, 5)


def test_vector_knn_state_at_raises(vector: SqliteVectorIndexRepository) -> None:
    with pytest.raises(NotImplementedError):
        vector.knn_state_at(b"\x00" * 8, 5, datetime(2026, 1, 1, tzinfo=UTC))


def test_vector_knn_known_at_raises(vector: SqliteVectorIndexRepository) -> None:
    with pytest.raises(NotImplementedError):
        vector.knn_known_at(b"\x00" * 8, 5, datetime(2026, 1, 1, tzinfo=UTC))


def test_vector_remove_for_rebuild_raises(vector: SqliteVectorIndexRepository) -> None:
    with pytest.raises(NotImplementedError):
        vector.remove_for_rebuild()


def test_vector_rebuild_raises(vector: SqliteVectorIndexRepository) -> None:
    with pytest.raises(NotImplementedError):
        vector.rebuild()


def test_vector_count_raises(vector: SqliteVectorIndexRepository) -> None:
    with pytest.raises(NotImplementedError):
        vector.count()


# --- every fts method raises NotImplementedError -----------------------------


def test_fts_upsert_raises(fts: SqliteFullTextIndexRepository) -> None:
    with pytest.raises(NotImplementedError):
        fts.upsert(FtsDoc(ep_id="e1", body_md="body"))


def test_fts_search_raises(fts: SqliteFullTextIndexRepository) -> None:
    with pytest.raises(NotImplementedError):
        fts.search("query", 5)


def test_fts_search_state_at_raises(fts: SqliteFullTextIndexRepository) -> None:
    with pytest.raises(NotImplementedError):
        fts.search_state_at("query", 5, datetime(2026, 1, 1, tzinfo=UTC))


def test_fts_search_known_at_raises(fts: SqliteFullTextIndexRepository) -> None:
    with pytest.raises(NotImplementedError):
        fts.search_known_at("query", 5, datetime(2026, 1, 1, tzinfo=UTC))


def test_fts_remove_for_rebuild_raises(fts: SqliteFullTextIndexRepository) -> None:
    with pytest.raises(NotImplementedError):
        fts.remove_for_rebuild("e1")


def test_fts_rebuild_raises(fts: SqliteFullTextIndexRepository) -> None:
    with pytest.raises(NotImplementedError):
        fts.rebuild([FtsDoc(ep_id="e1", body_md="body")])


def test_fts_count_raises(fts: SqliteFullTextIndexRepository) -> None:
    with pytest.raises(NotImplementedError):
        fts.count()


# --- signatures match the signed contract ------------------------------------


def test_vector_method_signatures_match_contract() -> None:
    contract = VectorIndexRepository
    stub = SqliteVectorIndexRepository
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


def test_fts_method_signatures_match_contract() -> None:
    contract = FullTextIndexRepository
    stub = SqliteFullTextIndexRepository
    for name in (
        "upsert",
        "search",
        "search_state_at",
        "search_known_at",
        "remove_for_rebuild",
        "rebuild",
        "count",
    ):
        c_sig = inspect.signature(getattr(contract, name))
        s_sig = inspect.signature(getattr(stub, name))
        assert c_sig == s_sig, f"signature drift on FullTextIndexRepository.{name}"


# --- MVP-0 migrations do NOT create the vec0 / FTS5 tables -------------------


def test_010_creates_vec_episodes_and_fts_tables(mgr: ConnectionManager) -> None:
    names = {
        r[0]
        for r in mgr.writer.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "vec_episodes_meta" in names  # SO-7a lateral
    assert "vec_episodes" in names  # vec0 virtual table (migration 010)
    assert "episode_fts" in names  # FTS5 table (migration 010)
