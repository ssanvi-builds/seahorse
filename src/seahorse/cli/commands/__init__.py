"""``seahorse`` command groups extracted from ``cli.app`` (P2b).

``app.py`` stays the composition root: it owns the Typer ``app``, the
``CliContext``, the global callback, and ``main()``; each submodule here
registers its group on the app it is handed (dependency injection instead of
importing ``app`` back — the CLI must stay ONE Typer instance).

Registration ORDER is frozen at the call sites in ``app.py`` (the blocks the
groups were extracted from), so the ``--help`` listing and the exit-code
contract are unchanged; the command bodies are pure moves.
"""