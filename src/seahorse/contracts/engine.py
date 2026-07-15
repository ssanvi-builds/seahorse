"""Bi-temporal Engine contracts — EpisodeRepository Protocol, AuditEvent, errors.

Owned by #2 (Bi-temporal Engine). Materialized here by #6 as the stable frontier
per SO-3. #2 IMPORTS these symbols; it does not relocate them.

The AuditEvent field set is inferred by #6 from the ``audit_events`` DDL
(f5-06 §3.4.5) and SO-3c. #2 must confirm the 11-field set matches when it ships;
additive extension is non-breaking, a rename/removal is breaking (R3).

References:
- f5-02 §6.1 (EpisodeRepository Protocol)
- f5-06 §3.4.5 (audit_events DDL — 11 columns)
- f6-signoffs.md SO-3 (3c AuditEvent store, 3b apply_fact fail-loud)
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from seahorse.contracts.episode import Episode


class NotFound(Exception):
    """Raised when a mutation targets an episode that never existed."""


class InvalidationConflictError(Exception):
    """Raised by ``set_invalid_at`` when the episode is already invalidated.

    The storage guard ``WHERE invalid_at IS NULL`` returned 0 rows because the
    row already has ``invalid_at`` set (SO-3 idempotency, I3 null->now once).
    """


@dataclass(frozen=True)
class AuditEvent:
    """Audit entry. Type defined by #2, serialized to ``audit_events`` by #6.

    Observability-local, NOT portable (lost on storage migration; f5-03 §12.3,
    SO-3 3c). The invalidation metadata (reason, agent_id) lives ONLY here,
    never in the invalidated row (I10/I3).
    """

    primitive: str  # apply|forget|improve|revalidate|decay|rebuild
    target_id: str | None
    transaction_time: datetime
    result: str  # added|updated|invalidated|decayed
    agent_id: str | None = None
    session_id: str | None = None
    successor_id: str | None = None
    valid_time: datetime | None = None
    reason: str | None = None
    cognitive_type: str | None = None


@runtime_checkable
class EpisodeRepository(Protocol):
    """Storage frontier the Bi-temporal Engine (#2) consumes.

    #6 provides ``SqliteEpisodeRepository`` as the MVP-0/MVP-1 implementation.
    The repository receives NO raw SQL predicate strings (ADR-04); it exposes
    typed methods only. There is NO ``delete``, NO ``update_body``,
    NO ``update_valid_at`` (ADR-07 retain-not-delete).
    """

    def append(self, episode: Episode) -> None: ...
    def set_invalid_at(self, ep_id: str, now: datetime) -> None: ...
    def get(self, ep_id: str) -> Episode | None: ...
    def find_vigent_by_fact_id(
        self, fact_id: str, exclude: str | None = None
    ) -> Episode | None: ...
    def chain_from(self, ep_id: str) -> list[Episode]: ...
    def query_vigent(self, subject: str | None = None) -> list[Episode]: ...
    def query_state_at(self, t: datetime, subject: str | None = None) -> list[Episode]: ...
    def query_known_at(self, t: datetime, subject: str | None = None) -> list[Episode]: ...

    @contextmanager
    def atomic(self) -> Iterator[None]: ...
