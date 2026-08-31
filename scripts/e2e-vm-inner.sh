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
# The CLI resolves the vault from SEAHORSE_VAULT (or --vault / cwd inside the
# vault); the inner script runs from $HOME, so the env var is required.
export SEAHORSE_VAULT="$VAULT"
# Command output is teed here so last_ep_id() can parse `ep_id:` lines.
LOG="$HOME/e2e-vm-inner.log"
: > "$LOG"

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
  info "STEP $1: $*"
  info "════════════════════════════════════════════════════════════"
}

run() {  # run <label> <cmd...> — transparent, live output, teed to $LOG
  local label="$1"; shift
  info ""
  info "▶ $*"
  set +e
  "$@" 2>&1 | tee -a "$LOG"
  local rc=${PIPESTATUS[0]}
  set -e
  if (( rc == 0 )); then ok "$label"; else fail "$label"; fi
}

run_critical() {  # run_critical <label> <cmd...> — abort on failure
  local label="$1"; shift
  info ""
  info "▶ $*"
  set +e
  "$@" 2>&1 | tee -a "$LOG"
  local rc=${PIPESTATUS[0]}
  set -e
  if (( rc == 0 )); then
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

retry() {  # retry <label> <attempts> <cmd...> — retry with linear backoff
  local label="$1"; shift
  local attempts="$1"; shift
  local n=0 rc=0
  while (( n < attempts )); do
    n=$((n + 1))
    info ""
    info "▶ (attempt $n/$attempts) $*"
    set +e
    "$@" 2>&1 | tee -a "$LOG"
    rc=${PIPESTATUS[0]}
    set -e
    if (( rc == 0 )); then
      ok "$label"
      return 0
    fi
    info "  attempt $n failed (rc=$rc); retrying in $((n * 10))s..."
    sleep $((n * 10))
  done
  fail "$label"
  return 1
}

last_ep_id() {  # most recent `ep_id:` line in the log (human remember/improve)
  sed -n 's/^[[:space:]]*ep_id:[[:space:]]*\([0-9a-f-]*\).*/\1/p' "$LOG" | tail -1
}

# --- step 1: provision -------------------------------------------------------
step 1 "Provision: apt python3 + venv + pip + curl (no uv, no git, no build-essential)"
run_critical "apt-get update" apt-get update -qq
# zstd: the Ollama install script needs it to extract the binary.
run_critical "apt-get install python3 python3-venv python3-pip curl zstd" \
  apt-get install -y -qq python3 python3-venv python3-pip curl zstd
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
# The wheel download is flaky on some networks (truncated → hash mismatch);
# retry with backoff. pip's HTTP cache keeps completed blobs across attempts.
if retry "pip install $PKG" 3 "$VENV/bin/pip" install "${PIP_ARGS[@]}" "$PKG"; then
  :
else
  info "ABORT: pip install failed after 3 attempts — cannot continue."
  exit 1
fi
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
# improve supersedes EP1 (sets invalid_at); forget must target the NEW episode.
EP2="$(last_ep_id)"
check "improve ep_id captured" test -n "$EP2"
run "forget (soft-delete)" "$SEAHORSE" forget "$EP2" --reason done
run "context (non-empty)" "$SEAHORSE" context
run "consolidate (idempotent)" "$SEAHORSE" consolidate

# --- step 4: Ollama in-VM ----------------------------------------------------
if (( FAST )); then
  info ""
  info "STEP 4: Ollama in-VM — SKIPPED (SEAHORSE_FAST=1)"
else
  step 4 "Ollama in-VM: install script + serve + pull qwen3:0.6b"
  run_critical "ollama install script" bash -c "curl -fsSL https://ollama.com/install.sh | sh"
  check "ollama binary present" command -v ollama
  # Some networks cannot reach Cloudflare R2 via its DNS-resolved anycast IPs
  # (172.64.66.1 / 172.64.190.1 time out: `dial tcp ... i/o timeout`), which
  # breaks the ollama blob download. Detect it and map the R2 hosts to a
  # known-good Cloudflare anycast IP (the one registry.ollama.ai uses);
  # verified by downloading a real blob range returning GGUF data.
  R2_BUCKET="dd20bb891979d25aebc8bec07b2b3bbc"
  if curl -sI -m 5 https://r2.cloudflarestorage.com >/dev/null 2>&1; then
    ok "R2 reachable via DNS (no hosts workaround needed)"
  else
    info "  R2 default route unreachable — applying /etc/hosts workaround"
    run_critical "R2 route workaround in /etc/hosts" bash -c "
      cat >> /etc/hosts <<'EOF'
104.18.16.170 r2.cloudflarestorage.com
104.18.16.170 $R2_BUCKET.r2.cloudflarestorage.com
EOF
"
  fi
  # The install script's systemd service does not start in a container (no
  # systemd as PID 1); serve manually in the background BEFORE pulling.
  nohup ollama serve > "$HOME/ollama.log" 2>&1 &
  OLLAMA_PID=$!
  info "  ollama serve pid $OLLAMA_PID"
  # Wait for the API to accept requests.
  API_UP=0
  for _ in $(seq 1 30); do
    if curl -s -m 2 http://localhost:11434 >/dev/null 2>&1; then API_UP=1; break; fi
    sleep 1
  done
  if (( API_UP )); then
    ok "ollama API up (localhost:11434)"
  else
    fail "ollama API up (localhost:11434)"
    info "  ollama.log tail:"
    tail -5 "$HOME/ollama.log" >&2 || true
    exit 1
  fi
  # The model blob download from Cloudflare R2 is flaky; retry with backoff
  # (ollama resumes partial blobs, so retries are cheap).
  if retry "ollama pull qwen3:0.6b" 3 ollama pull qwen3:0.6b; then
    :
  else
    info "ABORT: ollama pull failed after 3 attempts — cannot continue."
    exit 1
  fi
fi

# --- step 5: real LLM extraction ---------------------------------------------
if (( FAST )); then
  info ""
  info "STEP 5: LLM extraction — SKIPPED (SEAHORSE_FAST=1)"
else
  step 5 "Real LLM extraction: [llm] config → remember --extraction-mode llm"
  # `seahorse init` already writes a `[llm]` section (primary qwen3:1.7b);
  # appending a second one is a TOML duplicate-key error. Rewrite the section
  # through the library's own writer, which preserves the [seahorse] values.
  # Ollama's base URL defaults to http://localhost:11434 — the in-VM server.
  run "rewrite [llm] config" "$PYTHON" -c "
from pathlib import Path
from seahorse.cli.config import LlmConfig, write_llm_config
write_llm_config(Path('$VAULT'), LlmConfig(primary='ollama/qwen3:0.6b'))
print('  [llm] rewritten: primary = ollama/qwen3:0.6b')
"
  # The write path only routes the LLM extraction for source_type=agent (the
  # cost guard in decide_path); the CLI default is "human", which silently
  # degrades to skip. The agent source_type is the real user case here.
  run "remember --extraction-mode llm" \
    "$SEAHORSE" remember "The deploy pipeline gates v1.0 on a clean VM install" \
    --title "deploy pipeline [vm:1]" --extraction-mode llm --source-type agent
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
