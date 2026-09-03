"""Interactive LLM provider onboarding for the CLI.

``seahorse init --llm`` opens a no-TUI wizard (``typer.prompt`` /
``typer.confirm`` — the CLI stays Textual-free). Steps: detect what the user
HAS (a running Ollama, free-tier API keys in the environment), pick a provider
(default preselected by the detection), choose the model id (local size 1.7b /
0.6b for Ollama), an optional fallback model, an optional self-test, and write
the ``[llm]`` section to ``seahorse.toml``.

A user with NOTHING lands on local Ollama qwen3:1.7b (the factory default):
zero registration, zero key, data never leaves the machine. A free-tier cloud
key (Gemini / Groq / OpenRouter) is the QUALITY lever and is preselected when
present, with Ollama available as the no-network tertiary.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import typer
from pydantic import BaseModel, ConfigDict

from seahorse.cli.config import DEFAULT_LLM_TIMEOUT_S, LlmConfig, write_llm_config
from seahorse.llm.providers import CLOUD_PROVIDER_MODELS


@dataclass(frozen=True)
class _ProviderMeta:
    """One wizard-selectable provider: key env var (None = local), default
    model id, and a human label."""

    key: str | None
    model: str
    label: str


# Provider catalog. Ordered: Ollama first (local-first), then the cloud
# quality lever. The cloud entries derive from the single-source catalog in
# ``llm.providers`` (env var + default model); only the UI label lives here.
_WIZARD_LABELS: dict[str, str] = {
    "gemini": "Gemini (free tier)",
    "groq": "Groq (free tier)",
    "openrouter": "OpenRouter (:free)",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "deepseek": "DeepSeek",
}

_PROVIDERS: dict[str, _ProviderMeta] = {
    "ollama": _ProviderMeta(None, "qwen3:1.7b", "Ollama local (private, zero config)"),
    **{
        name: _ProviderMeta(env, model, _WIZARD_LABELS[name])
        for name, env, model in CLOUD_PROVIDER_MODELS
    },
}


class _SelfTestSchema(BaseModel):
    """Minimal schema for the wizard's provider self-test."""

    model_config = ConfigDict(extra="forbid")

    subject: str | None = None


def _ollama_running() -> bool:
    """True when Ollama answers on ``:11434`` (single probe in provider_bootstrap)."""
    from seahorse.cli.provider_bootstrap import ollama_status

    return ollama_status()[0]


def _detect() -> list[str]:
    """Providers currently usable: a running Ollama or a free-tier key set."""
    available: list[str] = []
    for name, meta in _PROVIDERS.items():
        if meta.key is None:
            if _ollama_running():
                available.append(name)
        elif os.environ.get(meta.key):
            available.append(name)
    return available


def _preselect(available: list[str]) -> str:
    """Pick the default: a cloud key (quality lever) beats bare Ollama."""
    cloud = [n for n in available if n != "ollama"]
    if cloud:
        return cloud[0]
    return "ollama"


def _choose_model(name: str) -> str:
    """Ask the model id for ``name``; for Ollama ask the size (1.7b / 0.6b)."""
    if name == "ollama":
        size = typer.prompt(
            "Local model size (1.7b = medium laptop, 0.6b = modest hardware)",
            default="1.7b",
        )
        return f"ollama/qwen3:{size}"
    model = typer.prompt("Model id", default=_PROVIDERS[name].model)
    return f"{name}/{model}"


def _self_test(primary: str) -> None:
    """Run a real extraction against the primary to validate the config."""
    try:
        from seahorse.llm import BudgetContext, LiteLLMBackend, RoleRoute
    except ImportError:
        typer.echo(
            "  extra 'llm' not installed — skipping self-test "
            "(`uv sync --extra llm`)"
        )
        return
    res = LiteLLMBackend(route=RoleRoute(primary=primary)).extract(
        "Test: Seahorse is a persistent memory engine for LLM agents.",
        _SelfTestSchema,
        budget=BudgetContext(),
    )
    if res.degraded_to_skip:
        typer.echo("  ✗ self-test failed — provider unreachable or not configured?")
    else:
        typer.echo(f"  ✓ self-test OK ({res.model_used})")


def run_llm_wizard(vault: Path) -> None:
    """Interactive provider setup → writes the ``[llm]`` section."""
    available = _detect()
    typer.echo("Seahorse LLM provider setup")
    typer.echo(
        "  detected: "
        + (", ".join(available) if available else "none — assuming local Ollama")
    )
    names = list(_PROVIDERS)
    default_idx = names.index(_preselect(available)) + 1
    typer.echo("Providers:")
    for i, name in enumerate(names, start=1):
        suffix = "  (default)" if i == default_idx else ""
        marker = "  [key detected]" if name in available else ""
        typer.echo(f"  {i}) {_PROVIDERS[name].label}{marker}{suffix}")
    choice = typer.prompt("Choose a provider", default=str(default_idx), type=int)
    if not (1 <= choice <= len(names)):
        raise typer.BadParameter(f"provider must be 1..{len(names)}")
    name = names[choice - 1]
    primary = _choose_model(name)

    fallback = typer.prompt(
        "Fallback model id (Enter = none; e.g. ollama/qwen3:1.7b)",
        default="",
    ).strip()
    secondary = fallback or None

    if typer.confirm("Run a quick provider self-test?", default=True):
        _self_test(primary)

    write_llm_config(
        vault,
        LlmConfig(
            primary=primary,
            secondary=secondary,
            tertiary=None,
            timeout_s=DEFAULT_LLM_TIMEOUT_S,
        ),
    )
    typer.echo(f"✓ [llm] written — primary {primary}")


__all__ = ["run_llm_wizard"]
