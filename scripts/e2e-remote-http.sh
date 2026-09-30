#!/usr/bin/env bash
# End-to-end: the remote MCP server over Streamable HTTP (ADR-013) on the REAL
# wire, then the same server through a REAL `claude` client registration.
#
# Sandbox isolation (pattern: scripts/e2e-fresh-user.sh): HOME is redirected to
# $SANDBOX/home and the SEAHORSE_* overrides point into the sandbox, so
# `seahorse setup` (hooks, config, observer, MCP registration, [http] token)
# and `claude mcp add` (local-scope registry) never touch the developer's real
# setup. The server binds an ephemeral port (--port 0, listen line on stderr)
# and its URL never leaves loopback.
#
# The CLI runs from the repo venv (`uv run --project`) — NOT `uv tool install`,
# which would overwrite the user's installed seahorse-memory tool.
#
# Usage: scripts/e2e-remote-http.sh [--keep]   (--keep preserves the sandbox)
# Not CI-gated by design: the `claude -p` step makes a real model call.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -d /private/tmp && -w /private/tmp ]]; then
  SANDBOX_BASE="/private/tmp"
else
  SANDBOX_BASE="${TMPDIR:-/tmp}"
fi
SANDBOX="$SANDBOX_BASE/seahorse-e2e-remote-http-$(date +%s)"
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
LISTEN_TIMEOUT=30

info() { echo "$*" | tee -a "$LOG" >&2; }
ok()   { PASS=$((PASS + 1)); info "  ✅ $1"; }
fail() { FAIL=$((FAIL + 1)); FAILED_STEPS+=("$1"); info "  ❌ $1"; }
run()  { local title="$1"; shift; if "$@" >>"$LOG" 2>&1; then ok "$title"; else fail "$title"; fi; }
check() { local title="$1"; shift; if "$@" >>"$LOG" 2>&1; then ok "$title"; else fail "$title"; fi; }

SEAHORSE=(uv run --project "$REPO_DIR" seahorse)
SERVER_PID=""
URL=""

cleanup() {
  local rc=${?}   # a set -e kill must not be masked as success
  "${SEAHORSE[@]}" observe stop >/dev/null 2>&1 || true
  if [[ -n "$SERVER_PID" ]]; then
    pkill -INT -f "seahorse.mcp --vault $VAULT" >/dev/null 2>&1 || true
    sleep 1
    kill -9 "$SERVER_PID" >/dev/null 2>&1 || true
  fi
  if (( FAIL > 0 )); then
    info ""
    info "❌ e2e-remote-http FAILED (${FAIL} failures)"
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

read_token() {
  # tomllib needs python3.11+; the repo venv (via uv run) guarantees a modern one.
  uv run --project "$REPO_DIR" python - "$VAULT/.seahorse/seahorse.toml" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as fh:
    print(tomllib.load(fh).get("http", {}).get("token", ""))
PY
}

# _curl_post BODY TOKEN OUTFILE → HTTP status on stdout; headers → $SANDBOX/headers.txt
_curl_post() {
  local body="$1" token="$2" extra=()
  [[ -n "$token" ]] && extra=(-H "Authorization: Bearer $token")
  curl -sS --max-time 15 -o "$3" -D "$SANDBOX/headers.txt" -w '%{http_code}' \
    "${extra[@]+"${extra[@]}"}" \
    -H 'Accept: application/json' \
    -H 'Content-Type: application/json' \
    -d "$body" "$URL"
}

check_initialize() {
  local code version
  code="$(_curl_post "$BODY_INIT" "$TOKEN" "$SANDBOX/resp-init.json")" || true
  # Parsed, not grepped: the server serializes with json.dumps defaults, so the
  # body carries '"protocolVersion": "2025-11-25"' WITH a space after the colon.
  version="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("result", {}).get("protocolVersion", ""))' \
    "$SANDBOX/resp-init.json")" || true
  [[ "$code" -eq 200 && "$version" == "2025-11-25" ]]
}

check_tools_15() {
  local code count
  code="$(_curl_post "$BODY_TOOLS" "$TOKEN" "$SANDBOX/resp-tools.json")" || true
  count="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["result"]["tools"]))' \
    "$SANDBOX/resp-tools.json")" || true
  [[ "$code" -eq 200 && "$count" -eq 15 ]]
}

check_remember_active() {
  local code
  code="$(_curl_post "$BODY_REMEMBER" "$TOKEN" "$SANDBOX/resp-remember.json")" || true
  [[ "$code" -eq 200 ]] && grep -q 'ACTIVE' "$SANDBOX/resp-remember.json"
}

check_notify_202() {
  local code
  code="$(_curl_post "$BODY_NOTIFY" "$TOKEN" "$SANDBOX/resp-notify.json")" || true
  [[ "$code" -eq 202 ]]
}

check_401() {
  local code
  code="$(_curl_post "$BODY_INIT" "" "$SANDBOX/resp-401.json")" || true
  [[ "$code" -eq 401 ]]
}

check_get_405() {
  local code
  code="$(curl -sS --max-time 15 -o /dev/null -D "$SANDBOX/get.headers" \
    -w '%{http_code}' -H "Authorization: Bearer $TOKEN" -H 'Accept: application/json' \
    "$URL")" || true
  [[ "$code" -eq 405 ]] || return 1
  grep -qi '^allow:.*post' "$SANDBOX/get.headers"
}

wait_for_listen() {
  local deadline=$(( $(date +%s) + LISTEN_TIMEOUT ))
  while ! grep -q "listening on http://" "$SANDBOX/server.log" 2>/dev/null; do
    kill -0 "$SERVER_PID" 2>/dev/null || return 1   # died before binding
    (( $(date +%s) > deadline )) && return 1
    sleep 0.5
  done
}

# Bodies: id'd requests answer 200; notifications answer 202 (spec 2025-11-25).
BODY_INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
BODY_TOOLS='{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
BODY_REMEMBER='{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"remember","arguments":{"body":"kickoff landmark for the remote-http e2e","by":{"agent_id":"curl","session_id":"e2e","source_type":"agent"}}}}'
BODY_NOTIFY='{"jsonrpc":"2.0","method":"notifications/initialized"}'

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
run "seahorse setup --skip-llm (hooks + config + observer + MCP + [http] token)" \
  "${SEAHORSE[@]}" setup --skip-llm

TOKEN="$(read_token || true)"
check "http bearer token written and non-empty ([http] in seahorse.toml)" test -n "$TOKEN"

info "  ▶ spawning seahorse-mcp --transport http --port 0 (env token wiring)"
# Job control ON for the spawn: a non-interactive shell runs `&` jobs with
# SIGINT *ignored* (POSIX async-job rule) — and CPython then installs no
# KeyboardInterrupt handler, so kill -INT would be a silent no-op and the
# server would never unwind. The venv python is spawned DIRECTLY (not via
# `uv run`, which wraps the child in its own pid) so $SERVER_PID IS the
# server and every signal lands where it is aimed.
set -m
SEAHORSE_HTTP_TOKEN="$TOKEN" "$REPO_DIR/.venv/bin/python" -m seahorse.mcp \
  --vault "$VAULT" --transport http --port 0 \
  >>"$SANDBOX/server.log" 2>&1 &
SERVER_PID=$!
set +m

check "seahorse-mcp bound after --port 0 (listen line on stderr)" wait_for_listen
ADDR="$(grep -m1 -oE 'listening on http://[^ ]+' "$SANDBOX/server.log" | sed 's|listening on http://||')"
URL="http://${ADDR}/mcp"
info "  ▶ endpoint: $URL"

# -- wire protocol over curl (each spec row once, the plan's five) ---------
check "initialize → 200 + protocolVersion 2025-11-25" check_initialize
check "tools/list → 200 with exactly 15 tools"        check_tools_15
check "remember → 200 + ACTIVE"                        check_remember_active
check "notification → 202 + empty body"                check_notify_202
check "unauthenticated POST → 401"                     check_401
check "GET → 405 + Allow: POST (no SSE)"               check_get_405

# -- real claude client ----------------------------------------------------
if command -v claude >/dev/null 2>&1; then
  python3 - "$HOME/.claude.json" <<'PY' >>"$LOG" 2>&1
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
data = json.loads(p.read_text()) if p.exists() and p.read_text().strip() else {}
data.setdefault("hasCompletedOnboarding", True)  # headless `claude -p` skips the wizard
p.write_text(json.dumps(data, indent=2))
PY
  run "merge hasCompletedOnboarding into the sandbox ~/.claude.json" test -s "$HOME/.claude.json"

  # --header is VARIADIC in `claude mcp add` — it comes LAST so it cannot
  # swallow the positional name/url.
  run "claude mcp add --transport http + --header (bearer token)" \
    claude mcp add -s local --transport http seahorse-remote "$URL" \
    --header "Authorization: Bearer $TOKEN"

  check_mcp_get() {
    local listing
    listing="$(claude mcp get seahorse-remote 2>&1)" || true
    echo "$listing" >>"$LOG"
    [[ -n "$listing" ]] && echo "$listing" | grep -q -- "$ADDR"
  }
  check "claude mcp get shows the registered remote" check_mcp_get

  check_claude_invoke() {
    local out
    out="$(claude -p 'Call the remember tool of the seahorse-remote MCP server with body "kickoff landmark for the remote-http e2e" and by {"agent_id":"claude-remote","session_id":"e2e","source_type":"agent"}; then call recall with query "kickoff" and report what came back. Use only those two MCP calls.' \
      --allowedTools "mcp__seahorse-remote__remember" \
      --allowedTools "mcp__seahorse-remote__recall" \
      --max-turns 6 --output-format text 2>&1)" || true
    echo "$out" >>"$LOG"
    [[ -n "${out//[[:space:]]/}" && "$out" == *kickoff* ]]
  }
  check "claude -p invokes the remote server end-to-end" check_claude_invoke

  claude mcp remove seahorse-remote >>"$LOG" 2>&1 || true   # cleanup, never gating
else
  info "ℹ️  claude CLI not on PATH — wiring + invocation steps skipped"
fi

# ----------------------------------------------------------------- teardown
info "  ▶ teardown: SIGINT the server, uninstall the sandbox setup"
kill -INT "$SERVER_PID" 2>/dev/null || true
wait_server_exit() {
  local i
  for i in {1..30}; do
    kill -0 "$SERVER_PID" 2>/dev/null || return 0
    sleep 0.5
  done
  return 1
}
check "server exits on SIGINT (serve_http unwinds clean)" wait_server_exit
run "seahorse setup --uninstall (removes MCP registration + stops observer)" \
  "${SEAHORSE[@]}" setup --uninstall
check_no_leaked_server() {
  [[ -n "$URL" ]] && ! pgrep -f "seahorse.mcp --vault $VAULT" >/dev/null 2>&1
}
check "no orphan seahorse.mcp server processes" check_no_leaked_server
# SERVER_PID stays set: the EXIT trap's pkill/kill-9 (vault-scoped pattern,
# never matching the developer's real server) is the safety net for orphans.

# ------------------------------------------------------------------- report
info ""
info "=== e2e-remote-http: $PASS checks passed, $FAIL failed ==="
if (( KEEP )); then info "sandbox kept for inspection: $SANDBOX (transcript: $LOG)"; fi
exit 0