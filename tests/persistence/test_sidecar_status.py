"""``read_sidecar_status`` — read-only sidecar snapshot for ``seahorse inspect``.

Guards the persistence-owned SQL that backs the CLI ``inspect`` command. The
snapshot reports schema_version + episode/episode_index counts + the two
bi-temporal predicates (currently valid vs active now) + the last known file mtime.

The predicates mirror the engine's bi-temporal definitions verbatim:
- currently valid = ``invalid_at IS NULL AND expired_at IS NULL``
  (``SqliteEpisodeIndexRepository.find_vigent_row_by_fact_id``)
- active now = ``(valid_at IS NULL OR valid_at <= now)
                AND (invalid_at IS NULL OR invalid_at > now)``
  (``_pit_predicate("state_at", now)``); ``valid_at IS NULL``
  ("from forever") is valid at any ``t`` and is INCLUDED.

The two measure DIFFERENT axes (currently valid = both axes open; active now =
valid-time active now, ignoring decay), so a row can be active now but NOT
currently valid (e.g. a future-scheduled invalidation, or a decayed-but-valid row).
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
        schema_version=12,
        episodes=0,
        episode_index=0,
        vigentes=0,
        activos_ahora=0,
        last_mtime_ms=None,
    )


# ---------------------------------------------------------------------------
# currently valid vs active now — the two predicates measure different axes.
# ---------------------------------------------------------------------------


def test_vigente_and_activos_ahora_distinguish_the_two_axes(conn):
    # ep1: valid now, no invalidation, no decay -> currently valid AND active now.
    _insert_index(
        conn,
        "ep1",
        valid_at=datetime(2026, 6, 1, tzinfo=UTC),
        invalid_at=None,
        expired_at=None,
    )
    # ep2: valid in the future -> currently valid but NOT active now (valid_at > now).
    _insert_index(
        conn,
        "ep2",
        valid_at=datetime(2026, 8, 1, tzinfo=UTC),
        invalid_at=None,
        expired_at=None,
    )
    # ep3: invalidated in the past -> NOT currently valid, NOT active now.
    _insert_index(
        conn,
        "ep3",
        valid_at=datetime(2026, 6, 1, tzinfo=UTC),
        invalid_at=datetime(2026, 6, 15, tzinfo=UTC),
        expired_at=None,
    )
    # ep4: decayed (expired_at set) but valid-time still open -> NOT currently valid,
    # but active now (state_at ignores the transaction-time decay axis).
    _insert_index(
        conn,
        "ep4",
        valid_at=datetime(2026, 6, 1, tzinfo=UTC),
        invalid_at=None,
        expired_at=datetime(2026, 6, 15, tzinfo=UTC),
    )
    # ep5: invalidation scheduled in the future -> NOT currently valid (invalid_at set),
    # but active now (invalid_at > now).
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
    # valid_at IS NULL = "from forever" — valid at
    # ANY t, so it is active now (and currently valid). The previous predicate excluded
    # NULL, mis-treating "from forever" as PENDING. Pins the sidecar SQL mirrors
    # _pit_predicate("state_at") verbatim.
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
    assert snap.vigentes == 2  # both currently valid (invalid_at NULL, expired_at NULL)


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
    assert snap.schema_version == 12


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