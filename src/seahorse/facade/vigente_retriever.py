"""The current-release ``Retriever`` — the current-state listing recall policy.

The facade's ``recall`` used to host its ranking policy inline. Extracting it
behind a ``Retriever`` extension point makes the listing → hybrid recall-regime
swap a single-point change at the composition root (``build_facade`` passes a
different ``Retriever``), not a multi-touch-point edit across facade/MCP/CLI.

This is the current-release impl: the canonical recall is the **current-state
listing** ordered by ``created_at`` desc (``ep_id`` asc tie-break, deterministic),
with no ranking by ``query``. It produces synthetic
``FusedCandidate(score=0.0, sources=())`` — the body-less shape
``materialize_index`` projects into the INDEX level. A later release will swap
in an adapter over ``seahorse.retrieval.recall`` (kNN + BM25 + RRF); that
adapter implements the same ``Retriever`` surface and is wired at
``build_facade``.

PIT (v1.3.0): an optional ``pit_source`` (the repository slice
``query_state_at`` / ``query_known_at``) turns the retriever into a PIT-capable
listing. With one injected, ``supports_pit`` is True (instance attribute) and a
caller pit routes by axis — ``state_at`` -> ``query_state_at``, ``known_at`` ->
``query_known_at`` (the repo owns the bi-temporal predicate; the ``t`` is
forwarded verbatim, never synthesized) — flowing through the SAME listing
contract downstream (filter, deterministic sort, truncate, synthetic score).
The PIT-resolved set is a set of currently-valid-AT-t episodes, not a ranking:
no ordering is inherited from the axis query. Without a ``pit_source`` nothing
changes and the facade still refuses a caller pit before delegating.

The retriever owns listing/filter/truncate; it does NOT call the shaper (the
facade owns the shaper call — separation of ranking from projection). It owns
its own clock (reproducibility — the same clock instance drives the engine and
facade, wired at the composition root).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol, runtime_checkable

from seahorse.contracts.episode import Episode
from seahorse.contracts.retrieval import FusedCandidate
from seahorse.disclosure.types import TOP_K, PITPoint
from seahorse.facade.errors import InvalidPITKind
from seahorse.facade.types import FacadeConfig


@runtime_checkable
class _VigenteSource(Protocol):
    """Engine surface the current-release retriever needs (a subset of the engine)."""

    def get_vigente(
        self, subject: str | None = ..., *, now: datetime | None = ...
    ) -> list[Episode]: ...


@runtime_checkable
class _PitSource(Protocol):
    """Repository PIT slice the retriever needs when serving a caller pit.

    A subset of ``EpisodeRepository`` (the canonical PIT predicates live there —
    ``query_state_at`` / ``query_known_at``). The retriever never synthesizes a
    bi-temporal predicate itself.
    """

    def query_state_at(
        self, t: datetime, subject: str | None = ...
    ) -> list[Episode]: ...

    def query_known_at(
        self, t: datetime, subject: str | None = ...
    ) -> list[Episode]: ...


class VigenteListingRetriever:
    """Current-release ``Retriever``: current-state listing, deterministic order, no ranking.

    Construct with the engine (for ``get_vigente``), the clock, the facade
    config (for the ``top_k`` clamp) and — to serve PIT recall — the optional
    ``pit_source`` (the repository ``query_state_at`` / ``query_known_at``
    slice). ``recall`` ignores ``query`` for ranking (the canonical recall is
    the full listing, PIT-resolved or current-state).

    ``supports_pit`` is an instance attribute: True iff a ``pit_source`` was
    injected. Without one the facade refuses a caller pit before consulting
    this retriever (the fail-loud ``E_PIT_RECALL_MVP_0`` contract for
    genuinely PIT-less retrievers stays).
    """

    def __init__(
        self,
        *,
        engine: _VigenteSource,
        clock: Callable[[], datetime],
        config: FacadeConfig,
        pit_source: _PitSource | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._config = config
        self._pit_source = pit_source
        self.supports_pit = pit_source is not None

    def recall(
        self,
        query: str,
        *,
        pit: PITPoint | None,
        k: int = TOP_K,
        cognitive_type: str | None = None,
        subject_filter: str | None = None,
        session_boost: bool = False,
    ) -> tuple[FusedCandidate, ...]:
        """Listing -> synthetic FusedCandidates (score=0.0, no sources), PIT-aware.

        Without a pit: the current-state listing (``get_vigente``). With one:
        the ``pit_source`` slice resolved at ``pit.t`` (routing by axis, the
        ``t`` forwarded verbatim). ``query`` and ``session_boost`` are accepted
        for Retriever-surface parity but never rank: the listing regime has no
        re-rank, PIT or not.
        """
        del query  # not used for ranking (the later adapter uses it).
        if pit is None:
            eps = self._engine.get_vigente(subject=subject_filter, now=self._clock())
        else:
            eps = self._query_at(pit, subject_filter)
        if cognitive_type is not None:
            eps = [e for e in eps if e.cognitive_type == cognitive_type]
        # Deterministic order: created_at desc, ep_id asc tie-break.
        # Two stable sorts: first ep_id asc, then created_at desc (stable keeps
        # ep_id asc for ties).
        eps = sorted(eps, key=lambda e: e.id)
        eps = sorted(eps, key=lambda e: e.created_at, reverse=True)
        k_eff = min(k, self._config.top_k)
        truncated = eps[:k_eff]
        return tuple(
            FusedCandidate(ep_id=e.id, score=0.0, sources=()) for e in truncated
        )

    def _query_at(self, pit: PITPoint, subject_filter: str | None) -> list[Episode]:
        """Route the caller pit by axis to the repository slice (verbatim ``t``)."""
        if self._pit_source is None:
            # Unreachable via the facade (it refuses PIT when supports_pit is
            # False); direct callers get the fail-loud contract, not silence.
            raise InvalidPITKind(pit.kind)
        if pit.kind == "state_at":
            return self._pit_source.query_state_at(pit.t, subject=subject_filter)
        if pit.kind == "known_at":
            return self._pit_source.query_known_at(pit.t, subject=subject_filter)
        # Only two PIT kinds exist; fail loud before any read (the shaper and
        # facade mirror this raise for the same reason).
        raise InvalidPITKind(pit.kind)


__all__ = ["VigenteListingRetriever"]