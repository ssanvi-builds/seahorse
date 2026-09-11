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

from seahorse.cli.config import LlmConfig, SeahorseConfig
from seahorse.cli.output import OutputFormat, render_message

# The live provider probe lives in provider_bootstrap (the onboarding owns it);
# doctor imports it. The legacy aliases keep the historical import paths.
from seahorse.cli.provider_bootstrap import (  # noqa: F401 — re-exported
    SelfTestSchema as _SelfTestSchema,
)
from seahorse.cli.provider_bootstrap import (
    provider_self_test as _provider_self_test,
)
from seahorse.llm import LLMError, resolve_provider

# The observer hook events + marker (shared with setup, which installs them).
_OBSERVER_EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop")
_HOOK_MARKER = "observe event"
_CONTEXT_PROBE_TIMEOUT_S = 10.0
# capture_health window: long enough to span quiet weeks, short enough that a
# silently-dead capture surfaces on the next doctor run.
CAPTURE_HEALTH_WINDOW_DAYS = 7

# Check names doctor --fix can repair (via onboarding.repair_steps_for).
# Per-harness MCP checks use the "mcp_registered:<harness>" prefix.
_REPAIRABLE_CHECKS = frozenset(
    {
        "claude_hooks",
        "codex_hooks",
        "mcp_registered",
        "agent_instructions",
        "skills_installed",
        "credentials",
        "consolidate",
        "db",
    }
)


def _repairable(check_name: str) -> bool:
    return (
        check_name in _REPAIRABLE_CHECKS
        or check_name.startswith("mcp_registered:")
        or check_name.startswith("agent_instructions:")
    )


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
        return "WARN", f"missing: {db} (run `seahorse setup` — it creates it)"
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


def _capture_health_check(config: SeahorseConfig) -> tuple[str, str]:
    """Episodes actually written in the last window — capture's ground truth.

    The file checks above can all be green while capture is silently dead
    (hooks installed but never approved via Codex's /hooks trust review, or a
    dead observer): zero episodes in the window is the observable symptom.
    OK at >=1 episode; WARN at 0 when hooks are installed (Claude Code or
    Codex); SKIP-OK with no hooks (capture-on-intent) or no db yet.
    """
    if not config.db_path.exists():
        return "OK", "no db yet — nothing captured (expected before first use)"
    hooks_installed = _hooks_check()[0] == "OK"
    if not hooks_installed:
        from seahorse.cli.codex_hooks import codex_hooks_installed

        hooks_installed = codex_hooks_installed()
    from datetime import UTC, datetime, timedelta

    from seahorse.persistence.storage import Storage

    since = datetime.now(UTC) - timedelta(days=CAPTURE_HEALTH_WINDOW_DAYS)
    try:
        storage = Storage(config.db_path)
        try:
            count = storage.episodes.count_created_since(since)
        finally:
            storage.close()
    except Exception as exc:  # noqa: BLE001 — diagnosis must not crash
        return "WARN", f"cannot probe db: {exc}"
    if count >= 1:
        return (
            "OK",
            f"{count} episode(s) written in the last {CAPTURE_HEALTH_WINDOW_DAYS}d",
        )
    if hooks_installed:
        return (
            "WARN",
            f"hooks installed but 0 episodes in {CAPTURE_HEALTH_WINDOW_DAYS}d — "
            "approve them via /hooks (Codex skips untrusted hooks) and check "
            "`seahorse observe status`",
        )
    return "OK", "no hooks — capture-on-intent (episodes land when the agent writes)"


def run_doctor(
    config: SeahorseConfig,
    *,
    fmt: OutputFormat = "human",
    out: TextIO,
    fix: bool = False,
) -> None:
    """Render the diagnostic report for the resolved vault config.

    ``fix=True`` attempts the repairs Seahorse owns for every actionable
    (WARN/FAIL) check in ``_REPAIRABLE_CHECKS`` and appends one
    ``fix:<check>`` line per attempt — the diagnosis itself is untouched.
    """
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
        # Keys pasted during setup live in the credentials store — load the
        # NAMES into the environment so a stored key counts as present.
        from seahorse.cli.credentials import load_credentials_env

        load_credentials_env()
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
    cap_status, cap_detail = _capture_health_check(config)
    checks.append({"check": "capture_health", "status": cap_status, "detail": cap_detail})
    ctx_ok, ctx_detail = _context_probe(config)
    checks.append(
        {"check": "context", "status": "OK" if ctx_ok else "WARN", "detail": ctx_detail}
    )

    # The agent surface: MCP registration, agent instructions, auto-consolidate
    # (opt-in — off is a valid state, never a WARN).
    from seahorse.cli.agent_instructions import installed as _ai_installed
    from seahorse.cli.mcp_register import is_mcp_registered

    checks.append(
        {
            "check": "mcp_registered",
            "status": "OK" if is_mcp_registered() else "WARN",
            "detail": (
                "registered (user scope)"
                if is_mcp_registered()
                else "seahorse-mcp not registered; run `seahorse setup`"
            ),
        }
    )
    # The other harnesses: only relevant when the harness itself appears
    # installed (its config file exists) — never WARN a Codex-only user
    # about a machine where Seahorse would be noise.
    from seahorse.cli.harness_targets import harness_ids, resolve_target

    for hid in harness_ids():
        if hid == "claude-code":
            continue
        target = resolve_target(hid)
        path = target.config_path()
        if not path.exists():
            checks.append(
                {
                    "check": f"mcp_registered:{hid}",
                    "status": "OK",
                    "detail": f"{hid} not installed ({path} absent) — skipped",
                }
            )
        elif target.is_registered(path):
            checks.append(
                {
                    "check": f"mcp_registered:{hid}",
                    "status": "OK",
                    "detail": f"registered in {path}",
                }
            )
        else:
            checks.append(
                {
                    "check": f"mcp_registered:{hid}",
                    "status": "WARN",
                    "detail": f"seahorse-mcp not in {path}; run `seahorse setup --harness {hid}`",
                }
            )
    if _ai_installed():
        checks.append(
            {"check": "agent_instructions", "status": "OK", "detail": "installed"}
        )
    else:
        checks.append(
            {
                "check": "agent_instructions",
                "status": "WARN",
                "detail": "no memory instructions in ~/.claude/CLAUDE.md; run `seahorse setup`",
            }
        )
    # The other harnesses' instruction files: same "only when the harness
    # appears installed" rule as the MCP checks above (proxy: the harness's
    # global instruction file exists).
    from seahorse.cli.agent_instructions import instructions_path_for

    for hid in ("codex", "antigravity", "gemini"):
        instr_path = instructions_path_for(hid)
        if instr_path is None or not instr_path.exists():
            checks.append(
                {
                    "check": f"agent_instructions:{hid}",
                    "status": "OK",
                    "detail": f"{hid} not installed ({instr_path} absent) — skipped",
                }
            )
        elif _ai_installed(instr_path):
            checks.append(
                {
                    "check": f"agent_instructions:{hid}",
                    "status": "OK",
                    "detail": f"installed in {instr_path}",
                }
            )
        else:
            checks.append(
                {
                    "check": f"agent_instructions:{hid}",
                    "status": "WARN",
                    "detail": (
                        f"no memory instructions in {instr_path}; "
                        f"run `seahorse setup --harness {hid}`"
                    ),
                }
            )
    # Codex GA hooks: same "harness appears installed" proxy as above, but the
    # directory is the proxy (the hooks file is exactly what may be missing).
    from seahorse.cli.codex_hooks import codex_hooks_installed, codex_hooks_path
    from seahorse.cli.harness_targets import read_json_dict

    codex_file = codex_hooks_path()
    if not codex_file.parent.exists():
        checks.append(
            {
                "check": "codex_hooks",
                "status": "OK",
                "detail": f"codex not installed ({codex_file.parent} absent) — skipped",
            }
        )
    elif not codex_file.exists():
        checks.append(
            {
                "check": "codex_hooks",
                "status": "WARN",
                "detail": "no codex hooks file; run `seahorse setup --harness codex`",
            }
        )
    elif read_json_dict(codex_file) is None:
        checks.append(
            {
                "check": "codex_hooks",
                "status": "WARN",
                "detail": (
                    f"cannot parse {codex_file} — fix or remove it, then re-run "
                    "`seahorse setup --harness codex`"
                ),
            }
        )
    elif codex_hooks_installed(codex_file):
        checks.append(
            {
                "check": "codex_hooks",
                "status": "OK",
                "detail": f"installed in {codex_file}",
            }
        )
    else:
        checks.append(
            {
                "check": "codex_hooks",
                "status": "WARN",
                "detail": (
                    f"seahorse capture hooks missing from {codex_file}; "
                    "run `seahorse setup --harness codex`"
                ),
            }
        )

    from seahorse.cli.skill_install import SKILL_NAMES, skill_path, skill_state

    states = {name: skill_state(name) for name in SKILL_NAMES}
    if all(state == "ours" for state in states.values()):
        checks.append(
            {
                "check": "skills_installed",
                "status": "OK",
                "detail": f"installed ({', '.join(SKILL_NAMES)})",
            }
        )
    else:
        parts = []
        for name in SKILL_NAMES:
            state = states[name]
            if state == "ours":
                parts.append(f"{name}: installed")
            elif state == "foreign":
                parts.append(
                    f"{name}: foreign SKILL.md at {skill_path(name)} — not repaired"
                )
            else:
                parts.append(f"{name}: missing")
        foreign = [n for n, s in states.items() if s == "foreign"]
        detail = "; ".join(parts) + "; run `seahorse setup`"
        if foreign:
            detail += (
                ", and remove or merge the foreign SKILL.md(s) manually"
                " (a foreign file is never overwritten)"
            )
        checks.append(
            {"check": "skills_installed", "status": "WARN", "detail": detail}
        )

    from seahorse.cli.credentials import check_permissions

    cred_ok, cred_detail = check_permissions()
    if cred_ok:
        checks.append({"check": "credentials", "status": "OK", "detail": cred_detail})
    else:
        checks.append({"check": "credentials", "status": "WARN", "detail": cred_detail})

    consolidate = config.consolidate
    if consolidate is not None and consolidate.auto_on_stop:
        checks.append(
            {"check": "consolidate", "status": "OK", "detail": "auto_on_stop = true"}
        )
    else:
        checks.append(
            {
                "check": "consolidate",
                "status": "OK",
                "detail": "off (opt-in: seahorse setup --auto-consolidate)",
            }
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

    if fix:
        actionable = [
            c["check"]
            for c in checks
            if c["status"] in ("WARN", "FAIL") and _repairable(c["check"])
        ]
        if actionable:
            from seahorse.cli.onboarding import repair_steps_for

            for step in repair_steps_for(actionable, vault=config.vault):
                try:
                    detail = step.run()
                except Exception as exc:  # noqa: BLE001 — a failed repair is a report, not a crash
                    detail = f"error: {exc}"
                status = "FAIL" if detail.startswith("error:") else "OK"
                checks.append(
                    {"check": f"fix:{step.check}", "status": status, "detail": detail}
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
