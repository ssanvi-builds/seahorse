"""SqliteSidecarIndexRepository — episode_paths + episode_index maintenance.

Implements ``seahorse.contracts.persistence.SidecarIndexRepository``. ``put_path``
is an UPSERT (file rename = UPDATE, allowed because episode_paths is mutable and
separate from the append-only episodes table). ``reindex`` wraps the path update
in the shared atomic so the caller's indexing work commits with the metadata.
``rebuild_all`` repopulates ``episode_index`` + ``episode_paths`` from ruamel-free
``ParsedNote`` payloads (clear-then-rebuild). No own ``atomic()``;
``rebuild_all`` borrows the shared ``ConnectionManager.atomic()``.

Ruamel-confinement invariant: this module is CORE and must NOT import
``ruamel.yaml``/``python-frontmatter``. The frontmatter codec is confined to
``frontmatter.handler``/``frontmatter.adapter``; the frontmatter migrator builds
``ParsedNote`` from parsed ``.md`` and hands it here. ``Episode`` is a core
contract (ruamel-free), so the sidecar may import it freely.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime

from seahorse.contracts.persistence import (
    ParsedNote,
    RebuildConflict,
    RebuildReport,
)
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.episode_index_columns import (
    EPISODE_INDEX_REBUILD_COLUMNS,
    index_insert_sql,
)

_PUT_PATH_SQL = (
    "INSERT INTO episode_paths (ep_id, file_path, mtime_ms, size) VALUES (?,?,?,?) "
    "ON CONFLICT(ep_id) DO UPDATE SET file_path=excluded.file_path, "
    "mtime_ms=excluded.mtime_ms, size=excluded.size"
)
# The rebuild INSERT column list is derived from the single source in
# ``episode_index_columns`` (all 18 columns — the rebuild owns the file metadata
# the hot path leaves NULL). The VALUES tuple below must stay in
# EPISODE_INDEX_REBUILD_COLUMNS order.
_REBUILD_INDEX_INSERT = index_insert_sql(EPISODE_INDEX_REBUILD_COLUMNS)
_DELETE_INDEX_SQL = "DELETE FROM episode_index"
_DELETE_PATHS_SQL = "DELETE FROM episode_paths"
_DUPLICATE_VIGENT_REASON = "duplicate-vigent-fact_id"
_DUPLICATE_EP_ID_REASON = "duplicate-ep_id"

# A secondary-index wipe hook. ``rebuild_all`` runs these inside its atomic
# (clear phase, after the episode_index/episode_paths DELETEs, before
# repopulate) so the FTS5/vec0 tables are cleared in the SAME transaction as the
# episode cache — no half-wiped ghost state where the secondary indexes still
# point at ep_ids deleted from episode_index. The current release passes none
# (the FTS5/vec0 tables do not exist yet — no ghost hits); the hook is in place
# for a later release to plug bulk-wipe callables (``DELETE FROM episode_fts`` /
# the vec0 wipe) without reopening ``rebuild_all``.
SecondaryIndexWipe = Callable[[sqlite3.Connection], None]


def _fmt_dt(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _skip_extraction(note: ParsedNote) -> int:
    # The skip path: skip_extraction=1 excludes from the FTS5 + embedding queue.
    # Derived from provenance["extraction_mode"] == "skip" (matches the shaper).
    # The migrator default is extraction_mode=skip, so migrated notes land at 1.
    mode = note.episode.provenance.get("extraction_mode")
    return 1 if mode == "skip" else 0


class SqliteSidecarIndexRepository:
    """SQLite implementation of the ``SidecarIndexRepository`` Protocol."""

    def __init__(self, cm: ConnectionManager) -> None:
        self._cm = cm

    def put_path(self, ep_id: str, file_path: str, mtime_ms: int, size: int) -> None:
        with self._cm.atomic() as w:
            w.execute(_PUT_PATH_SQL, (ep_id, file_path, mtime_ms, size))

    def get_path(self, ep_id: str) -> tuple[str, int, int] | None:
        with self._cm.read() as w:
            row = w.execute(
                "SELECT file_path, mtime_ms, size FROM episode_paths WHERE ep_id = ?",
                (ep_id,),
            ).fetchone()
            if row is None:
                return None
            return row["file_path"], row["mtime_ms"], row["size"]

    @contextmanager
    def reindex(self, ep_id: str, file_path: str, mtime_ms: int, size: int) -> Iterator[None]:
        """Update path metadata + run the caller's indexing work in one atomic."""
        with self._cm.atomic():
            self._cm.writer.execute(_PUT_PATH_SQL, (ep_id, file_path, mtime_ms, size))
            yield

    def rebuild_all(
        self,
        notes: Iterable[ParsedNote],
        *,
        secondary_index_wipes: Sequence[SecondaryIndexWipe] = (),
    ) -> RebuildReport:
        """Clear ``episode_index`` + ``episode_paths`` and repopulate from ``notes``.

        Clear-then-rebuild (not upsert): ``.md`` is the source of truth, the SQLite
        index a derived cache, so both tables are wiped and repopulated each call
        — vault deletions/edits propagate without a diff. Does NOT touch
        ``episodes``.

        Conflict policy: a duplicate currently-valid ``fact_id`` (the partial
        unique ``uq_episode_index_active_per_subject``) is NOT auto-resolved —
        ALL members of the conflict group are skipped and reported in
        ``RebuildReport.skipped``. The operator decides which note wins; the
        index never carries an arbitrary choice. ``fact_id IS NULL`` notes never
        conflict (SQLite treats NULLs as distinct in a UNIQUE index).

        Atomicity: the clear + repopulate is ONE ``ConnectionManager.atomic()``,
        so a mid-stream DB error (e.g. a CHECK violation the pre-pass does not
        screen) rolls back the DELETE too — the prior index is preserved, not
        half-wiped.

        Secondary-index wipe hook: ``secondary_index_wipes`` are caller-supplied
        hooks run inside the SAME atomic, in the clear phase (after the
        ``episode_index``/``episode_paths`` DELETEs, before the repopulate loop),
        each given the writer connection. They exist so a later release can clear
        the FTS5/vec0 tables in the same transaction as the episode cache —
        without this, a vault rebuild leaves the secondary indexes pointing at
        deleted ep_ids (ghost hits). The current release passes none: the
        FTS5/vec0 tables do not exist yet, there is nothing to wipe, and the
        default is a pure no-op. A wipe that raises rolls back the whole clear
        (the prior index is preserved, not half-wiped) — the coordinated wipe is
        atomic.
        """
        materialized = list(notes)
        conflict_ep_ids, ep_id_reason = self._conflict_group_ids(materialized)
        indexed = 0
        skipped: list[RebuildConflict] = []
        with self._cm.atomic() as w:
            w.execute(_DELETE_INDEX_SQL)
            w.execute(_DELETE_PATHS_SQL)
            for wipe in secondary_index_wipes:
                wipe(w)
            for note in materialized:
                ep = note.episode
                reason = ep_id_reason.get(ep.id)
                if reason is None and ep.id in conflict_ep_ids:
                    reason = _DUPLICATE_VIGENT_REASON
                if reason is not None:
                    skipped.append(
                        RebuildConflict(
                            ep_id=ep.id,
                            file_path=note.file_path,
                            fact_id=ep.fact_id or "",
                            reason=reason,
                        )
                    )
                    continue
                w.execute(
                    _REBUILD_INDEX_INSERT,
                    (
                        ep.id,
                        ep.subject,
                        ep.fact_id,
                        _fmt_dt(ep.valid_at),
                        _fmt_dt(ep.invalid_at),
                        _fmt_dt(ep.created_at),
                        _fmt_dt(ep.expired_at),
                        ep.supersedes,
                        ep.supersedes_reason,
                        ep.cognitive_type,
                        ep.source_type,
                        ep.schema_version,
                        _skip_extraction(note),
                        ep.title,
                        ep.summary,
                        note.file_path,
                        note.mtime_ms,
                        note.size,
                    ),
                )
                w.execute(_PUT_PATH_SQL, (ep.id, note.file_path, note.mtime_ms, note.size))
                indexed += 1
        return RebuildReport(indexed=indexed, skipped=skipped)

    @staticmethod
    def _conflict_group_ids(notes: list[ParsedNote]) -> tuple[set[str], dict[str, str]]:
        # Pre-pass for two integrity conflicts the DB would otherwise reject
        # mid-rebuild (raising an opaque IntegrityError instead of a report):
        #
        # 1. Duplicate currently-valid fact_id (partial unique
        #    uq_episode_index_active_per_subject): group currently-valid notes
        #    (invalid_at + expired_at both NULL) by non-NULL fact_id; any group
        #    with >1 member is a conflict — ALL members skipped (no auto-pick).
        #    NULL fact_ids never conflict (SQLite treats NULLs as distinct).
        # 2. Duplicate ep_id (PRIMARY KEY): two .md notes carrying the same id.
        #    The whole group is skipped + reported so the operator fixes the
        #    vault rather than receiving a raw IntegrityError.
        #
        # Returns the set of conflicting ep_ids and a per-ep_id reason map (the
        # reason map takes precedence so an ep_id in BOTH groups is reported with
        # the more specific duplicate-ep_id reason).
        vigent_groups: dict[str, list[str]] = {}
        ep_id_seen: dict[str, list[str]] = {}
        for note in notes:
            ep = note.episode
            ep_id_seen.setdefault(ep.id, []).append(note.file_path)
            if ep.fact_id is None or ep.invalid_at is not None or ep.expired_at is not None:
                continue
            vigent_groups.setdefault(ep.fact_id, []).append(ep.id)
        conflict_ep_ids = {
            ep_id for members in vigent_groups.values() if len(members) > 1 for ep_id in members
        }
        reason_map: dict[str, str] = {}
        for ep_id, paths in ep_id_seen.items():
            if len(paths) > 1:
                reason_map[ep_id] = _DUPLICATE_EP_ID_REASON
        return conflict_ep_ids, reason_map


__all__ = ["SqliteSidecarIndexRepository"]
