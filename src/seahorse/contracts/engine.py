"""Bi-temporal Engine contracts — EpisodeRepository Protocol, AuditEvent, errors.

Owned by the Bi-temporal Engine. Materialized here by the persistence layer as
the stable frontier. The engine IMPORTS these symbols; it does not relocate
them.

The AuditEvent field set is inferred by the persistence layer from the
``audit_events`` DDL and confirmed by the engine when it ships; additive
extension is non-breaking, a rename/removal is breaking.

The engine extends this frontier with ``WriteResult`` and ``FreshnessView`` —
additive, non-breaking. Consumed by the facade, the MCP server, the CLI, and
the benchmark harness.
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
    row already has ``invalid_at`` set (invalid_at is written once, null->now).
    """


@dataclass(frozen=True)
class AuditEvent:
    """Audit entry. Type defined by the engine, serialized to ``audit_events``
    by the persistence layer.

    Observability-local, NOT portable (lost on storage migration). The
    invalidation metadata (reason, agent_id) lives ONLY here, never in the
    invalidated row.
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
    """Storage frontier the Bi-temporal Engine consumes.

    The persistence layer provides ``SqliteEpisodeRepository`` as the
    implementation. The repository receives NO raw SQL predicate strings; it
    exposes typed methods only. There is NO ``delete``, NO ``update_body``,
    NO ``update_valid_at`` (retain, never delete).
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

    The episode UUID (``ep_id``) is separate from the subject hash
    (``fact_id = SHA-256(subject)[:32]``). ``WriteResult.fact_id`` equals
    ``IndexRow.fact_id`` by construction — the bridge to the benchmark harness.

    On collision (fail-loud) ``ep_id`` and ``fact_id`` are ``None`` and
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
    """Derived freshness snapshot of an episode (the MCP server exposes it in a later release).

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
    freshness_view`` (the engine) and ``DisclosureShaper.materialize_full``
    (progressive disclosure) both delegate here so the
    ``stale``/``pending_ingest``/``age_days``/``regime`` derivation cannot drift
    between the engine and the disclosure shaper.

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
