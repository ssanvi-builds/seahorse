"""Typer application + ``main()`` entrypoint for the Seahorse CLI (#14).

This is the parser/wiring layer only — every command body is a thin callback
that resolves the facade (via the composition root ``build_facade``) and calls
the parser-agnostic logic in ``primitives`` / ``management``. No domain logic
lives here (delegation purity, f5-14 §1).

Error model (f5-14 §3.3): ``main()`` is the single exception-translation seam.
Typer usage errors (vendored click ``ClickException``) → their ``.exit_code``
(2 for usage). Our ``CliError`` / the facade's ``SeahorseError`` / the engine's
``EngineError`` / #8 ``Cat B`` exceptions → ``exit_codes.translate()``. A
structured payload is written to stderr (JSON when ``--json``/``--format json``,
human otherwise). Nothing is swallowed — uncatalogued ``Exception`` → exit 1.

The facade/storage lifecycle: the global callback builds a ``CliContext`` per
invocation and pushes it on a module-level stack; ``main()`` pops + closes the
storage in ``finally`` so SQLite connections release (tests use ``tmp_path``).
"""

from __future__ import annotations

# Typer's idiomatic `typer.Option/Argument` in defaults trips B008; it is the
# required pattern for Typer command signatures, so silence it file-wide.
# ruff: noqa: B008
import io
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TextIO, cast

import typer

# Vendored click exception base (typer 0.27 ships its own click). Catching the
# base covers UsageError / NoSuchOption / BadParameter / MissingParameter.
from typer._click.exceptions import ClickException

from seahorse.cli.config import (
    SeahorseConfig,
    load_config,
    resolve_vault,
)
from seahorse.cli.exit_codes import EXIT_SUCCESS, translate
from seahorse.cli.management import run_init, run_reserved, run_status, run_uuid7
from seahorse.cli.output import OutputFormat
from seahorse.cli.primitives import (
    run_expire_revalidate,
    run_forget,
    run_improve,
    run_recall,
    run_recall_full,
    run_recall_timeline,
    run_remember,
)
from seahorse.cli.vault_ops import run_index_rebuild, run_inspect, run_migrate
from seahorse.contracts.index import PIT_KIND_VALUES
from seahorse.facade.facade import MemoryFacade
from seahorse.facade.factory import build_facade
from seahorse.facade.types import FacadeConfig
from seahorse.persistence.storage import Storage

# ---------------------------------------------------------------------------
# Per-invocation state (facade lifecycle).
# ---------------------------------------------------------------------------


@dataclass
class CliContext:
    """Per-invocation globals + lazy facade/storage build.

    Built by the Typer callback from ``--vault``/``--config``/``--format`` and
    pushed on ``_STATE_STACK`` so ``main()`` can close the storage in
    ``finally``. The facade is built lazily — commands that do not need one
    (``init`` / ``uuid7`` / ``status``) never open the SQLite connection.
    """

    vault: Path | None = None
    config: Path | None = None
    fmt: OutputFormat = "human"
    quiet: bool = False
    _facade: MemoryFacade | None = field(default=None, repr=False)
    _storage: Storage | None = field(default=None, repr=False)
    _resolved_config: SeahorseConfig | None = field(default=None, repr=False)

    def resolved_config(self) -> SeahorseConfig:
        if self._resolved_config is None:
            vault = resolve_vault(self.vault)
            self._resolved_config = load_config(vault, explicit_config=self.config)
        return self._resolved_config

    def facade(self) -> MemoryFacade:
        if self._facade is None:
            cfg = self.resolved_config()
            # config.load_config validates mode ∈ {skip, llm}; narrow for mypy.
            mode = cast("Literal['skip', 'llm']", cfg.default_extraction_mode)
            facade, storage = build_facade(
                cfg.db_path,
                config=FacadeConfig(default_extraction_mode=mode, top_k=cfg.top_k),
            )
            self._facade = facade
            self._storage = storage
        return self._facade

    def close(self) -> None:
        if self._storage is not None:
            self._storage.close()
            self._storage = None
            self._facade = None


# Stack so nested ``main()`` invocations (tests) do not clobber each other.
_STATE_STACK: list[CliContext] = []

# The argv of the current ``main()`` invocation (set before ``app()`` runs) so
# the global callback can locate the subcommand without reading ``sys.argv``
# (which ``main(argv)`` may not match in tests / embedded callers).
_CURRENT_ARGV: list[str] = []


# ---------------------------------------------------------------------------
# Typer app + global callback.
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="seahorse",
    help="Seahorse — persistent, self-evolving memory for LLM agents (MVP-0).",
    no_args_is_help=True,
    add_completion=False,
)


def _fmt_from(format_opt: str, json_flag: bool, jsonl_flag: bool) -> OutputFormat:
    if json_flag and jsonl_flag:
        raise typer.BadParameter("--json and --jsonl are mutually exclusive.")
    if json_flag:
        return "json"
    if jsonl_flag:
        return "jsonl"
    if format_opt not in ("human", "json", "jsonl"):
        # Typer's Enum/choices would catch this, but guard defensively.
        raise typer.BadParameter(f"--format must be human|json|jsonl, got {format_opt!r}")
    return format_opt  # type: ignore[return-value]


def _out(ctx: typer.Context) -> TextIO:
    """stdout sink for command output — discarded under ``--quiet``.

    ``--quiet`` (f5-14 §3.4) suppresses stdout for CI/scripts; errors still go
    to stderr via ``main()``'s translation seam. A throwaway ``StringIO`` is the
    discard sink so the run_* writers need no quiet-awareness.
    """
    return io.StringIO() if ctx.obj.quiet else sys.stdout


# Commands whose first embed triggers the one-time model download.
_EMBED_COMMANDS = {"remember", "improve", "recall"}
# Global flags that consume a value (skip their argument when locating the
# subcommand in ``sys.argv``).
_GLOBAL_VALUE_FLAGS = {"--vault", "--config", "--format", "-f"}


def _first_command(argv: list[str]) -> str | None:
    """First non-flag token in ``argv``, skipping global value flags."""
    skip_next = False
    for arg in argv:
        if skip_next:
            skip_next = False
            continue
        if arg in _GLOBAL_VALUE_FLAGS:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        return arg
    return None


def _announce_model_download(ctx: CliContext) -> None:
    """Announce the one-time mE5-small download (human mode, embed commands).

    The embedder pulls ~235MB lazily on the first embed; without a heads-up the
    first ``remember``/``recall`` looks hung. Announce only in human output, only
    for commands that embed, and only when the model isn't cached yet. Library
    code stays silent (the JSON contract owns stderr; this is stdout, human).
    """
    if ctx.quiet or ctx.fmt != "human":
        return
    if _first_command(_CURRENT_ARGV) not in _EMBED_COMMANDS:
        return
    try:
        from seahorse.embeddings.fastembed_backend import model_cached

        if model_cached():
            return
    except Exception:  # noqa: BLE001 — a notice must never block the command
        return
    sys.stdout.write(
        "First run: downloading mE5-small embedding model (~235MB) from "
        "HuggingFace — one-time, then cached.\n"
    )
    sys.stdout.flush()


@app.callback()
def _callback(
    ctx: typer.Context,
    vault: Path | None = typer.Option(None, "--vault", help="Vault root dir (default: discover)."),
    config: Path | None = typer.Option(None, "--config", help="Explicit seahorse.toml path."),
    format: str = typer.Option("human", "--format", "-f", help="human | json | jsonl."),
    json: bool = typer.Option(False, "--json", help="Shortcut for --format json."),
    jsonl: bool = typer.Option(
        False, "--jsonl", help="Shortcut for --format jsonl (one obj/line)."
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress stdout (errors still on stderr)."
    ),
) -> None:
    """Seahorse CLI — memory primitives + vault management."""
    ctx.obj = CliContext(
        vault=vault,
        config=config,
        fmt=_fmt_from(format, json, jsonl),
        quiet=quiet,
    )
    _STATE_STACK.append(ctx.obj)
    _announce_model_download(ctx.obj)


# ---------------------------------------------------------------------------
# Memory-native primitives.
# ---------------------------------------------------------------------------


@app.command()
def remember(
    ctx: typer.Context,
    body: str = typer.Argument(..., help="Fact body (UTF-8, <= BODY_MAX_CHARS)."),
    source_type: str = typer.Option(
        "human", "--source-type", help="agent | human | importer | system."
    ),
    agent_id: str | None = typer.Option(None, "--agent-id"),
    session_id: str | None = typer.Option(None, "--session-id"),
    valid_at: str | None = typer.Option(
        None, "--valid-at", help="ISO-8601 valid_at (caller authority)."
    ),
    cognitive_type: str | None = typer.Option(None, "--cognitive-type"),
    title: str | None = typer.Option(None, "--title"),
    skip_extraction: bool | None = typer.Option(None, "--skip-extraction/--extract"),
    extraction_mode: str | None = typer.Option(None, "--extraction-mode", help="skip | llm."),
) -> None:
    """Remember a fact (clean creation, ADR-09)."""
    run_remember(
        ctx.obj.facade(),
        body=body,
        source_type=source_type,
        agent_id=agent_id,
        session_id=session_id,
        valid_at=valid_at,
        cognitive_type=cognitive_type,
        title=title,
        skip_extraction=skip_extraction,
        extraction_mode=extraction_mode,
        fmt=ctx.obj.fmt,
        out=_out(ctx),
    )


@app.command(name="recall")
def recall_cmd(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Recall query (INDEX level, vigente listing)."),
    top_k: int = typer.Option(10, "--top-k", "-k"),
    cognitive_type: str | None = typer.Option(None, "--cognitive-type"),
    subject_filter: str | None = typer.Option(None, "--subject-filter"),
    pit_kind: str | None = typer.Option(
        None, "--pit-kind", help=f"PIT kind: {' | '.join(sorted(PIT_KIND_VALUES))}."
    ),
    pit_t: str | None = typer.Option(None, "--pit-t", help="ISO-8601 timestamp for the PIT."),
) -> None:
    """Recall the INDEX level (MVP-0 vigente listing)."""
    run_recall(
        ctx.obj.facade(),
        query=query,
        top_k=top_k,
        cognitive_type=cognitive_type,
        subject_filter=subject_filter,
        pit_kind=pit_kind,
        pit_t=pit_t,
        fmt=ctx.obj.fmt,
        out=_out(ctx),
    )


@app.command(name="recall-timeline")
def recall_timeline_cmd(
    ctx: typer.Context,
    anchor_ep_id: str = typer.Argument(..., help="Anchor episode id."),
    axis: str = typer.Option("supersedes_chain", "--axis"),
    pit_kind: str | None = typer.Option(
        None, "--pit-kind", help=f"PIT kind: {' | '.join(sorted(PIT_KIND_VALUES))}."
    ),
    pit_t: str | None = typer.Option(None, "--pit-t"),
) -> None:
    """Recall the TIMELINE level (anchor-based, no body)."""
    run_recall_timeline(
        ctx.obj.facade(),
        anchor_ep_id=anchor_ep_id,
        axis=axis,
        pit_kind=pit_kind,
        pit_t=pit_t,
        fmt=ctx.obj.fmt,
        out=_out(ctx),
    )


@app.command(name="recall-full")
def recall_full_cmd(
    ctx: typer.Context,
    ep_ids: list[str] = typer.Argument(..., help="Episode ids (<= MAX_FULL_BATCH)."),
    pit_kind: str | None = typer.Option(
        None, "--pit-kind", help=f"PIT kind: {' | '.join(sorted(PIT_KIND_VALUES))}."
    ),
    pit_t: str | None = typer.Option(None, "--pit-t"),
) -> None:
    """Recall the FULL level (hydrates body)."""
    run_recall_full(
        ctx.obj.facade(),
        ep_ids=ep_ids,
        pit_kind=pit_kind,
        pit_t=pit_t,
        fmt=ctx.obj.fmt,
        out=_out(ctx),
    )


@app.command()
def improve(
    ctx: typer.Context,
    ep_id: str = typer.Argument(..., help="Episode to supersede."),
    new_body: str = typer.Argument(..., help="Corrected body."),
    reason: str = typer.Option("correction", "--reason"),
    source_type: str = typer.Option("human", "--source-type"),
    agent_id: str | None = typer.Option(None, "--agent-id"),
    valid_at: str | None = typer.Option(None, "--valid-at"),
) -> None:
    """Improve a fact (editorial correction: invalidate + append)."""
    run_improve(
        ctx.obj.facade(),
        ep_id=ep_id,
        new_body=new_body,
        reason=reason,
        source_type=source_type,
        agent_id=agent_id,
        valid_at=valid_at,
        fmt=ctx.obj.fmt,
        out=_out(ctx),
    )


@app.command()
def forget(
    ctx: typer.Context,
    ep_id: str = typer.Argument(..., help="Episode to soft-delete."),
    reason: str = typer.Option(..., "--reason", help="Forget reason (required)."),
    source_type: str = typer.Option("human", "--source-type"),
    agent_id: str | None = typer.Option(None, "--agent-id"),
    now: str | None = typer.Option(None, "--now", help="Override the clock (ISO-8601)."),
) -> None:
    """Forget a fact (bi-temporal soft-delete, ADR-07)."""
    run_forget(
        ctx.obj.facade(),
        ep_id=ep_id,
        reason=reason,
        source_type=source_type,
        agent_id=agent_id,
        now=now,
        fmt=ctx.obj.fmt,
        out=_out(ctx),
    )


@app.command()
def expire(
    ctx: typer.Context,
    ep_id: str = typer.Argument(..., help="Episode to decay (reserved in MVP-0)."),
) -> None:
    """Decay a fact (set expired_at) — RESERVED in MVP-0 (SO-14-05)."""
    run_expire_revalidate("expire")


@app.command()
def revalidate(
    ctx: typer.Context,
    ep_id: str = typer.Argument(..., help="Episode to revalidate (reserved in MVP-0)."),
) -> None:
    """Revalidate a decayed fact — RESERVED in MVP-0 (SO-14-05)."""
    run_expire_revalidate("revalidate")


@app.command()
def mcp(ctx: typer.Context) -> None:
    """Run the stdio MCP server (io.seahorse.memory/v1) for this vault.

    The agent surface: newline-delimited JSON-RPC 2.0 over stdin/stdout. Shares
    the vault/db resolution + storage lifecycle of every other command (the
    facade comes from ``ctx.obj.facade()`` and is closed by ``main()``'s
    ``finally``). Writes JSON-RPC directly to ``sys.stdout`` regardless of
    ``--quiet`` (the protocol owns stdout). Mirrors the ``seahorse-mcp`` console
    script and ``python -m seahorse.mcp`` — same ``serve``, same profile.
    """
    # Long-lived server process: restore WARNING so the embedder self-test /
    # hybrid-degrade diagnostics reach the operator (main() suppresses them for
    # the one-shot CLI; stderr is not a protocol channel here).
    logging.getLogger("seahorse").setLevel(logging.WARNING)
    from seahorse.mcp.profile import serve

    serve(ctx.obj.facade(), stdin=sys.stdin, stdout=sys.stdout)


# ---------------------------------------------------------------------------
# Management commands.
# ---------------------------------------------------------------------------


@app.command()
def init(
    ctx: typer.Context,
    vault: Path = typer.Argument(..., help="Vault root dir to bootstrap."),
) -> None:
    """Bootstrap a Seahorse vault (.seahorse/seahorse.toml)."""
    run_init(vault, fmt=ctx.obj.fmt, out=_out(ctx))


@app.command()
def status(ctx: typer.Context) -> None:
    """Show the resolved vault / db / config snapshot."""
    run_status(ctx.obj.resolved_config(), fmt=ctx.obj.fmt, out=_out(ctx))


@app.command()
def uuid7(ctx: typer.Context) -> None:
    """Emit a fresh UUIDv7 (RFC 9562)."""
    run_uuid7(fmt=ctx.obj.fmt, out=_out(ctx))


@app.command(name="migrate")
def migrate_cmd(
    ctx: typer.Context,
    up_to: int | None = typer.Option(
        None, "--up-to", help="Cap the highest migration version to apply (inclusive)."
    ),
) -> None:
    """Apply SCHEMA migrations to the sidecar DB (DDL 001–009)."""
    run_migrate(ctx.obj.resolved_config(), up_to=up_to, fmt=ctx.obj.fmt, out=_out(ctx))


@app.command()
def inspect(ctx: typer.Context) -> None:
    """Read-only sidecar snapshot (schema_version, counts, vigentes vs activos-ahora)."""
    run_inspect(ctx.obj.resolved_config(), fmt=ctx.obj.fmt, out=_out(ctx))


@app.command(name="vigentes")
def vigentes_cmd(ctx: typer.Context) -> None:
    """Full vigente set with freshness — RESERVED in MVP-0 (MVP-1)."""
    run_reserved("vigentes")


@app.command(name="activos-ahora")
def activos_ahora_cmd(ctx: typer.Context) -> None:
    """Decay-aware active set — RESERVED (mediano, needs expire)."""
    run_reserved("activos-ahora")


# ``index`` group: ``index rebuild`` / ``index verify`` (both reserved).
index_app = typer.Typer(help="Index operations (rebuild / verify).")
app.add_typer(index_app, name="index")


@index_app.command(name="rebuild")
def index_rebuild_cmd(ctx: typer.Context) -> None:
    """Rebuild the sidecar index from the vault's .md notes (clear-then-rebuild)."""
    run_index_rebuild(ctx.obj.resolved_config(), fmt=ctx.obj.fmt, out=_out(ctx))


@index_app.command(name="verify")
def index_verify_cmd(ctx: typer.Context) -> None:
    """Verify index integrity — RESERVED in MVP-0."""
    run_reserved("index-verify")


# ---------------------------------------------------------------------------
# Exception translation + entrypoint.
# ---------------------------------------------------------------------------


def _emit_error(exc: BaseException, fmt: OutputFormat, err: TextIO) -> int:
    """Translate ``exc`` to an exit code and write a structured payload to ``err``."""
    code, info = translate(exc)
    if fmt in ("json", "jsonl"):
        import json

        # f5-14 §3.3: machine errors carry an {"error": {...}} envelope so
        # `jq '.error.seahorse_code'` works for both Cat A and Cat B.
        err.write(json.dumps({"error": info}, ensure_ascii=False) + "\n")
    else:
        # Human: a stable header line + the detail. CliError already formats
        # ``name: detail``; domain errors get ``seahorse_code``/``exception_class``.
        label = (
            info.get("seahorse_code")
            or info.get("exception_class")
            or info.get("cli_code")
            or "ERROR"
        )
        err.write(f"seahorse: {label}: {info.get('detail', str(exc))}\n")
        if "component" in info:
            err.write(f"  component: {info['component']}  exit: {code}\n")
    return code


def main(argv: list[str] | None = None) -> int:
    """Entrypoint: run the Typer app, translate exceptions, return exit code.

    ``argv`` defaults to ``sys.argv[1:]``. Returns the process exit code (does
    not call ``sys.exit`` itself — the ``console_scripts`` wrapper does, and
    tests want the int).
    """
    # The CLI's stderr is a structured error channel (human/JSON payloads via
    # exit_codes); library diagnostics must not leak into it. The embedder
    # startup self-test warns on every facade build — in a one-shot CLI that
    # pollutes stderr and breaks the JSON contract. Suppress seahorse logs here;
    # the long-lived MCP command re-enables WARNING so a server operator still
    # sees the prefix-drift signal at boot.
    logging.getLogger("seahorse").setLevel(logging.ERROR)
    args = sys.argv[1:] if argv is None else argv
    _CURRENT_ARGV[:] = list(args)
    fmt: OutputFormat = "human"
    try:
        app(args, standalone_mode=False, prog_name="seahorse")
        return EXIT_SUCCESS
    except ClickException as exc:
        # Typer usage/argparse errors (vendored click). Let it format its own
        # message; honor its exit_code (2 for usage).
        err = sys.stderr
        err.write(exc.format_message() + "\n")
        return int(exc.exit_code or 2)
    except SystemExit as exc:
        # Defensive: Typer should not raise SystemExit under standalone_mode=False,
        # but if it does, honor the code.
        return int(exc.code) if isinstance(exc.code, int) else 1
    except BaseException as exc:  # noqa: BLE001 — translate is the fail-loud seam
        # Determine fmt from the current invocation's CliContext if present.
        ctx = _STATE_STACK[-1] if _STATE_STACK else None
        fmt = ctx.fmt if ctx is not None else "human"
        return _emit_error(exc, fmt, sys.stderr)
    finally:
        # Pop this invocation's CliContext + release the SQLite storage.
        if _STATE_STACK:
            ctx = _STATE_STACK.pop()
            ctx.close()


def console_main() -> None:
    """``console_scripts`` entrypoint (``seahorse``)."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    console_main()


__all__ = ["app", "main", "console_main", "CliContext"]