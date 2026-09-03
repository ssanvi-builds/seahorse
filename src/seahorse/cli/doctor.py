"""`seahorse doctor` — vault + LLM provider health diagnostics.

The onboarding command: report the extraction regime, the installed ``llm``
extra, missing API keys (names only, never values), a live provider self-test
when possible, and the extraction mode. WARN/FAIL items are the actionable
list; a vault running pure ``skip`` (no ``[llm]``) is a valid state and reports
WARN, not FAIL.

Exit codes: the command itself reports health in the payload (exit 0 always —
diagnosis, not a gate). Config failures already surface as ``CliConfigInvalid``
(exit 83) during ``resolved_config()``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel, ConfigDict

from seahorse.cli.config import LlmConfig, SeahorseConfig
from seahorse.cli.output import OutputFormat, render_message
from seahorse.llm import LLMError, resolve_provider

# The observer hook events + marker (shared with setup, which installs them).
_OBSERVER_EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop")
_HOOK_MARKER = "observe event"
_CONTEXT_PROBE_TIMEOUT_S = 10.0


class _SelfTestSchema(BaseModel):
    """Minimal schema for the doctor's live provider probe.

    ``subject`` is required (the probe must produce the core extraction field);
    ``extra="allow"`` tolerates the extra fields small local models emit from
    the extraction pattern (e.g. ``valid_at``) — the probe tests reachability
    and schema-valid JSON, not strict field discipline.
    """

    model_config = ConfigDict(extra="allow")

    subject: str


def _sqlite_load_extension_supported() -> bool:
    """True when the runtime ``sqlite3`` can load extensions (sqlite-vec needs it).

    Some Python builds (e.g. a pyenv build without
    ``SQLITE_ENABLE_LOAD_EXTENSION``) lack ``enable_load_extension`` entirely,
    which breaks every DB command with a cryptic AttributeError. The doctor
    surfaces it as an actionable FAIL instead.
    """
    return hasattr(sqlite3.Connection, "enable_load_extension")


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
    """Run a real extraction against the configured route (live probe).

    A raised ``LLMError`` (unreachable backend, unknown pricing, missing extra)
    is a FAIL report, never a crash — the doctor diagnoses.
    """
    from seahorse.llm import BudgetContext, LiteLLMBackend, LLMError, RoleRoute

    route = RoleRoute(
        primary=llm.primary, secondary=llm.secondary, tertiary=llm.tertiary
    )
    try:
        res = LiteLLMBackend(route=route, timeout_s=llm.timeout_s).extract(
            "Doctor probe: Seahorse is a persistent memory engine for LLM agents.",
            _SelfTestSchema,
            budget=BudgetContext(),
        )
    except LLMError as exc:
        return False, f"error: {exc}"
    if res.degraded_to_skip:
        return False, "provider unreachable or misconfigured"
    return True, f"ok ({res.model_used})"


def _claude_settings_path() -> Path:
    env = os.environ.get("SEAHORSE_CLAUDE_SETTINGS")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "settings.json"


def _entry_commands(entry: dict) -> list[str]:
    """All commands in a settings.json hook entry (flat legacy or nested)."""
    commands = [entry["command"]] if entry.get("command") else []
    commands.extend(h["command"] for h in entry.get("hooks", []) if h.get("command"))
    return commands


def _hooks_check() -> tuple[str, str]:
    """The observer hooks as installed in Claude Code's settings.json."""
    path = _claude_settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (
            "WARN",
            f"no observer hooks in {path}; run `seahorse setup` to install capture",
        )
    installed: dict[str, bool] = {}
    for event in _OBSERVER_EVENTS:
        entries = data.get("hooks", {}).get(event, [])
        installed[event] = any(
            _HOOK_MARKER in c for entry in entries for c in _entry_commands(entry)
        )
    if not all(installed.values()):
        missing = ", ".join(e for e, ok in installed.items() if not ok)
        return "WARN", f"hooks missing for: {missing}; run `seahorse setup`"
    return "OK", f"installed ({len(_OBSERVER_EVENTS)} events)"


def _db_check(config: SeahorseConfig) -> tuple[str, str]:
    """Existence + writability + integrity of the memory database.

    Existence alone reports "OK" on a corrupt (garbage bytes) or read-only
    file — states a user can hit after a bad rsync or a chmod accident — so
    the probe runs a read-only ``PRAGMA quick_check`` and an os.access check.
    """
    db = config.db_path
    if not db.exists():
        return "WARN", f"missing: {db} (run `seahorse init`)"
    if not os.access(db, os.W_OK) or not os.access(db.parent, os.W_OK):
        return "FAIL", f"not writable: {db} (restore write permission on the vault)"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return "FAIL", f"unreadable: {exc} (restore from backup or re-init)"
    result = row[0] if row else "unknown"
    if result != "ok":
        return "FAIL", f"quick_check: {result} (restore from backup or re-init)"
    return "OK", str(db)


def _observer_check(config: SeahorseConfig) -> tuple[str, str]:
    """The observer worker as seen from the vault (pid liveness, not socket)."""
    if config.observe is None:
        return "WARN", "observer not configured; run `seahorse setup`"
    from seahorse.observe.cli import observer_liveness, socket_path

    if not socket_path(config).exists():
        return (
            "WARN",
            "observer not running; it auto-starts on the next Claude Code session",
        )
    running, pid = observer_liveness(config)
    if running:
        return "OK", f"observer running (pid {pid})"
    return (
        "WARN",
        f"stale socket without a live observer ({socket_path(config)}); "
        "remove it or run `seahorse observe start` (an orphaned observer.lock "
        "is harmless — liveness comes from the kernel flock, not the file)",
    )


def _context_probe(config: SeahorseConfig) -> tuple[bool, str]:
    """Render the SessionStart context through the real CLI (end-to-end)."""
    cmd = [
        sys.executable,
        "-m",
        "seahorse.cli.app",
        "--vault",
        str(config.vault),
        "context",
    ]
    try:
        res = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=_CONTEXT_PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"error: {exc}"
    if res.returncode != 0:
        return False, f"exit {res.returncode}"
    if not res.stdout.strip():
        return False, "empty context"
    return True, f"ok ({len(res.stdout)} chars)"


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
    db_status, db_detail = _db_check(config)
    checks.append({"check": "db", "status": db_status, "detail": db_detail})

    # Capture end-to-end: hooks installed in Claude Code, observer worker,
    # and the SessionStart context rendering through the real CLI.
    hooks_status, hooks_detail = _hooks_check()
    checks.append({"check": "claude_hooks", "status": hooks_status, "detail": hooks_detail})
    obs_status, obs_detail = _observer_check(config)
    checks.append({"check": "observer", "status": obs_status, "detail": obs_detail})
    ctx_ok, ctx_detail = _context_probe(config)
    checks.append(
        {"check": "context", "status": "OK" if ctx_ok else "WARN", "detail": ctx_detail}
    )

    checks.append(
        {
            "check": "python",
            "status": "OK",
            "detail": (
                f"{sys.version_info.major}.{sys.version_info.minor}."
                f"{sys.version_info.micro} (>=3.11 required)"
            ),
        }
    )
    if shutil.which("uv") is not None:
        checks.append({"check": "uv", "status": "OK", "detail": "present"})
    else:
        checks.append(
            {
                "check": "uv",
                "status": "WARN",
                "detail": (
                    "absent — install https://docs.astral.sh/uv/ "
                    "(needed for `uv tool install .`)"
                ),
            }
        )
    checks.append(
        {
            "check": "obsidian",
            "status": "OK",
            "detail": "optional — markdown layer, not required",
        }
    )
    if _sqlite_load_extension_supported():
        checks.append(
            {"check": "sqlite_vec", "status": "OK", "detail": "load_extension supported"}
        )
    else:
        checks.append(
            {
                "check": "sqlite_vec",
                "status": "FAIL",
                "detail": (
                    "sqlite3 lacks enable_load_extension — sqlite-vec cannot "
                    "load; use a Python build with load_extension support"
                ),
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
