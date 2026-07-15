"""SqliteSidecarIndexRepository — episode_paths + episode_index maintenance (§7a.5).

Implements ``seahorse.contracts.persistence.SidecarIndexRepository``. ``put_path``
is an UPSERT (file rename = UPDATE, allowed because episode_paths is mutable and
separate from the append-only episodes table). ``reindex`` wraps the path update
in the shared atomic so the caller's indexing work commits with the metadata.
``rebuild_all`` is the vault-backed repopulation seam — it is wired by #3 in a
later phase and raises ``NotImplementedError`` here. No own ``atomic()`` (SO-7a.6).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from seahorse.persistence.connection import ConnectionManager

_PUT_PATH_SQL = (
    "INSERT INTO episode_paths (ep_id, file_path, mtime_ms, size) VALUES (?,?,?,?) "
    "ON CONFLICT(ep_id) DO UPDATE SET file_path=excluded.file_path, "
    "mtime_ms=excluded.mtime_ms, size=excluded.size"
)


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

    def rebuild_all(self, vault: object | None = None) -> None:
        """Repopulate episode_index from the vault (vault-backed mode).

        Wired by #3 (Frontmatter adapter) in a later phase. The seam exists so #6's
        contract is complete; it raises ``NotImplementedError`` until #3 supplies the
        VaultFileAdapter. This is the documented MVP-0 vault-backed gap, not a bug.
        """
        raise NotImplementedError(
            "rebuild_all(vault) is wired by #3 (Frontmatter adapter) in a later phase"
        )


__all__ = ["SqliteSidecarIndexRepository"]
