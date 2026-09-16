"""``observe`` group — ``observe start|stop|status|run|event`` (the observer).

Pure move of the ``observe_app`` block from ``cli.app``: the sub-Typer is
created here and attached to the app handed to ``register`` (the app stays a
single Typer instance owned by the composition root). The command bodies lazy-
import ``seahorse.observe.cli`` exactly as before — the observer's process
machinery must not load for every other command.
"""

from __future__ import annotations

import typer


def register(app: typer.Typer, *, out) -> None:
    """Attach the ``observe`` group in the position its block occupied."""
    observe_app = typer.Typer(help="Observer capture layer.")
    app.add_typer(observe_app, name="observe")

    @observe_app.command("start")
    def observe_start_cmd(ctx: typer.Context) -> None:
        """Start the observer as a background process (single-writer)."""
        from seahorse.observe.cli import run_observe_start

        run_observe_start(ctx.obj.resolved_config(), fmt=ctx.obj.fmt, out=out(ctx))

    @observe_app.command("stop")
    def observe_stop_cmd(ctx: typer.Context) -> None:
        """Stop the observer (SIGTERM)."""
        from seahorse.observe.cli import run_observe_stop

        run_observe_stop(ctx.obj.resolved_config(), fmt=ctx.obj.fmt, out=out(ctx))

    @observe_app.command("status")
    def observe_status_cmd(ctx: typer.Context) -> None:
        """Report whether the observer is running."""
        from seahorse.observe.cli import run_observe_status

        run_observe_status(ctx.obj.resolved_config(), fmt=ctx.obj.fmt, out=out(ctx))

    @observe_app.command("run")
    def observe_run_cmd(ctx: typer.Context) -> None:
        """Run the observer in the foreground (endpoint + worker loop)."""
        from seahorse.observe.cli import run_observe_run

        run_observe_run(ctx.obj.resolved_config(), fmt=ctx.obj.fmt, out=out(ctx))

    @observe_app.command("event")
    def observe_event_cmd(
        ctx: typer.Context,
        agent_id: str = typer.Option(
            "",
            "--agent-id",
            help="Attribute the event to this agent when the hook payload has none.",
        ),
    ) -> None:
        """POST a hook event to the observer socket (called by the hooks)."""
        from seahorse.observe.cli import run_observe_event

        run_observe_event(
            ctx.obj.resolved_config(), fmt=ctx.obj.fmt, out=out(ctx), agent_id=agent_id
        )