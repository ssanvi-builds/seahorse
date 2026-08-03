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
import struct
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


# --- vector behavior (M1-A.3) -------------------------------------------------

_KNN_OVERFETCH_FACTOR = 5  # mirrors the impl constant; PIT JOIN may drop hits


_EMBED_DIM = 384  # migration 010 vec0 float[384]


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
) -> None:
    if fact_id is None:
        fact_id = f"fact-{ep_id}"  # keep distinct vigent fact_ids (I11 mirror)
    mgr.writer.execute(
        "INSERT INTO episode_index (ep_id, subject, fact_id, valid_at, invalid_at, "
        "created_at, expired_at, supersedes, cognitive_type, source_type, schema_version, "
        "skip_extraction, title, summary, supersedes_reason) "
        "VALUES (?, 'S', ?, ?, ?, ?, ?, NULL, ?, 'agent', '3.1', 0, '', '', NULL)",
        (ep_id, fact_id, valid_at, invalid_at, created_at, expired_at, cognitive_type),
    )
    mgr.writer.commit()


def _upsert_vector(
    vector: SqliteVectorIndexRepository,
    ep_id: str,
    *vals: float,
    model_identity: str = "fastembed:me5-small:abc123:384:fp32",
) -> None:
    vector.upsert(
        ep_id,
        _v(*vals),
        dim=_EMBED_DIM,
        model_identity=model_identity,
        content_hash=f"h-{ep_id}",
        embedded_at="2026-01-01T00:00:00+00:00",
    )


def test_vector_upsert_count_and_distinct_identities(
    vector: SqliteVectorIndexRepository,
) -> None:
    _upsert_vector(vector, "e1", 1.0, 0.0, 0.0, 0.0)
    _upsert_vector(vector, "e2", 0.0, 1.0, 0.0, 0.0)
    assert vector.count() == 2
    assert vector.distinct_model_identities() == ["fastembed:me5-small:abc123:384:fp32"]


def test_vector_upsert_overwrites_same_ep_id(
    vector: SqliteVectorIndexRepository,
) -> None:
    _upsert_vector(vector, "e1", 1.0, 0.0, 0.0, 0.0)
    _upsert_vector(vector, "e1", 0.0, 1.0, 0.0, 0.0)
    assert vector.count() == 1
    hits = vector.knn(_v(0.0, 1.0, 0.0, 0.0), 5)
    assert [h.ep_id for h in hits] == ["e1"]


def test_vector_knn_orders_by_distance_and_scores(
    vector: SqliteVectorIndexRepository,
) -> None:
    _upsert_vector(vector, "e1", 1.0, 0.0, 0.0, 0.0)
    _upsert_vector(vector, "e2", 0.0, 1.0, 0.0, 0.0)
    hits = vector.knn(_v(1.0, 0.0, 0.0, 0.0), 5)
    assert [h.ep_id for h in hits] == ["e1", "e2"]
    assert hits[0].distance == pytest.approx(0.0)
    assert hits[0].score == pytest.approx(1.0)  # 1/(1+0)
    assert hits[1].distance == pytest.approx(2**0.5)  # L2 over unit vectors
    assert hits[1].score == pytest.approx(1 / (1 + 2**0.5))


def test_vector_knn_vigent_only_excludes_invalidated(
    vector: SqliteVectorIndexRepository, mgr: ConnectionManager
) -> None:
    _upsert_vector(vector, "e1", 1.0, 0.0, 0.0, 0.0)
    _upsert_vector(vector, "e2", 0.0, 1.0, 0.0, 0.0)
    # M1-A.5 keeps vec_episodes.invalid_at in sync with episodes; here simulate
    # the sync directly to pin the vigent-only pushdown on the vec0 column.
    mgr.writer.execute(
        "UPDATE vec_episodes SET invalid_at = '2026-02-01T00:00:00+00:00' WHERE ep_id = 'e2'"
    )
    mgr.writer.commit()
    hits = vector.knn(_v(1.0, 0.0, 0.0, 0.0), 5)
    assert [h.ep_id for h in hits] == ["e1"]


def test_vector_knn_fact_id_filter_and_cognitive_types(
    vector: SqliteVectorIndexRepository, mgr: ConnectionManager
) -> None:
    # upsert derives fact_id / cognitive_type from episode_index (aux columns),
    # so the knn pushdown filters work without a JOIN.
    _insert_index_row(mgr, "e1", fact_id="f1", cognitive_type="episodic")
    _insert_index_row(mgr, "e2", fact_id="f2", cognitive_type="semantic")
    _upsert_vector(vector, "e1", 1.0, 0.0, 0.0, 0.0)
    _upsert_vector(vector, "e2", 0.0, 1.0, 0.0, 0.0)
    fact_hits = vector.knn(_v(1.0, 0.0, 0.0, 0.0), 5, fact_id_filter="f1")
    assert [h.ep_id for h in fact_hits] == ["e1"]
    cog_hits = vector.knn(_v(1.0, 0.0, 0.0, 0.0), 5, cognitive_types=["semantic"])
    assert [h.ep_id for h in cog_hits] == ["e2"]
    hits = vector.knn(
        _v(1.0, 0.0, 0.0, 0.0), 5, cognitive_types=["episodic", "semantic"]
    )
    assert {h.ep_id for h in hits} == {"e1", "e2"}


def test_vector_knn_state_at_includes_from_forever_and_excludes_future_valid(
    vector: SqliteVectorIndexRepository, mgr: ConnectionManager
) -> None:
    # CC-2: valid_at IS NULL ("from forever") is valid at any t; PENDING
    # (valid_at in the future) is excluded by the state_at predicate.
    _insert_index_row(mgr, "e1", valid_at=None)
    _insert_index_row(mgr, "e2", valid_at="2026-03-01T00:00:00+00:00")
    _upsert_vector(vector, "e1", 1.0, 0.0, 0.0, 0.0)
    _upsert_vector(vector, "e2", 0.0, 1.0, 0.0, 0.0)
    t = datetime(2026, 1, 1, tzinfo=UTC)
    hits = vector.knn_state_at(_v(1.0, 0.0, 0.0, 0.0), 5, t)
    assert [h.ep_id for h in hits] == ["e1"]


def test_vector_knn_known_at_respects_transaction_time(
    vector: SqliteVectorIndexRepository, mgr: ConnectionManager
) -> None:
    _insert_index_row(mgr, "e1", created_at="2026-01-01T00:00:00+00:00")
    _insert_index_row(mgr, "e2", created_at="2026-05-01T00:00:00+00:00")
    _upsert_vector(vector, "e1", 1.0, 0.0, 0.0, 0.0)
    _upsert_vector(vector, "e2", 0.0, 1.0, 0.0, 0.0)
    t = datetime(2026, 3, 1, tzinfo=UTC)
    hits = vector.knn_known_at(_v(1.0, 0.0, 0.0, 0.0), 5, t)
    assert [h.ep_id for h in hits] == ["e1"]


def test_vector_remove_for_rebuild_clears(
    vector: SqliteVectorIndexRepository,
) -> None:
    _upsert_vector(vector, "e1", 1.0, 0.0, 0.0, 0.0)
    assert vector.count() == 1
    vector.remove_for_rebuild()
    assert vector.count() == 0
    assert vector.distinct_model_identities() == []


def test_vector_rebuild_is_honest_noop(
    vector: SqliteVectorIndexRepository,
) -> None:
    # The signed contract's rebuild() takes no args; the actual backfill is #7's
    # job (RetrievalIndexer / index rebuild). Here it is an honest no-op.
    _upsert_vector(vector, "e1", 1.0, 0.0, 0.0, 0.0)
    vector.rebuild()
    assert vector.count() == 1


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
