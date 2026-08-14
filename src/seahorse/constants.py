"""Shared constants for the Seahorse wire projections (MCP server and CLI).

Single source of truth for the vocabularies and wire-level caps that BOTH
surfaces must expose identically. The two projections show the user the
SAME ``cognitive_type`` set and the SAME wire caps. If the MCP server changes
a cap, the CLI follows automatically via import.

What lives here:
- ``COGNITIVE_TYPES`` / ``SOURCE_TYPES`` — informative vocabularies lifted
  out of ``facade/types.py`` so both projections import one source.
  ``facade.types`` re-exports them to preserve the facade's public API.
- Wire-level caps owned by the MCP server and synced with the CLI: body /
  query / reason length caps and the tags-item cap. These are DoS + receiver
  token-budget guards, independent of any semantic limit the facade may impose.

What does NOT live here:
- ``TOP_K`` / ``MAX_TIMELINE_WINDOW`` / ``MAX_FULL_BATCH`` /
  ``SUMMARY_MAX_CHARS`` / ``SUBJECT_MAX_CHARS`` — owned by the
  progressive-disclosure layer, they stay in ``seahorse/disclosure/types.py``;
  the projections import them from there. Each cap lives where its owner lives.
- Schema versions — owned by the engine.

``COGNITIVE_TYPES`` is the canonical set: four active values (``episodic``,
``semantic``, ``social``, ``project_doc``) plus two reserved values
(``procedural``, ``working``) declared in the enum, accepted by the parser,
but not yet wired into decay/dual-mode routing. This resolves a CRITICAL
divergence in an earlier wire enum that omitted ``social``/``project_doc`` and
wrongly included only one reserved value.
"""

from __future__ import annotations

# Cognitive types — the canonical set: 4 active + 2 reserved.
# Not enforced by the facade at the boundary in the current release (the
# engine is authoritative); the MCP server enforces this enum at the
# wire-shape layer.
COGNITIVE_TYPES: frozenset[str] = frozenset(
    {
        "episodic",  # active
        "semantic",  # active
        "social",  # active
        "project_doc",  # active
        "procedural",  # reserved (medium-term goal)
        "working",  # reserved (short-term goal)
    }
)

# Caller authority values. The MCP server validates source_type against this.
SOURCE_TYPES: frozenset[str] = frozenset({"agent", "human", "importer", "system"})

# Wire-level caps owned by the MCP server, synced with the CLI via this import.
# These are wire-shape guards (DoS + receiver token budget), NOT semantic
# limits — the facade may impose additional constraints.
BODY_MAX_CHARS: int = 32_768  # remember body / improve new_body
QUERY_MAX_CHARS: int = 2_048  # recall query
REASON_MAX_CHARS: int = 512  # forget reason / improve reason
TAGS_MAX_ITEMS: int = 32  # remember tags
TAG_MAX_CHARS: int = 256  # remember tags per-item length

# Identifier / free-text caps. The wire is the DoS + token-budget guard per the
# module docstring, so every free-text field the engine persists verbatim needs
# a wire-level maxLength — otherwise a multi-megabyte agent_id passes wire-shape
# and is stored. These are sized for realistic identifiers, not tight semantic
# limits (the engine may impose additional constraints).
EP_ID_MAX_CHARS: int = 64  # ep_id / anchor_ep_id (UUID-sized)
PROVENANCE_ID_MAX_CHARS: int = 256  # agent_id / session_id
# model_used / prompt_hash / importer_vendor / source_record_id
PROVENANCE_SHORT_MAX_CHARS: int = 128
SUBJECT_FILTER_MAX_CHARS: int = QUERY_MAX_CHARS  # recall subject_filter (reuses query budget)


__all__ = [
    "COGNITIVE_TYPES",
    "SOURCE_TYPES",
    "BODY_MAX_CHARS",
    "QUERY_MAX_CHARS",
    "REASON_MAX_CHARS",
    "TAGS_MAX_ITEMS",
    "TAG_MAX_CHARS",
    "EP_ID_MAX_CHARS",
    "PROVENANCE_ID_MAX_CHARS",
    "PROVENANCE_SHORT_MAX_CHARS",
    "SUBJECT_FILTER_MAX_CHARS",
]