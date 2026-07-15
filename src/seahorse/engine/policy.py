"""Conflict resolution policy (owned by #2).

MVP-0 does not ship a conflict-resolution policy: resolving a concurrent
collision requires a deeper, signed policy (precedence by source_type,
cognitive_type, recency, human override) that is mediano work (ADR-10). The
``ConflictPolicy`` Protocol and ``DefaultConflictPolicyMVP1`` are SIGNED on
the engine surface so callers know the seam exists; both fail loud in MVP-0.

References:
- f5-02 §6.3 (ConflictPolicy seam)
- f6-signoffs.md SO-1 safeguard 2 (MVP-1 axis, revisable until MVP-1)
- f6-signoffs.md ADR-10 (no over-claiming reproducibility)
"""

from __future__ import annotations

from typing import Any, Protocol

from seahorse.engine.errors import E_NOT_IN_MVP_0, EngineError


class ConflictPolicy(Protocol):
    """Seam for resolving a detected collision into a write decision."""

    def resolve(self, collision: Any) -> Any:  # pragma: no cover - Protocol
        """Return the resolution for ``collision`` (decision + metadata)."""
        ...


class DefaultConflictPolicyMVP1:
    """Placeholder policy: signed seam, fail-loud until MVP-1.

    A real policy encodes precedence rules (human > importer > agent > system
    by default, overridable per cognitive_type). MVP-0 refuses to guess.
    """

    def resolve(self, collision: Any) -> Any:
        raise EngineError(E_NOT_IN_MVP_0, primitive="DefaultConflictPolicyMVP1.resolve")