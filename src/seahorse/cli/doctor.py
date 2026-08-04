"""`seahorse doctor` — vault + LLM provider health diagnostics (M4-C.3).

The onboarding backlog command: report the extraction regime, the installed
``llm`` extra, missing API keys (names only, never values), a live provider
self-test when possible, and the extraction mode. WARN/FAIL items are the
actionable list; a vault running pure ``skip`` (no ``[llm]``) is a valid state
and reports WARN, not FAIL.

Exit codes: the command itself reports health in the payload (exit 0 always —
diagnosis, not a gate). Config failures already surface as ``CliConfigInvalid``
(exit 83) during ``resolved_config()``.
"""

from __future__ import annotations

import importlib.util
import os
from typing import TextIO

from pydantic import BaseModel, ConfigDict

from seahorse.cli.config import LlmConfig, SeahorseConfig
from seahorse.cli.output import OutputFormat, render_message
from seahorse.llm import LLMError, resolve_provider


class _SelfTestSchema(BaseModel):
    """Minimal schema for the doctor's live provider probe."""

    model_config = ConfigDict(extra="forbid")

    subject: str | None = None


def _litellm_installed() -> bool:
    """True when the optional ``llm`` extra (LiteLLM) is installed.

    ``find_spec`` probes availability without importing (avoids the F401 and
    works when the extra is absent).
    """
    return importlib.util.find_spec("litellm") is not None


def _missing_keys(llm: LlmConfig) -> list[str]:
    """Env-var NAMES missing for the configured route (never their values).

    Local providers (Ollama/vLLM) have no key and never warn; an unknown model
    id is reported by its full id so the config typo is actionable.
    """
    missing: list[str] = []
    for model in (llm.primary, llm.secondary, llm.tertiary):
        if not model:
            continue
        try:
            prov = resolve_provider(model)
        except LLMError:
            missing.append(model)
            continue
        if prov.api_key_env is not None and not os.environ.get(prov.api_key_env):
            missing.append(prov.api_key_env)
    return missing


def _provider_self_test(llm: LlmConfig) -> tuple[bool, str]:
    """Run a real extraction against the configured route (live probe)."""
    from seahorse.llm import BudgetContext, LiteLLMBackend, RoleRoute

    route = RoleRoute(
        primary=llm.primary, secondary=llm.secondary, tertiary=llm.tertiary
    )
    res = LiteLLMBackend(route=route, timeout_s=llm.timeout_s).extract(
        "Doctor probe: Seahorse is a persistent memory engine for LLM agents.",
        _SelfTestSchema,
        budget=BudgetContext(),
    )
    if res.degraded_to_skip:
        return False, "provider unreachable or misconfigured"
    return True, f"ok ({res.model_used})"


def run_doctor(
    config: SeahorseConfig, *, fmt: OutputFormat = "human", out: TextIO
) -> None:
    """Render the diagnostic report for the resolved vault config."""
    checks: list[dict[str, str]] = []

    if config.llm is None:
        checks.append(
            {
                "check": "llm_config",
                "status": "WARN",
                "detail": "no [llm] section; run `seahorse init --llm`",
            }
        )
    else:
        chain = config.llm.primary
        if config.llm.secondary:
            chain += f" -> {config.llm.secondary}"
        if config.llm.tertiary:
            chain += f" -> {config.llm.tertiary}"
        checks.append({"check": "llm_config", "status": "OK", "detail": chain})

    if _litellm_installed():
        checks.append({"check": "litellm", "status": "OK", "detail": "installed"})
    else:
        checks.append(
            {
                "check": "litellm",
                "status": "WARN",
                "detail": "extra 'llm' not installed; `uv sync --extra llm`",
            }
        )

    if config.llm is not None:
        missing = _missing_keys(config.llm)
        if missing:
            checks.append(
                {
                    "check": "api_keys",
                    "status": "WARN",
                    "detail": "missing: " + ", ".join(missing),
                }
            )
        else:
            checks.append({"check": "api_keys", "status": "OK", "detail": "present"})

    if config.llm is not None and _litellm_installed():
        ok, detail = _provider_self_test(config.llm)
        checks.append(
            {"check": "provider", "status": "OK" if ok else "FAIL", "detail": detail}
        )

    checks.append(
        {
            "check": "extraction_mode",
            "status": "OK",
            "detail": config.default_extraction_mode,
        }
    )
    checks.append(
        {
            "check": "db",
            "status": "OK" if config.db_path.exists() else "WARN",
            "detail": str(config.db_path),
        }
    )

    healthy = all(c["status"] == "OK" for c in checks)
    payload = {"command": "doctor", "healthy": healthy, "checks": checks}
    lines = "".join(
        f"  {c['status']:<4} {c['check']:<16} {c['detail']}\n" for c in checks
    )
    human = (
        "Seahorse doctor\n"
        + lines
        + ("\n✓ healthy" if healthy else "\n⚠ fix the WARN/FAIL items above")
    )
    render_message(payload, fmt=fmt, out=out, human_text=human)


__all__ = ["run_doctor"]
