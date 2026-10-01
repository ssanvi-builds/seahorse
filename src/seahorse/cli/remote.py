"""Remote-access process manager — ``seahorse remote start|stop|status``.

Manages TWO detached children per vault (the observer precedent, applied
twice): the HTTP MCP server (``seahorse mcp --transport http`` — loopback
only) and the cloudflared quick tunnel (the public HTTPS URL that consumer
apps such as ChatGPT/Gemini web can reach).

State lives in ``{seahorse_dir}/remote/``: ``server.pid``/``server.log`` and
``tunnel.pid``/``tunnel.log``. The TWO pidfiles are independent because the
two processes have independent lifetimes: a live tunnel survives a server
respawn KEEPING THE SAME public URL (cloudflared retries its origin), so
``start`` reuses each live child separately instead of failing loud like
``observe start`` — there is no single-writer hazard here, nothing is ever
spawned twice.

Security posture: behind the tunnel the bearer token is the ONLY barrier.
``start`` therefore prints the warning and, whenever it is about to open a
NEW tunnel, asks for explicit consent (``--yes`` skips; a non-TTY context
without ``--yes`` refuses with exit 2 — no public exposure without a "yes").
The token NEVER goes on a child's argv (``ps`` would leak it): the server
child resolves it from ``[http]``/``SEAHORSE_HTTP_TOKEN`` itself.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TextIO

from seahorse.cli import procutil
from seahorse.cli.config import SeahorseConfig, load_config
from seahorse.cli.errors import (
    CliCloudflaredMissing,
    CliHttpTokenMissing,
    CliRemoteStartFailed,
    CliUsageError,
)
from seahorse.cli.output import OutputFormat, render_message
from seahorse.cli.remote_instructions import (
    SECURITY_WARNING,
    human_instructions,
    instruction_blocks,
)

REMOTE_DIR_NAME = "remote"
SERVER_PID_FILENAME = "server.pid"
SERVER_LOG_FILENAME = "server.log"
TUNNEL_PID_FILENAME = "tunnel.pid"
TUNNEL_LOG_FILENAME = "tunnel.log"

# serve_http prints "seahorse-mcp: listening on http://<host>:<port>" to
# stderr (redirected to server.log) — the line is the AUTHORITY for the
# bound port (``--port 0`` picks an ephemeral one).
_SERVER_URL_RE = re.compile(r"listening on (http://\S+)")
SERVER_READY_TIMEOUT_S = 15.0
SERVER_POLL_INTERVAL_S = 0.2
# cloudflared prints the quick-tunnel URL to its stderr (→ tunnel.log).
_TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
TUNNEL_READY_TIMEOUT_S = 30.0
TUNNEL_POLL_INTERVAL_S = 0.5
# stop: SIGTERM first (graceful), SIGKILL after this budget (orphan-proof).
STOP_TERM_WAIT_S = 3.0
STOP_POLL_INTERVAL_S = 0.05
BREW_INSTALL_TIMEOUT_S = 300.0

INSTALL_LINE = (
    "brew install cloudflared  (or: "
    "https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)"
)


# ---------------------------------------------------------------------------
# State layout.
# ---------------------------------------------------------------------------


def remote_dir(cfg: SeahorseConfig) -> Path:
    """The remote wizard's directory: ``{seahorse_dir}/remote/``."""
    return cfg.seahorse_dir / REMOTE_DIR_NAME


def server_pid_file(cfg: SeahorseConfig) -> Path:
    return remote_dir(cfg) / SERVER_PID_FILENAME


def server_log_file(cfg: SeahorseConfig) -> Path:
    return remote_dir(cfg) / SERVER_LOG_FILENAME


def tunnel_pid_file(cfg: SeahorseConfig) -> Path:
    return remote_dir(cfg) / TUNNEL_PID_FILENAME


def tunnel_log_file(cfg: SeahorseConfig) -> Path:
    return remote_dir(cfg) / TUNNEL_LOG_FILENAME


def _live_pid(pid_path: Path) -> int | None:
    """The pid recorded in ``pid_path`` when a live process owns it, else ``None``.

    The ``None``-when-dead shape lets the start flow narrow with ``is None``
    and reassign a freshly spawned pid over it — mypy can follow that, which
    it cannot do through the ``(running, pid)`` tuple.
    """
    pid = procutil.read_pid(pid_path)
    return pid if pid is not None and procutil.pid_alive(pid) else None


def server_liveness(cfg: SeahorseConfig) -> tuple[bool, int | None]:
    """``(running, pid)`` from the pid file + a kernel liveness check."""
    pid = _live_pid(server_pid_file(cfg))
    return pid is not None, pid


def tunnel_liveness(cfg: SeahorseConfig) -> tuple[bool, int | None]:
    pid = _live_pid(tunnel_pid_file(cfg))
    return pid is not None, pid


# ---------------------------------------------------------------------------
# Spawn + readiness.
# ---------------------------------------------------------------------------


def _spawn_child(argv: list[str], *, log_path: Path, start_new_session: bool) -> int:
    """Spawn a child logging to ``log_path``; return its pid.

    ``start_new_session`` detaches the child (daemon mode — it survives the
    terminal); foreground children stay in this session so Ctrl-C reaches
    them through the process group.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict = {}
    if start_new_session:
        kwargs["start_new_session"] = True
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(argv, stdout=log, stderr=log, **kwargs)  # noqa: S603
    return proc.pid


def _server_argv(cfg: SeahorseConfig, port: int | None) -> list[str]:
    # ``--vault`` is a GLOBAL option and must precede the subcommand (same
    # invariant as the observer spawn). The token is deliberately absent:
    # the child resolves it from [http]/SEAHORSE_HTTP_TOKEN itself.
    argv = [
        sys.executable,
        "-m",
        "seahorse.cli.app",
        "--vault",
        str(cfg.vault),
        "mcp",
        "--transport",
        "http",
    ]
    if port is not None:
        argv += ["--port", str(port)]
    return argv


def _tunnel_argv(cloudflared: str, bound_port: int) -> list[str]:
    return [
        cloudflared,
        "tunnel",
        "--no-autoupdate",
        "--url",
        f"http://127.0.0.1:{bound_port}",
    ]


def _extract_match(log_path: Path, pattern: re.Pattern[str]) -> str | None:
    """The LAST match of ``pattern`` in ``log_path`` (children append)."""
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    matches = pattern.findall(content)
    return matches[-1] if matches else None


def _wait_for_match(
    log_path: Path, pattern: re.Pattern[str], *, timeout_s: float, poll_s: float, pid: int
) -> str | None:
    """Poll ``log_path`` until ``pattern`` matches, or the child dies.

    Returns ``None`` on timeout or child death — the caller raises
    ``CliRemoteStartFailed`` with the child's name (and tears down whatever
    else it spawned).
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        found = _extract_match(log_path, pattern)
        if found:
            return found
        if not procutil.pid_alive(pid):
            return None
        time.sleep(poll_s)
    return _extract_match(log_path, pattern)


def _bound_port(server_url: str) -> int:
    # "http://host:port" — the port authority is the listen line itself.
    return int(server_url.rsplit(":", 1)[1])


def _terminate(pid: int, pid_path: Path) -> None:
    """SIGTERM → wait → SIGKILL fallback → remove the pid file (idempotent)."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        procutil.remove_pid(pid_path)
        return
    deadline = time.monotonic() + STOP_TERM_WAIT_S
    while procutil.pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(STOP_POLL_INTERVAL_S)
    if procutil.pid_alive(pid):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    procutil.remove_pid(pid_path)


# ---------------------------------------------------------------------------
# cloudflared resolution + the install offer.
# ---------------------------------------------------------------------------


def resolve_cloudflared() -> str | None:
    """The cloudflared binary path, or ``None`` when not installed.

    ``SEAHORSE_CLOUDFLARED_BIN`` (sandbox/e2e override) wins verbatim — and
    its presence DISABLES the install offer, mirroring the
    ``SEAHORSE_CLAUDE_JSON`` guard: an env-redirected environment must never
    install system packages.
    """
    override = os.environ.get("SEAHORSE_CLOUDFLARED_BIN")
    if override:
        return override
    return shutil.which("cloudflared")


def _offer_brew_install() -> str | None:
    """Offer ``brew install cloudflared`` with explicit consent (Sergio's
    chosen UX); ``None`` when declined or impossible (no TTY, no brew).

    brew's output goes to the terminal uncaptured — a silent 5-minute
    install looks broken to the non-technical user this wizard serves.
    """
    brew = shutil.which("brew")
    if brew is None:
        return None
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    print("cloudflared (the public tunnel) is not installed.")
    print("Install it now with Homebrew?")
    answer = input("[y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        return None
    result = subprocess.run(  # noqa: S603
        [brew, "install", "cloudflared"], timeout=BREW_INSTALL_TIMEOUT_S, check=False
    )
    if result.returncode != 0:
        return None
    return shutil.which("cloudflared")


def _ensure_cloudflared() -> str:
    cloudflared = resolve_cloudflared()
    if cloudflared is None and not os.environ.get("SEAHORSE_CLOUDFLARED_BIN"):
        cloudflared = _offer_brew_install()
    if cloudflared is None:
        raise CliCloudflaredMissing(INSTALL_LINE)
    return cloudflared


# ---------------------------------------------------------------------------
# Token + consent gates.
# ---------------------------------------------------------------------------


def _ensure_token(cfg: SeahorseConfig) -> tuple[SeahorseConfig, str]:
    """A config guaranteed to carry a token, and the token itself.

    Reuses ``write_http_config`` verbatim (idempotent — a present ``[http]``
    section is never rotated). The reload reads the CANONICAL config path —
    the same file the spawned server child will read — so parent and child
    can never disagree about the token.
    """
    token = os.environ.get("SEAHORSE_HTTP_TOKEN") or cfg.http.token
    if token:
        return cfg, token
    from seahorse.cli.setup import write_http_config  # heavy module — lazy

    write_http_config(cfg.vault)
    reloaded = load_config(cfg.vault)
    token = os.environ.get("SEAHORSE_HTTP_TOKEN") or reloaded.http.token
    if not token:
        raise CliHttpTokenMissing()  # unreachable (setup always writes) — defense
    return reloaded, token


def _confirm_tunnel(*, yes: bool) -> bool:
    """Consent gate for opening a NEW public tunnel.

    ``--yes`` skips the prompt. A non-TTY context without ``--yes`` refuses
    (exit 2): a tunnel is a public exposure, and a silent default would open
    it without anyone saying yes.
    """
    if yes:
        return True
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise CliUsageError(
            "opening a public tunnel needs --yes in a non-interactive context "
            "(the URL is public; the token is the only barrier)"
        )
    print("\nThe wizard is about to open a PUBLIC tunnel to this machine.")
    print(SECURITY_WARNING)
    print()
    answer = input("Open a public tunnel to the internet now? [y/N] ").strip().lower()
    return answer in ("y", "yes")


# ---------------------------------------------------------------------------
# Commands.
# ---------------------------------------------------------------------------


def run_remote_start(
    cfg: SeahorseConfig,
    *,
    fmt: OutputFormat,
    out: TextIO,
    port: int | None = None,
    app: str = "all",
    yes: bool = False,
    no_tunnel: bool = False,
    foreground: bool = False,
) -> None:
    """The wizard: token → preflight → consent → server → tunnel → blocks."""
    cfg, token = _ensure_token(cfg)
    tunnel_wanted = not no_tunnel
    server_pid = _live_pid(server_pid_file(cfg))
    tunnel_pid = _live_pid(tunnel_pid_file(cfg)) if tunnel_wanted else None
    server_reused = server_pid is not None
    tunnel_reused = tunnel_pid is not None

    # Preflight for a NEW tunnel — resolve + consent BEFORE anything is
    # spawned: declining must leave nothing started. (Reusing a live tunnel
    # opens no new public exposure, so it needs no gate.)
    cloudflared: str | None = None
    if tunnel_wanted and tunnel_pid is None:
        cloudflared = _ensure_cloudflared()
        if not _confirm_tunnel(yes=yes):
            render_message(
                {"started": False, "aborted": True},
                fmt=fmt,
                out=out,
                human_text="remote: aborted — nothing was started",
            )
            return

    # Server: reuse when live, spawn otherwise, readiness via the listen line.
    if server_pid is None:
        server_pid = _spawn_child(
            _server_argv(cfg, port),
            log_path=server_log_file(cfg),
            start_new_session=not foreground,
        )
        procutil.write_pid(server_pid_file(cfg), server_pid)
    server_url = _wait_for_match(
        server_log_file(cfg),
        _SERVER_URL_RE,
        timeout_s=SERVER_READY_TIMEOUT_S,
        poll_s=SERVER_POLL_INTERVAL_S,
        pid=server_pid,
    )
    if server_url is None:
        if not server_reused:
            _terminate(server_pid, server_pid_file(cfg))
        raise CliRemoteStartFailed("server", server_log_file(cfg))

    # Tunnel: reuse when live (same URL survives a server respawn), else spawn.
    tunnel_url = None
    if tunnel_wanted:
        if tunnel_pid is not None:
            tunnel_url = _extract_match(tunnel_log_file(cfg), _TUNNEL_URL_RE)
        else:
            # The preflight resolved cloudflared under this very condition
            # (a new tunnel was wanted); the assert is only the narrowing
            # mypy cannot carry across statements.
            assert cloudflared is not None
            tunnel_pid = _spawn_child(
                _tunnel_argv(cloudflared, _bound_port(server_url)),
                log_path=tunnel_log_file(cfg),
                start_new_session=not foreground,
            )
            procutil.write_pid(tunnel_pid_file(cfg), tunnel_pid)
            tunnel_url = _wait_for_match(
                tunnel_log_file(cfg),
                _TUNNEL_URL_RE,
                timeout_s=TUNNEL_READY_TIMEOUT_S,
                poll_s=TUNNEL_POLL_INTERVAL_S,
                pid=tunnel_pid,
            )
            if tunnel_url is None:
                # Close exposure first, then the server we spawned — the
                # wizard never leaves a half-started state behind.
                _terminate(tunnel_pid, tunnel_pid_file(cfg))
                if not server_reused:
                    _terminate(server_pid, server_pid_file(cfg))
                raise CliRemoteStartFailed("tunnel", tunnel_log_file(cfg))

    _render_start(
        fmt=fmt,
        out=out,
        app=app,
        token=token,
        server_pid=server_pid,
        server_url=server_url,
        server_reused=server_reused,
        tunnel_pid=tunnel_pid,
        tunnel_url=tunnel_url,
        tunnel_reused=tunnel_reused,
    )

    if foreground:
        _run_foreground(cfg, server_pid=server_pid, tunnel_pid=tunnel_pid)


def _run_foreground(
    cfg: SeahorseConfig, *, server_pid: int, tunnel_pid: int | None
) -> None:
    """Block until Ctrl-C or a child death; teardown both either way."""
    died = None
    try:
        while True:
            if not procutil.pid_alive(server_pid):
                died = "server"
                break
            if tunnel_pid is not None and not procutil.pid_alive(tunnel_pid):
                died = "tunnel"
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        _terminate(server_pid, server_pid_file(cfg))
        if tunnel_pid is not None:
            _terminate(tunnel_pid, tunnel_pid_file(cfg))
    if died is not None:
        log = server_log_file(cfg) if died == "server" else tunnel_log_file(cfg)
        raise CliRemoteStartFailed(died, log)


def run_remote_stop(cfg: SeahorseConfig, *, fmt: OutputFormat, out: TextIO) -> None:
    """Close the tunnel FIRST (exposure), then the server; idempotent."""
    stopped: dict[str, int] = {}
    for name, pid_path in (
        ("tunnel", tunnel_pid_file(cfg)),
        ("server", server_pid_file(cfg)),
    ):
        pid = procutil.read_pid(pid_path)
        if pid is None or not procutil.pid_alive(pid):
            procutil.remove_pid(pid_path)  # stale pidfile cleanup
            continue
        _terminate(pid, pid_path)
        stopped[name] = pid
    if not stopped:
        render_message(
            {"stopped": False},
            fmt=fmt,
            out=out,
            human_text="remote: not running",
        )
        return
    payload: dict[str, object] = {"stopped": True}
    payload.update({f"{name}_pid": pid for name, pid in stopped.items()})
    human = "remote: stopped (" + ", ".join(f"{n} pid {p}" for n, p in stopped.items()) + ")"
    render_message(payload, fmt=fmt, out=out, human_text=human)


def run_remote_status(
    cfg: SeahorseConfig, *, fmt: OutputFormat, out: TextIO, app: str = "all"
) -> None:
    """Read-only: live state + the same instructions ``start`` prints."""
    server_running, server_pid = server_liveness(cfg)
    tunnel_running, tunnel_pid = tunnel_liveness(cfg)
    server_url = _extract_match(server_log_file(cfg), _SERVER_URL_RE) if server_running else None
    tunnel_url = _extract_match(tunnel_log_file(cfg), _TUNNEL_URL_RE) if tunnel_running else None
    token = os.environ.get("SEAHORSE_HTTP_TOKEN") or cfg.http.token
    running = server_running or tunnel_running
    if not running:
        render_message(
            {"running": False},
            fmt=fmt,
            out=out,
            human_text="remote: not running",
        )
        return
    payload: dict[str, object] = {
        "running": True,
        "server": {"pid": server_pid, "url": server_url} if server_running else None,
        "tunnel": {"pid": tunnel_pid, "url": tunnel_url} if tunnel_running else None,
        "token": token,
        "warning": SECURITY_WARNING,
    }
    mcp_url = _mcp_url(tunnel_url, server_url)
    if mcp_url and token:
        payload["mcp_url"] = mcp_url
        payload["instructions"] = instruction_blocks(app, mcp_url=mcp_url, token=token)
    human = _human_state(
        server_running=server_running,
        server_pid=server_pid,
        server_url=server_url,
        tunnel_running=tunnel_running,
        tunnel_pid=tunnel_pid,
        tunnel_url=tunnel_url,
    )
    if mcp_url and token:
        human += "\n\n" + human_instructions(app, mcp_url=mcp_url, token=token)
    render_message(payload, fmt=fmt, out=out, human_text=human)


def _mcp_url(tunnel_url: str | None, server_url: str | None) -> str | None:
    if tunnel_url:
        return f"{tunnel_url}/mcp"
    if server_url:
        return f"{server_url}/mcp"
    return None


def _tunnel_degraded_line(pid: int | None) -> str:
    """A live tunnel whose log has no URL (hand-edited state): the pid is real
    exposure, so the honest line says how to recover instead of lying."""
    return (
        f"⚠ tunnel: running (pid {pid}) — URL not in log; "
        "run `seahorse remote stop` and start again"
    )


def _human_state(
    *,
    server_running: bool,
    server_pid: int | None,
    server_url: str | None,
    tunnel_running: bool,
    tunnel_pid: int | None,
    tunnel_url: str | None,
) -> str:
    lines: list[str] = []
    if server_running:
        lines.append(f"✓ server: running (pid {server_pid}) — {server_url}")
    if tunnel_running:
        if tunnel_url:
            lines.append(f"✓ tunnel: running (pid {tunnel_pid}) — {tunnel_url}")
        else:
            lines.append(_tunnel_degraded_line(tunnel_pid))
    return "\n".join(lines)


def _render_start(
    *,
    fmt: OutputFormat,
    out: TextIO,
    app: str,
    token: str,
    server_pid: int,
    server_url: str,
    server_reused: bool,
    tunnel_pid: int | None,
    tunnel_url: str | None,
    tunnel_reused: bool,
) -> None:
    # server_url is guaranteed non-None (the listen line was required), so
    # the MCP URL is always derivable here — unlike in status, where the
    # logs may hold no URL at all.
    mcp_url = f"{tunnel_url}/mcp" if tunnel_url else f"{server_url}/mcp"
    payload: dict[str, object] = {
        "started": True,
        "server": {"pid": server_pid, "url": server_url, "reused": server_reused},
        "tunnel": None,
        "mcp_url": mcp_url,
        "token": token,
        "warning": SECURITY_WARNING,
        "instructions": instruction_blocks(app, mcp_url=mcp_url, token=token),
    }
    if tunnel_pid is not None:
        payload["tunnel"] = {
            "pid": tunnel_pid,
            "url": tunnel_url,
            "ephemeral": True,
            "reused": tunnel_reused,
        }
    lines: list[str] = []
    server_state = "already running" if server_reused else "running"
    lines.append(f"✓ server: {server_state} (pid {server_pid}) — {server_url}")
    if tunnel_pid is not None:
        if tunnel_url:
            tunnel_state = "already running" if tunnel_reused else "running"
            lines.append(f"✓ tunnel: {tunnel_state} (pid {tunnel_pid}) — {tunnel_url}")
        else:
            lines.append(_tunnel_degraded_line(tunnel_pid))
    lines.append("")
    lines.append(f"⚠ {SECURITY_WARNING}")
    lines.append("")
    lines.append(human_instructions(app, mcp_url=mcp_url, token=token))
    lines.append("")
    lines.append("Use `seahorse remote stop` to close the tunnel.")
    render_message(payload, fmt=fmt, out=out, human_text="\n".join(lines))


__all__ = [
    "remote_dir",
    "server_pid_file",
    "server_log_file",
    "tunnel_pid_file",
    "tunnel_log_file",
    "server_liveness",
    "tunnel_liveness",
    "resolve_cloudflared",
    "run_remote_start",
    "run_remote_stop",
    "run_remote_status",
]