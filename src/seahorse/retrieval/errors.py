"""Hybrid Retrieval — typed errors.

- ``RetrievalInvalidPITKind``: raised ONCE at the recall entrypoint when
  ``pit.kind`` is not a signed PIT axis (``"state_at"``/``"known_at"``). Enforces
  the invariant that the two bi-temporal axes never mix within a single recall
  at the single validation point — the same ``kind`` then fans out to ALL
  sources.

  Named ``RetrievalInvalidPITKind`` (not ``InvalidPITKind``) to avoid a
  ``__name__`` collision with ``seahorse.facade.errors.InvalidPITKind`` — both
  were plain ``InvalidPITKind`` and the ``mcp``/``cli`` origin-by-class tables
  (which match on ``type(exc).__name__``) attributed BOTH to the facade. The
  facade class is a ``SeahorseError`` carrying ``.code = E_INVALID_PIT_KIND``
  (owned by the facade); this one is a plain ``Exception`` raised by the
  retrieval engine's recall entrypoint. The distinct ``__name__`` lets the
  tables attribute each to its real owner (the engine here, the facade's).
"""

from __future__ import annotations


class RetrievalInvalidPITKind(Exception):
    """Raised when ``pit.kind`` is not a signed PIT axis.

    A recall carries ONE ``pit.kind`` that fans to ALL sources. An unknown kind
    is rejected once, before any source is read — the engine never silently
    coerces an unknown kind to a default axis.
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(f"pit.kind must be 'state_at' | 'known_at'; got {kind!r}")


__all__ = ["RetrievalInvalidPITKind"]
