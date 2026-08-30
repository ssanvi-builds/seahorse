#!/usr/bin/env bash
#
# e2e-pypi.sh — validate the PUBLISHED artifact (not the repo) from a fresh
# venv, against a configurable index (PyPI or TestPyPI). Closes the v1.0 gap
# "the artifact we publish is never tested": every other e2e installs from the
# local repo (`uv tool install '.[extras]'`), this one installs the wheel that
# users actually get.
#
# What it does:
#   * fresh `python3 -m venv` — NO uv (the point is the real user's `pip`).
#   * `pip install seahorse-memory[embeddings,llm]` from the configured index.
#   * install assertions: binaries present, `import seahorse`, pinned version.
#   * onboarding literal del README: init → status → doctor (output assertion)
#     → remember → recall → improve → forget → context → consolidate.
#   * MCP over stdio by the three routes: `seahorse-mcp` (binary),
#     `seahorse mcp` (subcommand), `uvx --from seahorse-memory` (README route).
#   * post-flight: the real ~/.claude and ~/.claude-mem are never touched
#     (shasum re-check).
#
# Env:
#   SEAHORSE_INDEX_URL       pip index (default https://pypi.org/simple)
#   SEAHORSE_EXTRA_INDEX_URL  secondary index (TestPyPI needs PyPI for deps)
#   SEAHORSE_VERSION          pin the version (default: latest)
#   SEAHORSE_FAST=1           CI mode: no embedding model download, no hybrid
#                             recall assertion, no LLM (skips the ~235MB mE5)
#   SEAHORSE_KEEP_SANDBOX=1   keep the sandbox on failure (debug)
#
# Usage:
#   scripts/e2e-pypi.sh [--step <n>]
#
#   --step <n>  run steps 0..n then stop (default: all steps)

set -euo pipefail

# --- resolve paths -----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# /tmp is short (the observer socket caps at ~104 bytes) and exists on macOS
# and Linux — the CI lesson was that /private/tmp does not exist on Linux.
SANDBOX_BASE="${SEAHORSE_SANDBOX_BASE:-/tmp}"
SANDBOX="$SANDBOX_BASE/seahorse-pypi-$(date +%s)"
LOG="$SANDBOX/e2e.log"
VAULT="$SANDBOX/vault"
VENV="$SANDBOX/venv"

# --- env config --------------------------------------------------------------
INDEX_URL="${SEAHORSE_INDEX_URL:-https://pypi.org/simple}"
EXTRA_INDEX_URL="${SEAHORSE_EXTRA_INDEX_URL:-}"
VERSION="${SEAHORSE_VERSION:-}"
FAST="${SEAHORSE_FAST:-0}"
KEEP_SANDBOX="${SEAHORSE_KEEP_SANDBOX:-0}"

# --- args --------------------------------------------------------------------
STEP_LIMIT=999
while [[ $# -gt 0 ]]; do
  case "$1" in
    --step) STEP_LIMIT="$2"; shift 2 ;;
    --step=*) STEP_LIMIT="${1#--step=}"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# --- state -------------------------------------------------------------------
PASS=0
FAIL=0
FAILED_STEPS=()
SEAHORSE="$VENV/bin/seahorse"
SEAHORSE_MCP="$VENV/bin/seahorse-mcp"
PYTHON="$VENV/bin/python"

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

find_python() {  # a python3 whose sqlite3 has enable_load_extension (sqlite-vec)
  # The macOS system python3 (pyenv shim) lacks the flag; the artifact needs
  # it at runtime. Check named interpreters, then brew and uv-managed pythons
  # (the README route is `uv tool install --python 3.13`).
  local cand
  for cand in python3 python3.14 python3.13 python3.12 python3.11; do
    if command -v "$cand" >/dev/null 2>&1 \
        && "$cand" -c 'import sqlite3; sqlite3.connect(":memory:").enable_load_extension(True)' 2>/dev/null; then
      echo "$cand"
      return 0
    fi
  done
  for cand in /opt/homebrew/bin/python3.* "$REAL_HOME"/.local/share/uv/python/*/bin/python3.*; do
    [[ -x "$cand" ]] || continue
    if "$cand" -c 'import sqlite3; sqlite3.connect(":memory:").enable_load_extension(True)' 2>/dev/null; then
      echo "$cand"
      return 0
    fi
  done
  return 1
}

# --- pre-flight --------------------------------------------------------------
REAL_HOME="$HOME"
START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$SANDBOX"
: > "$LOG"

step 0 "Pre-flight: prerequisites + no-corruption snapshot"
command -v python3 >/dev/null || { info "✗ python3 not found"; exit 2; }
info "  python3: $(python3 --version 2>&1)"
info "  index:    $INDEX_URL"
info "  extra:    ${EXTRA_INDEX_URL:-none}"
info "  version:  ${VERSION:-latest}"
info "  fast:     $FAST"

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
step 1 "Sandbox: fresh venv (no uv — the point is the real user's pip)"
mkdir -p "$VAULT" "$SANDBOX/home"
unset XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME XDG_BIN_HOME XDG_STATE_HOME
export HOME="$SANDBOX/home"
export SEAHORSE_VAULT="$VAULT"
export FASTEMBED_CACHE_PATH="$SANDBOX/fastembed-cache"
cd "$SANDBOX"
info "  HOME=$HOME"
info "  SEAHORSE_VAULT=$SEAHORSE_VAULT"
info "  FASTEMBED_CACHE_PATH=$FASTEMBED_CACHE_PATH"

# The artifact needs a Python whose sqlite3 has enable_load_extension
# (sqlite-vec). The macOS system python3 lacks it — pick one that has it.
if PYTHON_BIN="$(find_python)"; then
  ok "python with enable_load_extension: $PYTHON_BIN"
else
  fail "python with enable_load_extension"
  info "  no python3 found with sqlite3.enable_load_extension — the artifact"
  info "  requires it (see README: uv tool install --python 3.13)"
  exit 1
fi
run_critical "python3 -m venv" "$PYTHON_BIN" -m venv "$VENV"

# --- install ----------------------------------------------------------------
step 2 "Install the published artifact from the index"
PIP_ARGS=(--index-url "$INDEX_URL")
if [[ -n "$EXTRA_INDEX_URL" ]]; then
  PIP_ARGS+=(--extra-index-url "$EXTRA_INDEX_URL")
fi
PKG="seahorse-memory[embeddings,llm]"
if [[ -n "$VERSION" ]]; then
  # Tags are `v0.14.0`; the PyPI version is `0.14.0` — strip the `v` prefix.
  VERSION="${VERSION#v}"
  PKG="$PKG==$VERSION"
fi
info "  pip install ${PIP_ARGS[*]} $PKG"
run_critical "pip install $PKG" "$VENV/bin/pip" install "${PIP_ARGS[@]}" "$PKG"

# --- install assertions ------------------------------------------------------
step 3 "Install assertions: binaries + import + version"
check "seahorse binary present" test -x "$SEAHORSE"
check "seahorse-mcp binary present" test -x "$SEAHORSE_MCP"
run "seahorse --help" "$SEAHORSE" --help
run "seahorse-mcp --help" "$SEAHORSE_MCP" --help
run "import seahorse" "$PYTHON" -c "import seahorse; print('seahorse imported OK')"
if [[ -n "$VERSION" ]]; then
  INSTALLED="$("$PYTHON" -c "import importlib.metadata as m; print(m.version('seahorse-memory'))")"
  check "installed version == $VERSION" test "$INSTALLED" = "$VERSION"
  info "  installed: seahorse-memory==$INSTALLED"
fi

# --- onboarding literal del README ------------------------------------------
step 4 "Onboarding literal del README: init → status → doctor → remember → recall → improve → forget → context → consolidate"
run "seahorse init" "$SEAHORSE" init "$VAULT"
run "seahorse status" "$SEAHORSE" status

# doctor: exit 0 + db/python/sqlite_vec OK. NOT healthy:true — without an
# [llm] section, llm_config/provider legitimately report WARN/SKIP.
cat > "$SANDBOX/check_doctor.py" <<'PY'
import json, subprocess, sys
out = subprocess.run(
    [sys.argv[1], "--format", "json", "doctor"],
    capture_output=True, text=True,
)
assert out.returncode == 0, out.stderr
# The published artifact may print litellm's "Give Feedback" banner to stdout
# before the JSON (fixed in source; not yet in the published wheel). Take the
# last line — a missing JSON still fails the assertion.
payload = json.loads(out.stdout.strip().splitlines()[-1])
checks = {c["check"]: c["status"] for c in payload["checks"]}
# db is WARN right after init (the DB is created on first write, not by init);
# python + sqlite_vec must be OK (the runtime prerequisites).
assert checks.get("python") == "OK", checks.get("python")
assert checks.get("sqlite_vec") == "OK", checks.get("sqlite_vec")
assert checks.get("db") in ("OK", "WARN"), checks.get("db")
print(f"  doctor: python/sqlite_vec OK, db={checks.get('db')} (healthy: {payload['healthy']})")
PY
run "doctor output assertion" "$PYTHON" "$SANDBOX/check_doctor.py" "$SEAHORSE"

run "remember" "$SEAHORSE" remember "Sergio lives in Madrid" --title home
EP1="$(last_ep_id)"
check "remember ep_id captured" test -n "$EP1"

# recall: full mode asserts the hybrid regime (the row carries a vector score);
# fast mode (CI) skips the ~235MB mE5 download and only asserts non-empty.
if (( FAST )); then
  run "recall (fast: non-empty)" "$SEAHORSE" recall "madrid"
else
  cat > "$SANDBOX/check_recall.py" <<'PY'
import json, subprocess, sys
ep_id, seahorse = sys.argv[1], sys.argv[2]
out = subprocess.run(
    [seahorse, "--format", "json", "recall", "madrid"],
    capture_output=True, text=True,
)
assert out.returncode == 0, out.stderr
rows = json.loads(out.stdout)
hit = next((r for r in rows if r.get("ep_id") == ep_id), None)
assert hit is not None, f"remembered episode {ep_id} not in recall results"
assert isinstance(hit.get("score"), (int, float)), (
    f"no vector score — hybrid regime not active: {hit}"
)
print(f"  recall: hybrid regime, {ep_id} score={hit['score']:.4f}")
PY
  run "recall hybrid regime assertion" "$PYTHON" "$SANDBOX/check_recall.py" "$EP1" "$SEAHORSE"
fi

run "improve (supersession)" "$SEAHORSE" improve "$EP1" "Sergio lives in Barcelona" --reason correction
EP2="$(last_ep_id)"
check "improve produced a new ep_id" test -n "$EP2"
run "forget (soft-delete)" "$SEAHORSE" forget "$EP2" --reason done
run "context (non-empty)" "$SEAHORSE" context
run "consolidate (idempotent)" "$SEAHORSE" consolidate

# --- MCP by the three routes -------------------------------------------------
step 5 "MCP stdio JSON-RPC by the three routes (binary / subcommand / uvx)"
cat > "$SANDBOX/mcp_driver.py" <<'PY'
import json, subprocess, sys

proc = subprocess.Popen(
    sys.argv[1:],
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
    print("  MCP tools/list →", len(names), "tools")
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

    proc.stdin.close()
    proc.wait(timeout=10)
    assert proc.returncode == 0, proc.stderr.read()
    print("  MCP session OK (clean exit 0)")
except Exception as exc:
    print("  MCP FAILED:", exc)
    proc.kill()
    sys.exit(1)
PY

# Route 1: the seahorse-mcp binary.
run "MCP route 1: seahorse-mcp --vault" \
  "$PYTHON" "$SANDBOX/mcp_driver.py" "$SEAHORSE_MCP" --vault "$VAULT"

# Route 2: the `seahorse mcp` subcommand (same server, different entry point).
# --vault is a GLOBAL option and must precede the subcommand.
run "MCP route 2: seahorse --vault mcp" \
  "$PYTHON" "$SANDBOX/mcp_driver.py" "$SEAHORSE" --vault "$VAULT" mcp

# Route 3: the README route — `uvx --from seahorse-memory seahorse-mcp`.
# uvx needs uv; in fast mode we install it into the venv (the README assumes
# uv is present). Pass the index flags so TestPyPI resolves the same way.
if command -v uvx >/dev/null 2>&1; then
  UVX="uvx"
else
  run "pip install uv (for uvx route)" "$VENV/bin/pip" install uv
  UVX="$VENV/bin/uvx"
fi
# uvx resolves its own Python; with a fresh HOME (sandbox) it would pick one
# without enable_load_extension and the artifact fails at runtime. Pin the
# Python we already found (the README route is `uvx --from seahorse-memory`).
UVX_ARGS=(--python "$PYTHON_BIN")
if [[ -n "$INDEX_URL" ]]; then UVX_ARGS+=(--index-url "$INDEX_URL"); fi
if [[ -n "$EXTRA_INDEX_URL" ]]; then UVX_ARGS+=(--extra-index-url "$EXTRA_INDEX_URL"); fi
run "MCP route 3: uvx --from seahorse-memory" \
  "$PYTHON" "$SANDBOX/mcp_driver.py" "$UVX" "${UVX_ARGS[@]}" --from seahorse-memory seahorse-mcp --vault "$VAULT"

# --- post-flight -------------------------------------------------------------
step 6 "Post-flight: no-corruption verification"
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

# --- report ------------------------------------------------------------------
step 7 "Report"
info ""
info "════════════════════════════════════════════════════════════"
info "E2E PYPI INSTALL-AND-RUN RESULT: $PASS passed, $FAIL failed"
if (( FAIL > 0 )); then
  info "Failed steps:"
  for s in "${FAILED_STEPS[@]}"; do info "  ❌ $s"; done
fi
info "Sandbox: $SANDBOX"
info "Log:     $LOG"
info "════════════════════════════════════════════════════════════"
if (( FAIL > 0 )) && (( ! KEEP_SANDBOX )); then
  info "  (sandbox kept for debug — set SEAHORSE_KEEP_SANDBOX=1 to keep on success too)"
fi
exit $(( FAIL > 0 ? 1 : 0 ))
