#!/usr/bin/env bash
#
# stress-core.sh — load-test the Seahorse core (engine, retrieval, embeddings,
# concurrency) against a real vault, with latency metrics.
#
# Scenarios:
#   S1  Ingest N episodes via `seahorse remember` (loop) — no degradation, time.
#   S2  Recall --top-k 100 over the populated vault — p95 ≤ 250ms (INDEX budget).
#   S3  2 parallel `remember` processes — single-writer (WAL + RLock), no corruption.
#   S4  `seahorse index rebuild` over the large vault — completes, counts correct.
#   S5  `seahorse import` with a synthetic claude-mem DB — idempotent re-run.
#   S6  improve/forget chain — the supersedes chain stays intact.
#
# Uses the same isolation strategy as e2e-fresh-user.sh (sandboxed HOME,
# isolated SEAHORSE_VAULT, shared FASTEMBED_CACHE_PATH). Installs with the
# `embeddings` extra by default (hybrid regime); `--core` installs core-only
# (honest listing regime) for a lighter run.
#
# Usage:
#   scripts/stress-core.sh [--episodes N] [--samples N] [--import-rows N]
#                          [--chain N] [--core] [--no-cache-reuse] [--keep-sandbox]
#
# Exit 0 if every scenario passed, 1 otherwise.

set -euo pipefail

# --- resolve paths -----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# Sandbox base: macOS keeps /private/tmp (writable, shares the FASTEMBED cache
# with e2e-fresh-user.sh); other platforms fall back to $TMPDIR (e.g. /tmp).
if [[ -d /private/tmp && -w /private/tmp ]]; then
  SANDBOX_BASE="/private/tmp"
else
  SANDBOX_BASE="${TMPDIR:-/tmp}"
fi
SANDBOX="$SANDBOX_BASE/seahorse-stress-$(date +%s)"
LOG="$SANDBOX/stress.log"
VAULT="$SANDBOX/vault"
SHARED_FASTEMBED_CACHE="$SANDBOX_BASE/seahorse-e2e-cache"
REAL_HOME="$HOME"

# --- args --------------------------------------------------------------------
EPISODES=1000
RECALL_SAMPLES=20
IMPORT_ROWS=500
CHAIN_LEN=100
REUSE_UV_CACHE=1
KEEP_SANDBOX=0
CORE_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --episodes) EPISODES="$2"; shift 2 ;;
    --episodes=*) EPISODES="${1#--episodes=}"; shift ;;
    --samples) RECALL_SAMPLES="$2"; shift 2 ;;
    --samples=*) RECALL_SAMPLES="${1#--samples=}"; shift ;;
    --import-rows) IMPORT_ROWS="$2"; shift 2 ;;
    --import-rows=*) IMPORT_ROWS="${1#--import-rows=}"; shift ;;
    --chain) CHAIN_LEN="$2"; shift 2 ;;
    --chain=*) CHAIN_LEN="${1#--chain=}"; shift ;;
    --core) CORE_ONLY=1; shift ;;
    --no-cache-reuse) REUSE_UV_CACHE=0; shift ;;
    --keep-sandbox) KEEP_SANDBOX=1; shift ;;
    -h|--help) grep -E "^#   " "$0" | sed 's/^#   //'; exit 0 ;;
    *) echo "unknown arg: $1 (see --help)" >&2; exit 2 ;;
  esac
done

# --- state -------------------------------------------------------------------
PASS=0
FAIL=0
FAILED=()
SEAHORSE=""

# --- helpers -----------------------------------------------------------------
info() { echo "$*" | tee -a "$LOG" >&2; }
ok()   { PASS=$((PASS + 1)); info "  ✅ $1"; }
fail() { FAIL=$((FAIL + 1)); FAILED+=("$1"); info "  ❌ $1"; }

step() {  # step <n> <title>
  info ""
  info "════════════════════════════════════════════════════════════"
  info "S$1: $2"
  info "════════════════════════════════════════════════════════════"
}

run() {  # run <label> <cmd...>
  local label="$1"; shift
  info "  ▶ $*"
  if "$@" 2>&1 | tee -a "$LOG"; then ok "$label"; else fail "$label"; fi
}

last_ep_id() {  # most recent `ep_id:` line in the log
  sed -n 's/^[[:space:]]*ep_id:[[:space:]]*\([0-9a-f-]*\).*/\1/p' "$LOG" | tail -1
}

inspect_count() {  # inspect_count <field> — numeric value of a field in `inspect`
  "$SEAHORSE" inspect 2>&1 | sed -n "s/^[[:space:]]*$1:[[:space:]]*\([0-9]*\).*/\1/p" | tail -1
}

# --- setup -------------------------------------------------------------------
mkdir -p "$SANDBOX" "$VAULT" "$SHARED_FASTEMBED_CACHE"
: > "$LOG"
unset XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME XDG_BIN_HOME XDG_STATE_HOME
export HOME="$SANDBOX/home"
mkdir -p "$HOME"
if (( REUSE_UV_CACHE )) && [[ -d "$REAL_HOME/.cache/uv" ]]; then
  export UV_CACHE_DIR="$REAL_HOME/.cache/uv"
fi
export SEAHORSE_VAULT="$VAULT"
export FASTEMBED_CACHE_PATH="$SHARED_FASTEMBED_CACHE"

info "stress sandbox: $SANDBOX"
info "episodes=$EPISODES samples=$RECALL_SAMPLES import_rows=$IMPORT_ROWS chain=$CHAIN_LEN"
info "regime: $([ "$CORE_ONLY" -eq 1 ] && echo 'core (listing)' || echo 'embeddings (hybrid)')"

# --- install + init ----------------------------------------------------------
if (( CORE_ONLY )); then
  run "uv tool install (core)" bash -c "cd '$REPO_DIR' && uv tool install --python 3.13 '.'"
else
  run "uv tool install (embeddings)" bash -c "cd '$REPO_DIR' && uv tool install --python 3.13 '.[embeddings]'"
fi
SEAHORSE="$HOME/.local/bin/seahorse"
if [[ ! -x "$SEAHORSE" ]]; then
  info "ABORT: seahorse binary not installed."
  exit 1
fi
run "seahorse init" "$SEAHORSE" init "$VAULT"

# --- S1 ingest ---------------------------------------------------------------
step 1 "Ingest $EPISODES episodes"
t0="$(date +%s%N)"
for i in $(seq 1 "$EPISODES"); do
  if ! "$SEAHORSE" remember "Stress episode $i: hybrid retrieval over sqlite-vec and FTS5" \
      --title "s$i" >/dev/null 2>&1; then
    fail "remember #$i"
    break
  fi
done
t1="$(date +%s%N)"
elapsed_ms=$(( (t1 - t0) / 1000000 ))
ok "ingested $EPISODES episodes in ${elapsed_ms}ms ($(( elapsed_ms / EPISODES ))ms/ep)"
count="$(inspect_count episodes)"
if [[ "$count" == "$EPISODES" ]]; then
  ok "inspect episodes == $EPISODES"
else
  fail "inspect episodes == $EPISODES (got '$count')"
fi

# --- S2 recall p95 -----------------------------------------------------------
step 2 "Recall --top-k 100 p95 (INDEX budget ≤ 250ms, in-process)"
# Measure in-process (facade.recall) — the INDEX budget is the retrieval cost,
# NOT the CLI process startup (~400ms of Python+typer+pydantic imports). A
# subprocess measurement would fail the 250ms budget on startup alone.
TOOL_PY="$HOME/.local/share/uv/tools/seahorse/bin/python"
if [[ ! -x "$TOOL_PY" ]]; then
  fail "tool env python not found at $TOOL_PY"
else
  if "$TOOL_PY" - "$VAULT/.seahorse/seahorse.db" "$RECALL_SAMPLES" <<'PY'
import sys, time
from seahorse.facade.factory import build_facade

db, n = sys.argv[1], int(sys.argv[2])
facade, storage = build_facade(db)
try:
    durs = []
    for _ in range(n):
        t0 = time.perf_counter()
        facade.recall("hybrid retrieval", k=100)
        durs.append((time.perf_counter() - t0) * 1000)
finally:
    storage.close()
durs.sort()
p95 = durs[int(len(durs) * 0.95) - 1]
print(f"  recall p95 = {p95:.1f}ms (n={n}, in-process)")
sys.exit(0 if p95 <= 250 else 1)
PY
  then
    ok "recall p95 ≤ 250ms"
  else
    fail "recall p95 ≤ 250ms"
  fi
fi

# --- S3 concurrency ----------------------------------------------------------
step 3 "Concurrency: 2 parallel remember (single-writer)"
"$SEAHORSE" remember "Concurrent stress A" --title ca >"$SANDBOX/ca.log" 2>&1 &
p1=$!
"$SEAHORSE" remember "Concurrent stress B" --title cb >"$SANDBOX/cb.log" 2>&1 &
p2=$!
s1=0; s2=0
wait "$p1" || s1=$?
wait "$p2" || s2=$?
if [[ "$s1" -eq 0 && "$s2" -eq 0 ]]; then
  ok "both concurrent remembers succeeded"
else
  fail "concurrent remembers (a=$s1 b=$s2)"
fi
if "$SEAHORSE" inspect >/dev/null 2>&1; then
  ok "inspect after concurrency (no corruption)"
else
  fail "inspect after concurrency (no corruption)"
fi

# --- S4 reindex --------------------------------------------------------------
step 4 "Reindex large vault"
t0="$(date +%s%N)"
if "$SEAHORSE" index rebuild >/dev/null 2>&1; then
  ok "index rebuild completed"
else
  fail "index rebuild completed"
fi
t1="$(date +%s%N)"
info "  index rebuild took $(( (t1 - t0) / 1000000 ))ms"
expected=$(( EPISODES + 2 ))  # S1 + the 2 concurrent episodes
count="$(inspect_count episodes)"
if [[ "$count" == "$expected" ]]; then
  ok "inspect episodes after reindex == $expected"
else
  fail "inspect episodes after reindex == $expected (got '$count')"
fi

# --- S5 import idempotent ----------------------------------------------------
step 5 "Import $IMPORT_ROWS observations (idempotent)"
python3 - "$SANDBOX/claude-mem.db" "$IMPORT_ROWS" <<'PY'
import sqlite3, sys
db, n = sys.argv[1], int(sys.argv[2])
conn = sqlite3.connect(db)
conn.execute(
    "CREATE TABLE observations (id INTEGER PRIMARY KEY, project TEXT, type TEXT, "
    "title TEXT, narrative TEXT, created_at TEXT, agent_id TEXT)"
)
for i in range(1, n + 1):
    conn.execute(
        "INSERT INTO observations VALUES (?, 'seahorse', 'decision', ?, ?, "
        "'2026-08-01T00:00:00Z', 'claude-code')",
        (i, f"Decision {i}", f"Narrative about extraction_mode {i}."),
    )
conn.commit()
conn.close()
PY
out1="$("$SEAHORSE" import --source "$SANDBOX/claude-mem.db" --project seahorse --mode commit 2>&1)"
if echo "$out1" | grep -q "skipped_idempotent=0"; then
  ok "import commit (fresh)"
else
  fail "import commit (fresh): $out1"
fi
out2="$("$SEAHORSE" import --source "$SANDBOX/claude-mem.db" --project seahorse --mode commit 2>&1)"
if echo "$out2" | grep -q "skipped_idempotent=$IMPORT_ROWS"; then
  ok "import re-run idempotent (skipped_idempotent=$IMPORT_ROWS)"
else
  fail "import re-run idempotent: $out2"
fi

# --- S6 improve/forget chain -------------------------------------------------
step 6 "Improve/forget chain of $CHAIN_LEN"
"$SEAHORSE" remember "Chain root episode" --title chain-root >>"$LOG" 2>&1
EP="$(last_ep_id)"
for i in $(seq 1 "$CHAIN_LEN"); do
  if ! "$SEAHORSE" improve "$EP" "Chain episode $i" --reason "correction $i" >>"$LOG" 2>&1; then
    fail "improve #$i"
    break
  fi
  EP="$(last_ep_id)"
done
# The timeline window is capped at MAX_TIMELINE_WINDOW=20 (disclosure/types.py),
# so a chain longer than that cannot be verified via recall-timeline. Walk the
# supersedes links in the sidecar DB instead — the honest chain-length check.
chain_len="$(python3 - "$VAULT/.seahorse/seahorse.db" "$EP" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
rows = conn.execute("SELECT id, supersedes FROM episodes").fetchall()
by_id = {r[0]: r[1] for r in rows}
count = 0
cur = sys.argv[2]
while cur:
    count += 1
    cur = by_id.get(cur)
print(count)
PY
)"
if [[ "$chain_len" == "$(( CHAIN_LEN + 1 ))" ]]; then
  ok "supersedes chain intact ($chain_len episodes)"
else
  fail "supersedes chain intact (got '$chain_len')"
fi
if "$SEAHORSE" forget "$EP" --reason done >>"$LOG" 2>&1; then
  ok "forget final episode"
else
  fail "forget final episode"
fi

# --- report ------------------------------------------------------------------
info ""
info "════════════════════════════════════════════════════════════"
info "STRESS-CORE RESULT: $PASS passed, $FAIL failed"
if (( FAIL > 0 )); then
  info "Failed scenarios:"
  for s in "${FAILED[@]}"; do info "  ❌ $s"; done
fi
info "Sandbox: $SANDBOX"
info "Log:     $LOG"
info "════════════════════════════════════════════════════════════"

if (( KEEP_SANDBOX )); then
  info "Sandbox kept at $SANDBOX (--keep-sandbox)"
else
  rm -rf "$SANDBOX"
  # Direct echo, not info(): the log lives inside the sandbox and was just
  # deleted — tee would fail and set -e would turn that into a spurious exit 1.
  echo "Sandbox cleaned up (use --keep-sandbox to retain for debugging)" >&2
fi

exit $(( FAIL > 0 ? 1 : 0 ))
