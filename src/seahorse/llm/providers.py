"""Providers registry — the multi-provider extension point.

``resolve_provider(model_id)`` maps a LiteLLM-style ``provider/model`` id to a
``ProviderConfig`` describing how to reach that provider (API base, env var that
holds the key, native structured-output support, context window). The config
records the NAME of the env var that holds a key — never the value (secrets
never live in source). The local-first stance makes Ollama, vLLM and llama.cpp
first-class backends, not add-ons; the onboarding wizard's free-tier providers
(Gemini/Groq/OpenRouter, decided 2026-08-04) are registered here too so a key
in the environment is enough to raise extraction quality.

``supports_json_schema`` / ``supports_tool_use`` only gate an OPTIONAL
optimization (``_kwargs_for`` in the backend): hard dependence on native
structured outputs is avoided — the plain-prompt + Pydantic validator path is
the always-available default. The CI gate (future, Ollama qwen3:0.6b) has
neither, which forces the base path to work.
"""

from __future__ import annotations

from dataclasses import dataclass

from seahorse.llm.errors import LLMError

# Default provider timeouts (extraction 20s, consolidation 120s).
# The base 30s covers the extraction role with headroom for slow local CPU.
_DEFAULT_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class ProviderConfig:
    """Static facts about one provider family.

    ``api_key_env`` is the NAME of the environment variable holding the key —
    the value is read at call time, never stored here. ``api_base`` is only set
    for providers with a non-default endpoint (local Ollama/vLLM, DeepSeek);
    ``None`` lets LiteLLM resolve the endpoint from the provider prefix.
    """

    name: str
    api_base: str | None = None
    api_key_env: str | None = None
    timeout_s: float = _DEFAULT_TIMEOUT_S
    supports_json_schema: bool = False
    supports_tool_use: bool = False
    max_context_tokens: int = 32_768


PROVIDERS: dict[str, ProviderConfig] = {
    # Local-first: no key, no data leaves the machine. The
    # factory default for a user who has nothing (decided 2026-08-04).
    "ollama": ProviderConfig(
        name="ollama",
        api_base="http://localhost:11434",
        max_context_tokens=32_768,
    ),
    # Free-tier providers (2026-08-04 decision): a key in the environment is
    # enough to raise extraction quality. Gemini is the CLI-user's current
    # default (they use it with claude-mem); Groq/OpenRouter are open-weight.
    "gemini": ProviderConfig(
        name="gemini",
        api_key_env="GEMINI_API_KEY",
        supports_json_schema=True,
        max_context_tokens=1_048_576,
    ),
    "groq": ProviderConfig(
        name="groq",
        api_key_env="GROQ_API_KEY",
        supports_json_schema=True,
        max_context_tokens=131_072,
    ),
    "openrouter": ProviderConfig(
        name="openrouter",
        api_key_env="OPENROUTER_API_KEY",
        supports_json_schema=True,
        max_context_tokens=131_072,
    ),
    # Paid cloud (verified Jul 2026).
    "openai": ProviderConfig(
        name="openai",
        api_key_env="OPENAI_API_KEY",
        supports_json_schema=True,
        supports_tool_use=True,
        max_context_tokens=128_000,
    ),
    "anthropic": ProviderConfig(
        name="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
        supports_tool_use=True,
        max_context_tokens=200_000,
    ),
    "deepseek": ProviderConfig(
        name="deepseek",
        api_base="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        max_context_tokens=1_000_000,
    ),
    "vllm": ProviderConfig(
        name="vllm",
        api_base="http://localhost:8000",
        supports_json_schema=True,
        max_context_tokens=32_768,
    ),
}


# Cloud provider catalog: (provider prefix, api-key env var, default model id),
# in free-tier-quality preference order. SINGLE SOURCE — both the setup
# bootstrap (``cli/provider_bootstrap``) and the interactive wizard
# (``cli/wizard``) derive their menus from this tuple, so a new provider is
# registered exactly once.
CLOUD_PROVIDER_MODELS: tuple[tuple[str, str, str], ...] = (
    ("gemini", "GEMINI_API_KEY", "gemini-2.5-flash"),
    ("groq", "GROQ_API_KEY", "llama-3.3-70b-versatile"),
    ("openrouter", "OPENROUTER_API_KEY", "deepseek/deepseek-r1:free"),
    ("openai", "OPENAI_API_KEY", "gpt-5-mini"),
    ("anthropic", "ANTHROPIC_API_KEY", "claude-haiku-4-5"),
    ("deepseek", "DEEPSEEK_API_KEY", "deepseek-chat"),
)


def resolve_provider(model_id: str) -> ProviderConfig:
    """Map a ``provider/model`` id to its ``ProviderConfig``.

    ``ollama/qwen3:1.7b`` → ``PROVIDERS['ollama']``. Raises ``LLMError`` for an
    unknown provider prefix — a config typo fails loud at construction, not
    silently at call time.
    """
    prefix = model_id.split("/", 1)[0]
    if prefix not in PROVIDERS:
        raise LLMError(f"Unknown provider prefix: {prefix}")
    return PROVIDERS[prefix]


__all__ = [
    "CLOUD_PROVIDER_MODELS",
    "ProviderConfig",
    "PROVIDERS",
    "resolve_provider",
]
