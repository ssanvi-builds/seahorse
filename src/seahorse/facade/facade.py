"""#12 MemoryFacade — the canonical Python API for the memory-native primitives.

The facade is **clean delegation**: routing + boundary shape-validation +
primitive logging. It does NOT host domain logic. It does not fuse (#11),
project (#8), extract (#5/#4), open transactions (``repo.atomic()`` lives in
#2), construct ``IndexRow`` (#8 owns that), derive ``subject``/``fact_id`` (#2),
or build ``supersedes`` (#2). The ``source_type → skip`` guard lives in #5's
``decide_path`` — #12 does not replicate it.

The four memory-native primitives + three progressive-disclosure read levels:
- ``remember``  → #5 ``WritePath.ingest`` (single write-path, ADR-09).
- ``recall``    → MVP-0 G2 vigente listing via #8 ``materialize_index``
                  (synthetic ``FusedCandidate`` score=0.0; no ranking, no PIT).
- ``recall_timeline`` / ``recall_full`` → #8 ``materialize_timeline`` / ``materialize_full``.
- ``improve`` / ``forget`` → #2 ``engine.improve`` / ``engine.forget`` directly.
- ``freshness_view`` / ``audit_log`` / ``follow_supersedes_chain`` → passthroughs.

Engine errors (``EngineError``) are propagated verbatim — never re-wrapped.
Boundary failures raise ``SeahorseError`` (stable ``code``). MVP-1 primitives
(``expire`` / ``revalidate``) raise ``E_NOT_IN_MVP_0_1`` at the border.

References:
- f5-12-primitives-facade.md (the design doc + 13 adversarial corrections)
- f6-signoffs.md SO-5 (write-path stub + llm freeze), ADR-09 (single write-path)
- seahorse/disclosure/shaper.py (#8 — owns IndexRow materialization)
- seahorse/engine/engine.py (#2 — owns improve/forget/remember/get_vigente)
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol, cast, runtime_checkable

from seahorse.contracts.embeddings import QueryEmbedder
from seahorse.contracts.engine import (
    AuditEvent,
    Episode,
    FreshnessView,
    WriteResult,
)
from seahorse.contracts.index import PIT_KIND_VALUES, PITKind
from seahorse.contracts.retrieval import FusedCandidate
from seahorse.disclosure.types import (
    TOP_K,
    FullDetail,
    IndexRow,
    PITPoint,
    TimelineWindow,
)
from seahorse.facade.errors import (
    E_EMPTY_BODY,
    E_INVALID_EXTRACTION_MODE,
    E_MISSING_SOURCE_TYPE,
    E_NOT_IN_MVP_0_1,
    E_PIT_REQUIRES_T,
    EmptyQueryError,
    InvalidPITKind,
    PitRecallNotSupportedMVP0,
    SeahorseError,
)
from seahorse.facade.stub_embedder import StubQueryEmbedder
from seahorse.facade.types import (
    ContextData,
    ContextEpisode,
    ExtractionMode,
    FacadeConfig,
    Provenance,
    RememberPayload,
)

_logger = logging.getLogger("seahorse.facade")

_VALID_PIT_KINDS: frozenset[str] = PIT_KIND_VALUES  # single-source from PITKind Literal
# Modes routable by the single-episode ``remember`` primitive. ``consolidated``
# is schema-valid (the wire round-trips it) but NOT routed here — the batch
# distillation writes via ``engine.remember`` directly, bypassing #5
# ``decide_path`` (obsiforge §5.4); ``llm_partial`` stays fully reserved.
_VALID_MODES: frozenset[str] = frozenset({"skip", "llm"})


def _default_clock() -> datetime:
    return datetime.now(UTC)


def _default_primitive_log(op: str, result: str) -> None:
    _logger.info("primitive op=%s result=%s", op, result)


@runtime_checkable
class _ShaperLike(Protocol):
    def materialize_index(
        self, candidates: Sequence[FusedCandidate], *, pit: PITPoint | None, now: datetime | None
    ) -> list[IndexRow]: ...

    def materialize_timeline(
        self,
        anchor_ep_id: str,
        *,
        axis: Any,
        pit: PITPoint | None,
        hops: int = 1,
        now: datetime | None = None,
    ) -> TimelineWindow: ...

    def materialize_full(
        self, ep_ids: Sequence[str], *, pit: PITPoint | None, now: datetime | None
    ) -> list[FullDetail]: ...


@runtime_checkable
class _WritePathLike(Protocol):
    def ingest(
        self,
        payload: RememberPayload,
        extraction_mode: ExtractionMode,
        *,
        now: datetime | None = ...,
    ) -> WriteResult: ...


@runtime_checkable
class _EngineLike(Protocol):
    def get_vigente(
        self, subject: str | None = ..., *, now: datetime | None = ...
    ) -> list[Episode]: ...

    def improve(
        self,
        ep_id: str,
        new_body: str,
        *,
        by: dict,
        valid_at: datetime | None = ...,
        reason: str = ...,
        now: datetime | None = ...,
    ) -> Episode: ...

    def forget(
        self, ep_id: str, *, reason: str, by: dict, now: datetime | None = ...
    ) -> Episode: ...

    def freshness_view(self, ep_id: str, *, now: datetime | None = ...) -> FreshnessView: ...

    def audit_log(self, ep_id: str) -> list[AuditEvent]: ...

    def follow_supersedes_chain(self, ep_id: str) -> list[Episode]: ...


@runtime_checkable
class _RetrieverLike(Protocol):
    """Recall-policy seam (C8.1): produces ranked ``FusedCandidate`` for #8.

    MVP-0 impl is ``VigenteListingRetriever`` (vigente listing, no ranking/PIT).
    MVP-1 swaps in an adapter over ``seahorse.retrieval.recall`` (kNN+BM25+RRF)
    at the composition root — a single-point change. The facade owns boundary
    validation (empty query, PIT refusal) + the #8 shaper call; the retriever
    owns listing/filter/truncate + ranking.

    M1-C.2: PIT capability is a DATA attribute (not a method) — the facade reads
    ``getattr(retriever, "supports_pit", False)`` before delegating. A retriever
    that does not declare it (e.g. G2) causes the facade to refuse a caller pit
    before any read (ADR-03); the MVP-1 ``HybridRetriever`` declares it True and
    receives the pit verbatim. ``@runtime_checkable`` does NOT check data
    attributes, which is why the facade uses ``getattr`` with a False default.
    """

    def recall(
        self,
        query: str,
        *,
        pit: PITPoint | None,
        k: int,
        cognitive_type: str | None,
        subject_filter: str | None,
    ) -> Sequence[FusedCandidate]: ...


class MemoryFacade:
    """The canonical Python API for the memory-native primitives (MVP-0).

    Construct with the engine (#2), write-path (#5), and shaper (#8) it
    delegates to. ``clock`` makes ``now`` injectable (ADR-10 reproducibility);
    ``primitive_log`` lets tests assert the primitive was emitted.
    """

    def __init__(
        self,
        *,
        engine: _EngineLike,
        write_path: _WritePathLike,
        shaper: _ShaperLike,
        retriever: _RetrieverLike,
        clock: Callable[[], datetime] | None = None,
        config: FacadeConfig | None = None,
        primitive_log: Callable[[str, str], None] | None = None,
        embedder: QueryEmbedder | None = None,
        on_episode_improved: Callable[[str], None] | None = None,
    ) -> None:
        self._engine = engine
        self._write_path = write_path
        self._shaper = shaper
        self._retriever = retriever
        self._clock = clock or _default_clock
        self._config = config or FacadeConfig()
        self._log = primitive_log or _default_primitive_log
        # C8.4: composition-root ``embedder`` slot (MVP-1 #7 seam). MVP-0 recall
        # is the vigente listing and never embeds, so the slot is inert; the
        # default stub raises E_NOT_IN_MVP_0 if a non-skip path invokes it. MVP-1
        # swaps in the real #7 adapter here — a single-point change.
        self._embedder: QueryEmbedder = embedder if embedder is not None else StubQueryEmbedder()
        # F7 enabler — post-``improve`` index hook (dependency injection, the
        # facade never knows the indexer). ``improve`` bypasses #5 (manual
        # supersede edit), so the successor would never reach vec0/FTS and the
        # hybrid recall could not recover it (f5-16 §4.6 knowledge_update_accuracy
        # = 0). The composition root wires this to the write-path indexer.
        self._on_episode_improved = on_episode_improved

    # ------------------------------------------------------------------ now

    def _now(self, now: datetime | None) -> datetime:
        return now if now is not None else self._clock()

    # ------------------------------------------------------------------ pit

    @staticmethod
    def _validate_pit_kind(kind: str) -> None:
        if kind not in _VALID_PIT_KINDS:
            raise InvalidPITKind(kind)

    # --------------------------------------------------------------- remember

    def remember(
        self,
        payload: RememberPayload,
        *,
        skip_extraction: bool | None = None,
        extraction_mode: ExtractionMode | None = None,
        now: datetime | None = None,
    ) -> WriteResult:
        """Remember a fact (delegates to #5 ``WritePath.ingest``, ADR-09).

        Boundary shape-validation only: body non-empty, ``by.source_type``
        present (caller authority), ``tags`` empty in MVP-0, resolved
        ``extraction_mode`` ∈ {skip, llm}. Domain invariants (valid_at guard,
        cognitive_type, collision, idempotency) are the engine's/#5's authority.
        """
        self._validate_remember_payload(payload)
        mode = self._resolve_mode(skip_extraction, extraction_mode)
        result = self._write_path.ingest(payload, mode, now=self._now(now))
        self._log("remember", result.status.lower())
        return result

    def _validate_remember_payload(self, payload: RememberPayload) -> None:
        if not payload.body or not payload.body.strip():
            raise SeahorseError(code=E_EMPTY_BODY, detail="remember payload body must be non-empty")
        if not payload.by.get("source_type"):
            raise SeahorseError(
                code=E_MISSING_SOURCE_TYPE,
                detail="remember payload by['source_type'] is required (caller authority)",
            )
        if payload.tags:
            raise SeahorseError(
                code=E_NOT_IN_MVP_0_1,
                detail="tags are not supported in MVP-0 remember",
            )

    def _resolve_mode(
        self, skip_extraction: bool | None, extraction_mode: ExtractionMode | None
    ) -> ExtractionMode:
        if extraction_mode is not None:
            if extraction_mode not in _VALID_MODES:
                raise SeahorseError(
                    code=E_INVALID_EXTRACTION_MODE,
                    detail=(
                        f"extraction_mode={extraction_mode!r} is not routable by "
                        "single-episode remember (valid: skip|llm; 'consolidated' "
                        "is schema-valid for batch distillation, which writes via "
                        "engine.remember directly — obsiforge §5.4; 'llm_partial' "
                        "is reserved)"
                    ),
                )
            return extraction_mode
        if skip_extraction is True:
            return "skip"
        if skip_extraction is False:
            return "llm"
        return self._config.default_extraction_mode

    # ----------------------------------------------------------------- recall

    def recall(
        self,
        query: str,
        *,
        pit: PITPoint | None = None,
        k: int = TOP_K,
        cognitive_type: str | None = None,
        subject_filter: str | None = None,
    ) -> list[IndexRow]:
        """Recall the INDEX level (MVP-0 G2 vigente listing / MVP-1 hybrid).

        Boundary validation only: ``query`` non-empty; PIT is refused before
        any read UNLESS the injected retriever declares ``supports_pit`` (the
        MVP-1 ``HybridRetriever`` — PIT routing is #11's job; ADR-03 axes never
        mixed). The ranking/listing policy is delegated to the injected
        ``Retriever`` (C8.1 seam); the retriever produces ``FusedCandidate`` and
        #12 forwards the (possibly PIT) candidates to #8 ``materialize_index``.
        #12 NEVER constructs ``IndexRow``.
        """
        if not query or not query.strip():
            raise EmptyQueryError()
        if pit is not None and not getattr(self._retriever, "supports_pit", False):
            # Fail loud before any read: the current regime has no PIT axis
            # (MVP-0 G2; ADR-03). The hybrid MVP-1 retriever declares
            # ``supports_pit`` and receives the pit verbatim.
            raise PitRecallNotSupportedMVP0()
        candidates = self._retriever.recall(
            query,
            pit=pit,
            k=k,
            cognitive_type=cognitive_type,
            subject_filter=subject_filter,
        )
        # #12 forwards the (possibly PIT) candidates to #8 materialize_index.
        return self._shaper.materialize_index(candidates, pit=pit, now=self._clock())

    def recall_timeline(
        self,
        anchor_ep_id: str,
        *,
        axis: str = "supersedes_chain",
        pit: PITPoint | None = None,
        hops: int = 1,
    ) -> TimelineWindow:
        """Recall the TIMELINE level (delegates to #8 ``materialize_timeline``).

        ``hops`` is the #10 ``graph_bfs`` traversal depth (1-2; >2 raises
        ``HopsCapExceeded`` in #8). ``pit=None`` resolves to ``state_at`` at
        the facade clock (ADR-03, no silent ``known_at``).
        """
        if pit is not None:
            self._validate_pit_kind(pit.kind)
        return self._shaper.materialize_timeline(
            anchor_ep_id, axis=axis, pit=pit, hops=hops, now=self._clock()
        )

    def recall_full(
        self, ep_ids: Sequence[str], *, pit: PITPoint | None = None
    ) -> list[FullDetail]:
        """Recall the FULL level (delegates to #8 ``materialize_full``).

        Border shape-validation only: ``pit.kind`` is validated here. The
        ``MAX_FULL_BATCH`` cap and ``PitFullNotSupported`` are #8's domain
        contract — #8 raises ``FullBatchTooLarge`` (before any fetch) and
        ``PitFullNotSupported`` (PIT in FULL is MVP-1). #12 does NOT replicate
        the batch guard (delegation purity: MAX_FULL_BATCH is owned by #8).
        """
        if pit is not None:
            self._validate_pit_kind(pit.kind)
        return self._shaper.materialize_full(ep_ids, pit=pit, now=self._clock())

    # ---------------------------------------------------------------- improve

    def improve(
        self,
        ep_id: str,
        new_body: str,
        *,
        by: Provenance,
        valid_at: datetime | None = None,
        reason: str = "correction",
        now: datetime | None = None,
    ) -> Episode:
        """Improve a fact (human edit): delegates to #2 ``engine.improve`` directly.

        Manual path (NOT #5): #5 owns the first-ingestion write-path, not the
        supersede edit. The effective provenance marks the edit as a skip-path
        extraction (``extraction_mode='skip'``, ``model_used=None``,
        ``prompt_hash=None``, ``confidence=1.0`` — a human edit is fully
        trusted) while preserving the caller's ``source_type`` (caller
        authority). #12 does NOT open ``repo.atomic()`` (#2 owns the I8
        atomic invalidate-then-append) and does NOT call ``write_path.ingest``.

        ``EngineError(E_COLLISION_EXISTS)`` is propagated verbatim (the gap from
        f5-12 §5.5 is resolved in the engine: atomic rollback, target stays
        vigente). #12 never catches it.
        """
        self._validate_improve_input(ep_id, new_body, by)
        effective_by: dict[str, Any] = {
            **by,
            "extraction_mode": "skip",
            "model_used": None,
            "prompt_hash": None,
            "confidence": 1.0,
        }
        result = self._engine.improve(
            ep_id, new_body, by=effective_by, valid_at=valid_at, reason=reason, now=self._now(now)
        )
        # F7 enabler: index the successor so the hybrid recall can recover the
        # new version (fires ONLY on success — a collision raises above). The
        # G2 regime wires no hook → honest no-op.
        if self._on_episode_improved is not None:
            self._on_episode_improved(result.id)
        self._log("improve", "updated")
        return result

    def _validate_improve_input(self, ep_id: str, new_body: str, by: Provenance) -> None:
        if not ep_id:
            raise SeahorseError(code=E_EMPTY_BODY, detail="improve ep_id must be non-empty")
        if not new_body or not new_body.strip():
            raise SeahorseError(code=E_EMPTY_BODY, detail="improve new_body must be non-empty")
        if not by.get("source_type"):
            raise SeahorseError(
                code=E_MISSING_SOURCE_TYPE,
                detail="improve by['source_type'] is required (caller authority)",
            )

    # ----------------------------------------------------------------- forget

    def forget(
        self,
        ep_id: str,
        *,
        reason: str,
        by: Provenance,
        now: datetime | None = None,
    ) -> Episode:
        """Forget a fact (soft-delete): delegates to #2 ``engine.forget`` directly.

        ``EngineError(E_PENDING_CANNOT_INVALIDATE)``, ``InvalidationConflictError``,
        and ``NotFound`` are propagated verbatim. #12 does NOT call
        ``write_path.ingest`` and does NOT touch ``expired_at`` (I7).
        """
        self._validate_forget_input(ep_id, reason, by)
        result = self._engine.forget(ep_id, reason=reason, by=dict(by), now=self._now(now))
        self._log("forget", "invalidated")
        return result

    @staticmethod
    def _validate_forget_input(ep_id: str, reason: str, by: Provenance) -> None:
        if not ep_id:
            raise SeahorseError(code=E_EMPTY_BODY, detail="forget ep_id must be non-empty")
        if not reason or not reason.strip():
            raise SeahorseError(code=E_EMPTY_BODY, detail="forget reason must be non-empty")
        if not by.get("source_type"):
            raise SeahorseError(
                code=E_MISSING_SOURCE_TYPE,
                detail="forget by['source_type'] is required (caller authority)",
            )

    # ----------------------------------------------------------- passthroughs

    def get_vigente(self, subject: str | None = None) -> list[Episode]:
        """Vigente episodes (passthrough to #2 ``get_vigente``).

        Exposed for the consolidate CLI (Sprint B): the distillation clusters
        the vigente set by subject recurrence (§5.3).
        """
        return self._engine.get_vigente(subject, now=self._clock())

    def distill(
        self,
        source_ep_ids: list[str],
        representative: Episode,
        consolidated_body: str,
        by: Provenance,
    ) -> WriteResult:
        """Distill source episodes into a consolidated semantic episode (§5.4).

        Delegates to the ``distill_episodes`` primitive (a client of #2) — the
        facade is the seam so the CLI never reaches the engine directly
        (delegation purity). The consolidated episode references its
        representative via ``supersedes`` WITHOUT invalidating the sources.
        """
        from seahorse.distill.distill import distill_episodes

        return distill_episodes(
            self._engine,
            source_ep_ids,
            representative,
            consolidated_body,
            dict(by),
        )

    def freshness_view(self, ep_id: str) -> FreshnessView:
        """Freshness snapshot (delegates to #2 ``engine.freshness_view``)."""
        return self._engine.freshness_view(ep_id, now=self._clock())

    def audit_log(self, ep_id: str) -> list[AuditEvent]:
        """Audit events for ``ep_id`` (delegates to #2 ``engine.audit_log``)."""
        return self._engine.audit_log(ep_id)

    def follow_supersedes_chain(self, ep_id: str) -> list[Episode]:
        """Supersedes closure for ``ep_id`` (delegates to #2)."""
        return self._engine.follow_supersedes_chain(ep_id)

    # ------------------------------------------------------------------- pit

    def build_pit(
        self,
        pit: PITPoint | None = None,
        *,
        pit_kind: str | None = None,
        t: datetime | None = None,
    ) -> PITPoint | None:
        """Build a ``PITPoint`` from either a ready point or (kind, t).

        ``pit`` wins (its kind is validated). ``pit_kind`` without ``t`` raises
        ``E_PIT_REQUIRES_T``. All-None returns ``None`` (no PIT).
        """
        if pit is not None:
            self._validate_pit_kind(pit.kind)
            return pit
        if pit_kind is None:
            return None
        if t is None:
            raise SeahorseError(
                code=E_PIT_REQUIRES_T,
                detail="pit_kind requires a t (timestamp)",
            )
        self._validate_pit_kind(pit_kind)
        return PITPoint(kind=cast(PITKind, pit_kind), t=t)

    # --------------------------------------------------------------- context

    def context(self, *, top_k: int | None = None) -> ContextData:
        """Bootstrap context by RECENCY, not semantics (obsiforge §6.1-6.2).

        The shared method behind the SessionStart hook and the CLI ``context``
        subcommand (single point of change). Four blocks at INDEX level, no
        body: (1) recent episodes (created_at desc, ep_id asc — sort G2,
        ADR-10); (2) current vigente state; (3) last session grouped by
        ``provenance.session_id`` (INDEX list, NOT an abstractive summary —
        honesty, §6.2); (4) header + counter + pointer (rendered by the
        assembler). Deterministic (ADR-10).
        """
        k = top_k if top_k is not None else self._config.top_k
        eps = self._engine.get_vigente(None, now=self._clock())
        # Deterministic G2 sort: created_at desc, ep_id asc tie-break (two
        # stable sorts, mirroring the vigente retriever).
        eps = sorted(eps, key=lambda e: e.id)
        eps = sorted(eps, key=lambda e: e.created_at, reverse=True)
        recent = eps[:k]

        by_session: dict[str, list[Episode]] = {}
        for e in recent:
            sid = e.provenance.get("session_id")
            if isinstance(sid, str) and sid:
                by_session.setdefault(sid, []).append(e)
        last_session_id: str | None = None
        last_session: list[Episode] = []
        if by_session:
            last_session_id = max(
                by_session,
                key=lambda sid: max(e.created_at for e in by_session[sid]),
            )
            last_session = by_session[last_session_id]

        return ContextData(
            recent=[self._to_context_episode(e) for e in recent],
            vigente_count=len(eps),
            last_session_id=last_session_id,
            last_session=[self._to_context_episode(e) for e in last_session],
            total_episodes=len(eps),
        )

    @staticmethod
    def _to_context_episode(e: Episode) -> ContextEpisode:
        return ContextEpisode(
            ep_id=e.id,
            subject=e.subject,
            summary=e.summary,
            created_at=e.created_at,
            session_id=e.provenance.get("session_id"),
        )

    # --------------------------------------------------------- MVP-1 stubs

    def expire(self, ep_id: str) -> Episode:
        """Decay (set ``expired_at``) — MVP mediano. Refused in MVP-0."""
        raise SeahorseError(
            code=E_NOT_IN_MVP_0_1,
            detail="expire is not in MVP-0/MVP-1 (decay is mediano)",
        )

    def revalidate(self, ep_id: str, *, by: Provenance) -> WriteResult:
        """SUPERSEDED → ACTIVE via a new episode — MVP-1. Refused in MVP-0."""
        raise SeahorseError(
            code=E_NOT_IN_MVP_0_1,
            detail="revalidate is not in MVP-0 (MVP-1 primitive)",
        )


__all__ = ["MemoryFacade"]