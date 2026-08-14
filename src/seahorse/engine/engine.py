"""Bi-temporal Engine behavior layer.

``BiTemporalEngine`` orchestrates the Persistence Layer via the
``EpisodeRepository`` Protocol: it derives ``subject``/``fact_id``, runs the
write-time guards, detects concurrent-subject collisions, and emits
``AuditEvent``s. It never moves frontier symbols (``WriteResult`` /
``FreshnessView`` live in ``seahorse.contracts.engine`` and are imported, not
relocated).

The first release implements ``apply_fact``; ``remember`` / ``forget`` /
``improve`` / readers / freshness are added in later phases of the same
component.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import UTC, datetime

from seahorse.contracts.engine import (
    AuditEvent,
    EpisodeRepository,
    FreshnessView,
    NotFound,
    WriteResult,
    freshness_of,
)
from seahorse.contracts.episode import Episode
from seahorse.contracts.persistence import AuditEventRepository
from seahorse.engine import errors
from seahorse.engine.canonical import canonical_body_hash
from seahorse.engine.collision import (
    CollisionDetector,
    derive_subject,
    fact_id_for,
    fact_id_of,
)
from seahorse.engine.guards import WriteGuards
from seahorse.engine.ids import deterministic_id, new_uuid7
from seahorse.frontmatter.schema import SupersedesReason

_STATUS_ACTIVE = "ACTIVE"
_STATUS_PENDING = "PENDING_INGEST"
_STATUS_COLLISION = "COLLISION"
_STATUS_NOOP = "NOOP"

# Semver 2.0.0 with optional patch and pre-release/build. Accepts "1.1" (no
# patch) so existing persisted episodes pass.
_SEMVER_RE = re.compile(
    r"^\d+\.\d+(\.\d+)?(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$"
)


class BiTemporalEngine:
    """Orchestrates the persistence storage behind the write/read primitives."""

    def __init__(
        self,
        repo: EpisodeRepository,
        audit: AuditEventRepository,
        *,
        guards: WriteGuards | None = None,
        collision: CollisionDetector | None = None,
    ) -> None:
        self._repo = repo
        self._audit = audit
        self._guards = guards or WriteGuards()
        self._collision = collision or CollisionDetector()
        # Engine-level single-writer serialization backstop. The
        # ConnectionManager serves a FastAPI threadpool, so cross-thread
        # interleaving of mutators is a real deployment scenario even though
        # the first release declares single-writer semantics. This reentrant
        # lock serializes the whole mutator span (get -> validate -> write); it
        # nests cleanly with the per-transaction ConnectionManager lock used by
        # repo.atomic().
        self._lock = threading.RLock()

    @staticmethod
    def _now(now: datetime | None) -> datetime:
        return now if now is not None else datetime.now(UTC)

    def apply_fact(
        self,
        candidate: Episode,
        *,
        now: datetime | None = None,
        subject_override: str | None = None,
    ) -> WriteResult:
        """Append a candidate episode, fail-loud on a concurrent collision.

        Derives ``subject``/``fact_id``, force-sets ``created_at=now``,
        ``expired_at=None`` and ``invalid_at=None`` (written null at ingest),
        runs the write guards, then detects collisions and appends INSIDE
        ``repo.atomic()`` so detect+append share one transaction (closes the
        time-of-check/time-of-use window). On collision: NO append, return
        ``WriteResult(ep_id=None, fact_id=None, status="COLLISION", ...)``. The
        partial unique index is the data backstop: a concurrent slip surfaces as
        ``sqlite3.IntegrityError``, caught and translated to the same COLLISION
        result (it never raises).
        """
        now = self._now(now)
        # LLM-path override: ``subject_override`` (passed only by ``remember``
        # when the extractor produced a subject) wins over the derived subject.
        # It is a SEPARATE argument, NOT ``candidate.subject`` — candidates may
        # carry a cosmetic ``subject`` (the apply_fact tests' fixture does) that
        # must keep being re-derived, so reading it would silently change
        # existing callers. ``fact_id_of(subject)`` equals the old
        # ``fact_id_for(body, title)`` when subject derives the same way
        # (regression pinned by the engine tests).
        subject = (
            subject_override if subject_override is not None else _derive_subject(candidate)
        )
        fact_id = fact_id_of(subject) if subject else None
        ep = candidate.model_copy(
            update={
                "created_at": now,
                "expired_at": None,
                "invalid_at": None,
                "subject": subject,
                "fact_id": fact_id,
            }
        )
        with self._lock:
            self._guards.validate(ep, repo=self._repo, op="apply_fact", now=now)
            collisions = self._collision.detect(ep, self._repo)
            if not collisions:
                # detect+append in one transaction so a concurrent append
                # cannot slip between them. The partial unique index is the
                # data-integrity backstop; IntegrityError is translated to the
                # fail-loud COLLISION result.
                # The audit.append runs INSIDE the same atomic so the episode
                # write and its AuditEvent commit together — a failure between
                # them cannot leave a persisted episode with no audit row (torn
                # audit trail). audit.append opens its own reentrant
                # cm.atomic() which just bumps the depth counter and reuses
                # this outer transaction.
                try:
                    with self._repo.atomic():
                        collisions = self._collision.detect(ep, self._repo)
                        if not collisions:
                            self._repo.append(ep)
                            self._audit.append(
                                AuditEvent(
                                    primitive="apply",
                                    target_id=ep.id,
                                    transaction_time=now,
                                    result="added",
                                    agent_id=_prov(candidate, "agent_id"),
                                    session_id=_prov(candidate, "session_id"),
                                    valid_time=ep.valid_at,
                                    cognitive_type=ep.cognitive_type,
                                )
                            )
                except sqlite3.IntegrityError:
                    # Concurrent-slip backstop: the partial unique index
                    # rejected the append. Re-detect to translate to a COLLISION
                    # result. The try now also wraps audit.append, so an
                    # IntegrityError from the audit INSERT (not the partial
                    # unique index) would otherwise be masked as a collision —
                    # and the atomic would have rolled the episode back, so
                    # re-detect finds no currently valid collision. Re-raise in
                    # that case so the real error surfaces instead of a bogus
                    # ACTIVE result for a rolled-back episode (never swallow an
                    # error as a collision it isn't).
                    collisions = self._collision.detect(ep, self._repo)
                    if not collisions:
                        raise
            if collisions:
                return WriteResult(
                    ep_id=None,
                    fact_id=None,
                    status=_STATUS_COLLISION,
                    collisions_detected=collisions,
                )
            status = (
                _STATUS_PENDING
                if ep.valid_at is not None and ep.valid_at > now
                else _STATUS_ACTIVE
            )
            return WriteResult(
                ep_id=ep.id,
                fact_id=ep.fact_id,
                status=status,
                collisions_detected=[],
            )

    def remember(
        self,
        *,
        body: str,
        by: dict,
        valid_at: datetime | None = None,
        cognitive_type: str | None = None,
        schema_version: str = "1.1",
        title: str | None = None,
        summary: str | None = None,
        subject: str | None = None,
        supersedes: str | None = None,
        supersedes_reason: str | None = None,
        now: datetime | None = None,
    ) -> WriteResult:
        """Single write entry point; picks the id by source.

        Importer path (``source_type == "importer"`` with ``importer_vendor``
        set) uses a deterministic UUIDv5 so re-import yields the same id; every
        other source uses a fresh UUIDv7. Idempotency is check-then-skip: if the
        derived id already exists with the same canonical body hash, the call is
        a NOOP (no append, no audit). Otherwise an ``Episode`` is built and
        delegated to ``apply_fact``.

        ``subject`` is the LLM-path override: when the extractor produced a
        subject, it wins over the derived subject in ``apply_fact``. The skip
        path never passes it, so its behaviour is byte-identical (regression
        pinned). ``summary`` is the editorial summary: the write path passes the
        caller's value or a deterministic fallback (first sentence of the body,
        ``SUMMARY_MAX_CHARS=200``); the engine persists it verbatim.
        LLM-extracted ``tags`` are intentionally NOT passed here: the SQLite
        episode store does not persist ``tags`` (the repo reads them back as
        ``[]``), so injecting them would be a silent lie.
        """
        now = self._now(now)
        source_type = by.get("source_type")
        importer_vendor = by.get("importer_vendor")
        if source_type == "importer" and importer_vendor is not None:
            source_record_id = by.get("source_record_id") or ""
            ep_id = deterministic_id(
                importer_vendor, source_record_id, canonical_body_hash(body)
            )
        else:
            ep_id = new_uuid7()

        with self._lock:
            # Serialize the idempotency check-then-skip end-to-end so a
            # concurrent re-import of the same importer payload cannot slip a
            # PK dup between our get and our apply_fact.
            existing = self._repo.get(ep_id)
            if existing is not None and canonical_body_hash(
                existing.body or ""
            ) == canonical_body_hash(body):
                return WriteResult(
                    ep_id=ep_id,
                    fact_id=existing.fact_id,
                    status=_STATUS_NOOP,
                    collisions_detected=[],
                )

            ep = Episode(
                id=ep_id,
                created_at=now,
                schema_version=schema_version,
                provenance=by,
                body=body,
                subject=subject,
                valid_at=valid_at,
                invalid_at=None,
                expired_at=None,
                supersedes=supersedes,
                supersedes_reason=supersedes_reason,
                cognitive_type=cognitive_type,
                source_type=source_type,
                title=title,
                summary=summary,
            )
            return self.apply_fact(ep, now=now, subject_override=subject)

    def forget(
        self,
        ep_id: str,
        *,
        reason: str,
        by: dict,
        now: datetime | None = None,
    ) -> Episode:
        """Soft-delete bi-temporal; reason lives in audit only.

        Marks ``invalid_at = now`` once (null->now). Preserves the row, never
        touches ``expired_at`` or the body/subject. The invalidation metadata
        (``reason``, ``agent_id``) is written ONLY to the ``AuditEvent``, never
        to the episode row.
        """
        now = self._now(now)
        with self._lock:
            ep = self._repo.get(ep_id)
            if ep is None:
                raise NotFound(ep_id)
            self._guards.validate(ep, repo=self._repo, op="forget", now=now)
            # The invalidation and its AuditEvent commit in ONE transaction.
            # Forget had no atomic block at all previously — a crash between
            # set_invalid_at and audit.append left an invalidated episode with
            # no forget audit row. The reentrant cm.atomic() nests clean
            # (set_invalid_at + audit.append each open their own, which just
            # bump the depth counter and reuse this outer transaction).
            # Storage-level idempotency backstop: WHERE invalid_at IS NULL. 0 rows
            # (already invalidated between our get and the update) -> InvalidationConflictError.
            with self._repo.atomic():
                self._repo.set_invalid_at(ep_id, now)
                self._audit.append(
                    AuditEvent(
                        primitive="forget",
                        target_id=ep_id,
                        transaction_time=now,
                        result="invalidated",
                        agent_id=_dict_str(by, "agent_id"),
                        session_id=_dict_str(by, "session_id"),
                        reason=reason,
                        valid_time=ep.valid_at,
                        cognitive_type=ep.cognitive_type,
                    )
                )
            return ep.model_copy(update={"invalid_at": now})

    def improve(
        self,
        ep_id: str,
        new_body: str,
        *,
        by: dict,
        valid_at: datetime | None = None,
        reason: str = "correction",
        now: datetime | None = None,
    ) -> Episode:
        """Human edit = invalidate-then-append atomically.

        Invalidates the old episode and appends a new one with
        ``supersedes=old`` inside ``repo.atomic()``. If the new body's subject
        collides with a THIRD currently valid episode (not the target, already
        invalidated), raises ``E_COLLISION_EXISTS`` and the whole transaction
        rolls back — the target is NOT invalidated, the new episode is NOT
        appended. Preserves the signed ``-> Episode`` return type.

        The successor carries ``supersedes_reason=
        SupersedesReason.CORRECTION`` — the portable enum that survives
        export/import. The free-text ``reason`` (observability) goes ONLY to the
        improve ``AuditEvent``; it is NOT copied into ``supersedes_reason`` (that
        would be type-confusion: free text -> enum).
        """
        now = self._now(now)
        with self._lock:
            old = self._repo.get(ep_id)
            if old is None:
                raise NotFound(ep_id)
            self._guards.validate(old, repo=self._repo, op="forget", now=now)
            new_ep = Episode(
                id=new_uuid7(),
                created_at=now,
                schema_version=old.schema_version,
                provenance=by,
                body=new_body,
                valid_at=valid_at or now,
                invalid_at=None,
                expired_at=None,
                supersedes=ep_id,
                # The successor of an improve is a CORRECTION — it carries the
                # portable ``SupersedesReason`` enum so it survives
                # export/import. The free-text ``reason`` (observability) goes
                # ONLY to the AuditEvent below; copying it here would be
                # type-confusion (free text -> enum).
                supersedes_reason=SupersedesReason.CORRECTION,
                cognitive_type=old.cognitive_type,
                source_type=by.get("source_type"),
            )
            # Derive subject/fact_id from the new body — apply_fact does this via
            # model_copy, but improve appends directly, so it must replicate the
            # derivation or the successor is stored with fact_id=None.
            new_ep = new_ep.model_copy(
                update={
                    "subject": _derive_subject(new_ep),
                    "fact_id": fact_id_for(new_ep.body or "", title=new_ep.title),
                }
            )
            with self._repo.atomic():  # atomic: full rollback if the 2nd write fails
                self._repo.set_invalid_at(ep_id, now)  # invalidate-then-append order
                self._guards.validate(new_ep, repo=self._repo, op="improve", now=now)
                collisions = self._collision.detect(new_ep, self._repo)
                if collisions:
                    # Fail-loud, consistent with the collision path. Rollback:
                    # target stays valid, new episode not appended. The caller
                    # (the facade's improve) decides.
                    raise errors.EngineError(errors.E_COLLISION_EXISTS, collisions=collisions)
                self._repo.append(new_ep)
                # audit INSIDE the atomic — the invalidation, the new episode,
                # and the improve AuditEvent commit together (or roll back
                # together). A crash after append but before audit can no longer
                # leave the successor persisted with no audit row.
                self._audit.append(
                    AuditEvent(
                        primitive="improve",
                        target_id=ep_id,
                        transaction_time=now,
                        result="updated",
                        agent_id=_dict_str(by, "agent_id"),
                        session_id=_dict_str(by, "session_id"),
                        successor_id=new_ep.id,
                        reason=reason,
                        valid_time=new_ep.valid_at,
                        cognitive_type=new_ep.cognitive_type,
                    )
                )
            return new_ep

    # ---------- readers -----------------------------------------------------

    def get_vigente(
        self, subject: str | None = None, *, now: datetime | None = None
    ) -> list[Episode]:
        """Currently active: valid rows whose valid_at has come into effect.

        ``repo.query_vigent`` returns the partial-index set (``invalid_at IS
        NULL AND expired_at IS NULL``), which INCLUDES ``PENDING_INGEST``
        (``valid_at`` in the future). This reader post-filters to *currently
        active*: ``valid_at IS NULL OR valid_at <= now``.
        """
        now = self._now(now)
        return [
            ep
            for ep in self._repo.query_vigent(subject)
            if ep.valid_at is None or ep.valid_at <= now
        ]

    def follow_supersedes_chain(self, ep_id: str) -> list[Episode]:
        """Bidirectional supersedes closure for ``ep_id`` (delegates to the persistence layer)."""
        return self._repo.chain_from(ep_id)

    def is_valid_at(self, ep_id: str, point_in_time: datetime) -> bool:
        """PIT real-world predicate, NULL-safe.

        ``valid_at IS NULL`` means "from forever" -> valid at any ``t`` while
        not yet invalidated. Unknown episode -> ``False``. ``point_in_time`` is
        normalized to UTC via ``astimezone`` (a naive datetime is interpreted as
        local time, then converted).
        """
        ep = self._repo.get(ep_id)
        if ep is None:
            return False
        pit = point_in_time.astimezone(UTC)
        after_valid = ep.valid_at is None or ep.valid_at <= pit
        before_invalid = ep.invalid_at is None or pit < ep.invalid_at
        return after_valid and before_invalid

    def is_known_at(self, ep_id: str, point_in_time: datetime) -> bool:
        """PIT system predicate, NULL-safe.

        Was the fact known to the system at ``t``? Requires ``created_at <= t``
        and not yet expired. Unknown episode -> ``False``. ``point_in_time`` is
        normalized to UTC via ``astimezone``.
        """
        ep = self._repo.get(ep_id)
        if ep is None:
            return False
        pit = point_in_time.astimezone(UTC)
        after_created = ep.created_at <= pit
        before_expired = ep.expired_at is None or pit < ep.expired_at
        return after_created and before_expired

    def audit_log(self, ep_id: str) -> list[AuditEvent]:
        """Audit events whose ``target_id`` is ``ep_id`` (delegates to the persistence layer)."""
        return self._audit.query(target_id=ep_id)

    def freshness_view(self, ep_id: str, *, now: datetime | None = None) -> FreshnessView:
        """Pure derivation of an episode's freshness snapshot (no external state).

        Fetches the episode via the repository and delegates the pure derivation
        to ``freshness_of`` (single source of truth shared with the disclosure
        shaper). ``fact_id`` is the subject hash (``ep.fact_id``), consistent
        with ``WriteResult.fact_id``.
        """
        now = self._now(now)
        ep = self._repo.get(ep_id)
        if ep is None:
            raise NotFound(ep_id)
        return freshness_of(ep, now)

    # ---------- later-release stubs (declared extension point, fail-loud) --
    # These accessors are part of the engine surface but revisable until a
    # later release. The current release refuses to over-claim behavior that
    # depends on a signed conflict policy (fail-loud honesty). See policy.py.

    def state_at(self, t: datetime, *, subject: str | None = None) -> list[Episode]:
        """PIT bulk query: reconstruct the valid state at ``t`` (a later release)."""
        raise errors.EngineError(errors.E_NOT_IN_MVP_0, primitive="state_at")

    def recall_pit(self, ep_id: str, t: datetime) -> Episode | None:
        """PIT recall: the episode known to the system at ``t`` (a later release)."""
        raise errors.EngineError(errors.E_NOT_IN_MVP_0, primitive="recall_pit")

    def detect_collisions(self, candidate: Episode) -> list:
        """Public collision detection (a later release). Internal
        ``_collision.detect`` already powers ``apply_fact``/``improve``
        fail-loud in the current release; this public accessor is declared for
        the MCP server and the benchmark harness but revisable until a later
        release."""
        raise errors.EngineError(errors.E_NOT_IN_MVP_0, primitive="detect_collisions")

    def resolve_conflict(self, *, collision: object) -> object:
        """Dispatch a collision to the ConflictPolicy (a later release)."""
        raise errors.EngineError(errors.E_NOT_IN_MVP_0, primitive="resolve_conflict")

    def revalidate(self, ep_id: str, *, by: dict, now: datetime | None = None) -> Episode:
        """SUPERSEDED -> ACTIVE via a new episode (a later release)."""
        raise errors.EngineError(errors.E_NOT_IN_MVP_0, primitive="revalidate")

    def expire(self, ep_id: str, *, now: datetime | None = None) -> Episode:
        """Decay (reserved for now, fail-loud honesty). Sets ``expired_at`` (a medium-term goal)."""
        raise errors.EngineError(errors.E_NOT_IN_MVP_0, primitive="expire")

    # ---------- write-path skip border contract ----------------------------

    def is_valid_skip_path(self, ep: Episode) -> bool:
        """Pure border validator for the write path's deterministic skip-path.

        A payload whose ``provenance["extraction_mode"] == "skip"`` is routable
        down the deterministic skip-path ONLY if it satisfies the contract:
        ``created_at`` present, ``invalid_at``/``expired_at`` null,
        ``valid_at <= created_at`` when ``valid_at`` is non-null, and a semver
        ``schema_version``. The validator reads no repo/audit state.

        Reconciliation with ``-> bool``:

        - ``extraction_mode != "skip"`` -> ``False`` (not a skip payload; the
          write path uses another mode, not an error).
        - ``extraction_mode == "skip"`` + contract holds -> ``True``.
        - ``extraction_mode == "skip"`` + contract BROKEN -> raise
          ``EngineError("E_SKIP_CONTRACT_VIOLATED")``: the payload claims skip
          but cannot be deterministically skipped, so the write path re-routes
          to ``llm``.
        """
        extraction_mode = ep.provenance.get("extraction_mode")
        if extraction_mode != "skip":
            return False
        if ep.created_at is None:
            raise errors.EngineError(
                errors.E_SKIP_CONTRACT_VIOLATED, field="created_at"
            )
        if ep.invalid_at is not None:
            raise errors.EngineError(
                errors.E_SKIP_CONTRACT_VIOLATED, field="invalid_at"
            )
        if ep.expired_at is not None:
            raise errors.EngineError(
                errors.E_SKIP_CONTRACT_VIOLATED, field="expired_at"
            )
        if ep.valid_at is not None and ep.valid_at > ep.created_at:
            raise errors.EngineError(
                errors.E_SKIP_CONTRACT_VIOLATED, field="valid_at"
            )
        if not _SEMVER_RE.match(ep.schema_version):
            raise errors.EngineError(
                errors.E_SKIP_CONTRACT_VIOLATED, field="schema_version"
            )
        return True


def _derive_subject(ep: Episode) -> str | None:
    return derive_subject(ep.body or "", title=ep.title)


def _dict_str(d: dict, key: str) -> str | None:
    """Read a string-typed value from a provenance dict, else None."""
    value = d.get(key)
    return value if isinstance(value, str) else None


def _prov(ep: Episode, key: str) -> str | None:
    return _dict_str(ep.provenance, key)