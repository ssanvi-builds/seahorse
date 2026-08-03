"""#11 Hybrid Retrieval — typed errors (f5-11 §7.6/§7.7).

- ``RetrievalInvalidPITKind``: raised ONCE at the recall entrypoint when
  ``pit.kind`` is not a signed PIT axis (``"state_at"``/``"known_at"``). Enforces
  ADR-03 (the two bi-temporal axes never mix within a single recall) at the
  single validation point — the same ``kind`` then fans out to ALL sources.

  Named ``RetrievalInvalidPITKind`` (not ``InvalidPITKind``) to avoid a
  ``__name__`` collision with ``seahorse.facade.errors.InvalidPITKind`` — both
  were plain ``InvalidPITKind`` and the ``mcp``/``cli`` origin-by-class tables
  (which match on ``type(exc).__name__``) attributed BOTH to ``#12``. The
  facade class is a ``SeahorseError`` carrying ``.code = E_INVALID_PIT_KIND``
  (owned by #12); this one is a plain ``Exception`` raised by #11's recall
  entrypoint. The distinct ``__name__`` lets the tables attribute each to its
  real owner (#11 here, #12 for the facade's).
- ``BfsKnownAtUnsupported``: raised by ``_bfs`` when ``pit=known_at`` is requested
  for the BFS axis but #10 has not signed off the ``pit_kind`` refinement
  (f5-10 TD-2). A declared limitation, NOT a silent fallback to ``state_at`` (a
  silent fallback would mix axes, violating ADR-03). The recall degrades to
  vector + bm25 + chain (the BFS axis is dropped), decided by the engine.

References:
- f5-11 §7.3 (ADR-03 axis invariant), §7.6 (known_at BFS blocked by TD-2)
"""

from __future__ import annotations


class RetrievalInvalidPITKind(Exception):
    """Raised when ``pit.kind`` is not a signed PIT axis.

    ADR-03: a recall carries ONE ``pit.kind`` that fans to ALL sources. An
    unknown kind is rejected once, before any source is read — #11 never
    silently coerces an unknown kind to a default axis.
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(f"pit.kind must be 'state_at' | 'known_at'; got {kind!r}")


class BfsKnownAtUnsupported(Exception):
    """Raised when BFS ``known_at`` is requested before #10/#8 sign-off (TD-2).

    f5-10's ``neighbors_state_at`` refinement (``pit_kind``) is pending #8
    sign-off. Without it, the BFS axis serves only the implicit ``state_at``
    axis. A ``known_at`` BFS is therefore refused loud, NOT silently routed to
    ``state_at`` (that would mix the two bi-temporal axes — ADR-03). The engine
    catches this and drops the BFS axis, degrading to vector + bm25 + chain.
    """

    def __init__(self) -> None:
        super().__init__(
            "BFS known_at pending #10/#8 sign-off (TD-2); use state_at or omit the BFS axis"
        )


__all__ = ["BfsKnownAtUnsupported", "RetrievalInvalidPITKind"]
