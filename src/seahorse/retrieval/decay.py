"""Decay as a ranking signal (Sprint D — an opt-in extension, default-OFF).

A small, localized post-RRF step in the retrieval engine's ``recall``, mirroring
the F1 recency boost. The bias is folded INTO ``FusedCandidate.score`` — never an
external reorder — so the disclosure layer's ``IndexRow.score`` passthrough stays
truthful (the retrieval engine owns the ranking; the disclosure layer projects
without re-ranking).

Formula (Ebbinghaus forgetting curve, multiplicative):

    score' = score · 2^(-age_days / half_life[type])

- ``age_days`` is the episode's age since ``created_at`` (clamped to ≥ 0).
- ``half_life[type]`` is the S₀ prior by ``cognitive_type`` (R3, memlocal
  priors: episodic 139d, semantic 347d, social 231d, procedural 347d). Unknown
  or missing types fall back to ``DecayConfig.default_half_life_days``.
- The factor lives in ``(0, 1]`` — decay downweights stale knowledge, never
  boosts. Unlike the bounded recency boost (factor in ``[1, 1+γ]``), decay is an
  unbounded forgetting curve: an obsolete old version can be pushed out of the
  top-k (the FAMA fix).
- Deterministic given ``now`` — the engine's injectable clock.

Gate: the bias applies ONLY when ``pit is None``. Decay is a signal of the "now"
regime; PIT queries reproduce state as-of-``t`` with pure RRF (never
``state_at(t≈now)``). Default-OFF: ``recall`` applies it only when a
``DecayConfig`` is explicitly passed.

No writes (R2): the read path never writes; ``expired_at`` stays NULL. The
materialization layer (``decay_sweep`` writing ``expired_at``) is a MAJOR 2.0
change, sequenced separately. No ``x-*`` provenance reads in the core (R1): the
bias reads only ``created_at`` + ``cognitive_type`` from ``IndexRowData``.

``created_at`` + ``cognitive_type`` are read in batch via ``index_repo.get_rows``
(one ``IN`` query for ≤k candidates, no N+1) by the caller; this module is a
pure function over the already-fetched maps. A candidate missing from the map is
left undecayed (honest — never invent a decay for a row the index does not
expose).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from seahorse.contracts.retrieval import FusedCandidate
from seahorse.retrieval.constants import (
    DECAY_DEFAULT_HALF_LIFE_DAYS,
    DECAY_HALF_LIVES_BY_TYPE,
)

_SECONDS_PER_DAY = 86_400.0


@dataclass(frozen=True)
class DecayConfig:
    """Decay bias parameters.

    ``half_lives`` maps ``cognitive_type`` to its S₀ half-life prior in days
    (R3, memlocal priors — experiment priors, NOT grounding); ``default_half_life_days``
    covers unknown/missing types. Both are pinned in ``retrieval/constants.py``
    (fixed up front, not tuned on a batch); a future calibration pass decides the
    real values.
    """

    half_lives: Mapping[str, float] = field(
        default_factory=lambda: DECAY_HALF_LIVES_BY_TYPE
    )
    default_half_life_days: float = DECAY_DEFAULT_HALF_LIFE_DAYS

    def __post_init__(self) -> None:
        if not math.isfinite(self.default_half_life_days) or self.default_half_life_days <= 0:
            raise ValueError(
                "default_half_life_days must be a finite value > 0, "
                f"got {self.default_half_life_days!r}"
            )
        for ct, hl in self.half_lives.items():
            if not math.isfinite(hl) or hl <= 0:
                raise ValueError(
                    f"half_lives[{ct!r}] must be a finite value > 0, got {hl!r}"
                )


def apply_decay_bias(
    candidates: Sequence[FusedCandidate],
    created_at_by_ep_id: Mapping[str, datetime],
    cognitive_type_by_ep_id: Mapping[str, str],
    now: datetime,
    config: DecayConfig,
    *,
    k: int,
) -> list[FusedCandidate]:
    """Fold the decay bias into ``FusedCandidate.score`` (O(k)).

    Rebuilds each candidate with ``score·factor`` (factor in ``(0, 1]``), then
    re-sorts by the decayed score (desc, ``ep_id`` asc tie-break) and re-truncates
    to ``k``. The bias is folded INTO the score — never an external reorder — so
    the disclosure layer's ``IndexRow.score`` passthrough stays truthful.
    Deterministic given ``now`` (the engine's injectable clock).

    A candidate with no ``created_at`` in the map (or a naive datetime) is left
    undecayed (honest — the index row is missing; never invent a decay). A
    ``cognitive_type`` missing from the map falls back to
    ``config.default_half_life_days``.
    """
    decayed: list[FusedCandidate] = []
    for c in candidates:
        created = created_at_by_ep_id.get(c.ep_id)
        if created is None or created.tzinfo is None:
            # No decay (honest): missing created_at, or a naive datetime the
            # index should never hold (the write path rejects naive) — never
            # invent a decay, and never crash the optional signal on it.
            decayed.append(c)
            continue
        age_days = max(0.0, (now - created).total_seconds() / _SECONDS_PER_DAY)
        half_life = config.half_lives.get(
            cognitive_type_by_ep_id.get(c.ep_id, ""), config.default_half_life_days
        )
        factor = 2.0 ** (-age_days / half_life)
        decayed.append(
            FusedCandidate(ep_id=c.ep_id, score=c.score * factor, sources=c.sources)
        )
    decayed.sort(key=lambda c: (-c.score, c.ep_id))
    return decayed[:k]


__all__ = ["DecayConfig", "apply_decay_bias"]
