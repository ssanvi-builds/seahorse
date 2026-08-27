"""Typer application + ``main()`` entrypoint for the Seahorse CLI.

This is the parser/wiring layer only — every command body is a thin callback
that resolves the facade (via the composition root ``build_facade``) and calls
the parser-agnostic logic in ``primitives`` / ``management``. No domain logic
lives here (delegation purity).

Error model: ``main()`` is the single exception-translation seam. Typer usage
errors (vendored click ``ClickException``) → their ``.exit_code``
(2 for usage). Our ``CliError`` / the facade's ``SeahorseError`` / the engine's
``EngineError`` / the disclosure layer's ``Cat B`` exceptions →
``exit_codes.translate()``. A structured payload is written to stderr (JSON
when ``--json``/``--format json``, human otherwise). Nothing is swallowed —
uncatalogued ``Exception`` → exit 1.

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
from seahorse.cli.doctor import run_doctor
from seahorse.cli.exit_codes import EXIT_SUCCESS, translate
from seahorse.cli.importer import run_import
from seahorse.cli.management import run_init, run_reserved, run_status, run_uuid7
from seahorse.cli.output import OutputFormat
from seahorse.cli.primitives import (
    run_audit_log,
    run_consolidate,
    run_context,
    run_expire_revalidate,
    run_follow_supersedes_chain,
    run_forget,
    run_freshness_view,
    run_improve,
    run_recall,
    run_recall_full,
    run_recall_timeline,
    run_remember,
)
from seahorse.cli.skills import (
    run_skill_add,
    run_skill_list,
    run_skill_search,
    run_skill_show,
)
from seahorse.cli.vault_ops import (
    run_frontmatter_migrate,
    run_index_rebuild,
    run_inspect,
    run_migrate,
)
from seahorse.cli.viewer import run_view
from seahorse.contracts.index import PIT_KIND_VALUES
from seahorse.facade.facade import MemoryFacade
from seahorse.facade.factory import build_facade
from seahorse.facade.types import FacadeConfig
from seahorse.llm import LLMClient, RoleRoute
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
    verbose: bool = False
    _facade: MemoryFacade | None = field(default=None, repr=False)
    _storage: Storage | None = field(default=None, repr=False)
    _resolved_config: SeahorseConfig | None = field(default=None, repr=False)
    _llm_client: LLMClient | None = field(default=None, repr=False)
    _llm_client_ready: bool = field(default=False, repr=False)

    def resolved_config(self) -> SeahorseConfig:
        if self._resolved_config is None:
            vault = resolve_vault(self.vault)
            self._resolved_config = load_config(vault, explicit_config=self.config)
        return self._resolved_config

    def llm_client(self) -> LLMClient | None:
        """The write-path LLM client (built once from the ``[llm]`` config).

        ``None`` (no ``[llm]`` section) keeps the honest llm→skip degrade. The
        facade and the ``consolidate --synthesis llm`` path share this client.
        """
        if not self._llm_client_ready:
            self._llm_client = self._build_llm_client(self.resolved_config())
            self._llm_client_ready = True
        return self._llm_client

    def facade(self) -> MemoryFacade:
        if self._facade is None:
            cfg = self.resolved_config()
            # config.load_config validates mode ∈ {skip, llm}; narrow for mypy.
            mode = cast("Literal['skip', 'llm']", cfg.default_extraction_mode)
            facade, storage = build_facade(
                cfg.db_path,
                config=FacadeConfig(default_extraction_mode=mode, top_k=cfg.top_k),
                llm_client=self.llm_client(),
            )
            self._facade = facade
            self._storage = storage
        return self._facade

    @staticmethod
    def _build_llm_client(cfg: SeahorseConfig) -> LLMClient | None:
        """Build the write-path LLM client from the ``[llm]`` config.

        ``None`` (no ``[llm]`` section) keeps the honest llm→skip degrade. A
        configured section builds the ``LiteLLMBackend`` over the extraction
        route; a missing ``llm`` extra surfaces later as a setup hint (the
        backend degrades with "install seahorse[llm]"), never a crash.
        """
        if cfg.llm is None:
            return None
        from seahorse.llm.lite_llm_backend import LiteLLMBackend

        return LiteLLMBackend(
            route=RoleRoute(
                primary=cfg.llm.primary,
                secondary=cfg.llm.secondary,
                tertiary=cfg.llm.tertiary,
            ),
            timeout_s=cfg.llm.timeout_s,
        )

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
    help="Seahorse — persistent, self-evolving memory for LLM agents.",
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

    ``--quiet`` suppresses stdout for CI/scripts; errors still go to stderr via
    ``main()``'s translation seam. A throwaway ``StringIO`` is the discard sink
    so the run_* writers need no quiet-awareness.
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
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Human output with per-operation timing (stderr)."
    ),
) -> None:
    """Seahorse CLI — memory primitives + vault management."""
    ctx.obj = CliContext(
        vault=vault,
        config=config,
        fmt=_fmt_from(format, json, jsonl),
        quiet=quiet,
        verbose=verbose,
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
    summary: str | None = typer.Option(
        None, "--summary", help="Editorial summary (default: first sentence of body)."
    ),
    skip_extraction: bool | None = typer.Option(None, "--skip-extraction/--extract"),
    extraction_mode: str | None = typer.Option(None, "--extraction-mode", help="skip | llm."),
) -> None:
    """Remember a fact (clean creation, near-zero cost)."""
    run_remember(
        ctx.obj.facade(),
        body=body,
        source_type=source_type,
        agent_id=agent_id,
        session_id=session_id,
        valid_at=valid_at,
        cognitive_type=cognitive_type,
        title=title,
        summary=summary,
        skip_extraction=skip_extraction,
        extraction_mode=extraction_mode,
        fmt=ctx.obj.fmt,
        out=_out(ctx),
        verbose=ctx.obj.verbose,
    )


@app.command(name="recall")
def recall_cmd(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Recall query (INDEX level, current-state listing)."),
    top_k: int = typer.Option(10, "--top-k", "-k"),
    cognitive_type: str | None = typer.Option(None, "--cognitive-type"),
    subject_filter: str | None = typer.Option(None, "--subject-filter"),
    pit_kind: str | None = typer.Option(
        None, "--pit-kind", help=f"PIT kind: {' | '.join(sorted(PIT_KIND_VALUES))}."
    ),
    pit_t: str | None = typer.Option(None, "--pit-t", help="ISO-8601 timestamp for the PIT."),
) -> None:
    """Recall the INDEX level (current-state listing)."""
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
        verbose=ctx.obj.verbose,
    )


@app.command()
def context(ctx: typer.Context) -> None:
    """Render the memory bootstrap context (SessionStart hook)."""
    run_context(
        ctx.obj.facade(), fmt=ctx.obj.fmt, out=_out(ctx), verbose=ctx.obj.verbose
    )


@app.command()
def consolidate(
    ctx: typer.Context,
    synthesis: str = typer.Option("skip", "--synthesis", help="skip | llm."),
    supersede: bool = typer.Option(
        False, "--supersede", help="Update existing notes when new episodes arrive (opt-in)."
    ),
) -> None:
    """Distill recurrent episodes into semantic knowledge notes."""
    run_consolidate(
        ctx.obj.facade(),
        fmt=ctx.obj.fmt,
        out=_out(ctx),
        verbose=ctx.obj.verbose,
        synthesis=synthesis,
        llm_client=ctx.obj.llm_client(),
        vault_path=ctx.obj.resolved_config().vault,
        supersede=supersede,
    )


@app.command(name="recall-timeline")
def recall_timeline_cmd(
    ctx: typer.Context,
    anchor_ep_id: str = typer.Argument(..., help="Anchor episode id."),
    axis: str = typer.Option("supersedes_chain", "--axis"),
    hops: int = typer.Option(1, "--hops", help="graph_bfs traversal depth (1-2)."),
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
        hops=hops,
        pit_kind=pit_kind,
        pit_t=pit_t,
        fmt=ctx.obj.fmt,
        out=_out(ctx),
        verbose=ctx.obj.verbose,
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
        verbose=ctx.obj.verbose,
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
        verbose=ctx.obj.verbose,
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
    """Forget a fact (bi-temporal soft-delete)."""
    run_forget(
        ctx.obj.facade(),
        ep_id=ep_id,
        reason=reason,
        source_type=source_type,
        agent_id=agent_id,
        now=now,
        fmt=ctx.obj.fmt,
        out=_out(ctx),
        verbose=ctx.obj.verbose,
    )


@app.command(name="freshness-view")
def freshness_view_cmd(
    ctx: typer.Context,
    ep_id: str = typer.Argument(..., help="Episode id."),
) -> None:
    """Freshness snapshot of an episode (age, stale, pending_ingest)."""
    run_freshness_view(
        ctx.obj.facade(),
        ep_id=ep_id,
        fmt=ctx.obj.fmt,
        out=_out(ctx),
        verbose=ctx.obj.verbose,
    )


@app.command(name="audit-log")
def audit_log_cmd(
    ctx: typer.Context,
    ep_id: str = typer.Argument(..., help="Episode id."),
) -> None:
    """The write-path history of an episode (audit events)."""
    run_audit_log(
        ctx.obj.facade(), ep_id=ep_id, fmt=ctx.obj.fmt, out=_out(ctx), verbose=ctx.obj.verbose
    )


@app.command(name="follow-supersedes-chain")
def follow_supersedes_chain_cmd(
    ctx: typer.Context,
    ep_id: str = typer.Argument(..., help="Episode id."),
) -> None:
    """The version history of an episode (supersedes closure)."""
    run_follow_supersedes_chain(
        ctx.obj.facade(),
        ep_id=ep_id,
        fmt=ctx.obj.fmt,
        out=_out(ctx),
        verbose=ctx.obj.verbose,
    )


@app.command()
def expire(
    ctx: typer.Context,
    ep_id: str = typer.Argument(..., help="Episode to decay (reserved in the current release)."),
) -> None:
    """Decay a fact (set expired_at) — reserved in the current release."""
    run_expire_revalidate("expire")


@app.command()
def revalidate(
    ctx: typer.Context,
    ep_id: str = typer.Argument(
        ..., help="Episode to revalidate (reserved in the current release)."
    ),
) -> None:
    """Revalidate a decayed fact — reserved in the current release."""
    run_expire_revalidate("revalidate")


@app.command(name="import")
def import_cmd(
    ctx: typer.Context,
    source: str | None = typer.Option(
        None, "--source", help="claude-mem DB path (default: ~/.claude-mem/claude-mem.db)."
    ),
    mode: str = typer.Option(
        "dry-run", "--mode", help="dry-run (default) | commit."
    ),
    project: str | None = typer.Option(
        None, "--project", help="Filter observations by project name."
    ),
    output_dir: str | None = typer.Option(
        None, "--output-dir", help="Manifest output dir (default: not persisted)."
    ),
) -> None:
    """Import claude-mem observations as canonical episodes (migration bridge)."""
    run_import(
        ctx.obj.facade(),
        source=source,
        mode=mode,
        project=project,
        output_dir=output_dir,
        fmt=ctx.obj.fmt,
        out=_out(ctx),
    )


# ``observe`` group: ``observe start|stop|status|run`` (the observer).
observe_app = typer.Typer(help="Observer capture layer.")
app.add_typer(observe_app, name="observe")


@observe_app.command("start")
def observe_start_cmd(ctx: typer.Context) -> None:
    """Start the observer as a background process (single-writer)."""
    from seahorse.observe.cli import run_observe_start

    run_observe_start(ctx.obj.resolved_config(), fmt=ctx.obj.fmt, out=_out(ctx))


@observe_app.command("stop")
def observe_stop_cmd(ctx: typer.Context) -> None:
    """Stop the observer (SIGTERM)."""
    from seahorse.observe.cli import run_observe_stop

    run_observe_stop(ctx.obj.resolved_config(), fmt=ctx.obj.fmt, out=_out(ctx))


@observe_app.command("status")
def observe_status_cmd(ctx: typer.Context) -> None:
    """Report whether the observer is running."""
    from seahorse.observe.cli import run_observe_status

    run_observe_status(ctx.obj.resolved_config(), fmt=ctx.obj.fmt, out=_out(ctx))


@observe_app.command("run")
def observe_run_cmd(ctx: typer.Context) -> None:
    """Run the observer in the foreground (endpoint + worker loop)."""
    from seahorse.observe.cli import run_observe_run

    run_observe_run(ctx.obj.resolved_config(), fmt=ctx.obj.fmt, out=_out(ctx))


@observe_app.command("event")
def observe_event_cmd(ctx: typer.Context) -> None:
    """POST a hook event to the observer socket (called by the hooks)."""
    from seahorse.observe.cli import run_observe_event

    run_observe_event(ctx.obj.resolved_config(), fmt=ctx.obj.fmt, out=_out(ctx))


# ``benchmark`` group: ``benchmark run`` / ``benchmark list`` / ``benchmark adapters``.
benchmark_app = typer.Typer(help="LMEB benchmark harness.")
app.add_typer(benchmark_app, name="benchmark")


@benchmark_app.command("run")
def benchmark_run_cmd(
    ctx: typer.Context,
    adapter: str = typer.Option("lmeb", "--adapter", help="Dataset adapter (e.g. lmeb)."),
    dataset_config: str = typer.Option("s", "--config", help="Dataset config (e.g. s)."),
    reader_model: str = typer.Option(
        "ollama/qwen3:1.7b", "--reader-model", help="Reader LLM (t=0, seed=42)."
    ),
    judge_model: str = typer.Option(
        "ollama/qwen2.5:7b", "--judge-model", help="Judge LLM (family-disjoint from reader)."
    ),
    temporal: bool = typer.Option(False, "--temporal", help="Temporal mode (source_type=human)."),
    output_dir: str = typer.Option("benchmark-output", "--output-dir"),
    top_k: int = typer.Option(10, "--top-k", "-k"),
    score_source: str = typer.Option(
        "mvp1_rrf",
        "--score-source",
        help="mvp1_rrf | mvp1_rrf_recency | mvp1_decay | rrf_rerank.",
    ),
    recency_gamma: float | None = typer.Option(
        None,
        "--recency-gamma",
        help="Recency max boost at age 0 (pairs with --recency-half-life).",
    ),
    recency_half_life: float | None = typer.Option(
        None,
        "--recency-half-life",
        help="Recency half-life in days (pairs with --recency-gamma).",
    ),
    decay_half_life: float | None = typer.Option(
        None,
        "--decay-half-life",
        help="Decay half-life in days for all cognitive types (default-OFF when unset).",
    ),
    embed_mode: str = typer.Option(
        "body+summary",
        "--embed-mode",
        help="Passage text to embed: body+summary (default) | body (baseline).",
    ),
    rerank_enable: bool = typer.Option(
        False,
        "--rerank-enable",
        help="Cross-encoder rerank (opt-in, score_source=rrf_rerank).",
    ),
    context_mode: str = typer.Option(
        "summary",
        "--context-mode",
        help=(
            "Reader context representation: summary (default) | body | body_bounded "
            "(the reader-context A/B axis)."
        ),
    ),
) -> None:
    """Run the LMEB benchmark harness (exit 0=Pass / 10=Fail / 3=Tampered)."""
    from seahorse.benchmark.cli import run_benchmark

    code = run_benchmark(
        adapter=adapter,
        dataset_config=dataset_config,
        reader_model=reader_model,
        judge_model=judge_model,
        temporal=temporal,
        output_dir=output_dir,
        top_k=top_k,
        score_source=score_source,
        recency_gamma=recency_gamma,
        recency_half_life=recency_half_life,
        decay_half_life=decay_half_life,
        embed_mode=embed_mode,
        rerank_enable=rerank_enable,
        context_mode=context_mode,
    )
    raise typer.Exit(code=code)


@benchmark_app.command("experiment")
def benchmark_experiment_cmd(
    ctx: typer.Context,
    experiment: str = typer.Argument(
        ...,
        help=(
            "recency | rerank | embed | decay_rrf | batch | entity_centric | "
            "multi_hop | decay | skills | rrf_k | rerank_body | end_to_end | "
            "reader_context | episode_granularity | reader_quality | "
            "context_assembly (which experiment to run)."
        ),
    ),
    corpus: str = typer.Option(
        "synthetic",
        "--corpus",
        help=(
            "synthetic (CI mechanical verification) | lmeb-s (authoritative) | "
            "claude-mem (real batch corpus)."
        ),
    ),
    output_dir: str = typer.Option("benchmark-output", "--output-dir"),
    reader_model: str = typer.Option(
        "ollama/qwen3:1.7b", "--reader-model", help="Reader LLM (t=0, seed=42)."
    ),
    strong_reader_model: str = typer.Option(
        "ollama/deepseek-v4-flash:0731-cloud",
        "--strong-reader-model",
        help=(
            "Strong reader LLM for the reader_quality A/B (the weak baseline is "
            "--reader-model; the strong candidate is this model)."
        ),
    ),
    judge_model: str = typer.Option(
        "ollama/qwen2.5:7b", "--judge-model", help="Judge LLM (family-disjoint from reader)."
    ),
    top_k: int = typer.Option(10, "--top-k", "-k"),
    temporal: bool = typer.Option(
        True, "--temporal/--no-temporal", help="Temporal ingestion (source_type=human)."
    ),
    pit_queries: bool = typer.Option(
        True,
        "--pit-queries/--no-pit-queries",
        help=(
            "Query active-now (pit=None) instead of state-at-question-date. "
            "Forced OFF for decay_rrf/recency: the recency/decay seams are gated "
            "by `pit is None` (ADR-03), so a PIT query would measure a forced null."
        ),
    ),
    retrieval_only: bool = typer.Option(
        False,
        "--retrieval-only",
        help=(
            "Retrieval-only pass: deterministic stub reader (no Ollama). The "
            "decision metrics (recall@10/ndcg@10) never consume the reader's answer "
            "— identical decision numbers, zero LLM cost."
        ),
    ),
    subsample: bool = typer.Option(
        True,
        "--subsample/--no-subsample",
        help=(
            "Apply the reproducible balanced 100-question subsample to the "
            "LMEB-S corpus (the documented compromise; the full-corpus ingest "
            "hangs on FTS5 and runs overnight). Default ON."
        ),
    ),
    context_mode: str = typer.Option(
        "summary",
        "--context-mode",
        help=(
            "Reader context representation: summary (default) | body | body_bounded "
            "(the reader-context A/B axis; the reader_context experiment runs all "
            "three and ignores this flag)."
        ),
    ),
) -> None:
    """Run an experiment and print the sweep table + decision."""
    from seahorse.benchmark.experiments.runner import (
        render_experiment_report,
        run_experiment,
    )
    from seahorse.benchmark.harness.reader_llm import StubReaderLLM

    report = run_experiment(
        experiment=experiment,
        corpus=corpus,
        output_dir=output_dir,
        reader_model=reader_model,
        strong_reader_model=strong_reader_model,
        judge_model=judge_model,
        top_k=top_k,
        temporal=temporal,
        pit_queries=pit_queries,
        reader_llm=StubReaderLLM() if retrieval_only else None,
        subsample=subsample,
        context_mode=context_mode,
    )
    typer.echo(render_experiment_report(report))


@benchmark_app.command("list")
def benchmark_list_cmd(ctx: typer.Context) -> None:
    """List available dataset adapters."""
    from seahorse.benchmark.cli import list_benchmarks

    for name in list_benchmarks():
        typer.echo(name)


@benchmark_app.command("adapters")
def benchmark_adapters_cmd(ctx: typer.Context) -> None:
    """List available SUT adapters."""
    from seahorse.benchmark.cli import list_adapters

    for name in list_adapters():
        typer.echo(name)


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
def setup(
    ctx: typer.Context,
    uninstall: bool = typer.Option(
        False, "--uninstall", help="Remove the observer hooks + [observe] config."
    ),
) -> None:
    """Install the observer: merge Claude Code hooks + write [observe] config."""
    from seahorse.cli.setup import run_setup, run_setup_uninstall

    cfg = ctx.obj.resolved_config()
    if uninstall:
        run_setup_uninstall(cfg.vault, fmt=ctx.obj.fmt, out=_out(ctx))
    else:
        run_setup(cfg.vault, fmt=ctx.obj.fmt, out=_out(ctx))


@app.command()
def init(
    ctx: typer.Context,
    vault: Path = typer.Argument(..., help="Vault root dir to bootstrap."),
    llm: bool = typer.Option(
        False, "--llm", help="Interactive LLM provider setup (wizard)."
    ),
) -> None:
    """Bootstrap a Seahorse vault (.seahorse/seahorse.toml).

    ``--llm`` additionally opens the interactive provider wizard (detects
    Ollama / free-tier keys, picks the extraction route, optional self-test).
    """
    run_init(vault, fmt=ctx.obj.fmt, out=_out(ctx))
    if llm:
        from seahorse.cli.wizard import run_llm_wizard

        run_llm_wizard(vault)


@app.command()
def status(ctx: typer.Context) -> None:
    """Show the resolved vault / db / config snapshot."""
    run_status(ctx.obj.resolved_config(), fmt=ctx.obj.fmt, out=_out(ctx))


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Diagnose the vault: LLM provider, keys, self-test, extraction mode."""
    run_doctor(ctx.obj.resolved_config(), fmt=ctx.obj.fmt, out=_out(ctx))


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
    """Read-only sidecar snapshot (schema_version, counts, current-state vs active-now)."""
    run_inspect(ctx.obj.resolved_config(), fmt=ctx.obj.fmt, out=_out(ctx))


@app.command(name="vigentes")
def vigentes_cmd(ctx: typer.Context) -> None:
    """Full current-state set with freshness — reserved in the current release."""
    run_reserved("vigentes")


@app.command(name="activos-ahora")
def activos_ahora_cmd(ctx: typer.Context) -> None:
    """Decay-aware active set — reserved (a medium-term goal; needs expire)."""
    run_reserved("activos-ahora")


# ``index`` group: ``index rebuild`` / ``index verify`` (both reserved).
index_app = typer.Typer(help="Index operations (rebuild / verify).")
app.add_typer(index_app, name="index")


@index_app.command(name="rebuild")
def index_rebuild_cmd(
    ctx: typer.Context,
    embed_mode: str = typer.Option(
        "body+summary",
        "--embed-mode",
        help="Passage text to embed: body+summary (default) | body (baseline).",
    ),
) -> None:
    """Rebuild the sidecar index from the vault's .md notes (clear-then-rebuild)."""
    run_index_rebuild(
        ctx.obj.resolved_config(),
        fmt=ctx.obj.fmt,
        out=_out(ctx),
        embed_mode=embed_mode,
    )


@index_app.command(name="verify")
def index_verify_cmd(ctx: typer.Context) -> None:
    """Verify index integrity — reserved in the current release."""
    run_reserved("index-verify")


# ``frontmatter`` group: the vault migrator — migrate legacy notes.
frontmatter_app = typer.Typer(
    help="Frontmatter operations (migrate legacy notes to the canonical format)."
)
app.add_typer(frontmatter_app, name="frontmatter")


@frontmatter_app.command(name="migrate")
def frontmatter_migrate_cmd(
    ctx: typer.Context,
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Classify + manifest only; never writes."
    ),
    resume: bool = typer.Option(
        False, "--resume", help="Skip notes unchanged since the last manifest."
    ),
    batch_size: int | None = typer.Option(
        None, "--batch-size", help="Manifest checkpoint cadence (default 500)."
    ),
) -> None:
    """Migrate legacy Obsidian notes to canonical frontmatter (cases A/B/C/D).

    The current release runs serialized (workers=1); parallel parsing is
    planned for a later release.
    """
    # resolve_vault (not resolved_config): migrating can precede `seahorse init`
    # — the migrator only touches .md files + the manifest, no config needed.
    vault = resolve_vault(ctx.obj.vault)
    run_frontmatter_migrate(
        vault,
        dry_run=dry_run,
        resume=resume,
        batch_size=batch_size,
        fmt=ctx.obj.fmt,
        out=_out(ctx),
    )


# ``skill`` group: procedural skills — add / list / search / show.
skill_app = typer.Typer(help="Procedural skills (deterministic, skip-first).")
app.add_typer(skill_app, name="skill")


@skill_app.command(name="add")
def skill_add_cmd(
    ctx: typer.Context,
    body: str = typer.Argument(..., help="Canonical SKILL.md body (## Trigger/Steps/...)."),
    title: str | None = typer.Option(None, "--title"),
    trigger: str | None = typer.Option(None, "--trigger", help="x-seahorse-skill-trigger."),
    scope: str | None = typer.Option(None, "--scope", help="x-seahorse-skill-scope."),
    version: str | None = typer.Option(None, "--version", help="x-seahorse-skill-version."),
    source_type: str = typer.Option("agent", "--source-type"),
    agent_id: str | None = typer.Option(None, "--agent-id"),
    session_id: str | None = typer.Option(None, "--session-id"),
) -> None:
    """Add a procedural skill (deterministic, cost ≈ 0)."""
    run_skill_add(
        ctx.obj.facade(),
        body=body,
        title=title,
        trigger=trigger,
        scope=scope,
        version=version,
        source_type=source_type,
        agent_id=agent_id,
        session_id=session_id,
        fmt=ctx.obj.fmt,
        out=_out(ctx),
        verbose=ctx.obj.verbose,
    )


@skill_app.command(name="list")
def skill_list_cmd(
    ctx: typer.Context,
    top_k: int = typer.Option(10, "--top-k"),
) -> None:
    """List procedural skills (Discovery level)."""
    run_skill_list(
        ctx.obj.facade(), top_k=top_k, fmt=ctx.obj.fmt, out=_out(ctx), verbose=ctx.obj.verbose
    )


@skill_app.command(name="search")
def skill_search_cmd(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query."),
    top_k: int = typer.Option(10, "--top-k"),
) -> None:
    """Search procedural skills (hybrid recall, procedural filter)."""
    run_skill_search(
        ctx.obj.facade(),
        query=query,
        top_k=top_k,
        fmt=ctx.obj.fmt,
        out=_out(ctx),
        verbose=ctx.obj.verbose,
    )


@skill_app.command(name="show")
def skill_show_cmd(
    ctx: typer.Context,
    ep_id: str = typer.Argument(..., help="Skill episode id."),
    min_trust: str | None = typer.Option(
        None, "--min-trust", help="low | medium | high (default: [procedural] config)."
    ),
) -> None:
    """Show a skill's gated body (Execution level, trust gate)."""
    cfg = ctx.obj.resolved_config()
    default_trust = cfg.procedural.min_trust if cfg.procedural is not None else "medium"
    run_skill_show(
        ctx.obj.facade(),
        ep_id=ep_id,
        min_trust=min_trust or default_trust,
        fmt=ctx.obj.fmt,
        out=_out(ctx),
        verbose=ctx.obj.verbose,
    )


@app.command()
def view(ctx: typer.Context) -> None:
    """Interactive read-only viewer (recent / search / timeline / skills)."""
    run_view(ctx.obj.facade(), out=_out(ctx))


# ---------------------------------------------------------------------------
# Exception translation + entrypoint.
# ---------------------------------------------------------------------------


def _emit_error(exc: BaseException, fmt: OutputFormat, err: TextIO) -> int:
    """Translate ``exc`` to an exit code and write a structured payload to ``err``."""
    code, info = translate(exc)
    if fmt in ("json", "jsonl"):
        import json

        # Machine errors carry an {"error": {...}} envelope so
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