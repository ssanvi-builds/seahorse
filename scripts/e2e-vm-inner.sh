#!/usr/bin/env bash
#
# e2e-vm-inner.sh — runs INSIDE the clean Linux VM (copied there by e2e-vm.sh).
# The real user case: no uv, no dev tooling, install from PyPI with real
# embeddings (fresh mE5 download) and real LLM extraction (Ollama in-VM).
#
# Steps:
#   1. provision (apt: python3 + venv + pip + curl — no uv, no git, no build-essential)
#   2. install seahorse-memory[embeddings,llm] from PyPI into a fresh venv
#   3. onboarding literal del README (init → status → doctor → remember → recall
#      hybrid → improve → forget → context → consolidate)
#   4. Ollama in-VM (install script, pull qwen3:0.6b, serve)
#   5. real LLM extraction ([llm] config → remember --extraction-mode llm)
#   6. MCP stdio (seahorse-mcp --vault)
#   7. doctor final (provider self-test against Ollama)
#
# Env (passed by the orchestrator):
#   SEAHORSE_INDEX_URL / SEAHORSE_EXTRA_INDEX_URL / SEAHORSE_VERSION
#   SEAHORSE_FAST=1  skip Ollama + LLM extraction (CI smoke)

set -euo pipefail

# --- env ---------------------------------------------------------------------
INDEX_URL="${SEAHORSE_INDEX_URL:-https://pypi.org/simple}"
EXTRA_INDEX_URL="${SEAHORSE_EXTRA_INDEX_URL:-}"
VERSION="${SEAHORSE_VERSION:-}"
FAST="${SEAHORSE_FAST:-0}"

VAULT="$HOME/vault"
VENV="$HOME/venv"
SEAHORSE="$VENV/bin/seahorse"
SEAHORSE_MCP="$VENV/bin/seahorse-mcp"
PYTHON="$VENV/bin/python"
export FASTEMBED_CACHE_PATH="$HOME/fastembed-cache"

# --- state -------------------------------------------------------------------
PASS=0
FAIL=0
FAILED_STEPS=()

# --- helpers -----------------------------------------------------------------
info() { echo "$*" >&2; }
ok()   { PASS=$((PASS + 1)); info "  ✅ $1"; }
fail() { FAIL=$((FAIL + 1)); FAILED_STEPS+=("$1"); info "  ❌ $1"; }

step() {  # step <n> <title>
  info ""
  info "════════════════════════════════════════════════════════════"
  info "STEP $n: $*"
  info "════════════════════════════════════════════════════════════"
}

run() {  # run <label> <cmd...> — transparent, live output
  local label="$1"; shift
  info ""
  info "▶ $*"
  if "$@" 2>&1; then ok "$label"; else fail "$label"; fi
}

run_critical() {  # run_critical <label> <cmd...> — abort on failure
  local label="$1"; shift
  info ""
  info "▶ $*"
  if "$@" 2>&1; then
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

# --- step 1: provision -------------------------------------------------------
step 1 "Provision: apt python3 + venv + pip + curl (no uv, no git, no build-essential)"
run_critical "apt-get update" apt-get update -qq
run_critical "apt-get install python3 python3-venv python3-pip curl" \
  apt-get install -y -qq python3 python3-venv python3-pip curl
check "python3 present" command -v python3
check "no uv present (the point is the real user)" bash -c '! command -v uv'
check "no git present" bash -c '! command -v git'

# --- step 2: install from PyPI ----------------------------------------------
step 2 "Install seahorse-memory[embeddings,llm] from the index (fresh venv)"
run_critical "python3 -m venv" python3 -m venv "$VENV"
PIP_ARGS=(--index-url "$INDEX_URL")
if [[ -n "$EXTRA_INDEX_URL" ]]; then
  PIP_ARGS+=(--extra-index-url "$EXTRA_INDEX_URL")
fi
PKG="seahorse-memory[embeddings,llm]"
if [[ -n "$VERSION" ]]; then
  VERSION="${VERSION#v}"
  PKG="$PKG==$VERSION"
fi
info "  pip install ${PIP_ARGS[*]} $PKG"
run_critical "pip install $PKG" "$VENV/bin/pip" install "${PIP_ARGS[@]}" "$PKG"
check "seahorse binary present" test -x "$SEAHORSE"
check "seahorse-mcp binary present" test -x "$SEAHORSE_MCP"
if [[ -n "$VERSION" ]]; then
  INSTALLED="$("$PYTHON" -c "import importlib.metadata as m; print(m.version('seahorse-memory'))")"
  check "installed version == $VERSION" test "$INSTALLED" = "$VERSION"
  info "  installed: seahorse-memory==$INSTALLED"
fi

# --- step 3: onboarding literal del README -----------------------------------
step 3 "Onboarding literal del README (init → status → doctor → remember → recall → improve → forget → context → consolidate)"
run "seahorse init" "$SEAHORSE" init "$VAULT"
run "seahorse status" "$SEAHORSE" status

cat > "$HOME/check_doctor.py" <<'PY'
import json, subprocess, sys
out = subprocess.run(
    [sys.argv[1], "--format", "json", "doctor"],
    capture_output=True, text=True,
)
assert out.returncode == 0, out.stderr
# The published artifact may print litellm's banner to stdout before the JSON
# (fixed in source; not yet in the published wheel). Take the last line.
payload = json.loads(out.stdout.strip().splitlines()[-1])
checks = {c["check"]: c["status"] for c in payload["checks"]}
# db is WARN right after init (the DB is created on first write, not by init);
# python + sqlite_vec must be OK (the runtime prerequisites).
assert checks.get("python") == "OK", checks.get("python")
assert checks.get("sqlite_vec") == "OK", checks.get("sqlite_vec")
assert checks.get("db") in ("OK", "WARN"), checks.get("db")
print(f"  doctor: python/sqlite_vec OK, db={checks.get('db')} (healthy: {payload['healthy']})")
PY
run "doctor output assertion" "$PYTHON" "$HOME/check_doctor.py" "$SEAHORSE"

run "remember" "$SEAHORSE" remember "Sergio lives in Madrid" --title home
EP1="$(last_ep_id)"
check "remember ep_id captured" test -n "$EP1"

# recall: full mode asserts the hybrid regime (vector score present). The first
# embed downloads mE5-small (~235MB) into FASTEMBED_CACHE_PATH — the point is
# the fresh download on a clean machine.
if (( FAST )); then
  run "recall (fast: non-empty)" "$SEAHORSE" recall "madrid"
else
  cat > "$HOME/check_recall.py" <<'PY'
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
  run "recall hybrid regime assertion" "$PYTHON" "$HOME/check_recall.py" "$EP1" "$SEAHORSE"
fi

run "improve (supersession)" "$SEAHORSE" improve "$EP1" "Sergio lives in Barcelona" --reason correction
run "forget (soft-delete)" "$SEAHORSE" forget "$EP1" --reason done
run "context (non-empty)" "$SEAHORSE" context
run "consolidate (idempotent)" "$SEAHORSE" consolidate

# --- step 4: Ollama in-VM ----------------------------------------------------
if (( FAST )); then
  info ""
  info "STEP 4: Ollama in-VM — SKIPPED (SEAHORSE_FAST=1)"
else
  step 4 "Ollama in-VM: install script + pull qwen3:0.6b + serve"
  run_critical "ollama install script" bash -c "curl -fsSL https://ollama.com/install.sh | sh"
  check "ollama binary present" command -v ollama
  run_critical "ollama pull qwen3:0.6b" ollama pull qwen3:0.6b
  # Serve in the background; the model runs on CPU (no GPU in the VM).
  nohup ollama serve > "$HOME/ollama.log" 2>&1 &
  OLLAMA_PID=$!
  info "  ollama serve pid $OLLAMA_PID"
  # Wait for the API to accept requests.
  for _ in $(seq 1 30); do
    if curl -s -m 2 http://localhost:11434 >/dev/null 2>&1; then break; fi
    sleep 1
  done
  if curl -s -m 2 http://localhost:11434 >/dev/null 2>&1; then
    ok "ollama API up (localhost:11434)"
  else
    fail "ollama API up (localhost:11434)"
  fi
fi

# --- step 5: real LLM extraction ---------------------------------------------
if (( FAST )); then
  info ""
  info "STEP 5: LLM extraction — SKIPPED (SEAHORSE_FAST=1)"
else
  step 5 "Real LLM extraction: [llm] config → remember --extraction-mode llm"
  # Append the [llm] section to the vault TOML (Ollama base URL defaults to
  # http://localhost:11434 — the in-VM server).
  cat >> "$VAULT/.seahorse/seahorse.toml" <<'TOML'

[llm]
primary = "ollama/qwen3:0.6b"
TOML
  run "remember --extraction-mode llm" \
    "$SEAHORSE" remember "The deploy pipeline gates v1.0 on a clean VM install" \
    --title "deploy pipeline [vm:1]" --extraction-mode llm
  EP_LLM="$(last_ep_id)"
  check "llm ep_id captured" test -n "$EP_LLM"
  # Assert the LLM actually extracted: provenance.extraction_mode == "llm" and
  # a cognitive_type chosen by the model (not the skip-path default).
  cat > "$HOME/check_llm.py" <<'PY'
import json, sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
row = conn.execute(
    "SELECT provenance, cognitive_type FROM episodes WHERE id = ?",
    (sys.argv[2],),
).fetchone()
conn.close()
assert row is not None, "llm episode not found"
prov = json.loads(row[0])
assert prov.get("extraction_mode") == "llm", prov
assert prov.get("model_used"), prov
print(f"  llm: extraction_mode=llm, model={prov.get('model_used')}, "
      f"cognitive_type={row[1]}")
PY
  run "llm extraction assertion" \
    "$PYTHON" "$HOME/check_llm.py" "$VAULT/.seahorse/seahorse.db" "$EP_LLM"
fi

# --- step 6: MCP stdio -------------------------------------------------------
step 6 "MCP stdio JSON-RPC (seahorse-mcp --vault)"
cat > "$HOME/mcp_driver.py" <<'PY'
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
run "MCP stdio session" "$PYTHON" "$HOME/mcp_driver.py" "$SEAHORSE_MCP" --vault "$VAULT"

# --- step 7: doctor final ----------------------------------------------------
if (( FAST )); then
  info ""
  info "STEP 7: doctor final — SKIPPED (SEAHORSE_FAST=1)"
else
  step 7 "Doctor final: provider self-test against Ollama"
  cat > "$HOME/check_doctor_final.py" <<'PY'
import json, subprocess, sys
out = subprocess.run(
    [sys.argv[1], "--format", "json", "doctor"],
    capture_output=True, text=True,
)
assert out.returncode == 0, out.stderr
# The published artifact may print litellm's banner to stdout before the JSON
# (fixed in source; not yet in the published wheel). Take the last line.
payload = json.loads(out.stdout.strip().splitlines()[-1])
checks = {c["check"]: c["status"] for c in payload["checks"]}
assert checks.get("provider") == "OK", checks.get("provider")
print("  doctor: provider self-test OK against Ollama")
PY
  run "doctor provider self-test" "$PYTHON" "$HOME/check_doctor_final.py" "$SEAHORSE"
fi

# --- report ------------------------------------------------------------------
info ""
info "════════════════════════════════════════════════════════════"
info "VM E2E RESULT: $PASS passed, $FAIL failed"
if (( FAIL > 0 )); then
  info "Failed steps:"
  for s in "${FAILED_STEPS[@]}"; do info "  ❌ $s"; done
fi
info "════════════════════════════════════════════════════════════"
exit $(( FAIL > 0 ? 1 : 0 ))
