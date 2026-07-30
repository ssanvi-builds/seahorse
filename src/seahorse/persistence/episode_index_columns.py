"""Single source of truth for the ``episode_index`` column set + order (C8.3 [36]).

Migration 003 created the bridge table; 009 added ``supersedes_reason`` (ALTER
appends it physically last, but the INSERTs group it with ``supersedes`` for
readability). Two write paths populate the table and used to hardcode the column
list independently:

- the Engine hot path (``sqlite_episode_repo._INDEX_INSERT``) writes the 15
  core columns, leaving the file-metadata trio NULL;
- the vault rebuild (``sqlite_sidecar._REBUILD_INDEX_INSERT``) writes all 18,
  owning the file metadata (``.md`` is source of truth).

A new column required touching both INSERT statements (and the DDL, and the
``_row_to_index`` reader) in sync — a silent drift hazard the audit flagged as
[36]. This module is the single source: both INSERT statements are derived from
``EPISODE_INDEX_CORE_COLUMNS`` (+ ``EPISODE_INDEX_FILE_COLUMNS`` for the rebuild)
via ``index_insert_sql``, so a column add is one place. ``tests/persistence/
test_episode_index_columns.py`` guards that the canonical set matches the
actual DDL (``PRAGMA table_info``), so the constant cannot drift from the schema.
"""

from __future__ import annotations

# The 15 columns the Engine hot path writes. Order matches the VALUES tuple in
# ``SqliteEpisodeRepository.append`` (do not reorder without updating both).
EPISODE_INDEX_CORE_COLUMNS = (
    "ep_id",
    "subject",
    "fact_id",
    "valid_at",
    "invalid_at",
    "created_at",
    "expired_at",
    "supersedes",
    "supersedes_reason",
    "cognitive_type",
    "source_type",
    "schema_version",
    "skip_extraction",
    "title",
    "summary",
)

# Lateral file metadata (migration 003 denormalized from episode_paths). The hot
# path leaves these NULL; the vault rebuild owns them (.md is source of truth).
EPISODE_INDEX_FILE_COLUMNS = ("file_path", "mtime_ms", "size")

# Full set the rebuild writes (core + file metadata).
EPISODE_INDEX_REBUILD_COLUMNS = EPISODE_INDEX_CORE_COLUMNS + EPISODE_INDEX_FILE_COLUMNS


def index_insert_sql(columns: tuple[str, ...]) -> str:
    """Build ``INSERT INTO episode_index (...) VALUES (...)`` for ``columns``.

    The VALUES tuple in the caller MUST be in the same order as ``columns``.
    Both callers derive ``columns`` from the constants above, so the SQL text is
    never duplicated — a column add is a single edit to the constant.
    """
    cols = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    return f"INSERT INTO episode_index ({cols}) VALUES ({placeholders})"


__all__ = [
    "EPISODE_INDEX_CORE_COLUMNS",
    "EPISODE_INDEX_FILE_COLUMNS",
    "EPISODE_INDEX_REBUILD_COLUMNS",
    "index_insert_sql",
]