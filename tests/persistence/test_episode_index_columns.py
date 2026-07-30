"""C8.3 [36] — ``episode_index`` column-list single source of truth.

Two write paths populate ``episode_index`` and used to hardcode the column list
independently: the Engine hot path (``sqlite_episode_repo._INDEX_INSERT`` —
15 core columns, file metadata left NULL) and the vault rebuild
(``sqlite_sidecar._REBUILD_INDEX_INSERT`` — all 18, owning file metadata). A new
column required touching both INSERT statements in sync, plus the DDL, plus the
``_row_to_index`` reader — a silent drift hazard the audit flagged as [36].

C8.3 centralizes the column set in ``episode_index_columns``: both INSERT
statements are derived from it via ``index_insert_sql``, and a DDL-equality
guard fails if the constant drifts from the actual ``PRAGMA table_info``. The
``_row_to_index`` reader is covered by a subset guard (every column it reads is
in the canonical set).
"""

from __future__ import annotations

import inspect
import re
import sqlite3

from seahorse.persistence.episode_index_columns import (
    EPISODE_INDEX_CORE_COLUMNS,
    EPISODE_INDEX_FILE_COLUMNS,
    EPISODE_INDEX_REBUILD_COLUMNS,
    index_insert_sql,
)
from seahorse.persistence.migrations.migrator import apply_migrations


def _episode_index_ddl_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(episode_index)")}


def test_rebuild_columns_match_ddl() -> None:
    # The canonical constant MUST track the actual schema. A new DDL column that
    # is not added to the constant fails here — forcing the single source to
    # update (and both derived INSERTs follow automatically).
    c = sqlite3.connect(":memory:")
    apply_migrations(c)
    assert set(EPISODE_INDEX_REBUILD_COLUMNS) == _episode_index_ddl_columns(c)
    c.close()


def test_core_columns_are_rebuild_minus_file_metadata() -> None:
    assert EPISODE_INDEX_FILE_COLUMNS == ("file_path", "mtime_ms", "size")
    assert set(EPISODE_INDEX_CORE_COLUMNS) == set(EPISODE_INDEX_REBUILD_COLUMNS) - set(
        EPISODE_INDEX_FILE_COLUMNS
    )
    assert len(EPISODE_INDEX_CORE_COLUMNS) == 15
    assert len(EPISODE_INDEX_REBUILD_COLUMNS) == 18


def test_index_insert_sql_core_omits_file_metadata() -> None:
    sql = index_insert_sql(EPISODE_INDEX_CORE_COLUMNS)
    assert sql.startswith("INSERT INTO episode_index (")
    assert "ep_id" in sql and "summary" in sql and "supersedes_reason" in sql
    # the hot path leaves the file-metadata trio NULL (not in the INSERT)
    assert "file_path" not in sql and "mtime_ms" not in sql and "size" not in sql
    assert sql.count("?") == len(EPISODE_INDEX_CORE_COLUMNS)


def test_index_insert_sql_rebuild_includes_file_metadata() -> None:
    sql = index_insert_sql(EPISODE_INDEX_REBUILD_COLUMNS)
    assert "file_path" in sql and "mtime_ms" in sql and "size" in sql
    assert sql.count("?") == len(EPISODE_INDEX_REBUILD_COLUMNS)


def test_row_to_index_reads_subset_of_canonical() -> None:
    # _row_to_index reads named columns; guard that every column it reads is in
    # the canonical DDL set, so a constant/DDL drift is caught relative to the
    # reader too (a new column the reader forgets surfaces as a missing field,
    # not a silent mismatch).
    from seahorse.persistence.sqlite_episode_index import _row_to_index

    src = inspect.getsource(_row_to_index)
    read_cols = set(re.findall(r'row\["([a-z_]+)"\]', src))
    assert read_cols, "no row[...] accesses found in _row_to_index source"
    extra = read_cols - set(EPISODE_INDEX_REBUILD_COLUMNS)
    assert not extra, f"_row_to_index reads columns outside the canonical set: {extra}"