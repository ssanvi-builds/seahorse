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
- ``BfsKnownAtUnsupported``: raised by ``_bfs`` when ``pit=known_at`` is requested
  for the BFS axis but the ``pit_kind`` refinement is not yet supported. A
  declared limitation, NOT a silent fallback to ``state_at`` (a silent fallback
  would mix the two bi-temporal axes). The recall degrades to vector + bm25 +
  chain (the BFS axis is dropped), decided by the engine.
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


class BfsKnownAtUnsupported(Exception):
    """Raised when BFS ``known_at`` is requested but not yet supported.

    The ``neighbors_state_at`` refinement (``pit_kind``) is not yet supported.
    Without it, the BFS axis serves only the implicit ``state_at`` axis. A
    ``known_at`` BFS is therefore refused loudly, NOT silently routed to
    ``state_at`` (that would mix the two bi-temporal axes). The engine catches
    this and drops the BFS axis, degrading to vector + bm25 + chain.
    """

    def __init__(self) -> None:
        super().__init__(
            "BFS known_at not supported; use state_at or omit the BFS axis"
        )


__all__ = ["BfsKnownAtUnsupported", "RetrievalInvalidPITKind"]
