"""Bi-temporal Engine contracts — EpisodeRepository Protocol, AuditEvent, errors.

Owned by #2 (Bi-temporal Engine). Materialized here by #6 as the stable frontier
per SO-3. #2 IMPORTS these symbols; it does not relocate them.

The AuditEvent field set is inferred by #6 from the ``audit_events`` DDL
(f5-06 §3.4.5) and SO-3c. #2 must confirm the 11-field set matches when it ships;
additive extension is non-breaking, a rename/removal is breaking (R3).

#2 EXTENDS this frontier (2026-07-15, Phase 0/1 F6 #2) with ``WriteResult`` and
``FreshnessView`` — additive, non-breaking (R3). Consumed by #12/#13/#14/#16.

References:
- f5-02 §6.1 (EpisodeRepository Protocol, WriteResult, FreshnessView)
- f5-06 §3.4.5 (audit_events DDL — 11 columns)
- f6-signoffs.md SO-3 (3c AuditEvent store, 3b apply_fact fail-loud)
- f6-signoffs.md SO-8c (WriteResult(ep_id, fact_id) correction, TD #14)
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


@dataclass(frozen=True)
class WriteResult:
    """Result of an Engine write primitive (``apply_fact`` / ``remember``).

    SO-8c separates the episode UUID (``ep_id``) from the subject hash
    (``fact_id = SHA-256(subject)[:32]``). ``WriteResult.fact_id`` equals
    ``IndexRow.fact_id`` by construction — the #16 bridge (TD #14).

    On collision (SO-3b fail-loud) ``ep_id`` and ``fact_id`` are ``None`` and
    ``status == "COLLISION"``; the candidate was NOT appended and the unique
    partial index ``uq_one_active_per_subject`` was never relaxed.

    ``collisions_detected`` is ``list`` (not ``list[Collision]``) because
    ``Collision`` is an Engine-internal type, not a frontier symbol.
    """

    ep_id: str | None
    fact_id: str | None
    status: str  # ACTIVE | PENDING_INGEST | NOOP | COLLISION
    collisions_detected: list  # list[Collision]; [] when clean


@dataclass(frozen=True)
class FreshnessView:
    """Derived freshness snapshot of an episode (#13 MCP exposes it MVP-1).

    Pure derivation from the episode's frontmatter — no state outside the
    format. ``stale`` means already invalidated; ``pending_ingest`` means the
    fact is scheduled for the future (``valid_at > now``).

    ``fact_id`` is ``str | None``: an episode whose body has no derivable
    subject (no ``title``, no first H1) carries ``fact_id=None``. Mirrors
    ``WriteResult.fact_id`` and ``Episode.fact_id`` for bridge consistency.
    """

    fact_id: str | None
    age_days: int
    stale: bool
    pending_ingest: bool
    regime: str


def freshness_of(episode: Episode, now: datetime) -> FreshnessView:
    """Pure derivation of an episode's freshness snapshot (no external state).

    Single source of truth for the freshness formula: ``BiTemporalEngine.
    freshness_view`` (#2) and ``DisclosureShaper.materialize_full`` (#8) both
    delegate here so the ``stale``/``pending_ingest``/``age_days``/``regime``
    derivation cannot drift between the engine and the disclosure shaper.

    ``stale`` = already invalidated (``invalid_at is not None``);
    ``pending_ingest`` = scheduled for the future (``valid_at is not None and
    valid_at > now``); ``regime`` = ``source_type`` or ``"unknown"``.
    """
    return FreshnessView(
        fact_id=episode.fact_id,
        age_days=(now - episode.created_at).days,
        stale=episode.invalid_at is not None,
        pending_ingest=episode.valid_at is not None and episode.valid_at > now,
        regime=episode.source_type or "unknown",
    )
