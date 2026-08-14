"""Tests for role routing — the fallback chain shape.

The extraction route is primary→secondary→tertiary. Only the ``extraction``
role is materialized in the first release; other roles are rejected loudly so
they are added deliberately.
"""

from __future__ import annotations

import pytest

from seahorse.llm import RoleRoute, RoutingConfig, route_for


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


class TestRoutingConfig:
    def test_route_for_extraction(self) -> None:
        cfg = RoutingConfig(extraction=RoleRoute(primary="ollama/qwen3:1.7b"))
        assert route_for("extraction", cfg) == cfg.extraction

    def test_non_extraction_role_rejected_for_mvp(self) -> None:
        cfg = RoutingConfig(extraction=RoleRoute(primary="ollama/qwen3:1.7b"))
        with pytest.raises(ValueError, match="Unknown role"):
            route_for("consolidation", cfg)
