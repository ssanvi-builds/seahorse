#!/usr/bin/env bash
#
# e2e-harnesses.sh — multi-harness onboarding end-to-end (setup --harness).
#
# Fully isolated: a sandbox HOME plus a SEAHORSE_* env override for every
# harness config path. Fake configs with foreign content are written BEFORE
# the run, so the suite proves the guarantees that matter: foreign keys
# preserved verbatim, seahorse-mcp registered idempotently, doctor green,
# uninstall symmetric, and the original foreign content intact afterwards.
#
# The CLI runs from the repo venv (`uv run --project`) — NOT `uv tool install`,
# which would overwrite the user's installed seahorse-memory tool. Packaging
# itself is covered by e2e-fresh-user.sh / e2e-pypi.sh.
#
# Usage:
#   scripts/e2e-harnesses.sh [--keep]
#
#   --keep   keep the sandbox after the run (default: remove it)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ -d /private/tmp && -w /private/tmp ]]; then
  SANDBOX_BASE="/private/tmp"
else
  SANDBOX_BASE="${TMPDIR:-/tmp}"
fi
SANDBOX="$SANDBOX_BASE/seahorse-e2e-harnesses-$(date +%s)"
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

run() {  # run <title> <cmd...> — failure is recorded, not fatal
  local title="$1"; shift
  info "  ▶ $title"
  if "$@" >>"$LOG" 2>&1; then
    ok "$title"
  else
    fail "$title"
  fi
}

check() {  # check <title> <cmd...>
  local title="$1"; shift
  if "$@" >>"$LOG" 2>&1; then
    ok "$title"
  else
    fail "$title"
  fi
}

cleanup() {
  # The sandbox env is still exported here, so this stops the sandbox observer.
  uv run --project "$REPO_DIR" seahorse observe stop >/dev/null 2>&1 || true
  if (( KEEP )); then
    info "🧹 kept sandbox: $SANDBOX"
  else
    rm -rf "$SANDBOX"
    mkdir -p "$SANDBOX"  # summary lines below still tee to $LOG
  fi
  if (( FAIL > 0 )); then
    info ""
    info "❌ e2e-harnesses FAILED (${FAIL} failures):"
    for f in "${FAILED_STEPS[@]}"; do info "   - $f"; done
    info "log: $LOG"
    exit 1
  fi
  info ""
  info "✅ e2e-harnesses passed (${PASS} checks)"
}
trap cleanup EXIT

mkdir -p "$SANDBOX/home"
cd "$SANDBOX"
REAL_HOME="${REAL_HOME:-$HOME}"  # still the caller's HOME (sandboxed below)
if [[ -d "$REAL_HOME/.cache/uv" ]]; then
  export UV_CACHE_DIR="$REAL_HOME/.cache/uv"
fi
export FASTEMBED_CACHE_PATH="$SANDBOX_BASE/seahorse-e2e-cache"
export SEAHORSE_VAULT="$VAULT"
export HOME="$SANDBOX/home"
# Every harness config path → sandbox (the whole point of this e2e).
export SEAHORSE_CLAUDE_JSON="$HOME/.claude.json"
export SEAHORSE_CODEX_CONFIG="$HOME/.codex/config.toml"
export SEAHORSE_CURSOR_MCP_JSON="$HOME/.cursor/mcp.json"
export SEAHORSE_VSCODE_MCP_JSON="$HOME/.config/Code/User/mcp.json"
export SEAHORSE_ANTIGRAVITY_CONFIG="$HOME/.gemini/config/mcp_config.json"
export SEAHORSE_GEMINI_SETTINGS="$HOME/.gemini/settings.json"
# Global instruction files (the agent-instructions block per harness).
# Gemini CLI and Antigravity share ~/.gemini/GEMINI.md — double install is a no-op.
export SEAHORSE_CODEX_AGENTS_MD="$HOME/.codex/AGENTS.md"
export SEAHORSE_GEMINI_MD="$HOME/.gemini/GEMINI.md"
export SEAHORSE_ANTIGRAVITY_MD="$HOME/.gemini/GEMINI.md"
export SEAHORSE_CLAUDE_MD="$HOME/.claude/CLAUDE.md"
export SEAHORSE_CLAUDE_SETTINGS="$HOME/.claude/settings.json"
export SEAHORSE_CLAUDE_SKILLS_DIR="$HOME/.claude/skills"
export SEAHORSE_CREDENTIALS="$HOME/.config/seahorse/credentials.json"

info "sandbox: $SANDBOX"
info "log:     $LOG"

resolve_path() {  # resolve_path <name> → that harness's sandbox config path
  case "$1" in
    codex)       echo "$SEAHORSE_CODEX_CONFIG" ;;
    cursor)      echo "$SEAHORSE_CURSOR_MCP_JSON" ;;
    vscode)      echo "$SEAHORSE_VSCODE_MCP_JSON" ;;
    antigravity) echo "$SEAHORSE_ANTIGRAVITY_CONFIG" ;;
    gemini)      echo "$SEAHORSE_GEMINI_SETTINGS" ;;
    claude)      echo "$SEAHORSE_CLAUDE_JSON" ;;
    *) echo "unknown harness: $1" >&2; return 2 ;;
  esac
}

# --- fake configs with foreign content (written BEFORE any seahorse run) ----
mkdir -p "$(dirname "$SEAHORSE_CODEX_CONFIG")" "$(dirname "$SEAHORSE_CURSOR_MCP_JSON")" \
         "$(dirname "$SEAHORSE_VSCODE_MCP_JSON")" "$(dirname "$SEAHORSE_ANTIGRAVITY_CONFIG")" \
         "$(dirname "$SEAHORSE_GEMINI_SETTINGS")"
printf '# user config\nmodel = "o4-mini"\n\n[profiles.work]\nmodel = "gpt-5.3"\n' \
  > "$SEAHORSE_CODEX_CONFIG"
printf '{"model": "foreign-model", "mcpServers": {"other": {"command": "other-cmd"}}}\n' \
  > "$SEAHORSE_CURSOR_MCP_JSON"
printf '{"chat.commandCenter.enabled": true, "servers": {"other": {"type": "stdio", "command": "other-cmd"}}}\n' \
  > "$SEAHORSE_VSCODE_MCP_JSON"
printf '{"theme": "dark", "mcpServers": {"other": {"command": "other-cmd"}}}\n' \
  > "$SEAHORSE_ANTIGRAVITY_CONFIG"
printf '{"theme": "auto", "mcpServers": {"other": {"command": "other-cmd"}}}\n' \
  > "$SEAHORSE_GEMINI_SETTINGS"
printf '{"numStartups": 42, "mcpServers": {"other": {"type": "stdio", "command": "other-cmd"}}}\n' \
  > "$SEAHORSE_CLAUDE_JSON"
printf '# my global agent rules\n- be terse\n' > "$SEAHORSE_CODEX_AGENTS_MD"
mkdir -p "$(dirname "$SEAHORSE_GEMINI_MD")"
printf 'My global rules for Gemini.\n' > "$SEAHORSE_GEMINI_MD"

check "fake configs written" test -f "$SEAHORSE_CODEX_CONFIG" -a -f "$SEAHORSE_CLAUDE_JSON"

# --- CLI: run from the repo venv (never touches the user's tool install) ------
info ""
info "── CLI via repo venv ──"
if bash -c "cd '$REPO_DIR' && uv run seahorse --help" >>"$LOG" 2>&1; then
  ok "seahorse CLI runs from repo venv"
else
  fail "seahorse CLI runs from repo venv"
  exit 1
fi
seahorse_cli() { uv run --project "$REPO_DIR" seahorse "$@"; }

# --- init the sandbox vault (setup/uninstall/doctor resolve through it) --------
info ""
info "── init sandbox vault ──"
run "seahorse init (sandbox vault)" seahorse_cli init "$VAULT"

# --- setup --harness: all six destinations ------------------------------------
info ""
info "── setup --harness (6 destinations, --skip-llm) ──"
run "seahorse setup --skip-llm --harness codex,cursor,vscode,antigravity,gemini,claude-code" \
  seahorse_cli setup --skip-llm \
  --harness codex,cursor,vscode,antigravity,gemini,claude-code

verify_registered() {  # verify_registered <file> <kind> [key]
  python3 - "$1" "$2" "${3:-mcpServers}" <<'EOF'
import json, sys, tomllib
path, kind, key = sys.argv[1], sys.argv[2], sys.argv[3]
if kind == "toml":
    data = tomllib.loads(open(path).read())
    servers = data.get("mcp_servers", {})
    print(servers.get("seahorse-mcp", {}).get("command", ""))
else:
    data = json.load(open(path))
    servers = data.get(key, {})
    print(servers.get("seahorse-mcp", {}).get("command", ""))
EOF
}

for dest in "codex:toml:" "cursor:json:mcpServers" "vscode:json:servers" \
            "antigravity:json:mcpServers" "gemini:json:mcpServers" "claude:json:mcpServers"; do
  IFS=":" read -r name kind key <<<"$dest"
  path="$(resolve_path "$name")"
  result="$(verify_registered "$path" "$kind" "$key")"
  if [[ "$result" == "seahorse-mcp" ]]; then
    ok "$name registered"
  else
    fail "$name registered (got: '$result')"
  fi
done

check "codex foreign model preserved" grep -q 'model = "o4-mini"' "$SEAHORSE_CODEX_CONFIG"
check "codex foreign profile preserved" grep -q 'gpt-5.3' "$SEAHORSE_CODEX_CONFIG"
check "cursor foreign server preserved" python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
sys.exit(0 if d['mcpServers'].get('other', {}).get('command') == 'other-cmd' else 1)
" "$SEAHORSE_CURSOR_MCP_JSON"
check "vscode foreign setting preserved" python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
sys.exit(0 if d['chat.commandCenter.enabled'] is True else 1)
" "$SEAHORSE_VSCODE_MCP_JSON"
check "claude foreign key preserved" python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
sys.exit(0 if d['numStartups'] == 42 else 1)
" "$SEAHORSE_CLAUDE_JSON"

# --- instructions blocks: installed per harness, foreign content preserved ---
info ""
info "── agent instructions blocks ──"
check "codex AGENTS.md block installed" \
  grep -q "seahorse-memory:begin" "$SEAHORSE_CODEX_AGENTS_MD"
check "codex AGENTS.md foreign rules preserved" \
  grep -q 'be terse' "$SEAHORSE_CODEX_AGENTS_MD"
check "gemini GEMINI.md block installed" \
  grep -q "seahorse-memory:begin" "$SEAHORSE_GEMINI_MD"
check "gemini foreign rules preserved" \
  grep -q 'My global rules for Gemini' "$SEAHORSE_GEMINI_MD"
if [[ "$(grep -c 'seahorse-memory:begin' "$SEAHORSE_GEMINI_MD")" -eq 1 ]]; then
  ok "gemini+antigravity share GEMINI.md (exactly one block)"
else
  fail "gemini+antigravity share GEMINI.md (exactly one block)"
fi

# --- idempotency: second setup leaves files byte-identical ---------------------
info ""
info "── idempotency: second setup run ──"
sha_before="$(sha256sum "$SEAHORSE_CODEX_CONFIG" "$SEAHORSE_CURSOR_MCP_JSON" \
  "$SEAHORSE_VSCODE_MCP_JSON" "$SEAHORSE_ANTIGRAVITY_CONFIG" \
  "$SEAHORSE_GEMINI_SETTINGS" "$SEAHORSE_CLAUDE_JSON")"
run "seahorse setup again (idempotent)" seahorse_cli setup --skip-llm \
  --harness codex,cursor,vscode,antigravity,gemini,claude-code
sha_after="$(sha256sum "$SEAHORSE_CODEX_CONFIG" "$SEAHORSE_CURSOR_MCP_JSON" \
  "$SEAHORSE_VSCODE_MCP_JSON" "$SEAHORSE_ANTIGRAVITY_CONFIG" \
  "$SEAHORSE_GEMINI_SETTINGS" "$SEAHORSE_CLAUDE_JSON")"
if [[ "$sha_before" == "$sha_after" ]]; then
  ok "second setup is byte-identical (idempotent)"
else
  fail "second setup is byte-identical (idempotent)"
fi

# --- doctor: all per-harness checks green + --fix repairs a removal ------------
info ""
info "── doctor + doctor --fix ──"
run "seahorse doctor (json)" seahorse_cli --json doctor
if seahorse_cli --json doctor 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
checks = {c['check']: c['status'] for c in d['checks']}
mcp = {k: v for k, v in checks.items() if k.startswith('mcp_registered')}
sys.exit(0 if all(v == 'OK' for v in mcp.values()) and len(mcp) == 6 else 1)
"; then
  ok "doctor: all 6 mcp_registered checks OK"
else
  fail "doctor: all 6 mcp_registered checks OK"
fi

# Remove the codex block, then doctor --fix must repair it.
python3 - "$SEAHORSE_CODEX_CONFIG" <<'EOF'
import sys
lines = open(sys.argv[1]).readlines()
kept = []
inside = False
for line in lines:
    if line.startswith("# seahorse-mcp:begin"):
        inside = True
        continue
    if line.startswith("# seahorse-mcp:end"):
        inside = False
        continue
    if not inside:
        kept.append(line)
open(sys.argv[1], "w").writelines(kept)
EOF
if grep -q "seahorse-mcp" "$SEAHORSE_CODEX_CONFIG"; then
  fail "codex block removed before --fix"
else
  ok "codex block removed before --fix"
fi
run "seahorse doctor --fix (json)" seahorse_cli --json doctor --fix
result="$(verify_registered "$SEAHORSE_CODEX_CONFIG" toml)"
if [[ "$result" == "seahorse-mcp" ]]; then
  ok "doctor --fix re-registered codex"
else
  fail "doctor --fix re-registered codex"
fi

# --- uninstall: symmetric removal, foreign content intact ----------------------
info ""
info "── uninstall --harness (symmetric) ──"
run "seahorse setup --uninstall --harness codex,cursor,vscode,antigravity,gemini,claude-code" \
  seahorse_cli setup --uninstall \
  --harness codex,cursor,vscode,antigravity,gemini,claude-code

for dest in "codex:toml:" "cursor:json:mcpServers" "vscode:json:servers" \
            "antigravity:json:mcpServers" "gemini:json:mcpServers" "claude:json:mcpServers"; do
  IFS=":" read -r name kind key <<<"$dest"
  path="$(resolve_path "$name")"
  result="$(verify_registered "$path" "$kind" "$key")"
  if [[ -z "$result" ]]; then
    ok "$name entry removed"
  else
    fail "$name entry removed (got: '$result')"
  fi
done

check "codex foreign content STILL intact" \
  grep -q 'model = "o4-mini"' "$SEAHORSE_CODEX_CONFIG"
check "claude foreign content STILL intact" python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
sys.exit(0 if d['numStartups'] == 42 else 1)
" "$SEAHORSE_CLAUDE_JSON"
check "vscode foreign content STILL intact" python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
sys.exit(0 if d['servers'].get('other', {}).get('command') == 'other-cmd' else 1)
" "$SEAHORSE_VSCODE_MCP_JSON"
if [[ "$(grep -c 'seahorse-memory:begin' "$SEAHORSE_CODEX_AGENTS_MD")" -eq 0 ]]; then
  ok "codex instructions block removed"
else
  fail "codex instructions block removed"
fi
check "codex AGENTS.md foreign rules STILL intact" \
  grep -q 'be terse' "$SEAHORSE_CODEX_AGENTS_MD"
if [[ "$(grep -c 'seahorse-memory:begin' "$SEAHORSE_GEMINI_MD")" -eq 0 ]]; then
  ok "gemini instructions block removed"
else
  fail "gemini instructions block removed"
fi
check "gemini foreign rules STILL intact" \
  grep -q 'My global rules for Gemini' "$SEAHORSE_GEMINI_MD"