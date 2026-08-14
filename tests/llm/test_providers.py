"""Tests for the providers registry.

The registry maps a ``provider/model`` id to static provider facts. The local
and free-tier cloud providers (2026-08-04 pricing decision) must be present so
the onboarding wizard and the fallback chain can address them; ``api_key_env``
holds the NAME of the key's env var, never the value (secrets never in source).
"""

from __future__ import annotations

import pytest

from seahorse.llm import (
    PROVIDERS,
    LLMError,
    ProviderConfig,
    resolve_provider,
)


class TestProvidersRegistry:
    def test_local_first_and_free_tier_providers_registered(self) -> None:
        # Local-first + the free-tier pricing decision (2026-08-04).
        for name in ("ollama", "gemini", "groq", "openrouter", "openai",
                     "anthropic", "deepseek", "vllm"):
            assert name in PROVIDERS
            assert isinstance(PROVIDERS[name], ProviderConfig)

    def test_ollama_is_keyless_local_endpoint(self) -> None:
        p = PROVIDERS["ollama"]
        assert p.api_base == "http://localhost:11434"
        assert p.api_key_env is None  # local: no key, no data leaves the machine
        assert p.supports_json_schema is False  # the plain-prompt base path must work
        assert p.supports_tool_use is False

    def test_gemini_holds_key_env_name_not_value(self) -> None:
        p = PROVIDERS["gemini"]
        assert p.api_key_env == "GEMINI_API_KEY"  # the NAME, not the key value
        assert "sk-" not in p.api_key_env  # never a real secret

    def test_openai_supports_native_json_schema(self) -> None:
        p = PROVIDERS["openai"]
        assert p.supports_json_schema is True
        assert p.supports_tool_use is True


class TestResolveProvider:
    def test_maps_provider_model_id_to_config(self) -> None:
        p = resolve_provider("ollama/qwen3:1.7b")
        assert p is PROVIDERS["ollama"]

    def test_unknown_prefix_raises_loud(self) -> None:
        with pytest.raises(LLMError, match="Unknown provider prefix: nosuch"):
            resolve_provider("nosuch/model-x")
