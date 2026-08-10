"""Skip/drop tool policy for the observer (obsiforge §4.3).

Two distinct policies (obsiforge §4.3):
- ``skip_tools`` — DISCARD the event (it never reaches the turn body). Default:
  WebSearch/WebFetch (network results are noise, not memory).
- ``drop_tools`` — DISCARD the event entirely, not just redact it. Default:
  Read/Bash — their content is entirely secret (obsiforge §15.2 redesign 3: the
  "stronger than claude-mem" claim only holds if Read/Bash content is covered;
  redaction alone cannot guarantee it).

Pure functions — the policy is a deterministic allowlist, configurable per
vault via the ``[observe]`` section.

References:
- obsiforge-evolution-architecture.md §4.3 (thresholding)
- obsiforge-evolution-architecture.md §15.2 redesign 3 (drop_tools Read/Bash)
"""

from __future__ import annotations

from collections.abc import Collection

DEFAULT_SKIP_TOOLS: frozenset[str] = frozenset({"WebSearch", "WebFetch"})
DEFAULT_DROP_TOOLS: frozenset[str] = frozenset({"Read", "Bash"})


def should_skip_event(
    tool_name: str, *, skip_tools: Collection[str] = DEFAULT_SKIP_TOOLS
) -> bool:
    """True iff the event for ``tool_name`` should be discarded (not rendered)."""
    return tool_name in skip_tools


def should_drop_event(
    tool_name: str, *, drop_tools: Collection[str] = DEFAULT_DROP_TOOLS
) -> bool:
    """True iff the event for ``tool_name`` should drop the whole turn."""
    return tool_name in drop_tools


__all__ = ["DEFAULT_SKIP_TOOLS", "DEFAULT_DROP_TOOLS", "should_skip_event", "should_drop_event"]
