"""Tests for role routing — the fallback chain shape.

The extraction route is primary→secondary→tertiary. The per-role
``RoutingConfig`` / ``route_for`` seam was removed (the backend never read the
``role`` argument); the fallback chain is the single ``RoleRoute`` the backend
walks.
"""

from __future__ import annotations

import pytest

from seahorse.llm import RoleRoute


class TestRoleRoute:
    def test_chain_drops_none_tail(self) -> None:
        r = RoleRoute(primary="ollama/qwen3:1.7b")
        assert r.chain() == ("ollama/qwen3:1.7b",)

    def test_chain_preserves_configured_order(self) -> None:
        r = RoleRoute(
            primary="gemini/gemini-2.5-flash",
            secondary="groq/llama-3.3-70b-versatile",
            tertiary="ollama/qwen3:1.7b",
        )
        assert r.chain() == (
            "gemini/gemini-2.5-flash",
            "groq/llama-3.3-70b-versatile",
            "ollama/qwen3:1.7b",
        )

    def test_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        r = RoleRoute(primary="x")
        with pytest.raises(FrozenInstanceError):
            r.primary = "y"  # type: ignore[misc]
