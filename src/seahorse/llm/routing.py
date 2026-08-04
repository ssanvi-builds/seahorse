"""#4 role routing (f5-04 §2.5) — the primary→secondary→tertiary fallback shape.

A ``RoleRoute`` is the ordered chain of model ids for one role (extraction).
The fallback chain (``fallback.py``) walks it: primary first, then secondary,
then tertiary (typically local Ollama as the last-resort without network).
The onboarding wizard builds an ``extraction`` route from the provider the
user picks + a fallback; the factory default for a user with nothing is a
single local Ollama model (2026-08-04 decision).

MVP scope: only the ``extraction`` role is materialized. ``consolidation`` /
``reflexion`` (cold-path, mediano) and ``gate`` (CI) are reserved in the design
(f5-04 §2.5) but not delivered here — ``route_for`` rejects them so a future
role is added explicitly, not silently defaulted.

References:
- f5-04-multi-llm.md §2.5 (RoleRoute, RoutingConfig, route_for, config/llm.toml)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleRoute:
    """Ordered fallback chain for one role (f5-04 §2.5).

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
    """Per-role routing for the episode. MVP: extraction only.

    ``consolidation`` / ``reflexion`` (mediano, cold path) and ``gate`` (CI)
    are f5-04 §2.5 roles reserved for later — they are NOT fields here yet.
    """

    extraction: RoleRoute


def route_for(role: str, cfg: RoutingConfig) -> RoleRoute:
    """Resolve the route for ``role`` (f5-04 §2.5).

    MVP-1 materializes only ``extraction``; any other role is rejected loudly
    so it is added deliberately when its feature lands.
    """
    if role != "extraction":
        raise ValueError(f"Unknown role for MVP: {role}")
    return cfg.extraction


__all__ = ["RoleRoute", "RoutingConfig", "route_for"]
