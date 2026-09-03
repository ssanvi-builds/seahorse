"""One-command onboarding: ``seahorse setup`` as a full-stack orchestrator.

The contract (ADR: one command, everything configured, exit 0 always):

1. Vault bootstrapped (missing config → factory default, portable default
   dir when nothing resolves without a TTY).
2. DB created eagerly (migrations applied — no cold-start surprise on the
   first capture).
3. ``[observe]`` + ``[materialize]`` config written (idempotent).
4. ``[consolidate]`` config written when ``--auto-consolidate``.
5. Observer hooks merged into Claude Code settings (+ consolidate-on-stop
   hook when opted in).
6. Global pointer written.
7. Observer started (already running = fine).
8. MCP server registered user-scope (``--no-mcp`` to skip).
9. Agent instructions block installed (``--no-agent-instructions`` to skip).
10. LLM provider detected + self-test-gated (``--skip-llm`` to skip).
11. Embeddings model warmed ONLY with ``--warm-embeddings`` (~235MB download
    needs explicit consent — never in a non-TTY default path).
12. Doctor-style summary printed. Individual step failures degrade to WARN
    lines — the command itself never fails the caller (a hook path calls it).

``repair_steps_for`` maps doctor check names to the repair callables —
``seahorse doctor --fix`` runs them (dependency direction: doctor imports
onboarding, never the reverse).
"""

from __future__ import annotations

import io
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from seahorse.cli.config import (
    ConsolidateConfig,
    is_initialized,
    load_config,
    write_consolidate_config,
)
from seahorse.cli.output import OutputFormat

# A step reports one of these states.
_OK = "OK"
_WARN = "WARN"
_SKIP = "SKIP"


def run_full_setup(
    vault: Path,
    *,
    settings_path: Path | str | None = None,
    fmt: OutputFormat = "human",
    out: TextIO,
    no_mcp: bool = False,
    no_agent_instructions: bool = False,
    skip_llm: bool = False,
    warm_embeddings: bool = False,
    auto_consolidate: bool = False,
) -> list[dict[str, str]]:
    """Run the whole onboarding; return the summary checks (never raises).

    Every step is independent: a failure marks that step WARN and carries on
    (a partially-configured machine with an actionable message beats a
    crashed half-setup).
    """
    from seahorse.cli.agent_instructions import install_agent_instructions
    from seahorse.cli.errors import CliObserverRunning
    from seahorse.cli.provider_bootstrap import bootstrap_llm_provider
    from seahorse.cli.setup import (
        merge_consolidate_hook,
        run_setup,
    )
    from seahorse.observe.cli import run_observe_start

    vault = vault.expanduser().resolve()
    if not is_initialized(vault):
        from seahorse.cli.setup import _bootstrap_vault

        _bootstrap_vault(vault)
    cfg = load_config(vault)

    checks: list[dict[str, str]] = []

    def step(name: str, run: Callable[[], str]) -> None:
        try:
            detail = run()
            checks.append({"check": name, "status": _OK, "detail": detail})
        except Exception as exc:  # noqa: BLE001 — a step failure is a WARN, never a crash
            checks.append({"check": name, "status": _WARN, "detail": f"error: {exc}"})

    def _db() -> str:
        from seahorse.cli.vault_ops import run_migrate

        buf = io.StringIO()
        run_migrate(cfg, up_to=None, fmt="json", out=buf)
        return "schema applied"

    def _config_and_hooks() -> str:
        buf = io.StringIO()
        run_setup(
            vault,
            settings_path=settings_path,
            fmt="human",
            out=buf,
            auto_consolidate=auto_consolidate,
        )
        return "hooks + [observe] + [materialize] config installed"

    def _observer() -> str:
        try:
            run_observe_start(load_config(vault), fmt="json", out=io.StringIO())
            return "started"
        except CliObserverRunning as exc:
            return f"already running (pid {exc.pid})"
        except Exception as exc:  # noqa: BLE001 — hook path must never raise
            return f"not started ({exc}) — auto-starts on the next session"

    def _mcp() -> str:
        ok, detail = _register_mcp()
        if not ok:
            raise RuntimeError(detail)
        return detail

    def _instructions() -> str:
        ok, detail = install_agent_instructions()
        if not ok:
            raise RuntimeError(detail)
        return detail

    def _llm() -> str:
        decision = bootstrap_llm_provider(vault, out=out)
        if decision.primary is None:
            return decision.detail
        return f"[llm] written — primary {decision.primary} ({decision.detail})"

    def _consolidate() -> str:
        write_consolidate_config(vault, ConsolidateConfig(auto_on_stop=True))
        settings = Path(settings_path) if settings_path else _default_settings_path()
        merge_consolidate_hook(
            settings,
            hook_command=f"{sys.executable} -m seahorse.cli.app consolidate --auto",
        )
        return "consolidate --auto on Stop ([consolidate] auto_on_stop = true)"

    step("vault", lambda: str(vault))
    step("db", _db)
    step("capture", _config_and_hooks)
    step("observer", _observer)
    if auto_consolidate:
        step("consolidate", _consolidate)
    if not no_mcp:
        step("mcp", _mcp)
    else:
        checks.append({"check": "mcp", "status": _SKIP, "detail": "--no-mcp"})
    if not no_agent_instructions:
        step("agent_instructions", _instructions)
    else:
        checks.append(
            {"check": "agent_instructions", "status": _SKIP, "detail": "--no-agent-instructions"}
        )
    if not skip_llm:
        step("llm", _llm)
    else:
        checks.append({"check": "llm", "status": _SKIP, "detail": "--skip-llm"})
    if warm_embeddings:
        step("embeddings", lambda: _warm_embeddings())
    else:
        checks.append(
            {
                "check": "embeddings",
                "status": _SKIP,
                "detail": "pass --warm-embeddings to pre-download the model (~235MB)",
            }
        )

    _render_summary(checks, fmt=fmt, out=out)
    return checks


def _register_mcp() -> tuple[bool, str]:
    """Import-lazy wrapper so tests can monkeypatch the real registrer."""
    from seahorse.cli.mcp_register import register_mcp

    return register_mcp()


def _default_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _warm_embeddings() -> str:
    """Build the embedder + embed one text (the ~235MB download happens here).

    Only called when the user passed --warm-embeddings: the download is
    explicit consent.
    """
    try:
        import fastembed  # type: ignore[import-not-found]  # noqa: F401 — the 'embeddings' extra
    except ImportError:
        return "extra 'embeddings' not installed — `uv sync --extra embeddings`"
    from seahorse.embeddings.fastembed_backend import model_cached

    if model_cached():
        return "already cached"
    from seahorse.embeddings.fastembed_backend import build_fastembed_embedder
    from seahorse.embeddings.query_adapter import run_coroutine

    run_coroutine(build_fastembed_embedder().embed(["warmup"], "passage"))
    return "model downloaded and warm"


def _render_summary(
    checks: list[dict[str, str]], *, fmt: OutputFormat, out: TextIO
) -> None:
    """Doctor-style summary. Exit 0 always — the payload carries the state."""
    import json

    healthy = all(c["status"] != _WARN for c in checks)
    payload = {"command": "setup", "healthy": healthy, "checks": checks}
    if fmt == "json":
        out.write(json.dumps(payload, indent=2) + "\n")
        return
    lines = "".join(
        f"  {c['status']:<4} {c['check']:<20} {c['detail']}\n" for c in checks
    )
    out.write(
        "seahorse setup complete\n"
        + lines
        + ("\n✓ everything configured" if healthy else "\n⚠ some steps need attention (see WARN)\n")
    )


@dataclass(frozen=True)
class RepairStep:
    """One doctor --fix action: what it fixes and the callable that does it."""

    check: str
    detail: str
    run: Callable[[], str]


def repair_steps_for(
    check_names: list[str],
    *,
    vault: Path,
    settings_path: Path | str | None = None,
) -> list[RepairStep]:
    """Map doctor check names to their repair actions (in a stable order).

    Unknown names are ignored — ``--fix`` only repairs checks Seahorse owns.
    """
    from seahorse.cli.agent_instructions import install_agent_instructions
    from seahorse.cli.config import (
        write_consolidate_config,
        write_global_pointer,
    )
    from seahorse.cli.mcp_register import register_mcp
    from seahorse.cli.setup import merge_consolidate_hook, merge_hooks, write_observe_config

    steps: list[RepairStep] = []
    settings = (
        Path(settings_path) if settings_path is not None else _default_settings_path()
    )

    def _hooks() -> str:
        write_observe_config(vault)
        merge_hooks(settings, hook_command=f"{sys.executable} -m seahorse.cli.app observe event")
        return f"observer hooks merged into {settings}"

    def _consolidate() -> str:
        write_consolidate_config(vault, ConsolidateConfig(auto_on_stop=True))
        merge_consolidate_hook(
            settings,
            hook_command=f"{sys.executable} -m seahorse.cli.app consolidate --auto",
        )
        return "consolidate-on-stop hook installed"

    def _mcp() -> str:
        ok, detail = register_mcp()
        if not ok:
            raise RuntimeError(detail)
        return detail

    def _instructions() -> str:
        ok, detail = install_agent_instructions()
        if not ok:
            raise RuntimeError(detail)
        return detail

    def _db() -> str:
        from seahorse.cli.vault_ops import run_migrate

        run_migrate(load_config(vault), up_to=None, fmt="json", out=io.StringIO())
        return "schema applied"

    def _pointer() -> str:
        write_global_pointer(vault)
        return f"pointer -> {vault}"

    mapping: dict[str, tuple[str, Callable[[], str]]] = {
        "claude_hooks": ("observer hooks merged", _hooks),
        "consolidate": ("consolidate-on-stop installed", _consolidate),
        "mcp_registered": ("MCP server registered", _mcp),
        "agent_instructions": ("agent instructions installed", _instructions),
        "db": ("schema applied", _db),
        "global_pointer": ("global pointer written", _pointer),
    }
    for name in check_names:
        if name in mapping:
            detail, run = mapping[name]
            steps.append(RepairStep(check=name, detail=detail, run=run))
    return steps


__all__ = ["RepairStep", "repair_steps_for", "run_full_setup"]