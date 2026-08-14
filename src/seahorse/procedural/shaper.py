"""``ProceduralShaper`` — skill progressive disclosure.

A decorator/wrapper over a ``DisclosureShaper`` that adds the skill-specific
three levels:
- **Discovery** (INDEX): the skill's summary, capped at 280 chars — what the
  agent sees first to decide relevance, without the body.
- **Activation** (TIMELINE): passthrough to the inner shaper (the skill's
  version history via ``supersedes_chain``, or the BFS ``graph_bfs`` axis).
- **Execution** (FULL): the gated body — the ONLY level that hydrates the body,
  and the point where the trust gate decides whether the body is safe to treat
  as an instruction (``as_instruction=True``) or must be delivered as
  citation/context (``as_instruction=False``).

The wrapper is a client of the inner ``DisclosureShaper`` — it never reaches the
engine or the repos directly (delegation purity).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from seahorse.contracts.retrieval import FusedCandidate
from seahorse.disclosure.types import (
    FullDetail,
    IndexRow,
    PITPoint,
    TimelineWindow,
)
from seahorse.procedural.config import SKILL_SUMMARY_MAX_CHARS
from seahorse.procedural.trust import TrustLevel, gate_skill


@dataclass(frozen=True)
class SkillDetail:
    """Execution level for a skill: the gated body.

    ``detail`` is the inner ``FullDetail`` (episode + provenance + freshness).
    ``trust`` is the derived trust level; ``as_instruction`` is True only when
    the skill's trust meets the gate's ``min_trust``. A low-trust skill is
    delivered with ``as_instruction=False`` — the caller treats the body as
    citation/context (data to verify), never as instructions to follow.
    """

    detail: FullDetail
    trust: TrustLevel
    as_instruction: bool
    body: str


def _discovery_row(row: IndexRow) -> IndexRow:
    """Cap the Discovery-level summary at 280 chars."""
    if row.summary is not None and len(row.summary) > SKILL_SUMMARY_MAX_CHARS:
        return replace(row, summary=row.summary[:SKILL_SUMMARY_MAX_CHARS])
    return row


def _gated(detail: FullDetail, min_trust: TrustLevel) -> SkillDetail:
    delivery = gate_skill(detail.episode, min_trust=min_trust)
    return SkillDetail(
        detail=detail,
        trust=delivery.trust,
        as_instruction=delivery.as_instruction,
        body=delivery.body,
    )


class ProceduralShaper:
    """Skill-specific progressive disclosure, wrapping a ``DisclosureShaper``.

    ``min_trust`` is the gate threshold: skills at or above it are delivered as
    instructions; below it, as citation/context.
    """

    def __init__(
        self,
        inner: Any,
        *,
        min_trust: TrustLevel = TrustLevel.MEDIUM,
    ) -> None:
        self._inner = inner
        self._min_trust = min_trust

    # Discovery — INDEX (summary ≤ 280 chars, no body).
    def materialize_index(
        self,
        candidates: list[FusedCandidate],
        *,
        pit: PITPoint | None = None,
        now: datetime | None = None,
    ) -> list[IndexRow]:
        rows = self._inner.materialize_index(candidates, pit=pit, now=now)
        return [_discovery_row(r) for r in rows]

    # Activation — TIMELINE (passthrough; the skill's version history / BFS).
    def materialize_timeline(
        self,
        anchor_ep_id: str,
        *,
        axis: str,
        pit: PITPoint | None = None,
        hops: int = 1,
        now: datetime | None = None,
    ) -> TimelineWindow:
        return self._inner.materialize_timeline(
            anchor_ep_id, axis=axis, pit=pit, hops=hops, now=now
        )

    # Execution — FULL (the ONLY level that hydrates body; gated by trust).
    def materialize_full(
        self,
        ep_ids: list[str],
        *,
        pit: PITPoint | None = None,
        now: datetime | None = None,
    ) -> list[SkillDetail]:
        details = self._inner.materialize_full(ep_ids, pit=pit, now=now)
        return [_gated(d, self._min_trust) for d in details]


__all__ = ["ProceduralShaper", "SkillDetail"]
