"""SqliteFullTextIndexRepository — MVP-1 stub (f5-06 §7a.3).

The ``FullTextIndexRepository`` Protocol is signed in
``seahorse.contracts.persistence``. The SQLite impl requires the FTS5 virtual
table, which is an MVP-1 concern — MVP-0 ships zero runtime deps (stdlib
``sqlite3`` only) and intentionally does NOT create ``episode_fts`` in the
migrations. Every method raises ``NotImplementedError`` so the Protocol is
satisfied structurally (E2E smoke asserts the raise).

No own ``atomic()`` (SO-7a.6).
"""

from __future__ import annotations

from datetime import datetime

from seahorse.contracts.persistence import (
    FtsDoc,
    FullTextHit,
)
from seahorse.persistence.connection import ConnectionManager

_MVP1 = "FullTextIndexRepository SQLite impl is MVP-1 (requires FTS5 table, not in MVP-0)"


class SqliteFullTextIndexRepository:
    """MVP-1 stub satisfying the ``FullTextIndexRepository`` Protocol."""

    def __init__(self, cm: ConnectionManager) -> None:
        self._cm = cm  # unused until MVP-1; kept for composition-root uniformity

    def upsert(self, doc: FtsDoc) -> None:
        raise NotImplementedError(_MVP1)

    def search(
        self,
        query: str,
        k: int,
        *,
        vigent_only: bool = True,
        subject_filter: str | None = None,
    ) -> list[FullTextHit]:
        raise NotImplementedError(_MVP1)

    def search_state_at(self, query: str, k: int, t: datetime) -> list[FullTextHit]:
        raise NotImplementedError(_MVP1)

    def search_known_at(self, query: str, k: int, t: datetime) -> list[FullTextHit]:
        raise NotImplementedError(_MVP1)

    def remove_for_rebuild(self, ep_id: str) -> None:
        raise NotImplementedError(_MVP1)

    def rebuild(self, docs: list[FtsDoc]) -> None:
        raise NotImplementedError(_MVP1)

    def count(self) -> int:
        raise NotImplementedError(_MVP1)


__all__ = ["SqliteFullTextIndexRepository"]
