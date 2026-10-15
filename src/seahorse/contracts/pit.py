"""Bi-temporal axis predicates — the single home for point-in-time validity checks.

Seahorse records two independent time axes and routes a point-in-time (PIT)
query on exactly one of them (the axes never mix, ADR-03):

- ``state_at`` (valid-time): *when the fact became true in the world*. Reads
  ONLY ``valid_at`` / ``invalid_at``.
- ``known_at`` (transaction-time): *when the system recorded the fact*. Reads
  ONLY ``created_at`` / ``expired_at``.

These two predicates are the Python single home for both axes. Every in-memory
PIT check delegates here so the axis-separation invariant and the NULL / strict
boundary semantics cannot drift between layers:

- ``BiTemporalEngine.is_valid_at`` / ``is_known_at`` (the engine readers) wrap
  these over the hydrated ``Episode`` (after a NULL-episode guard + UTC
  normalization).
- ``retrieval.engine._chain_vigent_at`` / ``_chain_known_at`` (the supersedes
  chain projection) filter with these.
- The SQL mirror is ``sqlite_episode_index._pit_predicate`` (the string form
  used in WHERE clauses) — same semantics, different representation.

Boundary semantics (locked by ``tests/engine/test_readers.py`` and
``tests/retrieval/test_chain_projection.py``):

- Lower bound inclusive (``valid_at <= t``, ``created_at <= t``): a fact that
  became valid / known exactly at ``t`` IS visible at ``t``.
- Upper bound strict (``t < invalid_at``, ``t < expired_at``): a fact
  invalidated / expired exactly at ``t`` is NOT visible at ``t``.
- NULL is unbounded: ``valid_at IS NULL`` means "valid from forever";
  ``invalid_at IS NULL`` / ``expired_at IS NULL`` mean "still valid / still known".
"""

from __future__ import annotations

from datetime import datetime

from seahorse.contracts.episode import Episode


def is_valid_at_t(episode: Episode, t: datetime) -> bool:
    """``state_at`` (valid-time) predicate — was the fact true in the world at ``t``?

    Reads ONLY ``valid_at`` / ``invalid_at``. ``expired_at`` (transaction-time)
    is deliberately ignored so the two bi-temporal axes never mix.
    """
    after_valid = episode.valid_at is None or episode.valid_at <= t
    before_invalid = episode.invalid_at is None or t < episode.invalid_at
    return after_valid and before_invalid


def is_known_at_t(episode: Episode, t: datetime) -> bool:
    """``known_at`` (transaction-time) predicate — did the system know the fact at ``t``?

    Reads ONLY ``created_at`` / ``expired_at``. ``valid_at`` / ``invalid_at``
    (valid-time) are deliberately ignored so the two bi-temporal axes never mix.
    """
    after_created = episode.created_at <= t
    before_expired = episode.expired_at is None or t < episode.expired_at
    return after_created and before_expired


__all__ = ["is_valid_at_t", "is_known_at_t"]
