"""Facade collaborator Protocols — the structural seams the facade delegates to.

Single-homed here (C4 prep, 2026-10-19) so ``facade.py`` stays small enough to
keep absorbing the recall seam work; the facade module re-exports these names
so annotations and the white-box tests keep resolving.

- ``_ShaperLike``       — progressive disclosure's materialize_* surface.
- ``_WritePathLike``    — the single write-path entry (WritePath.ingest).
- ``_MaterializerLike`` — the composition-root-injected .md materializer
                          (best-effort side effects, M9: never fail the write).
- ``_EngineLike``       — the engine reads/writes the facade routes verbatim.
- ``_RetrieverLike``    — the recall-policy extension point; PIT capability is
                          a DATA attribute read via ``getattr(..., False)``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from seahorse.contracts.engine import AuditEvent, Episode, FreshnessView, WriteResult
from seahorse.contracts.rerank import QueryReranker
from seahorse.contracts.retrieval import FusedCandidate
from seahorse.disclosure.types import FullDetail, IndexRow, PITPoint, TimelineWindow
from seahorse.facade.types import ExtractionMode, RememberPayload
from seahorse.retrieval.constants import RERANK_OVERFETCH_K


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
class _MaterializerLike(Protocol):
    """The materializer surface the facade needs (structural).

    The facade never constructs or configures the materializer — the
    composition root injects it (same pattern as ``on_episode_indexed``). The
    facade only calls ``materialize`` (ACTIVE write / distill / improve
    successor) and ``invalidate`` (forget / improve old episode) and treats
    both as best-effort side effects (M9: never fail the write path).
    """

    def materialize(self, ep: Episode) -> Any: ...

    def invalidate(self, ep: Episode) -> Any: ...


@runtime_checkable
class _EngineLike(Protocol):
    def get(self, ep_id: str) -> Episode | None: ...

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
    """Recall-policy extension point: produces ranked ``FusedCandidate`` for progressive disclosure.

    The current-release impl is ``VigenteListingRetriever`` (current-state
    listing, no ranking/PIT). A later release swaps in an adapter over
    ``seahorse.retrieval.recall`` (kNN+BM25+RRF) at the composition root — a
    single-point change. The facade owns boundary validation (empty query, PIT
    refusal) + the shaper call; the retriever owns listing/filter/truncate +
    ranking.

    PIT capability is declared here as a read-only member (``supports_pit``):
    a plain class/instance attribute satisfies it structurally. The facade
    still reads it via ``getattr(retriever, "supports_pit", False)`` — a
    defensive default, not a typing gap: ``@runtime_checkable`` ``isinstance``
    checks member PRESENCE (``getattr_static``-based on 3.12+, ``hasattr``
    before; property getters are not executed) while ``issubclass`` raises for
    non-method members, so ``getattr`` keeps "absent means incapable" true even
    for untyped doubles. NOTE: declaring the member HARDENS ``isinstance`` —
    a double that previously passed by omission now fails. A retriever that
    does not declare it (e.g. a deliberately PIT-less double) causes the facade
    to refuse a caller pit before any read; ``VigenteListingRetriever`` declares
    it dynamically
    (True iff a pit source is wired) and ``HybridRetriever`` declares True.
    """

    @property
    def supports_pit(self) -> bool: ...

    def recall(
        self,
        query: str,
        *,
        pit: PITPoint | None,
        k: int,
        cognitive_type: str | None,
        subject_filter: str | None,
        session_boost: bool = False,
        rrf_k: int | None = None,
        rerank_text: str = "summary",
        reranker: QueryReranker | None = None,
        k_rerank: int = RERANK_OVERFETCH_K,
    ) -> Sequence[FusedCandidate]: ...