"""Conflict resolution policy (owned by the engine).

The current release does not ship a conflict-resolution policy: resolving a
concurrent collision requires a deeper, signed policy (precedence by
source_type, cognitive_type, recency, human override) that is a medium-term
goal. The ``ConflictPolicy`` Protocol and ``DefaultConflictPolicyMVP1`` are
declared on the engine surface so callers know the extension point exists; both
fail loud in the current release.
"""

from __future__ import annotations

from typing import Any, Protocol

from seahorse.engine.errors import E_NOT_IN_MVP_0, EngineError


class ConflictPolicy(Protocol):
    """Extension point for resolving a detected collision into a write decision."""

    def resolve(self, collision: Any) -> Any:  # pragma: no cover - Protocol
        """Return the resolution for ``collision`` (decision + metadata)."""
        ...


class DefaultConflictPolicyMVP1:
    """Placeholder policy: declared extension point, fail-loud until a later release.

    A real policy encodes precedence rules (human > importer > agent > system
    by default, overridable per cognitive_type). The current release refuses to
    guess.
    """

    def resolve(self, collision: Any) -> Any:
        raise EngineError(E_NOT_IN_MVP_0, primitive="DefaultConflictPolicyMVP1.resolve")