"""``seahorse.cli.remote`` — the remote wizard's process manager.

The contract under test: two INDEPENDENT managed children (HTTP server +
cloudflared quick tunnel) with pidfile state, spawn readiness polled from
each child's log, fail-loud teardown (exit 101 never leaves a half-started
state), idempotent reuse of live children, the security gate (consent before
a NEW public tunnel), the cloudflared install offer (env override disables
it), token preflight (setup write + canonical reload), and the human/JSON
rendering backed by ``remote_instructions``.
"""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from seahorse.cli import remote
from seahorse.cli.config import HttpConfig, SeahorseConfig, config_path_for
from seahorse.cli.errors import (
    CliCloudflaredMissing,
    CliHttpTokenMissing,
    CliRemoteStartFailed,
    CliUsageError,
)
from seahorse.cli.exit_codes import EXIT_USAGE
from seahorse.cli.remote_instructions import APP_CHOICES, APP_KEYS
from tests.cli.conftest import invoke

TOKEN = "test-token-1234"
SERVER_URL = "http://127.0.0.1:8767"
TUNNEL_URL = "https://example-words.trycloudflare.com"
LISTEN_LINE = f"seahorse-mcp: listening on {SERVER_URL}\n"
TUNNEL_LINE = f"2026-10-01T12:00:00Z INF  {TUNNEL_URL}\n"
# The fake Popen's pid: dead (fits int32 → ESRCH on every sane kernel) so the
# readiness tests fail fast through the pid-alive branch, and nothing real is
# ever signalled by the teardown paths.
FAKE_PID = 999999999


def _cfg(tmp_path: Path, *, token: str | None = TOKEN) -> SeahorseConfig:
    """A vault-shaped config (the observe-test pattern): dir + ``.seahorse/``."""
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = SeahorseConfig(
        vault=vault,
        seahorse_dir=vault / ".seahorse",
        db_path=vault / ".seahorse" / "seahorse.db",
        http=HttpConfig(token=token),
    )
    cfg.seahorse_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def _out() -> io.StringIO:
    return io.StringIO()


def _write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def _write_log(path: Path, content: str) -> None:
    # The real _spawn_child mkdirs + append-opens the log; the tests that
    # pre-write a log describe a child that already wrote its ready line.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def spawn_calls(monkeypatch):
    """Fake Popen recording argv — the fake pid is DEAD by design so the
    readiness tests can only pass through their pre-written log lines."""
    calls: list[list[str]] = []

    class _FakePopen:
        pid = FAKE_PID

        def __init__(self, argv, **kwargs):
            calls.append(list(argv))

    monkeypatch.setattr(remote.subprocess, "Popen", _FakePopen)
    return calls


class _FakeTTYOut:
    """Enough of a stdout/stdin for the consent gates: claims to be a TTY."""

    def isatty(self) -> bool:
        return True

    def write(self, *_args) -> None:
        return None

    def flush(self) -> None:
        return None


def _json(out: io.StringIO) -> dict:
    return json.loads(out.getvalue())


# ---------------------------------------------------------------------------
# cloudflared resolution + install offer.
# ---------------------------------------------------------------------------


def test_resolve_cloudflared_env_override_wins_verbatim(monkeypatch) -> None:
    monkeypatch.setenv("SEAHORSE_CLOUDFLARED_BIN", "/opt/fake/cloudflared")
    assert remote.resolve_cloudflared() == "/opt/fake/cloudflared"


def test_resolve_cloudflared_which_fallback(monkeypatch) -> None:
    monkeypatch.delenv("SEAHORSE_CLOUDFLARED_BIN", raising=False)
    monkeypatch.setattr(remote.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    assert remote.resolve_cloudflared() == "/usr/local/bin/cloudflared"


def test_resolve_cloudflared_absent(monkeypatch) -> None:
    monkeypatch.delenv("SEAHORSE_CLOUDFLARED_BIN", raising=False)
    monkeypatch.setattr(remote.shutil, "which", lambda name: None)
    assert remote.resolve_cloudflared() is None


def test_cloudflared_missing_fails_loud_no_spawn(tmp_path, monkeypatch, spawn_calls) -> None:
    """Exit 100 BEFORE any spawn: no server is left running behind a tunnel
    that cannot exist (the CliCloudflaredMissing contract)."""
    cfg = _cfg(tmp_path)
    monkeypatch.delenv("SEAHORSE_CLOUDFLARED_BIN", raising=False)
    monkeypatch.setattr(remote.shutil, "which", lambda name: None)
    with pytest.raises(CliCloudflaredMissing) as exc_info:
        remote.run_remote_start(cfg, fmt="human", out=_out(), yes=True)
    assert exc_info.value.exit_code == 100
    assert "brew install cloudflared" in exc_info.value.detail
    assert spawn_calls == []


def test_env_override_disables_install_offer(tmp_path, monkeypatch, spawn_calls) -> None:
    """Under SEAHORSE_CLOUDFLARED_BIN the offer never runs (the SEAHORSE_CLAUDE_JSON
    guard principle: an env-redirected environment must not install packages)."""
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("SEAHORSE_CLOUDFLARED_BIN", "/opt/fake/cloudflared")
    monkeypatch.setattr(
        remote.shutil,
        "which",
        lambda name: pytest.fail("which must not run under the env override"),
    )
    _write_log(remote.server_log_file(cfg), LISTEN_LINE)
    _write_log(remote.tunnel_log_file(cfg), TUNNEL_LINE)
    out = _out()
    remote.run_remote_start(cfg, fmt="json", out=out, yes=True)
    payload = _json(out)
    assert payload["started"] is True
    assert spawn_calls[1][0] == "/opt/fake/cloudflared"  # server first, tunnel second


def test_brew_offer_installs_with_consent(tmp_path, monkeypatch, spawn_calls) -> None:
    """The chosen UX: TTY + consent → brew install runs visible → re-which finds
    the binary → the wizard continues."""
    cfg = _cfg(tmp_path)
    fake_bin = "/opt/homebrew/bin/cloudflared"
    fake_which = {"brew": "/opt/homebrew/bin/brew", "cloudflared": None}
    monkeypatch.delenv("SEAHORSE_CLOUDFLARED_BIN", raising=False)
    monkeypatch.setattr(remote.shutil, "which", lambda name: fake_which.get(name))
    monkeypatch.setattr("sys.stdin", _FakeTTYOut())
    monkeypatch.setattr("sys.stdout", _FakeTTYOut())
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    ran: list[list[str]] = []

    def fake_run(argv, **kwargs):
        ran.append(list(argv))
        fake_which["cloudflared"] = fake_bin  # installed → the re-which finds it
        return subprocess.CompletedProcess(args=argv, returncode=0)

    monkeypatch.setattr(remote.subprocess, "run", fake_run)
    _write_log(remote.server_log_file(cfg), LISTEN_LINE)
    _write_log(remote.tunnel_log_file(cfg), TUNNEL_LINE)
    out = _out()
    remote.run_remote_start(cfg, fmt="json", out=out)
    assert ran == [["/opt/homebrew/bin/brew", "install", "cloudflared"]]
    assert _json(out)["started"] is True


def test_brew_offer_declined_raises_missing(tmp_path, monkeypatch, spawn_calls) -> None:
    cfg = _cfg(tmp_path)
    monkeypatch.delenv("SEAHORSE_CLOUDFLARED_BIN", raising=False)
    monkeypatch.setattr(
        remote.shutil,
        "which",
        lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None,
    )
    monkeypatch.setattr("sys.stdin", _FakeTTYOut())
    monkeypatch.setattr("sys.stdout", _FakeTTYOut())
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    with pytest.raises(CliCloudflaredMissing):
        remote.run_remote_start(cfg, fmt="human", out=_out())
    assert spawn_calls == []


def test_brew_offer_refuses_without_tty(tmp_path, monkeypatch, spawn_calls) -> None:
    """A non-interactive context must never prompt (it would hang): the offer
    returns None and the wizard fails loud with the install line."""
    cfg = _cfg(tmp_path)
    monkeypatch.delenv("SEAHORSE_CLOUDFLARED_BIN", raising=False)
    monkeypatch.setattr(
        remote.shutil,
        "which",
        lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO())  # isatty() is False
    monkeypatch.setattr("sys.stdout", io.StringIO())
    with pytest.raises(CliCloudflaredMissing) as exc_info:
        remote.run_remote_start(cfg, fmt="human", out=_out(), yes=False)
    assert "brew install cloudflared" in exc_info.value.detail
    assert spawn_calls == []


# ---------------------------------------------------------------------------
# token preflight.
# ---------------------------------------------------------------------------


def test_missing_token_writes_http_config_and_reloads(
    tmp_path, monkeypatch, spawn_calls
) -> None:
    """No token anywhere → write_http_config (setup's idempotent write) → the
    canonical reload picks the token the spawned child will also read."""
    cfg = _cfg(tmp_path, token=None)
    monkeypatch.delenv("SEAHORSE_HTTP_TOKEN", raising=False)
    written: list[Path] = []

    def fake_write_http_config(vault: Path) -> None:
        written.append(vault)
        config_path_for(vault).write_text(
            '[seahorse]\ndb_path = "seahorse.db"\n\n[http]\ntoken = "written-token"\n',
            encoding="utf-8",
        )

    monkeypatch.setattr("seahorse.cli.setup.write_http_config", fake_write_http_config)
    _write_log(remote.server_log_file(cfg), LISTEN_LINE)
    out = _out()
    remote.run_remote_start(cfg, fmt="json", out=out, no_tunnel=True)
    assert _json(out)["token"] == "written-token"
    assert written == [cfg.vault]


def test_env_token_skips_config_write(tmp_path, monkeypatch, spawn_calls) -> None:
    cfg = _cfg(tmp_path, token=None)
    monkeypatch.setenv("SEAHORSE_HTTP_TOKEN", "env-token")

    def fail_write(vault: Path) -> None:
        raise AssertionError("write_http_config must not run when env carries the token")

    monkeypatch.setattr("seahorse.cli.setup.write_http_config", fail_write)
    _write_log(remote.server_log_file(cfg), LISTEN_LINE)
    out = _out()
    remote.run_remote_start(cfg, fmt="json", out=out, no_tunnel=True)
    assert _json(out)["token"] == "env-token"


def test_token_missing_everywhere_fails_loud(tmp_path, monkeypatch, spawn_calls) -> None:
    """Exit 99 BEFORE any spawn (the CliHttpTokenMissing contract: no socket
    may exist without the barrier)."""
    cfg = _cfg(tmp_path, token=None)
    monkeypatch.delenv("SEAHORSE_HTTP_TOKEN", raising=False)
    monkeypatch.setattr(
        "seahorse.cli.setup.write_http_config",
        lambda vault: config_path_for(vault).write_text(
            '[seahorse]\ndb_path = "seahorse.db"\n', encoding="utf-8"
        ),  # the write produced a config WITHOUT a token — the defense branch
    )
    with pytest.raises(CliHttpTokenMissing):
        remote.run_remote_start(cfg, fmt="human", out=_out(), no_tunnel=True)
    assert spawn_calls == []


# ---------------------------------------------------------------------------
# spawn argv + readiness + fail-loud teardown.
# ---------------------------------------------------------------------------


def test_server_argv_has_no_token(tmp_path, monkeypatch, spawn_calls) -> None:
    """The ps-leak guard: the token NEVER travels on a child's argv (the
    server child resolves it from [http]/env itself)."""
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("SEAHORSE_CLOUDFLARED_BIN", "/opt/fake/cloudflared")
    _write_log(remote.server_log_file(cfg), LISTEN_LINE)
    _write_log(remote.tunnel_log_file(cfg), TUNNEL_LINE)
    remote.run_remote_start(cfg, fmt="json", out=_out(), yes=True, port=9101)
    server_argv = spawn_calls[0]
    assert server_argv[0] == sys.executable
    assert server_argv[1:4] == ["-m", "seahorse.cli.app", "--vault"]
    assert server_argv[4] == str(cfg.vault)
    assert server_argv[5:] == ["mcp", "--transport", "http", "--port", "9101"]
    assert TOKEN not in " ".join(server_argv)
    # The listen line is the AUTHORITY for the bound port: the tunnel points
    # at the port the log reports (8767), not the one requested (9101) — in
    # reality they are the same line; the fake log just pins the behavior.
    assert spawn_calls[1] == [
        "/opt/fake/cloudflared",
        "tunnel",
        "--no-autoupdate",
        "--url",
        "http://127.0.0.1:8767",
    ]


def test_server_dies_before_listen_fails_101(tmp_path, spawn_calls) -> None:
    cfg = _cfg(tmp_path)
    # --no-tunnel isolates the server; empty log + dead fake pid → readiness
    # finds neither a line nor a live child.
    with pytest.raises(CliRemoteStartFailed) as exc_info:
        remote.run_remote_start(cfg, fmt="human", out=_out(), no_tunnel=True)
    assert exc_info.value.exit_code == 101
    assert "server" in exc_info.value.detail
    assert not remote.server_pid_file(cfg).exists()  # teardown removed what it spawned


def test_tunnel_deadline_tears_down_spawned_server(tmp_path, monkeypatch, spawn_calls) -> None:
    """Exit 101 names the tunnel AND removes the server it spawned — a
    half-started state must not survive the wizard."""
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("SEAHORSE_CLOUDFLARED_BIN", "/opt/fake/cloudflared")
    _write_log(remote.server_log_file(cfg), LISTEN_LINE)
    _write_log(remote.tunnel_log_file(cfg), "2026-10-01 INF Connection registered\n")
    with pytest.raises(CliRemoteStartFailed) as exc_info:
        remote.run_remote_start(cfg, fmt="human", out=_out(), yes=True)
    assert exc_info.value.exit_code == 101
    assert "tunnel" in exc_info.value.detail
    assert not remote.server_pid_file(cfg).exists()
    assert not remote.tunnel_pid_file(cfg).exists()
    assert len(spawn_calls) == 2  # both children were spawned, then torn down


def test_spawn_child_detaches_only_in_daemon_mode(tmp_path, monkeypatch) -> None:
    """Daemon children get a new session (survive the terminal); foreground
    children stay in ours (Ctrl-C reaches them through the process group)."""
    calls: list[dict] = []

    class _FakePopen:
        pid = FAKE_PID

        def __init__(self, argv, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(remote.subprocess, "Popen", _FakePopen)
    log = tmp_path / "child.log"
    remote._spawn_child(["sleep", "1"], log_path=log, start_new_session=True)
    remote._spawn_child(["sleep", "1"], log_path=log, start_new_session=False)
    # Only the daemon call carries the key — foreground children stay in this
    # session (stdout/stderr kwargs are the log handles, not part of the check).
    assert "start_new_session" in calls[0] and calls[0]["start_new_session"] is True
    assert "start_new_session" not in calls[1]
    assert log.exists()  # the append-mode log exists before the child writes


# ---------------------------------------------------------------------------
# idempotent reuse (the two independent pidfiles).
# ---------------------------------------------------------------------------


def test_start_reuses_live_children_without_spawning(tmp_path, spawn_calls) -> None:
    """os.getpid() is alive: start must not spawn (Popen would record it), not
    prompt (reuse is not a new exposure), and not terminate anything."""
    cfg = _cfg(tmp_path)
    _write_pid(remote.server_pid_file(cfg), os.getpid())
    _write_pid(remote.tunnel_pid_file(cfg), os.getpid())
    _write_log(remote.server_log_file(cfg), LISTEN_LINE)
    _write_log(remote.tunnel_log_file(cfg), TUNNEL_LINE)
    out = _out()
    remote.run_remote_start(cfg, fmt="json", out=out)  # no --yes: reuse needs no consent
    payload = _json(out)
    assert payload["server"]["reused"] is True
    assert payload["tunnel"]["reused"] is True
    assert payload["tunnel"]["url"] == TUNNEL_URL
    assert spawn_calls == []


def test_tunnel_survives_server_respawn_same_url(tmp_path, spawn_calls) -> None:
    """THE independence contract: a dead server + live tunnel → the server is
    respawned, the tunnel is reused UNTOUCHED (same public URL — cloudflared
    retries its origin)."""
    cfg = _cfg(tmp_path)
    _write_pid(remote.tunnel_pid_file(cfg), os.getpid())
    _write_log(remote.tunnel_log_file(cfg), TUNNEL_LINE)
    _write_pid(remote.server_pid_file(cfg), FAKE_PID)  # dead
    _write_log(remote.server_log_file(cfg), LISTEN_LINE)
    out = _out()
    remote.run_remote_start(cfg, fmt="json", out=out)
    payload = _json(out)
    assert payload["server"]["reused"] is False
    assert payload["tunnel"]["reused"] is True
    assert payload["tunnel"]["url"] == TUNNEL_URL
    assert len(spawn_calls) == 1  # only the server
    assert "mcp" in spawn_calls[0]


def test_start_with_live_tunnel_but_no_log_url_warns(tmp_path, spawn_calls) -> None:
    """A live tunnel whose log lost the URL: honest degraded state, no invented
    URL, no second tunnel spawned (the live pid IS public exposure)."""
    cfg = _cfg(tmp_path)
    _write_pid(remote.server_pid_file(cfg), os.getpid())
    _write_pid(remote.tunnel_pid_file(cfg), os.getpid())
    _write_log(remote.server_log_file(cfg), LISTEN_LINE)
    _write_log(remote.tunnel_log_file(cfg), "no url here\n")
    out = _out()
    remote.run_remote_start(cfg, fmt="json", out=out)
    payload = _json(out)
    assert payload["tunnel"]["url"] is None
    assert payload["tunnel"]["reused"] is True
    assert spawn_calls == []


# ---------------------------------------------------------------------------
# the security gate.
# ---------------------------------------------------------------------------


def test_confirm_tunnel_yes_skips_prompt() -> None:
    assert remote._confirm_tunnel(yes=True) is True


def test_confirm_tunnel_non_tty_without_yes_refuses(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO())  # isatty() is False
    monkeypatch.setattr("sys.stdout", io.StringIO())
    with pytest.raises(CliUsageError):
        remote._confirm_tunnel(yes=False)


def test_confirm_tunnel_tty_declined(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", _FakeTTYOut())
    monkeypatch.setattr("sys.stdout", _FakeTTYOut())
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert remote._confirm_tunnel(yes=False) is False


def test_confirm_tunnel_tty_accepted(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", _FakeTTYOut())
    monkeypatch.setattr("sys.stdout", _FakeTTYOut())
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert remote._confirm_tunnel(yes=False) is True


def test_start_declined_renders_aborted_no_spawn(tmp_path, monkeypatch, spawn_calls) -> None:
    """Declined consent is exit-0 semantics: an honest "aborted", never an
    error — and never a spawned child."""
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("SEAHORSE_CLOUDFLARED_BIN", "/opt/fake/cloudflared")
    monkeypatch.setattr(remote, "_confirm_tunnel", lambda **kwargs: False)
    out = _out()
    remote.run_remote_start(cfg, fmt="json", out=out)
    assert _json(out) == {"started": False, "aborted": True}
    assert spawn_calls == []


# ---------------------------------------------------------------------------
# stop + status.
# ---------------------------------------------------------------------------


def test_stop_with_nothing_running_is_a_no_op(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    out = _out()
    remote.run_remote_stop(cfg, fmt="json", out=out)
    assert _json(out) == {"stopped": False}


def test_stop_kills_real_children_tunnel_first(tmp_path, monkeypatch) -> None:
    """Real ``sleep`` children prove the SIGTERM path: exposure (tunnel)
    closes FIRST, then the server; both pidfiles are reaped and removed."""
    cfg = _cfg(tmp_path)
    server = subprocess.Popen(["sleep", "30"])
    tunnel = subprocess.Popen(["sleep", "30"])
    _write_pid(remote.server_pid_file(cfg), server.pid)
    _write_pid(remote.tunnel_pid_file(cfg), tunnel.pid)
    monkeypatch.setattr(remote, "STOP_TERM_WAIT_S", 0.2)  # zombies answer signal 0
    kills: list[tuple[int, int]] = []
    real_kill = os.kill

    def record_kill(pid: int, sig: int) -> None:
        if sig != 0:  # pid-alive probes use signal 0 — not a kill
            kills.append((pid, sig))
        real_kill(pid, sig)

    monkeypatch.setattr("os.kill", record_kill)
    try:
        out = _out()
        remote.run_remote_stop(cfg, fmt="json", out=out)
        server.wait(timeout=5)
        tunnel.wait(timeout=5)
    finally:
        for proc in (server, tunnel):
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
    terms = [kill for kill in kills if kill[1] == signal.SIGTERM]
    assert terms == [(tunnel.pid, signal.SIGTERM), (server.pid, signal.SIGTERM)]
    payload = _json(out)
    assert payload["stopped"] is True
    assert payload["tunnel_pid"] == tunnel.pid
    assert payload["server_pid"] == server.pid
    assert not remote.server_pid_file(cfg).exists()
    assert not remote.tunnel_pid_file(cfg).exists()
    for proc in (server, tunnel):  # truly gone, not zombie
        with pytest.raises(ProcessLookupError):
            os.kill(proc.pid, 0)


def test_stop_cleans_stale_pidfiles(tmp_path) -> None:
    """A dead pid (reaped long ago) is not an error: the stale pidfile is
    swept and stop reports not-running."""
    cfg = _cfg(tmp_path)
    _write_pid(remote.server_pid_file(cfg), FAKE_PID)
    _write_pid(remote.tunnel_pid_file(cfg), FAKE_PID)
    out = _out()
    remote.run_remote_stop(cfg, fmt="human", out=out)
    assert out.getvalue().strip() == "remote: not running"
    assert not remote.server_pid_file(cfg).exists()
    assert not remote.tunnel_pid_file(cfg).exists()


def test_status_not_running(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    out = _out()
    remote.run_remote_status(cfg, fmt="json", out=out)
    assert _json(out) == {"running": False}


def test_status_reextracts_urls_from_logs(tmp_path) -> None:
    """status is the scrollback-recovery command: live pids + the logs
    reconstruct the full paste-ready state without spawning anything."""
    cfg = _cfg(tmp_path)
    _write_pid(remote.server_pid_file(cfg), os.getpid())
    _write_pid(remote.tunnel_pid_file(cfg), os.getpid())
    _write_log(remote.server_log_file(cfg), LISTEN_LINE)
    _write_log(remote.tunnel_log_file(cfg), TUNNEL_LINE)
    out = _out()
    remote.run_remote_status(cfg, fmt="json", out=out)
    payload = _json(out)
    assert payload["running"] is True
    assert payload["server"]["url"] == SERVER_URL
    assert payload["tunnel"]["url"] == TUNNEL_URL
    assert payload["mcp_url"] == f"{TUNNEL_URL}/mcp"
    assert payload["token"] == TOKEN
    assert set(payload["instructions"]) == set(APP_KEYS)


def test_status_degraded_tunnel_warns_honestly(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    _write_pid(remote.tunnel_pid_file(cfg), os.getpid())
    _write_log(remote.tunnel_log_file(cfg), "no url here\n")
    out = _out()
    remote.run_remote_status(cfg, fmt="human", out=out)
    text = out.getvalue()
    assert "⚠ tunnel: running" in text
    assert "URL not in log" in text


# ---------------------------------------------------------------------------
# foreground mode.
# ---------------------------------------------------------------------------


def test_foreground_dies_with_child_error(tmp_path) -> None:
    """A child dying under --foreground: teardown runs, then the wizard fails
    loud naming the child (a silent exit would look like a clean stop)."""
    cfg = _cfg(tmp_path)
    _write_pid(remote.server_pid_file(cfg), FAKE_PID)
    _write_pid(remote.tunnel_pid_file(cfg), FAKE_PID)
    with pytest.raises(CliRemoteStartFailed) as exc_info:
        remote._run_foreground(cfg, server_pid=FAKE_PID, tunnel_pid=FAKE_PID)
    assert "server" in exc_info.value.detail
    assert not remote.server_pid_file(cfg).exists()
    assert not remote.tunnel_pid_file(cfg).exists()


def test_foreground_ctrl_c_tears_down_cleanly(tmp_path, monkeypatch) -> None:
    """Ctrl-C: teardown both, NO CliRemoteStartFailed — interrupting your own
    foreground wizard is a clean stop, not a failure."""
    cfg = _cfg(tmp_path)
    server = subprocess.Popen(["sleep", "30"])
    tunnel = subprocess.Popen(["sleep", "30"])
    _write_pid(remote.server_pid_file(cfg), server.pid)
    _write_pid(remote.tunnel_pid_file(cfg), tunnel.pid)
    # Zombies answer signal 0 — keep _terminate's graceful budget short.
    monkeypatch.setattr(remote, "STOP_TERM_WAIT_S", 0.2)
    # The loop's FIRST sleep is the Ctrl-C; every later sleep (inside
    # _terminate) must behave so the teardown can finish.
    sleeps = {"n": 0}

    def interrupt_first(_s: float) -> None:
        sleeps["n"] += 1
        if sleeps["n"] == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(remote.time, "sleep", interrupt_first)
    remote._run_foreground(cfg, server_pid=server.pid, tunnel_pid=tunnel.pid)
    assert not remote.server_pid_file(cfg).exists()
    assert not remote.tunnel_pid_file(cfg).exists()
    for proc in (server, tunnel):  # reaped → truly gone, not zombie
        proc.wait(timeout=5)
        with pytest.raises(ProcessLookupError):
            os.kill(proc.pid, 0)


# ---------------------------------------------------------------------------
# liveness + small helpers.
# ---------------------------------------------------------------------------


def test_liveness_absent_pidfiles(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    assert remote.server_liveness(cfg) == (False, None)
    assert remote.tunnel_liveness(cfg) == (False, None)


def test_liveness_live_and_stale(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    _write_pid(remote.server_pid_file(cfg), os.getpid())
    _write_pid(remote.tunnel_pid_file(cfg), FAKE_PID)
    assert remote.server_liveness(cfg) == (True, os.getpid())
    assert remote.tunnel_liveness(cfg) == (False, None)


def test_bound_port_parses_listen_url() -> None:
    assert remote._bound_port("http://127.0.0.1:8767") == 8767


def test_mcp_url_prefers_tunnel() -> None:
    assert remote._mcp_url(TUNNEL_URL, SERVER_URL) == f"{TUNNEL_URL}/mcp"
    assert remote._mcp_url(None, SERVER_URL) == f"{SERVER_URL}/mcp"
    assert remote._mcp_url(None, None) is None


# ---------------------------------------------------------------------------
# CLI wiring (the ``commands/remote.py`` group through the real Typer app).
# ---------------------------------------------------------------------------


def test_app_choices_covers_every_filter_key() -> None:
    """``--app`` validates against exactly the keys the blocks filter by."""
    assert set(APP_CHOICES) == {"all", "chatgpt", "gemini", "gemini-cli", "claude-code"}


def test_remote_help_lists_start_stop_status(vault) -> None:
    code, out, err = invoke(["--vault", str(vault), "remote", "--help"])
    assert code == 0, err
    for word in ("start", "stop", "status"):
        assert word in out


def test_remote_status_not_running_reports_cleanly(vault) -> None:
    code, out, err = invoke(["--vault", str(vault), "--json", "remote", "status"])
    assert code == 0, err
    assert json.loads(out) == {"running": False}


def test_remote_start_rejects_unknown_app(vault) -> None:
    code, out, err = invoke(
        ["--vault", str(vault), "remote", "start", "--app", "slack"]
    )
    assert code == EXIT_USAGE
    assert "slack" in err
    for choice in APP_CHOICES:  # the message teaches the valid values
        assert choice in err