"""``read_sidecar_status`` — read-only sidecar snapshot for ``seahorse inspect`` (#3).

Guards the #6-owned SQL that backs the CLI ``inspect`` command (commit 5). The
snapshot reports schema_version + episode/episode_index counts + the two
bi-temporal predicates (vigente vs activo-ahora) + the last known file mtime.

The predicates mirror the engine's bi-temporal definitions verbatim:
- vigente      = ``invalid_at IS NULL AND expired_at IS NULL``
  (``SqliteEpisodeIndexRepository.find_vigent_row_by_fact_id``)
- activo-ahora = ``(valid_at IS NULL OR valid_at <= now)
                  AND (invalid_at IS NULL OR invalid_at > now)``
  (``_pit_predicate("state_at", now)``); CC-2 (C8.6): ``valid_at IS NULL``
  ("from forever") is valid at any ``t`` and is INCLUDED.

The two measure DIFFERENT axes (vigente = both axes open; activo-ahora =
valid-time active now, ignoring decay), so a row can be activo-ahora but NOT
vigente (e.g. a future-scheduled invalidation, or a decayed-but-valid row).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sidecar_status import SidecarSnapshot, read_sidecar_status

_NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


@pytest.fixture()
def conn(tmp_path: Path):
    mgr = ConnectionManager(tmp_path / "seahorse.db", pool_size=0, extensions=("vec0",))
    mgr.open()
    apply_migrations(mgr.writer)
    yield mgr.writer
    mgr.close()


def _insert_index(
    conn,
    ep_id: str,
    *,
    valid_at: datetime | None,
    invalid_at: datetime | None,
    expired_at: datetime | None,
    mtime_ms: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO episode_index ("
        "ep_id, subject, fact_id, valid_at, invalid_at, created_at, expired_at, "
        "supersedes, cognitive_type, source_type, schema_version, skip_extraction, "
        "file_path, mtime_ms, size, title, summary"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            ep_id,
            "s",
            None,
            _iso(valid_at) if valid_at else None,
            _iso(invalid_at) if invalid_at else None,
            _iso(datetime(2026, 1, 1, tzinfo=UTC)),
            _iso(expired_at) if expired_at else None,
            None,
            "semantic",
            "agent",
            "3.1",
            1,
            f"{ep_id}.md",
            mtime_ms,
            100,
            "t",
            "sum",
        ),
    )


def _insert_episode(conn, ep_id: str) -> None:
    conn.execute(
        "INSERT INTO episodes ("
        "id, subject, fact_id, body_md, valid_at, invalid_at, created_at, expired_at, "
        "supersedes, cognitive_type, source_type, schema_version, provenance"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            ep_id,
            "s",
            None,
            "body",
            _iso(datetime(2026, 6, 1, tzinfo=UTC)),
            None,
            _iso(datetime(2026, 1, 1, tzinfo=UTC)),
            None,
            None,
            "semantic",
            "agent",
            "3.1",
            "{}",
        ),
    )


def _insert_path(conn, ep_id: str, mtime_ms: int) -> None:
    conn.execute(
        "INSERT INTO episode_paths (ep_id, file_path, mtime_ms, size) VALUES (?,?,?,?)",
        (ep_id, f"{ep_id}.md", mtime_ms, 100),
    )


# ---------------------------------------------------------------------------
# Empty / fresh DB.
# ---------------------------------------------------------------------------


def test_empty_db_snapshot_is_all_zeros(conn):
    snap = read_sidecar_status(conn, now=_NOW)
    assert snap == SidecarSnapshot(
        schema_version=10,
        episodes=0,
        episode_index=0,
        vigentes=0,
        activos_ahora=0,
        last_mtime_ms=None,
    )


# ---------------------------------------------------------------------------
# vigente vs activo-ahora — the two predicates measure different axes.
# ---------------------------------------------------------------------------


def test_vigente_and_activos_ahora_distinguish_the_two_axes(conn):
    # ep1: valid now, no invalidation, no decay -> vigente AND activo-ahora.
    _insert_index(
        conn,
        "ep1",
        valid_at=datetime(2026, 6, 1, tzinfo=UTC),
        invalid_at=None,
        expired_at=None,
    )
    # ep2: valid in the future -> vigente but NOT activo-ahora (valid_at > now).
    _insert_index(
        conn,
        "ep2",
        valid_at=datetime(2026, 8, 1, tzinfo=UTC),
        invalid_at=None,
        expired_at=None,
    )
    # ep3: invalidated in the past -> NOT vigente, NOT activo-ahora.
    _insert_index(
        conn,
        "ep3",
        valid_at=datetime(2026, 6, 1, tzinfo=UTC),
        invalid_at=datetime(2026, 6, 15, tzinfo=UTC),
        expired_at=None,
    )
    # ep4: decayed (expired_at set) but valid-time still open -> NOT vigente,
    # but activo-ahora (state_at ignores the transaction-time decay axis).
    _insert_index(
        conn,
        "ep4",
        valid_at=datetime(2026, 6, 1, tzinfo=UTC),
        invalid_at=None,
        expired_at=datetime(2026, 6, 15, tzinfo=UTC),
    )
    # ep5: invalidation scheduled in the future -> NOT vigente (invalid_at set),
    # but activo-ahora (invalid_at > now).
    _insert_index(
        conn,
        "ep5",
        valid_at=datetime(2026, 6, 1, tzinfo=UTC),
        invalid_at=datetime(2026, 8, 1, tzinfo=UTC),
        expired_at=None,
    )
    conn.commit()
    snap = read_sidecar_status(conn, now=_NOW)
    assert snap.episode_index == 5
    assert snap.vigentes == 2  # ep1, ep2
    assert snap.activos_ahora == 3  # ep1, ep4, ep5


def test_activos_ahora_includes_valid_at_none_from_forever(conn):
    # CC-2 (C8.6): valid_at IS NULL = "from forever" (f5-02 §2 line 85) — valid at
    # ANY t, so it is activo-ahora (and vigente). The previous predicate excluded
    # NULL, mis-treating "from forever" as PENDING. Pins the sidecar SQL mirrors
    # _pit_predicate("state_at") verbatim after the CC-2 alignment.
    _insert_index(
        conn,
        "ep_forever",
        valid_at=None,  # "from forever"
        invalid_at=None,
        expired_at=None,
    )
    _insert_index(
        conn,
        "ep_pending",
        valid_at=datetime(2026, 8, 1, tzinfo=UTC),  # future = real PENDING
        invalid_at=None,
        expired_at=None,
    )
    conn.commit()
    snap = read_sidecar_status(conn, now=_NOW)
    assert snap.activos_ahora == 1  # ep_forever only; ep_pending (future) excluded
    assert snap.vigentes == 2  # both vigente (invalid_at NULL, expired_at NULL)


# ---------------------------------------------------------------------------
# episodes count is independent of episode_index count (vault-backed mode).
# ---------------------------------------------------------------------------


def test_episodes_count_independent_of_episode_index(conn):
    _insert_episode(conn, "a")
    _insert_episode(conn, "b")
    _insert_index(
        conn,
        "ix1",
        valid_at=datetime(2026, 6, 1, tzinfo=UTC),
        invalid_at=None,
        expired_at=None,
    )
    conn.commit()
    snap = read_sidecar_status(conn, now=_NOW)
    assert snap.episodes == 2
    assert snap.episode_index == 1


# ---------------------------------------------------------------------------
# last_mtime_ms = MAX(mtime_ms) over episode_paths; None when empty.
# ---------------------------------------------------------------------------


def test_last_mtime_is_max_over_episode_paths(conn):
    _insert_path(conn, "p1", 1_000)
    _insert_path(conn, "p2", 3_000)
    _insert_path(conn, "p3", 2_000)
    conn.commit()
    snap = read_sidecar_status(conn, now=_NOW)
    assert snap.last_mtime_ms == 3_000


def test_last_mtime_none_when_no_paths(conn):
    snap = read_sidecar_status(conn, now=_NOW)
    assert snap.last_mtime_ms is None


# ---------------------------------------------------------------------------
# schema_version reflects the applied migration set.
# ---------------------------------------------------------------------------


def test_schema_version_reflects_applied_migrations(conn):
    snap = read_sidecar_status(conn, now=_NOW)
    assert snap.schema_version == 10


def test_schema_version_zero_on_unmigrated_db(tmp_path):
    import sqlite3

    # A DB file that exists but has no schema_version table.
    raw = sqlite3.connect(tmp_path / "empty.db")
    raw.execute("CREATE TABLE episodes (id TEXT)")  # some table, no migrations
    raw.commit()
    raw.close()
    conn = sqlite3.connect(tmp_path / "empty.db")
    snap = read_sidecar_status(conn, now=_NOW)
    # Missing schema_version -> 0; missing episode_index/episode_paths -> 0.
    assert snap.schema_version == 0
    assert snap.episode_index == 0
    assert snap.episodes == 0
    conn.close()