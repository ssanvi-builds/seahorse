#!/usr/bin/env bash
#
# e2e-vm.sh — simulate the REAL user on a clean Linux machine (OrbStack VM).
# Closes the v1.0 gap "no clean machine": every other e2e runs on the dev
# machine (uv + tooling preinstalled). This one provisions a fresh
# debian:bookworm VM with NO uv, NO git, NO build-essential, installs
# seahorse-memory[embeddings,llm] from PyPI with a fresh mE5-small download,
# runs the README onboarding, and — in full mode — runs Ollama INSIDE the VM
# for real LLM extraction.
#
# The actual steps run in scripts/e2e-vm-inner.sh, which is copied into the VM
# (the VM is a test environment, not the repo).
#
# Usage:
#   scripts/e2e-vm.sh [--keep] [--reuse] [--fast]
#
#   --keep    keep the VM on failure (debug); default: delete at the end
#   --reuse   reuse an existing VM named $MACHINE instead of creating fresh
#   --fast    SEAHORSE_FAST=1: skip Ollama + LLM extraction (CI smoke)
#
# Env:
#   SEAHORSE_INDEX_URL / SEAHORSE_EXTRA_INDEX_URL / SEAHORSE_VERSION
#   SEAHORSE_FAST=1     skip Ollama + LLM extraction
#   SEAHORSE_KEEP=1     same as --keep
#
# Budget (full mode): ~10-20 min (VM create 1-2, apt+pip 3-5, mE5 2-5,
# Ollama 2-5, onboarding+MCP+LLM 2-3). OrbStack is not on GitHub runners, so
# this is a manual release gate.

set -euo pipefail

# --- resolve paths -----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INNER="$SCRIPT_DIR/e2e-vm-inner.sh"
LOG_DIR="${SEAHORSE_LOG_DIR:-/tmp}"
LOG="$LOG_DIR/seahorse-vm-$(date +%s).log"

MACHINE="${SEAHORSE_MACHINE:-seahorse-e2e}"
DISTRO="${SEAHORSE_DISTRO:-debian:bookworm}"

# --- env config --------------------------------------------------------------
FAST="${SEAHORSE_FAST:-0}"
KEEP="${SEAHORSE_KEEP:-0}"

# --- args --------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep) KEEP=1; shift ;;
    --reuse) REUSE=1; shift ;;
    --fast) FAST=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# --- helpers -----------------------------------------------------------------
info() { echo "$*" | tee -a "$LOG" >&2; }
ok()   { info "  ✅ $1"; }
fail() { info "  ❌ $1"; }

# --- pre-flight --------------------------------------------------------------
info "════════════════════════════════════════════════════════════"
info "VM E2E — clean Linux machine, install from PyPI"
info "════════════════════════════════════════════════════════════"
command -v orbctl >/dev/null || { info "✗ orbctl not found (install OrbStack)"; exit 2; }
info "  orbctl: $(orbctl version 2>&1 | head -1)"
info "  distro: $DISTRO"
info "  machine: $MACHINE"
info "  fast: $FAST"
info "  log: $LOG"
: > "$LOG"

# --- VM lifecycle ------------------------------------------------------------
if [[ "${REUSE:-0}" == "1" ]]; then
  if orbctl list --format json 2>/dev/null | grep -q "\"$MACHINE\""; then
    info "  reusing existing VM $MACHINE"
  else
    info "✗ --reuse but VM $MACHINE does not exist"
    exit 2
  fi
else
  info "  creating VM $MACHINE ($DISTRO)..."
  # 4G RAM + 2 CPUs: qwen3:0.6b runs on CPU and needs ~1-2GB to load.
  if orbctl create --memory 4G --cpus 2 "$DISTRO" "$MACHINE"; then
    ok "VM created"
  else
    fail "VM created"
    info "  (a leftover VM? run: orbctl delete $MACHINE)"
    exit 1
  fi
fi

# --- wait for the VM to accept commands ---------------------------------------
# orbctl create returns as soon as the machine is created; it may not be ready
# to run commands yet. Poll until `orbctl run` answers (or give up).
info "  waiting for the VM to accept commands..."
VM_READY=0
for _ in $(seq 1 30); do
  if orbctl run -m "$MACHINE" bash -c 'true' >/dev/null 2>&1; then
    VM_READY=1
    break
  fi
  sleep 2
done
if (( VM_READY )); then
  ok "VM ready"
else
  fail "VM ready (no response after 60s)"
  exit 1
fi

# --- copy + run the inner script ---------------------------------------------
info "  copying e2e-vm-inner.sh into the VM..."
if orbctl run -m "$MACHINE" bash -c 'cat > /tmp/e2e-vm-inner.sh' < "$INNER"; then
  ok "inner script copied"
else
  fail "inner script copied"
  exit 1
fi

info "  running the inner script in the VM (output streamed below)..."
info "────────────────────────────────────────────────────────────"
# Run as root: `orbctl run` defaults to the host user (uid 501), which cannot
# apt-get. The inner script provisions apt packages, so it needs root.
# The inner script's exit code decides the gate; tee keeps a host-side log.
set +e
orbctl run -m "$MACHINE" -u root bash /tmp/e2e-vm-inner.sh 2>&1 | tee -a "$LOG"
INNER_RC=${PIPESTATUS[0]}
set -e
info "────────────────────────────────────────────────────────────"
info "  inner script exit code: $INNER_RC"

# --- cleanup ----------------------------------------------------------------
if (( KEEP )); then
  info "  --keep: VM $MACHINE left running for debug"
  info "    orbctl shell $MACHINE"
  info "    orbctl delete $MACHINE   (when done)"
else
  info "  deleting VM $MACHINE..."
  if orbctl delete "$MACHINE"; then
    ok "VM deleted"
  else
    fail "VM deleted"
  fi
fi

info ""
info "════════════════════════════════════════════════════════════"
if (( INNER_RC == 0 )); then
  info "VM E2E RESULT: PASS"
else
  info "VM E2E RESULT: FAIL (exit $INNER_RC)"
fi
info "Log: $LOG"
info "════════════════════════════════════════════════════════════"
exit $INNER_RC
