"""F1 — Recency as a ranking signal (seam default-OFF, ADR-10).

A small, localized post-RRF step in #11 ``recall`` (cerebras-f-feasibility §3).
The boost is folded INTO ``FusedCandidate.score`` — never an external reorder —
so #8's ``IndexRow.score`` passthrough stays truthful (f5-11 §2.1: #11 owns the
ranking; #8 projects without re-ranking).

Formula (bounded exponential decay, multiplicative):

    score' = score · (1 + γ·exp(-ln2·age_days/half_life))

- ``γ`` (``RecencyConfig.gamma``) is the max boost at age 0; the factor lives in
  ``[1, 1+γ]``, so a fresh-but-irrelevant candidate cannot outrank a relevant
  one by more than ``1+γ``.
- ``half_life_days`` is the exponential half-life of the signal.
- Deterministic given ``now`` — #11's injectable clock (ADR-10 reproducibility).

Gate: the boost applies ONLY when ``pit is None``. Recency is a signal of the
"now" regime; PIT queries reproduce state as-of-``t`` with pure RRF (never
``state_at(t≈now)``). Default-OFF: ``recall`` applies it only when a
``RecencyConfig`` is explicitly passed.

``created_at`` is read in batch via ``index_repo.get_rows([ep_ids])`` (one ``IN``
query for ≤k candidates, no N+1) by the caller; this module is a pure function
over the already-fetched map. A candidate missing from the map is left unboosted
(honest — never invent a boost for a row the index does not expose).

References:
- cerebras-f-feasibility.md §3 (F1 — veredicto ALTO, decay acotado, gate pit is None)
- f5-11 §2.1 (delegation purity: #11 owns ranking, #8 projects)
- ADR-10 (reproducibility: default-off preserves the bit-comparable fingerprint)
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from seahorse.contracts.retrieval import FusedCandidate
from seahorse.retrieval.constants import RECENCY_GAMMA, RECENCY_HALF_LIFE_DAYS

_LN2 = math.log(2)
_SECONDS_PER_DAY = 86_400.0


@dataclass(frozen=True)
class RecencyConfig:
    """Recency boost parameters (F1, cerebras-f-feasibility §3).

    ``gamma`` is the max multiplicative boost at age 0 (factor in ``[1, 1+γ]``);
    ``half_life_days`` is the exponential half-life of the signal. Both are
    pinned in ``retrieval/constants.py`` (ADR-10: fixed up front, not tuned on a
    batch); the F7 recency experiment (LMEB) decides the real calibration.
    """

    gamma: float = RECENCY_GAMMA
    half_life_days: float = RECENCY_HALF_LIFE_DAYS

    def __post_init__(self) -> None:
        if not math.isfinite(self.gamma) or self.gamma < 0:
            raise ValueError(f"gamma must be a finite value >= 0, got {self.gamma!r}")
        if not math.isfinite(self.half_life_days) or self.half_life_days <= 0:
            raise ValueError(
                f"half_life_days must be a finite value > 0, got {self.half_life_days!r}"
            )


def apply_recency_boost(
    candidates: Sequence[FusedCandidate],
    created_at_by_ep_id: Mapping[str, datetime],
    now: datetime,
    config: RecencyConfig,
    *,
    k: int,
) -> list[FusedCandidate]:
    """Fold the recency boost into ``FusedCandidate.score`` (O(k), ADR-10).

    Rebuilds each candidate with ``score·factor`` (factor in ``[1, 1+γ]``), then
    re-sorts by the boosted score (desc, ``ep_id`` asc tie-break) and re-truncates
    to ``k``. The boost is folded INTO the score — never an external reorder — so
    #8's ``IndexRow.score`` passthrough stays truthful. Deterministic given
    ``now`` (the injectable clock of #11).

    A candidate with no ``created_at`` in the map is left unboosted (honest — the
    index row is missing; never invent a boost).
    """
    boosted: list[FusedCandidate] = []
    for c in candidates:
        created = created_at_by_ep_id.get(c.ep_id)
        if created is None or created.tzinfo is None:
            # No boost (honest): missing created_at, or a naive datetime the
            # index should never hold (the write path rejects naive) — never
            # invent a boost, and never crash the optional signal on it.
            boosted.append(c)
            continue
        age_days = max(0.0, (now - created).total_seconds() / _SECONDS_PER_DAY)
        factor = 1.0 + config.gamma * math.exp(
            -_LN2 * age_days / config.half_life_days
        )
        boosted.append(
            FusedCandidate(ep_id=c.ep_id, score=c.score * factor, sources=c.sources)
        )
    boosted.sort(key=lambda c: (-c.score, c.ep_id))
    return boosted[:k]


__all__ = ["RecencyConfig", "apply_recency_boost"]
