"""``skill`` group — procedural skills: ``skill add|list|search|show``.

Pure move of the ``skill_app`` block from ``cli.app``: the sub-Typer is
created here and attached to the app handed to ``register`` (the app stays a
single Typer instance owned by the composition root). The command bodies
delegate to ``cli.skills`` (deterministic, skip-first write path) exactly as
before.
"""

from __future__ import annotations

import typer

from seahorse.cli.skills import (
    run_skill_add,
    run_skill_list,
    run_skill_search,
    run_skill_show,
)


def register(app: typer.Typer, *, out) -> None:
    """Attach the ``skill`` group in the position its block occupied."""
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
            out=out(ctx),
            verbose=ctx.obj.verbose,
        )

    @skill_app.command(name="list")
    def skill_list_cmd(
        ctx: typer.Context,
        top_k: int = typer.Option(10, "--top-k"),
    ) -> None:
        """List procedural skills (Discovery level)."""
        run_skill_list(
            ctx.obj.facade(), top_k=top_k, fmt=ctx.obj.fmt, out=out(ctx), verbose=ctx.obj.verbose
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
            out=out(ctx),
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
            out=out(ctx),
            verbose=ctx.obj.verbose,
        )