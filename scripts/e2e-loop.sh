#!/usr/bin/env bash
#
# e2e-loop.sh — validate the self-evolving loop end-to-end (v1.0 gate #2).
#
# The loop: observe (hook injection) → consolidate → supersede → recall → decay.
#   * observe     — inject Claude Code hook events via env vars + `seahorse
#                   observe event` (no real Claude Code); the worker drains the
#                   queue and writes episodes.
#   * consolidate — N≥3 recurrent episodes cluster → distilled into a semantic
#                   knowledge note (extraction_mode=consolidated); idempotent.
#   * supersede   — new episodes → the note is updated in-place (improve chain);
#                   a human-edited vault note is NEVER superseded (guard).
#   * recall      — the consolidated note is retrievable and ranks.
#   * decay       — score' = score · 2^(-age_days/half_life) (default-OFF seam;
#                   validated via the facade factory, the same seam the
#                   benchmark SUT wires).
#   * bi-temporal — every episode carries created_at/valid_at; recall-timeline
#                   reproduces the supersedes chain.
#
# Isolation: same strategy as e2e-fresh-user.sh — HOME/SEAHORSE_VAULT/
# FASTEMBED_CACHE_PATH redirected to a temp sandbox; the real ~/.claude and
# ~/.claude-mem are never touched (post-flight re-checks shasums).
#
# Not wired into CI (needs the observer + timing); it is the local evidence for
# the v1.0 release gate.
#
# Usage:
#   scripts/e2e-loop.sh [--no-cache-reuse] [--step <n>]
#
#   --no-cache-reuse  do not reuse the real uv cache (fully isolated, slower)
#   --step <n>        run steps 0..n then stop (default: all steps)

set -euo pipefail

# --- resolve paths -----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# The observer binds a unix socket at {vault}/.seahorse/observer/observer.sock;
# AF_UNIX caps the path at ~104 bytes. $TMPDIR on macOS (/var/folders/...) is
# too long for that socket — use /tmp (short, exists on macOS and Linux).
SANDBOX_BASE="${SEAHORSE_SANDBOX_BASE:-/tmp}"
SANDBOX="$SANDBOX_BASE/seahorse-loop-$(date +%s)"
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
TOOL_PY=""

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

wait_for_socket() {  # wait_for_socket <sock> [timeout_s] — poll until it accepts
  local sock="$1"
  local timeout_s="${2:-15}"
  local deadline=$(( $(date +%s) + timeout_s ))
  while (( $(date +%s) < deadline )); do
    if [[ -S "$sock" ]] && python3 - "$sock" <<'PY' 2>/dev/null
import socket, sys
try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        s.connect(sys.argv[1])
    sys.exit(0)
except OSError:
    sys.exit(1)
PY
    then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

# --- pre-flight --------------------------------------------------------------
REAL_HOME="$HOME"
START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$SANDBOX"
: > "$LOG"

step 0 "Pre-flight: prerequisites + no-corruption snapshot"
command -v uv >/dev/null || { info "✗ uv not found — install https://docs.astral.sh/uv/"; exit 2; }
command -v python3 >/dev/null || { info "✗ python3 not found"; exit 2; }
info "  uv:      $(command -v uv)"
info "  python3: $(python3 --version 2>&1)"

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

# --- sandbox -----------------------------------------------------------------
step 1 "Sandbox: isolated HOME + env"
mkdir -p "$SANDBOX/home" "$VAULT" "$SANDBOX_BASE/seahorse-loop-cache"
unset XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME XDG_BIN_HOME XDG_STATE_HOME
export HOME="$SANDBOX/home"
if (( REUSE_UV_CACHE )) && [[ -d "$REAL_HOME/.cache/uv" ]]; then
  export UV_CACHE_DIR="$REAL_HOME/.cache/uv"
  info "  UV_CACHE_DIR=$UV_CACHE_DIR (reuse real cache)"
else
  info "  UV_CACHE_DIR: isolated (no reuse)"
fi
# Shared across runs so re-runs reuse the ~235MB model instead of re-downloading.
export FASTEMBED_CACHE_PATH="$SANDBOX_BASE/seahorse-loop-cache"
export SEAHORSE_VAULT="$VAULT"
cd "$SANDBOX"
info "  HOME=$HOME"
info "  SEAHORSE_VAULT=$SEAHORSE_VAULT"
info "  FASTEMBED_CACHE_PATH=$FASTEMBED_CACHE_PATH"

# --- install ----------------------------------------------------------------
step 2 "Install (fresh user): uv tool install . --extra embeddings --extra llm"
# Pin a Python whose sqlite3 supports load_extension (needed by sqlite-vec).
run_critical "uv tool install" bash -c "cd '$REPO_DIR' && uv tool install --python 3.13 '.[embeddings,llm]'"
SEAHORSE="$SANDBOX/home/.local/bin/seahorse"
check "seahorse binary present" test -x "$SEAHORSE"
# The tool venv python — used for the DB-level assertions (provenance, decay).
TOOL_PY="$(find "$SANDBOX/home/.local/share/uv/tools" -name python -path '*/bin/*' | head -1)"
check "tool venv python found" test -n "$TOOL_PY"
info "  tool venv python: $TOOL_PY"

# --- init + setup ------------------------------------------------------------
step 3 "Init vault + setup observer (isolated HOME)"
run_critical "seahorse init" "$SEAHORSE" init "$VAULT"
check "config written" test -f "$VAULT/.seahorse/seahorse.toml"
run "seahorse setup" "$SEAHORSE" setup
check "hooks written to isolated settings" test -f "$SANDBOX/home/.claude/settings.json"
if grep -q "observe event" "$SANDBOX/home/.claude/settings.json" 2>/dev/null; then
  ok "isolated settings contain the 'observe event' hook marker"
else
  fail "isolated settings contain the 'observe event' hook marker"
fi
check "observe section in seahorse.toml" grep -q "\[observe\]" "$VAULT/.seahorse/seahorse.toml"

# --- observe -----------------------------------------------------------------
step 4 "Observe: inject hook events → worker drains → episodes written"
run "observe start" "$SEAHORSE" observe start
OBSERVER_STARTED=1
check "observer pid file" test -f "$VAULT/.seahorse/observer/observer.pid"

# The child binds the unix socket a moment after spawn — injecting before that
# silently drops every envelope (the hook path is a no-op when the socket is
# absent). Wait for the socket to accept connections before injecting.
SOCK="$VAULT/.seahorse/observer/observer.sock"
if wait_for_socket "$SOCK" 15; then
  ok "observer socket ready (accepts connections)"
else
  fail "observer socket ready (accepts connections)"
fi

# Inject a full turn for session loop-s1 (SessionStart + UserPromptSubmit +
# PostToolUse + Stop) and bare UserPromptSubmit turns for loop-s2/loop-s3.
# The first line of each prompt is "Deploy pipeline" so the three episodes
# cluster under the same key (the worker's H1 = first line + [session_tag:n]).
# PostToolUse uses Edit — Read/Bash are in the default drop_tools and would
# discard the whole turn.
CLAUDE_HOOK_EVENT_NAME=SessionStart CLAUDE_SESSION_ID=loop-s1 \
  "$SEAHORSE" observe event
CLAUDE_HOOK_EVENT_NAME=UserPromptSubmit CLAUDE_SESSION_ID=loop-s1 \
  CLAUDE_PROMPT="Deploy pipeline
Uses GitHub Actions with a TestPyPI verification gate before publishing" \
  "$SEAHORSE" observe event
CLAUDE_HOOK_EVENT_NAME=PostToolUse CLAUDE_SESSION_ID=loop-s1 \
  CLAUDE_TOOL_NAME=Edit CLAUDE_TOOL_USE_ID=call_1 \
  CLAUDE_TOOL_INPUT='{"path": "publish.yml"}' CLAUDE_TOOL_RESPONSE="ok" \
  "$SEAHORSE" observe event
CLAUDE_HOOK_EVENT_NAME=Stop CLAUDE_SESSION_ID=loop-s1 \
  "$SEAHORSE" observe event
CLAUDE_HOOK_EVENT_NAME=UserPromptSubmit CLAUDE_SESSION_ID=loop-s2 \
  CLAUDE_PROMPT="Deploy pipeline
Runs e2e-pypi.sh against the published artifact in a fresh venv" \
  "$SEAHORSE" observe event
CLAUDE_HOOK_EVENT_NAME=UserPromptSubmit CLAUDE_SESSION_ID=loop-s3 \
  CLAUDE_PROMPT="Deploy pipeline
Gates v1.0 on a clean VM install with real embeddings and LLM extraction" \
  "$SEAHORSE" observe event

# The worker drains every 5s — wait past one interval.
sleep 8

if "$SEAHORSE" observe status 2>&1 | grep -q "running"; then
  ok "observer is running after start"
else
  fail "observer is running after start"
fi

# The three turns (one per session) must have been written as episodes.
cat > "$SANDBOX/count_observe.py" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
n = conn.execute(
    "SELECT COUNT(*) FROM episodes WHERE subject LIKE 'deploy pipeline%'"
).fetchone()[0]
conn.close()
print(n)
PY

# The worker drains every 5s; the first write also downloads mE5-small
# (~235MB) into FASTEMBED_CACHE_PATH — poll until the episodes land.
OBSERVED=0
for _ in $(seq 1 60); do
  OBSERVED="$("$TOOL_PY" "$SANDBOX/count_observe.py" "$VAULT/.seahorse/seahorse.db" 2>/dev/null || echo 0)"
  if (( OBSERVED >= 3 )); then break; fi
  sleep 2
done
if (( OBSERVED >= 3 )); then
  ok "worker drained envelopes to episodes ($OBSERVED episodes)"
else
  fail "worker drained envelopes to episodes (only $OBSERVED after ~120s)"
fi

# The observer is no longer needed — stop it to avoid DB contention later.
run "observe stop" "$SEAHORSE" observe stop
OBSERVER_STARTED=0

# --- consolidate -------------------------------------------------------------
step 5 "Consolidate: N≥3 recurrent episodes → semantic knowledge note (idempotent)"
run "consolidate (first pass)" "$SEAHORSE" consolidate
if grep -q "consolidated: deploy pipeline" "$LOG"; then
  ok "consolidate distilled the 'deploy pipeline' cluster"
else
  fail "consolidate distilled the 'deploy pipeline' cluster"
fi

# The distilled note must be semantic with provenance.extraction_mode=consolidated.
cat > "$SANDBOX/check_consolidated.py" <<'PY'
import json, sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
row = conn.execute(
    "SELECT id, provenance FROM episodes "
    "WHERE subject = 'deploy pipeline' AND cognitive_type = 'semantic' "
    "ORDER BY created_at DESC LIMIT 1"
).fetchone()
conn.close()
assert row is not None, "no consolidated semantic note found"
prov = json.loads(row[1])
assert prov.get("extraction_mode") == "consolidated", prov
print(f"  consolidate: note {row[0]} extraction_mode=consolidated")
PY
run "consolidated note is semantic + extraction_mode=consolidated" \
  "$TOOL_PY" "$SANDBOX/check_consolidated.py" "$VAULT/.seahorse/seahorse.db"

# Idempotency: a second pass must find the cluster but skip it (no new note).
run "consolidate (second pass, idempotent)" "$SEAHORSE" consolidate
if grep -q "no clusters to distill" "$LOG"; then
  ok "consolidate is idempotent (cluster skipped)"
else
  fail "consolidate is idempotent (cluster skipped)"
fi

# --- supersede ---------------------------------------------------------------
step 6 "Supersede: new episodes update the note; human-edit guard prevails"
run "remember #4 (deploy pipeline)" \
  "$SEAHORSE" remember "The deploy pipeline now also validates the wheel on a clean VM" \
  --title "deploy pipeline [session_tag:4]"
run "remember #5 (deploy pipeline)" \
  "$SEAHORSE" remember "The deploy pipeline gates v1.0 on the e2e-pypi artifact check" \
  --title "deploy pipeline [session_tag:5]"

run "consolidate --supersede (new episodes → note updated)" \
  "$SEAHORSE" consolidate --supersede

# The note must have been updated in-place: a NEW semantic version whose
# supersedes points at the previous note (the improve chain).
cat > "$SANDBOX/check_supersede.py" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
rows = conn.execute(
    "SELECT id, supersedes FROM episodes "
    "WHERE subject = 'deploy pipeline' AND cognitive_type = 'semantic' "
    "ORDER BY created_at"
).fetchall()
conn.close()
assert len(rows) >= 2, f"expected >=2 semantic versions, got {len(rows)}: {rows}"
latest, prev = rows[-1], rows[-2]
assert latest[1] == prev[0], f"latest {latest} does not supersede {prev}"
print(f"  supersede: note {latest[0]} supersedes {prev[0]}")
PY
run "supersede produced a new note version (improve chain)" \
  "$TOOL_PY" "$SANDBOX/check_supersede.py" "$VAULT/.seahorse/seahorse.db"

# Human-edit guard: a vault .md whose H1 matches the note's subject and whose
# mtime is newer than the note's creation is human-touched → never superseded.
mkdir -p "$VAULT/notes"
cat > "$VAULT/notes/deploy-pipeline.md" <<'EOF'
# deploy pipeline

Edited by hand — the human prevails.
EOF
sleep 1  # ensure mtime > note.created_at
touch "$VAULT/notes/deploy-pipeline.md"
run "remember #6 (deploy pipeline)" \
  "$SEAHORSE" remember "The deploy pipeline requires a TestPyPI pre-release gate" \
  --title "deploy pipeline [session_tag:6]"

run "consolidate --supersede (human-edited note → guard prevails)" \
  "$SEAHORSE" consolidate --supersede

cat > "$SANDBOX/check_guard.py" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
n = conn.execute(
    "SELECT COUNT(*) FROM episodes "
    "WHERE subject = 'deploy pipeline' AND cognitive_type = 'semantic'"
).fetchone()[0]
conn.close()
assert n == 2, f"human-edit guard failed: expected 2 semantic versions, got {n}"
print("  guard: human-edited note was NOT superseded (still 2 versions)")
PY
run "human-edit guard prevented overwrite" \
  "$TOOL_PY" "$SANDBOX/check_guard.py" "$VAULT/.seahorse/seahorse.db"

# --- recall ------------------------------------------------------------------
step 7 "Recall: the consolidated note is retrievable and ranks"
run "recall 'deploy pipeline'" "$SEAHORSE" --format json recall "deploy pipeline"
if grep -q '"subject": "deploy pipeline".*"cognitive_type": "semantic"' "$LOG"; then
  ok "consolidated note retrievable via recall"
else
  fail "consolidated note retrievable via recall"
fi

# --- decay -------------------------------------------------------------------
step 8 "Decay: score' = score · 2^(-age_days/half_life) (default-OFF seam)"
run "remember decay probe" \
  "$SEAHORSE" remember "This is a decay probe episode for the self-evolving loop validation" \
  --title "decay probe"
EP_DECAY="$(last_ep_id)"
check "decay probe ep_id captured" test -n "$EP_DECAY"

# Backdate the probe's created_at (episodes + episode_index) so age_days ≈ 30,
# then compare recall scores with and without a DecayConfig via the facade
# factory — the same seam the benchmark SUT wires (the CLI recall does not
# expose decay; it is a benchmark/benchmark-SUT concern, default-OFF).
cat > "$SANDBOX/check_decay.py" <<'PY'
import json, sqlite3, sys
from datetime import UTC, datetime

from seahorse.facade.factory import build_facade
from seahorse.retrieval.decay import DecayConfig

db_path, ep_id = sys.argv[1], sys.argv[2]
BACKDATED = "2026-07-31T12:00:00Z"  # ~30 days before the run

conn = sqlite3.connect(db_path)
conn.execute("UPDATE episodes SET created_at = ? WHERE id = ?", (BACKDATED, ep_id))
conn.execute("UPDATE episode_index SET created_at = ? WHERE ep_id = ?", (BACKDATED, ep_id))
conn.commit()
conn.close()

def score_of(facade, ep_id):
    rows = facade.recall("decay probe", k=50)
    hits = [r for r in rows if r.ep_id == ep_id]
    assert hits, f"decay probe {ep_id} not in recall results"
    return hits[0].score

# Pure RRF (decay default-OFF).
facade, storage = build_facade(db_path)
score_no_decay = score_of(facade, ep_id)
storage.close()

# Decay with a 30-day half-life for ALL types (empty per-type map → default).
facade, storage = build_facade(
    db_path, decay=DecayConfig(half_lives={}, default_half_life_days=30)
)
score_decay = score_of(facade, ep_id)
storage.close()

created = datetime.fromisoformat(BACKDATED.replace("Z", "+00:00"))
age_days = max(0.0, (datetime.now(UTC) - created).total_seconds() / 86400.0)
expected = score_no_decay * (2.0 ** (-age_days / 30.0))
assert abs(score_decay - expected) < 1e-6, (
    f"decay mismatch: {score_decay:.6f} != {expected:.6f} "
    f"(no-decay {score_no_decay:.6f}, age {age_days:.1f}d)"
)
print(f"  decay: score {score_no_decay:.6f} -> {score_decay:.6f} "
      f"(expected {expected:.6f}, age {age_days:.1f}d)")
PY
run "decay halves the score per the Ebbinghaus formula" \
  "$TOOL_PY" "$SANDBOX/check_decay.py" "$VAULT/.seahorse/seahorse.db" "$EP_DECAY"

# --- bi-temporal integrity ---------------------------------------------------
step 9 "Bi-temporal integrity: created_at/valid_at everywhere + timeline chain"
cat > "$SANDBOX/check_bitemporal.py" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
missing_created = conn.execute(
    "SELECT COUNT(*) FROM episodes WHERE created_at IS NULL"
).fetchone()[0]
total = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
# valid_at NULL is a first-class state ("from forever" — valid at any t); the
# invariant is that created_at (transaction time) is always present and valid_at
# is either NULL or a parseable timestamp.
bad_valid = conn.execute(
    "SELECT COUNT(*) FROM episodes WHERE valid_at IS NOT NULL "
    "AND valid_at NOT GLOB '*[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'"
).fetchone()[0]
conn.close()
assert missing_created == 0, f"{missing_created}/{total} episodes missing created_at"
assert bad_valid == 0, f"{bad_valid}/{total} episodes with malformed valid_at"
print(f"  bitemporal: {total} episodes, all with created_at; valid_at NULL-or-ISO")
PY
run "all episodes carry created_at (valid_at NULL-or-ISO)" \
  "$TOOL_PY" "$SANDBOX/check_bitemporal.py" "$VAULT/.seahorse/seahorse.db"

# The timeline anchored at the latest semantic note reproduces the chain.
cat > "$SANDBOX/latest_note.py" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
row = conn.execute(
    "SELECT id FROM episodes WHERE subject = 'deploy pipeline' "
    "AND cognitive_type = 'semantic' ORDER BY created_at DESC LIMIT 1"
).fetchone()
conn.close()
print(row[0] if row else "")
PY
LATEST_NOTE="$("$TOOL_PY" "$SANDBOX/latest_note.py" "$VAULT/.seahorse/seahorse.db")"
check "latest semantic note id captured" test -n "$LATEST_NOTE"
info ""
info "▶ $SEAHORSE --format json recall-timeline $LATEST_NOTE"
if "$SEAHORSE" --format json recall-timeline "$LATEST_NOTE" 2>&1 | tee -a "$LOG" > "$SANDBOX/timeline.json"; then
  ok "recall-timeline ran"
else
  fail "recall-timeline ran"
fi
if grep -q '"supersedes": "[0-9a-f-]' "$SANDBOX/timeline.json"; then
  ok "timeline reproduces the supersedes chain"
else
  fail "timeline reproduces the supersedes chain"
fi

# --- post-flight -------------------------------------------------------------
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
info "E2E SELF-EVOLVING LOOP RESULT: $PASS passed, $FAIL failed"
if (( FAIL > 0 )); then
  info "Failed steps:"
  for s in "${FAILED_STEPS[@]}"; do info "  ❌ $s"; done
fi
info "Sandbox: $SANDBOX"
info "Log:     $LOG"
info "════════════════════════════════════════════════════════════"
exit $(( FAIL > 0 ? 1 : 0 ))
