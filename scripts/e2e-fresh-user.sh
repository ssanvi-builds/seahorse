#!/usr/bin/env bash
#
# e2e-fresh-user.sh — validate Seahorse from a fresh user's perspective, fully
# isolated from the real ~/.claude, ~/.claude-mem, and ~/obsidian-vaults.
#
# What it does (mirrors the README Quickstart + the agent surface):
#   install (uv tool install) → init → core CLI (remember/recall/improve/forget/
#   doctor/inspect/migrate/uuid7/index) → hybrid embeddings → LLM (Ollama, with
#   honest degrade) → setup/observer → import (claude-mem bridge) → MCP stdio.
#
# Isolation strategy:
#   * HOME is overridden to a temp sandbox — the master switch that neutralises
#     the only two global paths Seahorse touches: ~/.claude/settings.json
#     (seahorse setup) and ~/.claude-mem/claude-mem.db (import default source).
#   * The vault is a fresh temp dir; every command resolves it via SEAHORSE_VAULT.
#   * The embedding model downloads into a temp FASTEMBED_CACHE_PATH.
#   * The real claude-mem DB is only ever READ (sqlite3 .backup to the sandbox).
#   * Post-flight re-checks the real files' shasums and scans ~/obsidian-vaults
#     for any file touched during the run.
#
# Transparency: every command is echoed before it runs and its output is teed
# to the terminal AND to $SANDBOX/e2e.log — nothing is suppressed.
#
# Usage:
#   scripts/e2e-fresh-user.sh [--no-cache-reuse] [--step <n>]
#
#   --no-cache-reuse  do not reuse the real uv cache (fully isolated, slower)
#   --step <n>        run steps 0..n then stop (default: all steps)

set -euo pipefail

# --- resolve paths -----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SANDBOX="/private/tmp/seahorse-e2e-$(date +%s)"
LOG="$SANDBOX/e2e.log"
VAULT="$SANDBOX/vault"

# --- args --------------------------------------------------------------------
REUSE_UV_CACHE=1
STEP_LIMIT=999
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-cache-reuse) REUSE_UV_CACHE=0; shift ;;
    --step) STEP_LIMIT="$2"; shift 2 ;;
    --step=*) STEP_LIMIT="${1#--step=}"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# --- state -------------------------------------------------------------------
PASS=0
FAIL=0
FAILED_STEPS=()
OBSERVER_STARTED=0
SEAHORSE=""
SEAHORSE_MCP=""

# --- helpers -----------------------------------------------------------------
info() { echo "$*" | tee -a "$LOG" >&2; }   # informational → stderr + log

ok()   { PASS=$((PASS + 1)); info "  ✅ $1"; }
fail() { FAIL=$((FAIL + 1)); FAILED_STEPS+=("$1"); info "  ❌ $1"; }

step() {  # step <n> <title>
  local n="$1"; shift
  if (( n > STEP_LIMIT )); then
    info ""
    info "⏸ paused before step $n ($*) — run with --step=$n to continue"
    exit 0
  fi
  info ""
  info "════════════════════════════════════════════════════════════"
  info "STEP $n: $*"
  info "════════════════════════════════════════════════════════════"
}

run() {  # run <label> <cmd...> — transparent, live output
  local label="$1"; shift
  info ""
  info "▶ $*"
  if "$@" 2>&1 | tee -a "$LOG"; then ok "$label"; else fail "$label"; fi
}

run_critical() {  # run_critical <label> <cmd...> — abort on failure
  local label="$1"; shift
  info ""
  info "▶ $*"
  if "$@" 2>&1 | tee -a "$LOG"; then
    ok "$label"
  else
    fail "$label"
    info "ABORT: $label failed — cannot continue."
    exit 1
  fi
}

check() {  # check <label> <test-args...>
  local label="$1"; shift
  if "$@"; then ok "$label"; else fail "$label"; fi
}

last_ep_id() {  # most recent `ep_id:` line in the log (human remember/improve)
  sed -n 's/^[[:space:]]*ep_id:[[:space:]]*\([0-9a-f-]*\).*/\1/p' "$LOG" | tail -1
}

# --- B0 pre-flight -----------------------------------------------------------
REAL_HOME="$HOME"
START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$SANDBOX"
: > "$LOG"

step 0 "Pre-flight: prerequisites + no-corruption snapshot"
command -v uv >/dev/null || { info "✗ uv not found — install https://docs.astral.sh/uv/"; exit 2; }
command -v python3 >/dev/null || { info "✗ python3 not found"; exit 2; }
command -v sqlite3 >/dev/null || info "  ⚠ sqlite3 not found — import backup will fall back to cp"
OLLAMA_UP=0
if command -v curl >/dev/null && curl -s -m 2 http://localhost:11434 >/dev/null 2>&1; then
  OLLAMA_UP=1
fi
info "  uv:      $(command -v uv)"
info "  python3: $(python3 --version 2>&1)"
info "  ollama:  $([ "$OLLAMA_UP" -eq 1 ] && echo up || echo down)"

SNAP="$SANDBOX/snapshot-before.txt"
: > "$SNAP"
for f in "$REAL_HOME/.claude/settings.json" "$REAL_HOME/.claude-mem/claude-mem.db"; do
  if [[ -f "$f" ]]; then
    echo "$(shasum -a 256 "$f" | awk '{print $1}')  $f" >> "$SNAP"
  else
    echo "ABSENT  $f" >> "$SNAP"
  fi
done
info "  snapshot (real files, before):"
cat "$SNAP" | tee -a "$LOG" >&2

# --- B1 sandbox --------------------------------------------------------------
step 1 "Sandbox: isolated HOME + env"
mkdir -p "$SANDBOX/home" "$VAULT" "/private/tmp/seahorse-e2e-cache"
unset XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME XDG_BIN_HOME XDG_STATE_HOME
export HOME="$SANDBOX/home"
if (( REUSE_UV_CACHE )) && [[ -d "$REAL_HOME/.cache/uv" ]]; then
  export UV_CACHE_DIR="$REAL_HOME/.cache/uv"
  info "  UV_CACHE_DIR=$UV_CACHE_DIR (reuse real cache)"
else
  info "  UV_CACHE_DIR: isolated (no reuse)"
fi
# Shared across runs so re-runs reuse the ~235MB model instead of re-downloading.
export FASTEMBED_CACHE_PATH="/private/tmp/seahorse-e2e-cache"
export SEAHORSE_VAULT="$VAULT"
cd "$SANDBOX"
info "  HOME=$HOME"
info "  SEAHORSE_VAULT=$SEAHORSE_VAULT"
info "  FASTEMBED_CACHE_PATH=$FASTEMBED_CACHE_PATH"

# --- B2 install --------------------------------------------------------------
step 2 "Install (fresh user): uv tool install . --extra embeddings --extra llm"
# Pin a Python whose sqlite3 supports load_extension (needed by sqlite-vec).
# `uv tool install` otherwise picks the default interpreter, which on some
# machines (e.g. a pyenv build without SQLITE_ENABLE_LOAD_EXTENSION) breaks
# every DB command with a cryptic AttributeError.
run_critical "uv tool install" bash -c "cd '$REPO_DIR' && uv tool install --python 3.13 '.[embeddings,llm]'"
SEAHORSE="$SANDBOX/home/.local/bin/seahorse"
SEAHORSE_MCP="$SANDBOX/home/.local/bin/seahorse-mcp"
check "seahorse binary present" test -x "$SEAHORSE"
check "seahorse-mcp binary present" test -x "$SEAHORSE_MCP"
run "seahorse --help" "$SEAHORSE" --help
run "seahorse-mcp --help" "$SEAHORSE_MCP" --help

# --- B3 init + notes ---------------------------------------------------------
step 3 "Init vault + a valid F3.1 episode note (Obsidian layer)"
run_critical "seahorse init" "$SEAHORSE" init "$VAULT"
check "config written" test -f "$VAULT/.seahorse/seahorse.toml"
check "no .obsidian created (Obsidian not required)" test ! -d "$VAULT/.obsidian"
mkdir -p "$VAULT/notes"
cat > "$VAULT/notes/alpha.md" <<'EOF'
---
id: 01234567-89ab-7def-8123-456789abcdef
created_at: 2026-07-16T12:00:00Z
schema_version: 0.1.0
provenance: {}
---
# Madrid
Sergio lives in Madrid.
EOF
ok "created a valid F3.1 episode note"

# --- B4 core CLI -------------------------------------------------------------
step 4 "Core CLI: status / remember / recall / improve / forget / doctor / inspect / migrate / uuid7 / index rebuild"
run "status" "$SEAHORSE" status
run "remember #1 (Madrid) — first embed downloads mE5-small" \
  "$SEAHORSE" remember "Sergio lives in Madrid" --title home
EP1="$(last_ep_id)"
check "ep_id captured" test -n "$EP1"
run "remember #2 (hybrid)" \
  "$SEAHORSE" remember "Seahorse uses sqlite-vec for hybrid retrieval" --title tech
EP2="$(last_ep_id)"
check "ep_id #2 captured" test -n "$EP2"
run "recall 'madrid'" "$SEAHORSE" recall "madrid"
run "recall-full $EP1" "$SEAHORSE" recall-full "$EP1"
run "recall-timeline $EP1" "$SEAHORSE" recall-timeline "$EP1"
run "improve $EP1 (supersede)" \
  "$SEAHORSE" improve "$EP1" "Sergio lives in Barcelona" --reason correction
NEW_ID="$(last_ep_id)"
check "improve produced a new ep_id" test -n "$NEW_ID"
run "forget $NEW_ID (soft-delete)" "$SEAHORSE" forget "$NEW_ID" --reason done
run "doctor" "$SEAHORSE" doctor
run "inspect" "$SEAHORSE" inspect
run "migrate --up-to 10" "$SEAHORSE" migrate --up-to 10
run "uuid7" "$SEAHORSE" uuid7
run "index rebuild" "$SEAHORSE" index rebuild

# --- B5 embeddings ------------------------------------------------------------
step 5 "Embeddings: hybrid semantic retrieval"
run "status (retrieval regime)" "$SEAHORSE" status
if "$SEAHORSE" status 2>&1 | grep -q "hybrid RRF"; then
  ok "retrieval regime = hybrid RRF"
else
  fail "retrieval regime = hybrid RRF"
fi
run "recall 'hybrid' (ranked)" "$SEAHORSE" recall "hybrid" --top-k 3

# --- B6 LLM with Ollama ------------------------------------------------------
step 6 "LLM extraction (Ollama, honest degrade if down)"
if [[ "$OLLAMA_UP" -eq 1 ]]; then
  run "remember --extraction-mode llm" \
    "$SEAHORSE" remember "Sergio works on Seahorse, an open memory standard" \
    --title llm --extraction-mode llm
else
  info "  ⚠ Ollama not reachable — verifying the honest llm→skip degrade instead"
  run "remember --extraction-mode llm (expect degrade)" \
    "$SEAHORSE" remember "Sergio works on Seahorse, an open memory standard" \
    --title llm --extraction-mode llm
fi

# --- B7 setup + observer -----------------------------------------------------
step 7 "Setup + observer (isolated HOME)"
run "seahorse setup" "$SEAHORSE" setup
check "hooks written to isolated settings" test -f "$SANDBOX/home/.claude/settings.json"
if grep -q "observe event" "$SANDBOX/home/.claude/settings.json" 2>/dev/null; then
  ok "isolated settings contain the 'observe event' hook marker"
else
  fail "isolated settings contain the 'observe event' hook marker"
fi
run "observe start" "$SEAHORSE" observe start
OBSERVER_STARTED=1
check "observer pid file" test -f "$VAULT/.seahorse/observer/observer.pid"
if "$SEAHORSE" observe status 2>&1 | grep -q "running"; then
  ok "observer is running after start"
else
  fail "observer is running after start"
fi
run "observe stop" "$SEAHORSE" observe stop
OBSERVER_STARTED=0
run "setup --uninstall" "$SEAHORSE" setup --uninstall
if grep -q "observe event" "$SANDBOX/home/.claude/settings.json" 2>/dev/null; then
  fail "hooks removed after setup --uninstall"
else
  ok "hooks removed after setup --uninstall"
fi

# --- B8 import ---------------------------------------------------------------
step 8 "Import (claude-mem bridge, read-only copy)"
REAL_DB="$REAL_HOME/.claude-mem/claude-mem.db"
COPY="$SANDBOX/claude-mem-copy.db"
if [[ -f "$REAL_DB" ]]; then
  if command -v sqlite3 >/dev/null; then
    run "backup claude-mem DB (read-only)" sqlite3 "$REAL_DB" ".backup '$COPY'"
  else
    run "copy claude-mem DB (read-only)" cp "$REAL_DB" "$COPY"
  fi
else
  info "  ⚠ no real claude-mem DB at $REAL_DB — creating a synthetic one"
  python3 - "$COPY" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.execute(
    "CREATE TABLE observations (id INTEGER PRIMARY KEY, project TEXT, type TEXT, "
    "title TEXT, narrative TEXT, created_at TEXT, agent_id TEXT)"
)
conn.execute(
    "INSERT INTO observations VALUES (1, 'seahorse', 'decision', 'A decision', "
    "'The narrative about extraction_mode.', '2026-08-01T00:00:00Z', 'claude-code')"
)
conn.commit()
conn.close()
PY
  ok "synthetic claude-mem DB created"
fi
run "import dry-run" "$SEAHORSE" import --source "$COPY" --project seahorse --mode dry-run
run "import commit" "$SEAHORSE" import --source "$COPY" --project seahorse --mode commit
run "recall imported term" "$SEAHORSE" recall "extraction_mode" --top-k 3

# --- B9 MCP ------------------------------------------------------------------
step 9 "MCP: stdio JSON-RPC session (agent surface)"
run "MCP stdio session" python3 - "$SEAHORSE_MCP" "$VAULT" <<'PY'
import json, subprocess, sys

proc = subprocess.Popen(
    [sys.argv[1], "--vault", sys.argv[2]],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, bufsize=1,
)

def send(obj):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()

def recv():
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("server closed stdout early")
    return json.loads(line)

try:
    send({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    init = recv()
    assert init["result"]["protocolVersion"] == "2025-11-25", init
    assert init["result"]["serverInfo"]["name"] == "seahorse-memory", init
    print("  MCP initialize →", init["result"]["serverInfo"])

    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = sorted(t["name"] for t in recv()["result"]["tools"])
    print("  MCP tools/list →", names)
    # 7 memory primitives are the contract; procedural/read-only tools may grow
    # over time, so assert a superset instead of an exact count (was 7, now 12).
    primitives = {"remember", "recall", "recall_timeline", "recall_full",
                  "improve", "forget", "build_pit"}
    assert primitives.issubset(names), (names, primitives - set(names))

    send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "remember",
        "arguments": {"body": "MCP wrote an episode",
                      "by": {"agent_id": "e2e", "session_id": "s1", "source_type": "agent"}},
    }})
    wr = json.loads(recv()["result"]["content"][0]["text"])
    print("  MCP remember →", wr["status"], wr["ep_id"])
    assert wr["status"] == "ACTIVE", wr

    send({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
        "name": "recall", "arguments": {"query": "MCP"}}})
    rows = json.loads(recv()["result"]["content"][0]["text"])
    print("  MCP recall →", len(rows), "rows")
    assert any(r["ep_id"] == wr["ep_id"] for r in rows), rows

    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({"jsonrpc": "2.0", "id": 5, "method": "tools/list"})
    assert recv()["id"] == 5
    print("  MCP notification consumed ✓")

    proc.stdin.close()
    proc.wait(timeout=10)
    assert proc.returncode == 0, proc.stderr.read()
    print("  MCP session OK (clean exit 0)")
except Exception as exc:
    print("  MCP FAILED:", exc)
    proc.kill()
    sys.exit(1)
PY

# --- B10 post-flight ---------------------------------------------------------
step 10 "Post-flight: no-corruption verification"
SNAP_AFTER="$SANDBOX/snapshot-after.txt"
: > "$SNAP_AFTER"
for f in "$REAL_HOME/.claude/settings.json" "$REAL_HOME/.claude-mem/claude-mem.db"; do
  if [[ -f "$f" ]]; then
    echo "$(shasum -a 256 "$f" | awk '{print $1}')  $f" >> "$SNAP_AFTER"
  else
    echo "ABSENT  $f" >> "$SNAP_AFTER"
  fi
done
if diff -q "$SNAP" "$SNAP_AFTER" >/dev/null 2>&1; then
  ok "real ~/.claude + ~/.claude-mem unchanged"
else
  fail "real ~/.claude + ~/.claude-mem unchanged"
  diff "$SNAP" "$SNAP_AFTER" | tee -a "$LOG" >&2
fi

TOUCHED="$(find "$REAL_HOME/obsidian-vaults" -type f -newermt "$START_TS" 2>/dev/null || true)"
if [[ -z "$TOUCHED" ]]; then
  ok "no files touched in ~/obsidian-vaults during the run"
else
  info "  ⚠ files touched in ~/obsidian-vaults during the run (review manually):"
  echo "$TOUCHED" | tee -a "$LOG" >&2
fi

if [[ -f "$SANDBOX/home/.claude/settings.json" ]]; then
  ok "setup wrote to the ISOLATED settings (redirection worked)"
else
  fail "setup wrote to the ISOLATED settings (redirection worked)"
fi

if pgrep -f "seahorse.cli.app observe run" >/dev/null 2>&1; then
  fail "no orphan observer process"
  pgrep -af "seahorse.cli.app observe run" | tee -a "$LOG" >&2
else
  ok "no orphan observer process"
fi

# --- report ------------------------------------------------------------------
step 11 "Report"
info ""
info "════════════════════════════════════════════════════════════"
info "E2E FRESH-USER RESULT: $PASS passed, $FAIL failed"
if (( FAIL > 0 )); then
  info "Failed steps:"
  for s in "${FAILED_STEPS[@]}"; do info "  ❌ $s"; done
fi
info "Sandbox: $SANDBOX"
info "Log:     $LOG"
info "════════════════════════════════════════════════════════════"
exit $(( FAIL > 0 ? 1 : 0 ))
