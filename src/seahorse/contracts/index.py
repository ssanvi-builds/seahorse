"""Index row contract and PIT graph primitives.

Owned by #8 (IndexRowData) and #10 (PITKind, HopsCapExceeded, MAX_HOPS_MVP1).
Materialized here by #6 as the stable frontier per SO-2 safeguard 1 and SO-8a.
#8 and #10 IMPORT these symbols; they do not relocate them. Any move requires
a new sign-off.

References:
- f5-08 §3.1 (IndexRowData, 14 fields, no body)
- f6-signoffs.md SO-2 2e (freeze), SO-8a (PITKind, HopsCapExceeded, MAX_HOPS_MVP1)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, get_args

PITKind = Literal["state_at", "known_at"]
"""ADR-03: two PIT axes, never mixed. ``state_at`` filters the valid_time axis
(valid_at/invalid_at); ``known_at`` filters the transaction_time axis
(created_at/expired_at)."""

PIT_KIND_VALUES: frozenset[str] = frozenset(get_args(PITKind))
"""Single-source set of PIT kind strings, derived from ``PITKind``.

Consumers that need the kind set (#12 facade validation, #13 wire enum + the
deserialize codec) import this instead of hardcoding ``{"state_at","known_at"}``
again — so a future ADR-03 change lives in one place (the ``PITKind`` Literal).
``frozenset`` order is undefined; wire enums that need a stable order sort it."""

MAX_HOPS_MVP1: int = 2
"""BFS hop cap for MVP-1 (SO-8a). ``hops > MAX_HOPS_MVP1`` raises HopsCapExceeded."""


@dataclass(frozen=True)
class IndexRowData:
    """Lightweight row over ``episode_index`` — NO body. Backs INDEX and TIMELINE.

    Frozen (SO-2 2e): this shape is a stable contract. It is NOT moved without a
    new sign-off. Defined by #8, consumed by #6/#11/#16.
    """

    ep_id: str
    fact_id: str
    subject: str
    title: str | None
    summary: str | None
    cognitive_type: str
    source_type: str | None
    schema_version: str
    skip_extraction: bool
    valid_at: datetime | None
    invalid_at: datetime | None
    created_at: datetime
    expired_at: datetime | None
    supersedes: str | None


class HopsCapExceeded(Exception):
    """Raised by ``bfs_neighbors_state_at`` when ``hops > MAX_HOPS_MVP1``.

    Deep traversal is a mediano concern; MVP-1 caps BFS at 1-2 hops (SO-8a).
    """

    def __init__(self, hops: int, cap: int) -> None:
        self.hops = hops
        self.cap = cap
        super().__init__(f"hops={hops} exceeds MVP-1 cap={cap}; deep traversal is mediano")
