"""Role routing — the primary→secondary→tertiary fallback shape.

A ``RoleRoute`` is the ordered chain of model ids for one role (extraction).
The fallback chain (``fallback.py``) walks it: primary first, then secondary,
then tertiary (typically local Ollama as the last-resort without network).
The onboarding wizard builds an ``extraction`` route from the provider the
user picks + a fallback; the factory default for a user with nothing is a
single local Ollama model (2026-08-04 decision).

Scope: only the ``extraction`` role is materialized. ``consolidation`` /
``reflexion`` (cold-path, a medium-term goal) and ``gate`` (CI) are reserved
in the design but not delivered here — ``route_for`` rejects them so a future
role is added explicitly, not silently defaulted.
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


@dataclass(frozen=True)
class RoutingConfig:
    """Per-role routing for the episode. Extraction only.

    ``consolidation`` / ``reflexion`` (a medium-term goal, cold path) and
    ``gate`` (CI) are roles reserved for later — they are NOT fields here yet.
    """

    extraction: RoleRoute


def route_for(role: str, cfg: RoutingConfig) -> RoleRoute:
    """Resolve the route for ``role``.

    Only ``extraction`` is materialized; any other role is rejected loudly so
    it is added deliberately when its feature lands.
    """
    if role != "extraction":
        raise ValueError(f"Unknown role for this release: {role}")
    return cfg.extraction


__all__ = ["RoleRoute", "RoutingConfig", "route_for"]
