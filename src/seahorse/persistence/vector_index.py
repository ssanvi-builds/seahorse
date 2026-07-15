"""SqliteVectorIndexRepository — MVP-1 stub (SO-7b).

The ``VectorIndexRepository`` Protocol (SO-7b: fold-into-upsert +
``distinct_model_identities``) is signed in
``seahorse.contracts.persistence``. The SQLite impl requires the ``sqlite-vec``
extension (vec0 virtual table), which is an MVP-1 runtime dependency — MVP-0
ships zero runtime deps (stdlib ``sqlite3`` only). Every method therefore raises
``NotImplementedError`` so the Protocol is satisfied structurally (E2E smoke
asserts the raise) without pulling ``sqlite-vec`` into ``main``.

No own ``atomic()`` (SO-7a.6): when the impl lands in MVP-1 it will reuse the
shared ``ConnectionManager.atomic()``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from seahorse.contracts.persistence import VectorHit
from seahorse.persistence.connection import ConnectionManager

_MVP1 = "VectorIndexRepository SQLite impl is MVP-1 (requires sqlite-vec, not in MVP-0)"


class SqliteVectorIndexRepository:
    """MVP-1 stub satisfying the ``VectorIndexRepository`` Protocol."""

    def __init__(self, cm: ConnectionManager) -> None:
        self._cm = cm  # unused until MVP-1; kept for composition-root uniformity

    def upsert(
        self,
        ep_id: str,
        vector: bytes,
        *,
        dim: int,
        model_identity: str,
        content_hash: str,
        embedded_at: str,
    ) -> None:
        raise NotImplementedError(_MVP1)

    def distinct_model_identities(self) -> list[str]:
        raise NotImplementedError(_MVP1)

    def knn(
        self,
        query: Any,
        k: int,
        *,
        vigent_only: bool = True,
        fact_id_filter: str | None = None,
        cognitive_types: list[str] | None = None,
    ) -> list[VectorHit]:
        raise NotImplementedError(_MVP1)

    def knn_state_at(self, query: Any, k: int, t: datetime) -> list[VectorHit]:
        raise NotImplementedError(_MVP1)

    def knn_known_at(self, query: Any, k: int, t: datetime) -> list[VectorHit]:
        raise NotImplementedError(_MVP1)

    def remove_for_rebuild(self) -> None:
        raise NotImplementedError(_MVP1)

    def rebuild(self) -> None:
        raise NotImplementedError(_MVP1)

    def count(self) -> int:
        raise NotImplementedError(_MVP1)


__all__ = ["SqliteVectorIndexRepository"]
