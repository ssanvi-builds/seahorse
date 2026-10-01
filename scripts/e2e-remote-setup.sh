#!/usr/bin/env bash
# End-to-end: the `seahorse remote` WIZARD — a REAL server daemon plus a FAKE
# cloudflared, sandboxed, zero network, CI-runnable.
#
# The wizard is the whole last mile for a non-technical user, so this e2e walks
# it end to end: token preflight (the wizard writes [http] itself when the
# config lacks it), the consent gate (--yes), both spawns, log-readiness, the
# paste-ready payload, idempotent reuse (start twice, same children), the REAL
# MCP wire against the spawned daemon (initialize / 15 tools / remember),
# status, stop, and the no-orphan contract.
#
# cloudflared is faked via SEAHORSE_CLOUDFLARED_BIN: a stub that prints a
# trycloudflare URL and sleeps. The wizard cannot tell it from the real one —
# it only reads the URL from the tunnel log and checks the pid — which is
# exactly the interface under test.
#
# Sandbox isolation (pattern: scripts/e2e-remote-http.sh): HOME is redirected
# to $SANDBOX/home and the SEAHORSE_* overrides point into the sandbox, so
# nothing (config writes, [http] token, spawned children) touches the
# developer's real setup. The server binds an ephemeral port (--port 0; the
# listen line is the authority).
#
# Set SEAHORSE_E2E_REAL_TUNNEL=1 to run against the REAL cloudflared (Sergio's
# machine only — it opens a REAL public tunnel; never CI).
#
# Usage: scripts/e2e-remote-setup.sh [--keep]   (--keep preserves the sandbox)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -d /private/tmp && -w /private/tmp ]]; then
  SANDBOX_BASE="/private/tmp"
else
  SANDBOX_BASE="${TMPDIR:-/tmp}"
fi
SANDBOX="$SANDBOX_BASE/seahorse-e2e-remote-setup-$(date +%s)"
LOG="$SANDBOX/e2e.log"
VAULT="$SANDBOX/vault"

KEEP=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep) KEEP=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

PASS=0
FAIL=0
FAILED_STEPS=()

info() { echo "$*" | tee -a "$LOG" >&2; }
ok()   { PASS=$((PASS + 1)); info "  ✅ $1"; }
fail() { FAIL=$((FAIL + 1)); FAILED_STEPS+=("$1"); info "  ❌ $1"; }
run()  { local title="$1"; shift; if "$@" >>"$LOG" 2>&1; then ok "$title"; else fail "$title"; fi; }
check() { local title="$1"; shift; if "$@" >>"$LOG" 2>&1; then ok "$title"; else fail "$title"; fi; }

SEAHORSE=(uv run --project "$REPO_DIR" seahorse)
SERVER_PID=""
TUNNEL_PID=""

cleanup() {
  local rc=${?}   # a set -e kill must not be masked as success
  "${SEAHORSE[@]}" remote stop >/dev/null 2>&1 || true
  if [[ -n "$SERVER_PID" ]]; then
    kill -9 "$SERVER_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$TUNNEL_PID" ]]; then
    kill -9 "$TUNNEL_PID" >/dev/null 2>&1 || true
  fi
  if (( FAIL > 0 )); then
    info ""
    info "❌ e2e-remote-setup FAILED (${FAIL} failures)"
    for step in "${FAILED_STEPS[@]}"; do info "   - $step"; done
    info "sandbox kept for inspection: $SANDBOX (transcript: $LOG)"
    exit 1
  fi
  if (( rc != 0 )); then
    exit "$rc"
  fi
  if (( KEEP )); then
    info "🧹 sandbox kept: $SANDBOX (transcript: $LOG)"
  else
    rm -rf "$SANDBOX"
  fi
  exit 0
}
trap cleanup EXIT

# ---------------------------------------------------------------- MCP bodies
BODY_INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
BODY_TOOLS='{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
BODY_REMEMBER='{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"remember","arguments":{"body":"kickoff landmark for the remote-setup e2e","by":{"agent_id":"e2e","session_id":"e2e-remote-setup","source_type":"agent"}}}}'

# ---------------------------------------------------------------- main flow
mkdir -p "$SANDBOX/home"
REAL_HOME="$HOME"
export HOME="$SANDBOX/home"
export UV_CACHE_DIR="$REAL_HOME/.cache/uv"
export FASTEMBED_CACHE_PATH="$SANDBOX_BASE/seahorse-e2e-cache"
export SEAHORSE_VAULT="$VAULT"
export SEAHORSE_CLAUDE_JSON="$SANDBOX/home/.claude.json"
export SEAHORSE_CLAUDE_MD="$SANDBOX/home/.claude/CLAUDE.md"
export SEAHORSE_CLAUDE_SETTINGS="$SANDBOX/home/.claude/settings.json"
export SEAHORSE_CLAUDE_SKILLS_DIR="$SANDBOX/home/.claude/skills"
export SEAHORSE_CREDENTIALS="$SANDBOX/home/.config/seahorse/credentials.json"

run "seahorse init (sandbox vault)" "${SEAHORSE[@]}" init "$VAULT"

if [[ "${SEAHORSE_E2E_REAL_TUNNEL:-0}" == "1" ]]; then
  info "  ▶ SEAHORSE_E2E_REAL_TUNNEL=1: the REAL cloudflared will open a PUBLIC tunnel"
else
  # The fake cloudflared: prints the URL the wizard's readiness poll wants,
  # then stays alive as a real child (log-readiness + pid-alive both hold).
  mkdir -p "$SANDBOX/bin"
  cat >"$SANDBOX/bin/cloudflared" <<'STUB'
#!/usr/bin/env bash
echo "2026-10-01T12:00:00Z INF  Registered tunnel connection https://e2e-fake.trycloudflare.com"
exec sleep 600
STUB
  chmod +x "$SANDBOX/bin/cloudflared"
  export SEAHORSE_CLOUDFLARED_BIN="$SANDBOX/bin/cloudflared"
fi

# ---------------------------------------------------------------- the wizard
info "  ▶ remote start --yes --port 0 (the wizard: token → gate → both children)"

if "${SEAHORSE[@]}" --json remote start --yes --port 0 >"$SANDBOX/start.json" 2>>"$LOG"; then
  ok "remote start exited 0"
else
  fail "remote start exited 0"
fi

check "start payload: started, live server, tunnel URL, token, all four app blocks" \
  python3 - "$SANDBOX/start.json" <<'PY'
import json, re, sys
p = json.load(open(sys.argv[1]))
assert p["started"] is True, p
assert p["server"]["pid"] > 0
assert p["server"]["url"].startswith("http://127.0.0.1:")
assert p["server"]["reused"] is False
assert re.match(r"https://[a-z0-9-]+\.trycloudflare\.com", p["tunnel"]["url"]), p["tunnel"]
assert p["tunnel"]["ephemeral"] is True
assert p["mcp_url"] == p["tunnel"]["url"] + "/mcp"
assert p["token"], p
assert set(p["instructions"]) == {"chatgpt", "gemini_web", "gemini_cli", "claude_code"}
assert "token is the only barrier" in p["warning"]
PY

SERVER_PID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["server"]["pid"])' "$SANDBOX/start.json")"
SERVER_URL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["server"]["url"])' "$SANDBOX/start.json")"
TUNNEL_PID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tunnel"]["pid"])' "$SANDBOX/start.json")"
TUNNEL_URL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tunnel"]["url"])' "$SANDBOX/start.json")"
TOKEN="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["token"])' "$SANDBOX/start.json")"

info "  ▶ server pid $SERVER_PID on $SERVER_URL, tunnel pid $TUNNEL_PID at $TUNNEL_URL"

# ---------------------------------------------------------------- idempotent reuse
if "${SEAHORSE[@]}" --json remote start --yes --port 0 >"$SANDBOX/restart.json" 2>>"$LOG"; then
  ok "second remote start exited 0 (idempotent)"
else
  fail "second remote start exited 0 (idempotent)"
fi

check "second start REUSES both live children (same pids, no respawn)" \
  env SERVER_PID="$SERVER_PID" TUNNEL_PID="$TUNNEL_PID" \
  python3 - "$SANDBOX/restart.json" <<'PY'
import json, os, sys
p = json.load(open(sys.argv[1]))
assert p["started"] is True, p
assert p["server"]["reused"] is True, p["server"]
assert p["tunnel"]["reused"] is True, p["tunnel"]
assert p["server"]["pid"] == int(os.environ["SERVER_PID"])
assert p["tunnel"]["pid"] == int(os.environ["TUNNEL_PID"])
assert p["tunnel"]["url"]  # reused tunnel keeps its URL from the log
PY

# ---------------------------------------------------------------- the real wire
MCP_URL="$SERVER_URL/mcp"

curl_post() {  # BODY OUTFILE → HTTP status on stdout
  local body="$1"
  curl -sS --max-time 15 -o "$2" -w '%{http_code}' \
    -H "Authorization: Bearer $TOKEN" \
    -H 'Accept: application/json' \
    -H 'Content-Type: application/json' \
    -d "$body" "$MCP_URL"
}

check_initialize() {
  local code version
  code="$(curl_post "$BODY_INIT" "$SANDBOX/resp-init.json")" || true
  version="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("result", {}).get("protocolVersion", ""))' \
    "$SANDBOX/resp-init.json")" || true
  [[ "$code" -eq 200 && "$version" == "2025-11-25" ]]
}

check_tools_15() {
  local code count
  code="$(curl_post "$BODY_TOOLS" "$SANDBOX/resp-tools.json")" || true
  count="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["result"]["tools"]))' \
    "$SANDBOX/resp-tools.json")" || true
  [[ "$code" -eq 200 && "$count" -eq 15 ]]
}

check_remember_active() {
  local code
  code="$(curl_post "$BODY_REMEMBER" "$SANDBOX/resp-remember.json")" || true
  [[ "$code" -eq 200 ]] && grep -q 'ACTIVE' "$SANDBOX/resp-remember.json"
}

check "initialize over the spawned daemon (200, protocol 2025-11-25)" check_initialize
check "tools/list over the spawned daemon (15 tools)" check_tools_15
check "remember over the spawned daemon (ACTIVE)" check_remember_active

# ---------------------------------------------------------------- status
if "${SEAHORSE[@]}" --json remote status >"$SANDBOX/status.json" 2>>"$LOG"; then
  ok "remote status exited 0"
else
  fail "remote status exited 0"
fi

check "status reports both children alive with the same pids and the token" \
  env SERVER_PID="$SERVER_PID" TUNNEL_PID="$TUNNEL_PID" TOKEN="$TOKEN" TUNNEL_URL="$TUNNEL_URL" \
  python3 - "$SANDBOX/status.json" <<'PY'
import json, os, sys
p = json.load(open(sys.argv[1]))
assert p["running"] is True, p
assert p["server"]["pid"] == int(os.environ["SERVER_PID"])
assert p["tunnel"]["pid"] == int(os.environ["TUNNEL_PID"])
assert p["tunnel"]["url"] == os.environ["TUNNEL_URL"]
assert p["token"] == os.environ["TOKEN"]
assert p["mcp_url"] == os.environ["TUNNEL_URL"] + "/mcp"
assert p["instructions"]
PY

# ---------------------------------------------------------------- stop
if "${SEAHORSE[@]}" --json remote stop >"$SANDBOX/stop.json" 2>>"$LOG"; then
  ok "remote stop exited 0"
else
  fail "remote stop exited 0"
fi

check "stop reports both children stopped" \
  env SERVER_PID="$SERVER_PID" TUNNEL_PID="$TUNNEL_PID" \
  python3 - "$SANDBOX/stop.json" <<'PY'
import json, os, sys
p = json.load(open(sys.argv[1]))
assert p["stopped"] is True, p
assert p["tunnel_pid"] == int(os.environ["TUNNEL_PID"])
assert p["server_pid"] == int(os.environ["SERVER_PID"])
PY

# ---------------------------------------------------------------- no orphans
orphan_check() {
  # A dead pid makes kill -0 fail with "No such process" — the no-orphan
  # contract is that BOTH children are gone when stop returns.
  if kill -0 "$1" 2>/dev/null; then return 1; fi
  [[ ! -e "$2" ]]  # and the pidfile is swept
}

check "server truly gone after stop (no zombie pidfile)" \
  orphan_check "$SERVER_PID" "$VAULT/.seahorse/remote/server.pid"
check "tunnel truly gone after stop (no zombie pidfile)" \
  orphan_check "$TUNNEL_PID" "$VAULT/.seahorse/remote/tunnel.pid"

# A second stop is a no-op success, not an error.
if "${SEAHORSE[@]}" --json remote stop >"$SANDBOX/stop2.json" 2>>"$LOG"; then
  ok "second remote stop is an idempotent no-op"
else
  fail "second remote stop is an idempotent no-op"
fi
check "second stop reports not-running" \
  python3 - "$SANDBOX/stop2.json" <<'PY'
import json, sys
assert json.load(open(sys.argv[1])) == {"stopped": False}
PY

info ""
info "e2e-remote-setup: ${PASS} passed, ${FAIL} failed"