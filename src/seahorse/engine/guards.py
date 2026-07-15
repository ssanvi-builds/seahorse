"""Write-time invariant guards I1-I11 (owned by #2).

The guard chain runs before ``repo.append`` (apply_fact / improve) and before
``repo.set_invalid_at`` (forget). Each guard raises a typed ``EngineError`` (or
``InvalidationConflictError`` for the I3/I6 storage-idempotency cases) on the
first violated invariant. Guards are pure validators over the ``Episode`` +
repo; they never mutate.

SO-4a amends I2: the allowed set for an arbitrary ``valid_at`` is
``{human, importer, system}``; ``agent`` / ``project_doc`` (and unknown/None)
are restricted to ``null`` or ``now``.

Reference: f5-02 §3 (I1-I11), §6 (guard chain), SO-4 4a (I2 amendment).
"""

from __future__ import annotations

from datetime import datetime

from seahorse.contracts.engine import EpisodeRepository, InvalidationConflictError
from seahorse.contracts.episode import Episode
from seahorse.engine import errors

# SO-4a: sources permitted to set an arbitrary valid_at (past/future/null).
_VALID_AT_ARBITRARY_SOURCES = frozenset({"human", "importer", "system"})


class WriteGuards:
    """Dispatch table for the write-time invariant chain."""

    def validate(
        self,
        ep: Episode,
        repo: EpisodeRepository,
        op: str,
        now: datetime,
    ) -> None:
        if op in ("apply_fact", "improve"):
            # op="improve" validates the NEW episode with the append-guard set;
            # atomicity (I8) is enforced by the ``with repo.atomic():`` block in
            # the engine, not by a guard.
            self._i1_created_at_engine_owned(ep)
            self._i2_valid_at_by_source(ep, now)
            self._i5_monotonic(ep)
            self._i4_expired_at_null_mvp(ep)
            self._supersedes_exists(ep, repo)
        elif op == "forget":
            self._i3_invalid_at_null_before(ep)
            self._i5_valid_le_now(ep, now)
            self._i6_no_overwrite(ep)
            self._i7_keep_expired_at_untouched(ep)
        elif op in ("revalidate", "decay"):
            raise errors.EngineError(errors.E_NOT_IN_MVP_0, op=op)
        else:
            raise ValueError(f"unknown op: {op!r}")

    # --- apply_fact / improve guards -----------------------------------------

    def _i1_created_at_engine_owned(self, ep: Episode) -> None:
        # I1: created_at is Engine-owned and set once at ingest. apply_fact
        # force-sets it before validate; reaching None here is an engine bug.
        if ep.created_at is None:
            raise errors.EngineError(errors.E_CREATED_AT_ENGINE_OWNED, ep_id=ep.id)

    def _i2_valid_at_by_source(self, ep: Episode, now: datetime) -> None:
        # I2 (SO-4a): arbitrary valid_at is human/importer/system only. Every
        # other source (agent, project_doc, unknown) is restricted to null or now.
        if ep.source_type in _VALID_AT_ARBITRARY_SOURCES:
            return
        if ep.valid_at is not None and ep.valid_at != now:
            raise errors.EngineError(
                errors.E_VALID_AT_HUMAN_ONLY,
                source_type=ep.source_type,
                valid_at=ep.valid_at.isoformat(),
            )

    def _i5_monotonic(self, ep: Episode) -> None:
        # I5 null-safe: the ordering constraint applies ONLY when both operands
        # of a bi-temporal pair are non-null.
        if (
            ep.valid_at is not None
            and ep.invalid_at is not None
            and ep.valid_at > ep.invalid_at
        ):
            raise errors.EngineError(
                errors.E_MONOTONICITY_VIOLATED,
                pair="valid_at>invalid_at",
                valid_at=ep.valid_at.isoformat(),
                invalid_at=ep.invalid_at.isoformat(),
            )
        if (
            ep.expired_at is not None
            and ep.created_at is not None
            and ep.created_at > ep.expired_at
        ):
            raise errors.EngineError(
                errors.E_MONOTONICITY_VIOLATED,
                pair="created_at>expired_at",
                created_at=ep.created_at.isoformat(),
                expired_at=ep.expired_at.isoformat(),
            )

    def _i4_expired_at_null_mvp(self, ep: Episode) -> None:
        # I4 MVP-0: the decay feature (expired_at non-null) is not available;
        # a non-null value is rejected with the named I4 code (f5-02 §8.2),
        # not the generic MVP-1-stub code. MVP-1 accepts it opaque, core ignores it.
        if ep.expired_at is not None:
            raise errors.EngineError(
                errors.E_EXPIRED_AT_NON_NULL,
                field="expired_at",
                reason="decay is mediano; expired_at must be null in MVP-0",
            )

    def _supersedes_exists(self, ep: Episode, repo: EpisodeRepository) -> None:
        # A non-null supersedes must point to a real episode; dangling refs break
        # the chain and are rejected up front.
        if ep.supersedes is None:
            return
        if repo.get(ep.supersedes) is None:
            raise errors.EngineError(errors.E_DANGLING_SUPERSEDES, supersedes=ep.supersedes)

    # --- forget guards -------------------------------------------------------

    def _i3_invalid_at_null_before(self, ep: Episode) -> None:
        # I3: invalid_at is set once null->now. Re-invalidating an already
        # invalidated episode is a storage-idempotency conflict (the repo's
        # WHERE invalid_at IS NULL would affect 0 rows).
        if ep.invalid_at is not None:
            raise InvalidationConflictError(
                f"invalid_at already set on {ep.id}; use revalidate() (I9)"
            )

    def _i5_valid_le_now(self, ep: Episode, now: datetime) -> None:
        # I5: invalidating a PENDING_INGEST (valid_at > now) would yield
        # valid_at > invalid_at=now — ALWAYS forbidden, not policy-admitted.
        if ep.valid_at is not None and ep.valid_at > now:
            raise errors.EngineError(
                errors.E_PENDING_CANNOT_INVALIDATE,
                valid_at=ep.valid_at.isoformat(),
            )

    def _i6_no_overwrite(self, ep: Episode) -> None:
        # I6: forget operates only on a vigente episode. A decayed episode
        # (expired_at non-null) is not vigente and cannot be invalidated.
        if ep.expired_at is not None:
            raise InvalidationConflictError(
                f"episode {ep.id} is decayed (expired_at set); cannot invalidate"
            )

    def _i7_keep_expired_at_untouched(self, ep: Episode) -> None:
        # I7: forget touches invalid_at only; expired_at is never written by the
        # invalidation path. This is a structural guarantee (set_invalid_at
        # updates one column), restated here as the documented invariant marker.
        return