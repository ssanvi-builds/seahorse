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
from seahorse.cli.credentials import load_credentials_env
from seahorse.llm.providers import CLOUD_PROVIDER_MODELS

_OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
_OLLAMA_PULL_MODEL = "qwen3:0.6b"

# Cloud candidates for keys present in the environment, in preference order
# (derived from the single-source catalog in ``llm/providers.py``).
_CLOUD_CANDIDATES: tuple[tuple[str, str], ...] = tuple(
    (env, f"{name}/{model}") for name, env, model in CLOUD_PROVIDER_MODELS
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
    remediate: Callable[[], ProviderDecision | None] | None = None,
) -> ProviderDecision:
    """Detect, self-test and (only on success) write the ``[llm]`` section.

    Candidates in preference order; the first whose live self-test passes is
    written, a failing primary falls through to the next. When nothing passes
    the section is NOT written (skip extraction is first-class) and the exact
    fix is printed. On a TTY, ``_no_provider_fallback`` offers a remediation
    menu (pull a local model / paste an API key / skip) — big downloads and
    key entry never happen without explicit consent. ``remediate`` overrides
    the menu for tests.
    """
    load_credentials_env()  # a stored key becomes an ordinary env candidate
    probe: Callable[[str], tuple[bool, str]] = self_test or _default_probe
    for primary in candidate_primaries():
        ok, detail = probe(primary)
        if ok:
            _write_primary(vault, primary)
            return ProviderDecision(primary=primary, detail=detail)
        out.write(f"  llm: candidate {primary} failed self-test ({detail})\n")
    return _no_provider_fallback(vault, out, probe=probe, remediate=remediate)


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


def _no_provider_fallback(
    vault: Path,
    out: TextIO,
    *,
    probe: Callable[[str], tuple[bool, str]] | None = None,
    remediate: Callable[[], ProviderDecision | None] | None = None,
) -> ProviderDecision:
    """Nothing self-tested clean: offer remediation on a TTY, then skip extraction."""
    if remediate is None and sys.stdin.isatty():
        remediate = lambda: _offer_remediation(  # noqa: E731 — thin closure
            vault, out, probe=probe or _default_probe
        )
    if remediate is not None:
        decision = remediate()
        if decision is not None:
            return decision
    reachable, models = ollama_status()
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


def _offer_remediation(
    vault: Path,
    out: TextIO,
    *,
    probe: Callable[[str], tuple[bool, str]],
) -> ProviderDecision | None:
    """TTY-only menu: pull a local model, paste an API key, or skip.

    The default is skip — a 400 MB download and key entry must never be the
    Enter default. Returns None when the user declines (fall through to the
    honest non-TTY-style message).
    """
    import typer

    from seahorse.cli.credentials import credentials_path

    reachable, _ = ollama_status()
    pull_note = (
        "~400 MB, local)"
        if reachable
        else "~400 MB, local — Ollama is NOT running, start it first)"
    )
    out.write("  llm: no provider passed the self-test. Options:\n")
    out.write(f"    1) Pull {_OLLAMA_PULL_MODEL} via Ollama ({pull_note}\n")
    out.write(
        f"    2) Paste an API key (stored 0600 in {credentials_path()})\n"
    )
    out.write("    3) Skip — extraction stays deterministic\n")
    for _attempt in range(2):
        choice = str(typer.prompt("Choice", default="3")).strip()
        if choice == "1":
            return _pull_local_model(vault, probe=probe)
        if choice == "2":
            return _paste_key_flow(vault, out, probe=probe)
        if choice in ("", "3"):
            return None
        out.write("  llm: invalid choice\n")
    return None


def _pull_local_model(
    vault: Path,
    *,
    probe: Callable[[str], tuple[bool, str]],
) -> ProviderDecision | None:
    """Pull the small local model (explicit consent already given)."""
    subprocess.run(["ollama", "pull", _OLLAMA_PULL_MODEL], check=False)  # noqa: S603
    primary = f"ollama/{_OLLAMA_PULL_MODEL}"
    ok, detail = probe(primary)
    if not ok:
        return None
    _write_primary(vault, primary)
    return ProviderDecision(primary=primary, detail=detail)


def _paste_key_flow(
    vault: Path,
    out: TextIO,
    *,
    probe: Callable[[str], tuple[bool, str]],
) -> ProviderDecision | None:
    """Prompt for a provider, model and API key; store the key 0600.

    The key goes into the credentials store and the process environment (so
    the live self-test sees it); ``[llm]`` is written only if the self-test
    passes. A failing test keeps the stored key (a 401 is often transient)
    and prints a masked explanation.
    """
    import typer

    from seahorse.cli.credentials import credentials_path, mask_secret, save_api_key

    out.write("  llm: providers:\n")
    for idx, (env, model) in enumerate(_CLOUD_CANDIDATES, start=1):
        out.write(f"    {idx}) {model}  ({env})\n")
    raw = str(typer.prompt(f"Provider [1-{len(_CLOUD_CANDIDATES)}]", default="1")).strip()
    if not raw.isdigit() or not 1 <= int(raw) <= len(_CLOUD_CANDIDATES):
        return None
    env_name, default_model = _CLOUD_CANDIDATES[int(raw) - 1]
    model = str(typer.prompt("Model id", default=default_model)).strip() or default_model
    key = str(typer.prompt(env_name, hide_input=True)).strip()
    if not key:
        return None
    save_api_key(env_name, key)
    os.environ[env_name] = key
    ok, detail = probe(model)
    if ok:
        _write_primary(vault, model)
        return ProviderDecision(primary=model, detail=detail)
    out.write(
        mask_secret(
            f"  llm: pasted key failed the self-test ({detail}) — key stored in "
            f"{credentials_path()}; nothing written to [llm]\n",
            key,
        )
    )
    return None


__all__ = [
    "ProviderDecision",
    "bootstrap_llm_provider",
    "candidate_primaries",
    "ollama_status",
    "provider_self_test",
]