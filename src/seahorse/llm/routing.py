"""Role routing — the primary→secondary→tertiary fallback shape.

A ``RoleRoute`` is the ordered chain of model ids for one role (extraction).
The fallback chain (``fallback.py``) walks it: primary first, then secondary,
then tertiary (typically local Ollama as the last-resort without network).
The onboarding wizard builds an ``extraction`` route from the provider the
user picks + a fallback; the factory default for a user with nothing is a
single local Ollama model (2026-08-04 decision).

The per-role ``RoutingConfig`` / ``route_for`` seam was never adopted by the
backend (the ``role`` argument is not read) and was removed — the fallback
chain is the single ``RoleRoute`` the backend walks.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleRoute:
    """Ordered fallback chain for one role.

    ``primary`` is required; ``secondary`` / ``tertiary`` are optional. The
    chain is never empty — ``primary`` always runs first. A ``None`` tail is
    dropped by ``chain()`` so the fallback loop only walks configured models.
    """

    primary: str
    secondary: str | None = None
    tertiary: str | None = None

    def chain(self) -> tuple[str, ...]:
        """The configured model ids in fallback order (non-None tail)."""
        return tuple(m for m in (self.primary, self.secondary, self.tertiary) if m)


__all__ = ["RoleRoute"]
