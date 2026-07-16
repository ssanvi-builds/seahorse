"""Shared constants for the Seahorse wire projections (#13 MCP, #14 CLI).

Single source of truth for the vocabularies and wire-level caps that BOTH
sister projections must expose identically. Per f5-14 §Pins and the SO-14-03
alignment (closed inline during F6): the two projections show the user the
SAME ``cognitive_type`` set and the SAME wire caps. If #13 changes a cap,
#14 follows automatically via import.

What lives here:
- ``COGNITIVE_TYPES`` / ``SOURCE_TYPES`` — informative vocabularies lifted
  out of ``facade/types.py`` so #13/#14 import one source. ``facade.types``
  re-exports them to preserve #12's public API.
- Wire-level caps owned by #13 (the MCP profile) and synced with #14: body /
  query / reason length caps and the tags-item cap. These are DoS + receiver
  token-budget guards, independent of any semantic limit #12 may impose.

What does NOT live here:
- ``TOP_K`` / ``MAX_TIMELINE_WINDOW`` / ``MAX_FULL_BATCH`` /
  ``SUMMARY_MAX_CHARS`` / ``SUBJECT_MAX_CHARS`` — owned by #8, they stay in
  ``seahorse/disclosure/types.py``; #13/#14 import them from there. Each cap
  lives where its owner lives.
- Schema versions — owned by #1.

``COGNITIVE_TYPES`` is the F3.1 canonical set (f5-01 §2.4): four active MVP-1
values (``episodic``, ``semantic``, ``social``, ``project_doc``) plus two
reserved values (``procedural``, ``working``) declared in the enum, accepted
by the parser, but not routing decay/dual-mode in MVP-1. This is the
resolution to the CRITICAL divergence flagged in f5-14 §8.2 (f5-13's stale
wire enum ``["semantic","episodic","procedural",null]`` omitted
``social``/``project_doc`` and wrongly included only one reserved value).

References:
- f5-01 §2.4 (cognitive_type vocabulary — the authority)
- f5-14 §Pins (shared caps module) + §8.2 (alignment action)
- SO-14-03 (cognitive_type enum alignment, closed inline during F6)
"""

from __future__ import annotations

# Cognitive types — F3.1 canonical (f5-01 §2.4). 4 active MVP-1 + 2 reserved.
# NOT enforced by #12 at the boundary in MVP-0 (engine/#1 authority); #13
# enforces this enum at the wire-shape layer.
COGNITIVE_TYPES: frozenset[str] = frozenset(
    {
        "episodic",  # active
        "semantic",  # active
        "social",  # active
        "project_doc",  # active
        "procedural",  # reserved (mediano, L2c)
        "working",  # reserved (mediano, short-term)
    }
)

# Caller authority values (f5-12 §3). #13 validates source_type against this.
SOURCE_TYPES: frozenset[str] = frozenset({"agent", "human", "importer", "system"})

# Wire-level caps owned by #13, synced with #14 via this import (f5-13 §7.5).
# These are wire-shape guards (DoS + receiver token budget), NOT semantic
# limits — #12 may impose additional constraints.
BODY_MAX_CHARS: int = 32_768  # remember body / improve new_body
QUERY_MAX_CHARS: int = 2_048  # recall query
REASON_MAX_CHARS: int = 512  # forget reason / improve reason
TAGS_MAX_ITEMS: int = 32  # remember tags


__all__ = [
    "COGNITIVE_TYPES",
    "SOURCE_TYPES",
    "BODY_MAX_CHARS",
    "QUERY_MAX_CHARS",
    "REASON_MAX_CHARS",
    "TAGS_MAX_ITEMS",
]