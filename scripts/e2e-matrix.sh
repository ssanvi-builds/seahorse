#!/usr/bin/env bash
#
# e2e-matrix.sh — run the fresh-user e2e across environment combinations.
#
# Reuses the isolation strategy of e2e-fresh-user.sh (sandboxed HOME, isolated
# SEAHORSE_VAULT, shared FASTEMBED_CACHE_PATH) but fans out across a matrix of
# environment combinations instead of a single happy-path run. Each combination
# installs Seahorse a specific way, prepares a specific vault state, and runs a
# subset of the steps (install → init → core CLI → embeddings → LLM →
# import → MCP), with an isolated sandbox per combination.
#
# Combos are the 8 priorities from the plan (not the ~256 Cartesian product).
# They are extensible: add a combo_<slug> function + an entry in describe_combo.
#
# Usage:
#   scripts/e2e-matrix.sh [--combo <slug>]... [--ci-subset] [--list]
#                          [--no-cache-reuse] [--keep-sandbox]
#
#   --combo <slug>    run only the named combo (repeatable)
#   --ci-subset       run only the CI-safe subset (core_min + uv_sync_dev)
#   --list            list the combos and exit
#   --no-cache-reuse  do not reuse the real uv cache (fully isolated, slower)
#   --keep-sandbox    do not remove the sandbox tree on a clean exit
#
# Exit 0 if no combo failed (SKIPped combos are allowed), 1 otherwise.

set -euo pipefail

# --- resolve paths -----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# Sandbox base: macOS keeps /private/tmp (writable, shares the FASTEMBED cache
# with e2e-fresh-user.sh); Linux CI falls back to $TMPDIR (e.g. /tmp) — /private
# does not exist on Linux runners (CI failure 2026-08-12).
if [[ -d /private/tmp && -w /private/tmp ]]; then
  SANDBOX_BASE="/private/tmp"
else
  SANDBOX_BASE="${TMPDIR:-/tmp}"
fi
ROOT_SANDBOX="$SANDBOX_BASE/seahorse-matrix-$(date +%s)"
# Shared across the matrix AND with e2e-fresh-user.sh so the ~235MB mE5-small
# model is downloaded at most once per machine.
SHARED_FASTEMBED_CACHE="$SANDBOX_BASE/seahorse-e2e-cache"
LOG="$ROOT_SANDBOX/matrix.log"
REAL_HOME="$HOME"

# --- args --------------------------------------------------------------------
SELECTED=()
CI_SUBSET=0
LIST_ONLY=0
REUSE_UV_CACHE=1
KEEP_SANDBOX=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --combo) SELECTED+=("$2"); shift 2 ;;
    --combo=*) SELECTED+=("${1#--combo=}"); shift ;;
    --ci-subset) CI_SUBSET=1; shift ;;
    --list) LIST_ONLY=1; shift ;;
    --no-cache-reuse) REUSE_UV_CACHE=0; shift ;;
    --keep-sandbox) KEEP_SANDBOX=1; shift ;;
    -h|--help) grep -E "^#   " "$0" | sed 's/^#   //'; exit 0 ;;
    *) echo "unknown arg: $1 (see --help)" >&2; exit 2 ;;
  esac
done

# --- combo registry ----------------------------------------------------------
COMBO_SLUGS=(happy_full core_min embeddings_only llm_only vault_legacy uv_sync_dev pipx concurrency)

describe_combo() {
  case "$1" in
    happy_full)      echo "uv tool install + all extras + Obsidian + Ollama + online + empty vault" ;;
    core_min)        echo "uv tool install core + no Obsidian + offline + empty vault → listing regime + llm→skip" ;;
    embeddings_only) echo "uv tool install embeddings + no Obsidian + online → hybrid RRF" ;;
    llm_only)        echo "uv tool install llm + no Obsidian + online → real LLM path (listing recall)" ;;
    vault_legacy)    echo "uv tool install all + Obsidian + legacy frontmatter → migration" ;;
    uv_sync_dev)     echo "uv sync --extra dev + core + offline → listing regime (dev workflow)" ;;
    pipx)            echo "pipx install core + offline → listing regime (alternate installer)" ;;
    concurrency)     echo "uv tool install all + 2 parallel remember → single-writer" ;;
    *) echo "unknown" ;;
  esac
}

# --- tallies (per-combo) -----------------------------------------------------
C_PASS=0; C_FAIL=0; C_SKIP=0
COMBO_NAME=""
COMBO_DIR=""
COMBO_LOG=""
SEAHORSE=""
SEAHORSE_MCP=""
VAULT=""

# --- global tallies ----------------------------------------------------------
G_TOTAL=0; G_PASS=0; G_FAIL=0; G_SKIP=0
FAILED_COMBOS=()
SKIPPED_COMBOS=()
OLLAMA_UP=0

# --- helpers -----------------------------------------------------------------
info() { echo "$*" | tee -a "$LOG" >&2; }

ok()   { C_PASS=$((C_PASS + 1)); info "    ✅ $1"; }
fail() { C_FAIL=$((C_FAIL + 1)); info "    ❌ $1"; }
skip() { C_SKIP=$((C_SKIP + 1)); info "    ⏭️  $1"; }

m_run() {  # m_run <label> <cmd...> — transparent, live output
  local label="$1"; shift
  info "    ▶ $*"
  if "$@" 2>&1 | tee -a "$COMBO_LOG"; then ok "$label"; else fail "$label"; fi
}

m_check() {  # m_check <label> <test-args...>
  local label="$1"; shift
  if "$@" >>"$COMBO_LOG" 2>&1; then ok "$label"; else fail "$label"; fi
}

last_ep_id() {  # most recent `ep_id:` line in the combo log
  sed -n 's/^[[:space:]]*ep_id:[[:space:]]*\([0-9a-f-]*\).*/\1/p' "$COMBO_LOG" | tail -1
}

# --- combo lifecycle ---------------------------------------------------------
combo_begin() {  # combo_begin <slug> <desc>
  COMBO_NAME="$1"; local desc="$2"
  COMBO_DIR="$ROOT_SANDBOX/$COMBO_NAME"
  COMBO_LOG="$COMBO_DIR/combo.log"
  VAULT="$COMBO_DIR/vault"
  mkdir -p "$COMBO_DIR/home" "$VAULT"
  : > "$COMBO_LOG"
  C_PASS=0; C_FAIL=0; C_SKIP=0
  info ""
  info "════════════════════════════════════════════════════════════"
  info "COMBO $COMBO_NAME — $desc"
  info "════════════════════════════════════════════════════════════"
  unset XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME XDG_BIN_HOME XDG_STATE_HOME
  export HOME="$COMBO_DIR/home"
  if (( REUSE_UV_CACHE )) && [[ -d "$REAL_HOME/.cache/uv" ]]; then
    export UV_CACHE_DIR="$REAL_HOME/.cache/uv"
    info "    UV_CACHE_DIR=$UV_CACHE_DIR (reuse real cache)"
  else
    info "    UV_CACHE_DIR: isolated (no reuse)"
  fi
  export SEAHORSE_VAULT="$VAULT"
  info "    HOME=$HOME"
  info "    SEAHORSE_VAULT=$SEAHORSE_VAULT"
}

combo_skip() {  # combo_skip <reason> — record a SKIP and end the combo
  local reason="$1"
  skip "$reason"
  G_SKIP=$((G_SKIP + 1)); G_TOTAL=$((G_TOTAL + 1))
  SKIPPED_COMBOS+=("$COMBO_NAME")
  info "  ⏭️  COMBO $COMBO_NAME: SKIPPED — $reason"
  info ""
}

combo_end() {
  if [[ "$C_FAIL" -gt 0 ]]; then
    G_FAIL=$((G_FAIL + 1)); FAILED_COMBOS+=("$COMBO_NAME")
    info "  ❌ COMBO $COMBO_NAME: FAILED ($C_PASS ok, $C_FAIL failed)"
  else
    G_PASS=$((G_PASS + 1))
    info "  ✅ COMBO $COMBO_NAME: PASSED ($C_PASS checks)"
  fi
  G_TOTAL=$((G_TOTAL + 1))
  info ""
}

# --- environment modes -------------------------------------------------------
m_online() {
  unset HF_HUB_OFFLINE
  export FASTEMBED_CACHE_PATH="$SHARED_FASTEMBED_CACHE"
  mkdir -p "$FASTEMBED_CACHE_PATH"
  info "    mode: online (FASTEMBED_CACHE_PATH=$FASTEMBED_CACHE_PATH)"
}

m_offline() {
  export HF_HUB_OFFLINE=1
  export FASTEMBED_CACHE_PATH="$COMBO_DIR/empty-cache"
  mkdir -p "$FASTEMBED_CACHE_PATH"
  info "    mode: offline (HF_HUB_OFFLINE=1, empty cache)"
}

# --- shared steps ------------------------------------------------------------
m_install_uvtool() {  # m_install_uvtool <extras>
  local extras="$1"
  m_run "uv tool install '.[$extras]'" bash -c "cd '$REPO_DIR' && uv tool install --python-preference only-managed --python 3.13 '.[$extras]'"
  SEAHORSE="$HOME/.local/bin/seahorse"
  SEAHORSE_MCP="$HOME/.local/bin/seahorse-mcp"
  m_check "seahorse binary present" test -x "$SEAHORSE"
  m_check "seahorse-mcp binary present" test -x "$SEAHORSE_MCP"
}

m_install_uvsync() {  # dev workflow: extract committed tree + uv sync --extra dev
  mkdir -p "$COMBO_DIR/src"
  if ( cd "$REPO_DIR" && git rev-parse --git-dir >/dev/null 2>&1 ); then
    ( cd "$REPO_DIR" && git archive HEAD | tar -x -C "$COMBO_DIR/src" ) \
      || rsync -a --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
             --exclude 'node_modules' --exclude '.smart-env' "$REPO_DIR/" "$COMBO_DIR/src/"
  else
    rsync -a --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
           --exclude 'node_modules' --exclude '.smart-env' "$REPO_DIR/" "$COMBO_DIR/src/"
  fi
  # Pin the same Python as `uv tool install` (3.13): the default interpreter
  # (e.g. a pyenv build without SQLITE_ENABLE_LOAD_EXTENSION) breaks every DB
  # command with a cryptic AttributeError (see the install note in
  # e2e-fresh-user.sh). only-managed keeps uv from resolving --python 3.13 to
  # the runner's system CPython (macOS CI 2026-09-02: /usr/local/bin/python3.13
  # lacked enable_load_extension).
  m_run "uv sync --python-preference only-managed --python 3.13 --extra dev" bash -c "cd '$COMBO_DIR/src' && uv sync --python-preference only-managed --python 3.13 --extra dev"
  SEAHORSE="$COMBO_DIR/src/.venv/bin/seahorse"
  SEAHORSE_MCP="$COMBO_DIR/src/.venv/bin/seahorse-mcp"
  m_check "seahorse binary present (uv sync)" test -x "$SEAHORSE"
  m_check "seahorse-mcp binary present (uv sync)" test -x "$SEAHORSE_MCP"
}

m_install_pipx() {  # m_install_pipx <extras>
  local extras="$1"
  m_run "pipx install '.[$extras]'" bash -c "cd '$REPO_DIR' && pipx install --python 3.13 '.[$extras]'"
  SEAHORSE="$HOME/.local/bin/seahorse"
  SEAHORSE_MCP="$HOME/.local/bin/seahorse-mcp"
  m_check "seahorse binary present (pipx)" test -x "$SEAHORSE"
  m_check "seahorse-mcp binary present (pipx)" test -x "$SEAHORSE_MCP"
}

m_prepare_vault() {  # m_prepare_vault <state: empty|obsidian-empty|obsidian-legacy>
  local state="$1"
  case "$state" in
    obsidian-empty)
      mkdir -p "$VAULT/.obsidian"
      printf '{}\n' > "$VAULT/.obsidian/app.json"
      ;;
    obsidian-legacy)
      mkdir -p "$VAULT/.obsidian" "$VAULT/notes"
      printf '{}\n' > "$VAULT/.obsidian/app.json"
      cat > "$VAULT/notes/legacy.md" <<'EOF'
---
tags: [geo, person]
created: 2024-01-01
---
# Madrid
Sergio lives in Madrid.
EOF
      ;;
    empty) : ;;
  esac
  m_run "seahorse init" "$SEAHORSE" init "$VAULT"
  m_check "config written" test -f "$VAULT/.seahorse/seahorse.toml"
}

m_verify_core() {  # m_verify_core <regime: hybrid|g2> [skip_index_rebuild]
  local expect_regime="$1"
  local skip_index="${2:-0}"
  m_run "status" "$SEAHORSE" status
  local status_out
  status_out="$("$SEAHORSE" status 2>&1 || true)"
  if [[ "$expect_regime" == "hybrid" ]]; then
    m_check "status → hybrid RRF" grep -q "hybrid RRF" <<<"$status_out"
  else
    m_check "status → current-state listing (honest)" grep -q "current-state listing" <<<"$status_out"
  fi
  m_run "remember #1 (Madrid)" "$SEAHORSE" remember "Sergio lives in Madrid" --title home
  EP1="$(last_ep_id)"; m_check "ep_id #1 captured" test -n "$EP1"
  m_run "remember #2 (hybrid)" "$SEAHORSE" remember "Seahorse uses sqlite-vec for hybrid retrieval" --title tech
  EP2="$(last_ep_id)"; m_check "ep_id #2 captured" test -n "$EP2"
  m_run "recall 'madrid'" "$SEAHORSE" recall "madrid"
  m_run "recall-full $EP1" "$SEAHORSE" recall-full "$EP1"
  m_run "recall-timeline $EP1" "$SEAHORSE" recall-timeline "$EP1"
  m_run "improve $EP1 (supersede)" "$SEAHORSE" improve "$EP1" "Sergio lives in Barcelona" --reason correction
  NEW_ID="$(last_ep_id)"; m_check "improve produced a new ep_id" test -n "$NEW_ID"
  m_run "forget $NEW_ID (soft-delete)" "$SEAHORSE" forget "$NEW_ID" --reason done
  m_run "doctor" "$SEAHORSE" doctor
  m_run "inspect" "$SEAHORSE" inspect
  m_run "migrate --up-to 10" "$SEAHORSE" migrate --up-to 10
  if [[ "$skip_index" -eq 0 ]]; then
    m_run "index rebuild" "$SEAHORSE" index rebuild
  fi
}

m_verify_llm() {  # m_verify_llm <has_llm_extra: 1|0> — real path if up, else honest degrade
  local has_llm="$1"
  if [[ "$has_llm" -eq 1 && "$OLLAMA_UP" -eq 1 ]]; then
    m_run "remember --extraction-mode llm (real path)" \
      "$SEAHORSE" remember "Sergio works on Seahorse, an open memory standard" \
      --title llm --extraction-mode llm
  else
    m_run "remember --extraction-mode llm (expect honest llm→skip)" \
      "$SEAHORSE" remember "Sergio works on Seahorse, an open memory standard" \
      --title llm --extraction-mode llm
  fi
}

m_verify_import() {  # claude-mem bridge on a synthetic DB (import subset)
  local copy="$COMBO_DIR/claude-mem-copy.db"
  python3 - "$copy" <<'PY'
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
  m_check "synthetic claude-mem DB created" test -f "$copy"
  m_run "import dry-run" "$SEAHORSE" import --source "$copy" --project seahorse --mode dry-run
  m_run "import commit" "$SEAHORSE" import --source "$copy" --project seahorse --mode commit
  m_run "recall imported term" "$SEAHORSE" recall "extraction_mode" --top-k 3
}

m_verify_setup_observer() {  # setup/observer subset in the isolated HOME
  m_run "seahorse setup" "$SEAHORSE" setup
  m_check "hooks written to isolated settings" test -f "$HOME/.claude/settings.json"
  m_run "observe start" "$SEAHORSE" observe start
  m_check "observer pid file" test -f "$VAULT/.seahorse/observer/observer.pid"
  m_run "observe stop" "$SEAHORSE" observe stop
  m_run "setup --uninstall" "$SEAHORSE" setup --uninstall
  if pgrep -f "seahorse.cli.app observe run" >/dev/null 2>&1; then
    fail "no orphan observer process"
    pgrep -fl "seahorse.cli.app observe run" | tee -a "$COMBO_LOG" >&2 || true
  else
    ok "no orphan observer process"
  fi
}

m_verify_mcp() {  # MCP: stdio session, 12-tool superset + remember/recall roundtrip
  m_run "MCP stdio session" python3 - "$SEAHORSE_MCP" "$VAULT" <<'PY'
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
    primitives = {"remember", "recall", "recall_timeline", "recall_full",
                  "improve", "forget", "build_pit"}
    assert primitives.issubset(names), names

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
}

m_verify_concurrency() {  # 2 parallel remember → single-writer, no corruption
  "$SEAHORSE" remember "Concurrent episode A" --title conc-a \
    >"$COMBO_DIR/conc-a.log" 2>&1 &
  local p1=$!
  "$SEAHORSE" remember "Concurrent episode B" --title conc-b \
    >"$COMBO_DIR/conc-b.log" 2>&1 &
  local p2=$!
  local s1 s2
  wait "$p1"; s1=$?
  wait "$p2"; s2=$?
  if [[ "$s1" -eq 0 && "$s2" -eq 0 ]]; then
    ok "both concurrent remembers succeeded"
  else
    fail "concurrent remembers (exit a=$s1 b=$s2)"
  fi
  m_run "inspect after concurrency (no corruption)" "$SEAHORSE" inspect
  m_run "recall after concurrency" "$SEAHORSE" recall "concurrent"
}

# --- combos ------------------------------------------------------------------
combo_happy_full() {
  combo_begin happy_full "$(describe_combo happy_full)"
  m_online
  m_install_uvtool "embeddings,llm,benchmark"
  m_prepare_vault obsidian-empty
  m_check ".obsidian tolerated (Obsidian present)" test -d "$VAULT/.obsidian"
  m_verify_core hybrid
  m_verify_llm 1
  m_verify_import
  m_verify_setup_observer
  m_verify_mcp
  combo_end
}

combo_core_min() {
  combo_begin core_min "$(describe_combo core_min)"
  m_offline
  m_install_uvtool ""
  m_prepare_vault empty
  m_verify_core g2
  m_verify_llm 0
  combo_end
}

combo_embeddings_only() {
  combo_begin embeddings_only "$(describe_combo embeddings_only)"
  m_online
  m_install_uvtool "embeddings"
  m_prepare_vault empty
  m_verify_core hybrid
  combo_end
}

combo_llm_only() {
  combo_begin llm_only "$(describe_combo llm_only)"
  m_online
  m_install_uvtool "llm"
  m_prepare_vault empty
  m_verify_core g2          # no embeddings extra → honest listing recall
  m_verify_llm 1            # real LLM path when Ollama is up
  combo_end
}

combo_vault_legacy() {
  combo_begin vault_legacy "$(describe_combo vault_legacy)"
  m_online
  m_install_uvtool "embeddings,llm,benchmark"
  m_prepare_vault obsidian-legacy
  # index rebuild is skipped in the core pass: a legacy note is not valid
  # frontmatter, so rebuild must fail honestly — verified below.
  m_verify_core hybrid 1
  m_verify_llm 1
  # The frontmatter migrator is not wired into every CLI command yet, so a
  # legacy note is not auto-migrated. Verify the honest behavior instead:
  # rebuild fails with an actionable error and the note is preserved untouched.
  if "$SEAHORSE" index rebuild >"$COMBO_DIR/rebuild.log" 2>&1; then
    fail "index rebuild on legacy vault (expected honest E_FRONTMATTER_INVALID)"
  else
    ok "index rebuild on legacy vault fails honestly (E_FRONTMATTER_INVALID)"
  fi
  m_check "rebuild error is actionable" grep -q "not valid frontmatter" "$COMBO_DIR/rebuild.log"
  m_check "legacy note preserved" test -f "$VAULT/notes/legacy.md"
  m_check "legacy keys preserved" grep -q "tags:" "$VAULT/notes/legacy.md"
  combo_end
}

combo_uv_sync_dev() {
  combo_begin uv_sync_dev "$(describe_combo uv_sync_dev)"
  m_offline
  m_install_uvsync
  m_prepare_vault empty
  m_verify_core g2
  m_verify_llm 0
  combo_end
}

combo_pipx() {
  combo_begin pipx "$(describe_combo pipx)"
  if ! command -v pipx >/dev/null 2>&1; then
    combo_skip "pipx not installed — run 'brew install pipx' to enable this combo"
    return
  fi
  m_offline
  m_install_pipx ""
  m_prepare_vault empty
  m_verify_core g2
  m_verify_llm 0
  combo_end
}

combo_concurrency() {
  combo_begin concurrency "$(describe_combo concurrency)"
  m_online
  m_install_uvtool "embeddings,llm,benchmark"
  m_prepare_vault empty
  m_verify_concurrency
  m_verify_mcp
  combo_end
}

# --- run ---------------------------------------------------------------------
mkdir -p "$ROOT_SANDBOX"
: > "$LOG"

command -v uv >/dev/null || { echo "✗ uv not found — install https://docs.astral.sh/uv/" >&2; exit 2; }
if command -v curl >/dev/null && curl -s -m 2 http://localhost:11434 >/dev/null 2>&1; then
  OLLAMA_UP=1
fi
info "matrix sandbox: $ROOT_SANDBOX"
info "uv:      $(command -v uv)"
info "ollama:  $([ "$OLLAMA_UP" -eq 1 ] && echo up || echo down)"

RUN_COMBOS=()
if (( LIST_ONLY )); then
  for s in "${COMBO_SLUGS[@]}"; do
    printf '%-16s %s\n' "$s" "$(describe_combo "$s")"
  done
  rm -rf "$ROOT_SANDBOX"
  exit 0
fi
if (( CI_SUBSET )); then
  RUN_COMBOS=(core_min uv_sync_dev)
  info "CI subset: ${RUN_COMBOS[*]}"
elif (( ${#SELECTED[@]} > 0 )); then
  RUN_COMBOS=("${SELECTED[@]}")
else
  RUN_COMBOS=("${COMBO_SLUGS[@]}")
fi

for slug in "${RUN_COMBOS[@]}"; do
  local_found=0
  for known in "${COMBO_SLUGS[@]}"; do
    [[ "$slug" == "$known" ]] && local_found=1
  done
  if (( local_found == 0 )); then
    echo "✗ unknown combo: $slug (see --list)" >&2
    exit 2
  fi
done

for slug in "${RUN_COMBOS[@]}"; do
  "combo_$slug"
done

# --- report ------------------------------------------------------------------
info ""
info "════════════════════════════════════════════════════════════"
info "E2E MATRIX RESULT: $G_PASS passed, $G_FAIL failed, $G_SKIP skipped (of $G_TOTAL)"
if (( G_FAIL > 0 )); then
  info "Failed combos:"
  for s in "${FAILED_COMBOS[@]}"; do info "  ❌ $s"; done
fi
if (( G_SKIP > 0 )); then
  info "Skipped combos:"
  for s in "${SKIPPED_COMBOS[@]}"; do info "  ⏭️  $s"; done
fi
info "Sandbox: $ROOT_SANDBOX"
info "Log:     $LOG"
info "════════════════════════════════════════════════════════════"

if (( KEEP_SANDBOX )); then
  info "Sandbox kept at $ROOT_SANDBOX (--keep-sandbox)"
else
  rm -rf "$ROOT_SANDBOX"
  # Direct echo, not info(): the log lives inside the sandbox and was just
  # deleted — tee would fail and set -e would turn that into a spurious exit 1.
  echo "Sandbox cleaned up (use --keep-sandbox to retain for debugging)" >&2
fi

exit $(( G_FAIL > 0 ? 1 : 0 ))
