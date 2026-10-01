"""``remote`` group — ``remote start|stop|status`` (the remote-access wizard).

Same pattern as ``commands.observe``: the sub-Typer is created here and
attached to the app handed to ``register``; the command bodies lazy-import
``seahorse.cli.remote`` so the process machinery never loads for any other
command. ``--app`` is validated at this boundary (``typer.BadParameter``,
exit 2) against ``APP_CHOICES`` — the single source ``instruction_blocks``
itself uses.
"""

from __future__ import annotations

import typer

from seahorse.cli.remote_instructions import APP_CHOICES


def _validate_app_choice(app_choice: str) -> None:
    if app_choice not in APP_CHOICES:
        raise typer.BadParameter(
            f"invalid --app '{app_choice}' — choose from: {', '.join(APP_CHOICES)}"
        )


def register(app: typer.Typer, *, out) -> None:
    """Attach the ``remote`` group (registered right after ``mcp``)."""
    remote_app = typer.Typer(
        help="Remote access wizard: MCP server + public tunnel, paste-ready instructions."
    )
    app.add_typer(remote_app, name="remote")

    @remote_app.command("start")
    def remote_start_cmd(
        ctx: typer.Context,
        port: int | None = typer.Option(
            None, "--port", help="Port for the local server (default: config)."
        ),
        app_choice: str = typer.Option(
            "all",
            "--app",
            help="Instructions for one app: all|chatgpt|gemini|gemini-cli|claude-code.",
        ),
        yes: bool = typer.Option(
            False, "--yes", help="Skip the public-tunnel consent prompt."
        ),
        no_tunnel: bool = typer.Option(
            False, "--no-tunnel", help="Serve on loopback only; no public URL."
        ),
        foreground: bool = typer.Option(
            False, "--foreground", help="Stay attached; Ctrl-C stops both children."
        ),
    ) -> None:
        """Start the server (+ public tunnel); print paste-ready instructions."""
        _validate_app_choice(app_choice)
        from seahorse.cli.remote import run_remote_start

        run_remote_start(
            ctx.obj.resolved_config(),
            fmt=ctx.obj.fmt,
            out=out(ctx),
            port=port,
            app=app_choice,
            yes=yes,
            no_tunnel=no_tunnel,
            foreground=foreground,
        )

    @remote_app.command("stop")
    def remote_stop_cmd(ctx: typer.Context) -> None:
        """Stop the tunnel and the server (idempotent)."""
        from seahorse.cli.remote import run_remote_stop

        run_remote_stop(ctx.obj.resolved_config(), fmt=ctx.obj.fmt, out=out(ctx))

    @remote_app.command("status")
    def remote_status_cmd(
        ctx: typer.Context,
        app_choice: str = typer.Option(
            "all",
            "--app",
            help="Instructions for one app: all|chatgpt|gemini|gemini-cli|claude-code.",
        ),
    ) -> None:
        """Report live state; re-print the instructions start showed."""
        _validate_app_choice(app_choice)
        from seahorse.cli.remote import run_remote_status

        run_remote_status(
            ctx.obj.resolved_config(), fmt=ctx.obj.fmt, out=out(ctx), app=app_choice
        )