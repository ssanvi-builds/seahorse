"""MVP-0 ``Retriever`` — the vigente-listing recall policy extracted from #12.

C8.1 (seam hardening, audit #3/#4): the facade's MVP-0 ``recall`` used to host
its ranking policy inline (``_recall_mvp0``). Extracting it behind a ``Retriever``
seam makes the MVP-0 → MVP-1 recall-regime swap a single-point change at the
composition root (``build_facade`` passes a different ``Retriever``), not a 6+
touch-point edit across facade/MCP/CLI.

This is the MVP-0 impl: the canonical recall is the **vigente listing** ordered
by ``created_at`` desc (``ep_id`` asc tie-break, ADR-10 determinism), with no
ranking by ``query`` and no PIT axis (the facade refuses PIT before delegating).
It produces synthetic ``FusedCandidate(score=0.0, sources=())`` — the body-less
shape #8 ``materialize_index`` projects into the INDEX level. MVP-1 will swap in
an adapter over ``seahorse.retrieval.recall`` (kNN + BM25 + RRF); that adapter
implements the same ``Retriever`` surface and is wired at ``build_facade``.

The retriever owns listing/filter/truncate; it does NOT call #8 (the facade owns
the shaper call — separation of ranking from projection). It owns its own clock
(ADR-10 reproducibility — the same clock instance drives the engine and facade,
wired at the composition root).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol, runtime_checkable

from seahorse.contracts.episode import Episode
from seahorse.contracts.retrieval import FusedCandidate
from seahorse.disclosure.types import TOP_K, PITPoint
from seahorse.facade.types import FacadeConfig


@runtime_checkable
class _VigenteSource(Protocol):
    """Engine surface the MVP-0 retriever needs (a subset of #2)."""

    def get_vigente(
        self, subject: str | None = ..., *, now: datetime | None = ...
    ) -> list[Episode]: ...


class VigenteListingRetriever:
    """MVP-0 ``Retriever``: vigente listing, deterministic order, no ranking/PIT.

    Construct with the engine (#2, for ``get_vigente``), the clock (ADR-10), and
    the facade config (for the ``top_k`` clamp). ``recall`` ignores ``query`` for
    ranking (MVP-0 canonical recall is the full vigente listing) and ignores
    ``pit`` (the facade refuses PIT before delegating; forwarded for
    forward-compat with the MVP-1 adapter, which DOES use it).

    M1-C.2: ``supports_pit = False`` declares the G2 regime explicitly — the
    facade refuses a caller pit before consulting this retriever (ADR-03).
    """

    supports_pit = False

    def __init__(
        self,
        *,
        engine: _VigenteSource,
        clock: Callable[[], datetime],
        config: FacadeConfig,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._config = config

    def recall(
        self,
        query: str,
        *,
        pit: PITPoint | None,
        k: int = TOP_K,
        cognitive_type: str | None = None,
        subject_filter: str | None = None,
    ) -> tuple[FusedCandidate, ...]:
        """Vigente listing -> synthetic FusedCandidates (score=0.0, no sources).

        The ``query`` and ``pit`` args are accepted for Retriever-surface parity
        with the MVP-1 hybrid adapter but are not used here: MVP-0 recall does
        not rank by query and has no PIT axis.
        """
        del query, pit  # MVP-0: not used for ranking (MVP-1 adapter uses both).
        eps = self._engine.get_vigente(subject=subject_filter, now=self._clock())
        if cognitive_type is not None:
            eps = [e for e in eps if e.cognitive_type == cognitive_type]
        # Deterministic order (ADR-10): created_at desc, ep_id asc tie-break.
        # Two stable sorts: first ep_id asc, then created_at desc (stable keeps
        # ep_id asc for ties).
        eps = sorted(eps, key=lambda e: e.id)
        eps = sorted(eps, key=lambda e: e.created_at, reverse=True)
        k_eff = min(k, self._config.top_k)
        truncated = eps[:k_eff]
        return tuple(
            FusedCandidate(ep_id=e.id, score=0.0, sources=()) for e in truncated
        )


__all__ = ["VigenteListingRetriever"]