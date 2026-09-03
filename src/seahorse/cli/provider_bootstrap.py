"""LLM provider detection + bootstrap for one-command onboarding.

The hard rule: a ``[llm]`` section is only written after a self-test that
passes — a default that cannot extract is worse than no default (the honest
skip path works everywhere). The module owns three pieces:

- ``ollama_status()`` — the stdlib probe of ``GET /api/tags``; the single
  source of truth for "is Ollama up and what does it serve" (the wizard
  imports it).
- ``provider_self_test()`` — the live extraction probe, moved here from
  ``doctor`` (which now imports it — dependency direction: onboarding →
  doctor, never the reverse).
- ``bootstrap_llm_provider()`` — detection + auto-selection + the guarded
  write. Big downloads (an ``ollama pull``) never happen without consent.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel, ConfigDict

from seahorse.cli.config import DEFAULT_LLM_TIMEOUT_S, LlmConfig, write_llm_config

_OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
_OLLAMA_PULL_MODEL = "qwen3:0.6b"

# Cloud candidates for keys present in the environment, in preference order
# (free-tier quality lever first — mirrors the wizard catalog).
_CLOUD_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("GEMINI_API_KEY", "gemini/gemini-2.5-flash"),
    ("GROQ_API_KEY", "groq/llama-3.3-70b-versatile"),
    ("OPENROUTER_API_KEY", "openrouter/deepseek/deepseek-r1:free"),
    ("OPENAI_API_KEY", "openai/gpt-5-mini"),
    ("ANTHROPIC_API_KEY", "anthropic/claude-haiku-4-5"),
    ("DEEPSEEK_API_KEY", "deepseek/deepseek-chat"),
)


class SelfTestSchema(BaseModel):
    """Minimal schema for the provider self-test probe.

    ``subject`` is required (the probe must produce the core extraction
    field); ``extra="allow"`` tolerates the extra fields small local models
    emit from the extraction pattern (e.g. ``valid_at``).
    """

    model_config = ConfigDict(extra="allow")

    subject: str


def ollama_status() -> tuple[bool, list[str]]:
    """``(reachable, model names)`` from Ollama's ``/api/tags``.

    A probe failure just means "not running" — never an error.
    """
    try:
        with urllib.request.urlopen(_OLLAMA_TAGS_URL, timeout=0.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 — a probe failure just means "not running"
        return False, []
    models = data.get("models", [])
    names = [
        m["name"]
        for m in models
        if isinstance(m, dict) and isinstance(m.get("name"), str)
    ]
    return True, names


def candidate_primaries() -> list[str]:
    """Provider/model ids in preference order: local Ollama (qwen3 first),
    then cloud providers whose API key is already in the environment."""
    _, models = ollama_status()
    ranked = sorted(models, key=lambda m: (0 if m.startswith("qwen3") else 1, m))
    candidates = [f"ollama/{m}" for m in ranked]
    candidates.extend(model for env, model in _CLOUD_CANDIDATES if os.environ.get(env))
    return candidates


@dataclass(frozen=True)
class ProviderDecision:
    """What the bootstrap decided about the ``[llm]`` section."""

    primary: str | None
    detail: str


def provider_self_test(llm: LlmConfig) -> tuple[bool, str]:
    """Run a real extraction against the configured route (live probe).

    A raised ``LLMError`` (unreachable backend, unknown pricing, missing
    extra) is a ``False`` report, never a crash. A missing ``llm`` extra
    reports the exact fix.
    """
    try:
        from seahorse.llm import BudgetContext, LiteLLMBackend, LLMError, RoleRoute
    except ImportError:
        return False, "extra 'llm' not installed — `uv sync --extra llm`"
    route = RoleRoute(
        primary=llm.primary, secondary=llm.secondary, tertiary=llm.tertiary
    )
    try:
        res = LiteLLMBackend(route=route, timeout_s=llm.timeout_s).extract(
            "Doctor probe: Seahorse is a persistent memory engine for LLM agents.",
            SelfTestSchema,
            budget=BudgetContext(),
        )
    except LLMError as exc:
        return False, f"error: {exc}"
    if res.degraded_to_skip:
        return False, "provider unreachable or misconfigured"
    return True, f"ok ({res.model_used})"


def bootstrap_llm_provider(
    vault: Path,
    *,
    out: TextIO,
    self_test: Callable[[str], tuple[bool, str]] | None = None,
) -> ProviderDecision:
    """Detect, self-test and (only on success) write the ``[llm]`` section.

    Candidates in preference order; the first whose live self-test passes is
    written, a failing primary falls through to the next. When nothing passes
    the section is NOT written (skip extraction is first-class) and the exact
    fix is printed. An empty-but-running Ollama on a TTY offers the model
    pull — never automatic (a 400 MB download needs consent).
    """
    probe: Callable[[str], tuple[bool, str]] = self_test or _default_probe
    for primary in candidate_primaries():
        ok, detail = probe(primary)
        if ok:
            _write_primary(vault, primary)
            return ProviderDecision(primary=primary, detail=detail)
        out.write(f"  llm: candidate {primary} failed self-test ({detail})\n")
    return _no_provider_fallback(vault, out)


def _write_primary(vault: Path, primary: str) -> None:
    write_llm_config(
        vault,
        LlmConfig(
            primary=primary,
            secondary=None,
            tertiary=None,
            timeout_s=DEFAULT_LLM_TIMEOUT_S,
        ),
    )


def _default_probe(primary: str) -> tuple[bool, str]:
    return provider_self_test(
        LlmConfig(
            primary=primary, secondary=None, tertiary=None, timeout_s=DEFAULT_LLM_TIMEOUT_S
        )
    )


def _no_provider_fallback(vault: Path, out: TextIO) -> ProviderDecision:
    """Nothing self-tested clean: skip extraction, say exactly what fixes it."""
    reachable, models = ollama_status()
    if reachable and not models and sys.stdin.isatty():
        decision = _offer_ollama_pull(vault)
        if decision is not None:
            return decision
    if reachable and not models:
        out.write(
            f"  llm: Ollama is running but has no models — `ollama pull "
            f"{_OLLAMA_PULL_MODEL}` enables LLM extraction "
            "(skip extraction works without it)\n"
        )
    elif not reachable:
        out.write(
            "  llm: no Ollama and no provider keys in the environment — "
            "extraction stays skip (`seahorse init --llm` configures one)\n"
        )
    else:
        out.write(
            "  llm: no provider passed the self-test — extraction stays skip "
            "(`seahorse init --llm` configures one)\n"
        )
    return ProviderDecision(
        primary=None, detail="no provider passed the self-test — extraction skip"
    )


def _offer_ollama_pull(vault: Path) -> ProviderDecision | None:
    """TTY-only offer to pull a small local model (explicit consent)."""
    import typer

    if not typer.confirm(
        f"Ollama is running but has no models. Pull {_OLLAMA_PULL_MODEL} "
        "(~400 MB) for local extraction?",
        default=True,
    ):
        return None
    subprocess.run(["ollama", "pull", _OLLAMA_PULL_MODEL], check=False)  # noqa: S603
    ok, detail = _default_probe(f"ollama/{_OLLAMA_PULL_MODEL}")
    if not ok:
        return None
    _write_primary(vault, f"ollama/{_OLLAMA_PULL_MODEL}")
    return ProviderDecision(primary=f"ollama/{_OLLAMA_PULL_MODEL}", detail=detail)


__all__ = [
    "ProviderDecision",
    "bootstrap_llm_provider",
    "candidate_primaries",
    "ollama_status",
    "provider_self_test",
]