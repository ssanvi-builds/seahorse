#!/usr/bin/env bash
#
# demo.sh — a presentational walkthrough of Seahorse, formatted for recording
# (asciinema / vhs / terminal capture). It is NOT a test: it shows the core
# loop — init, remember, recall, improve, forget — plus the claude-mem import
# bridge and the observer status, in a throwaway vault.
#
# Isolation: the vault is a fresh temp dir; the real ~/.claude and
# ~/.claude-mem are never written. `seahorse import` runs in dry-run mode
# (read-only) against the real claude-mem DB by default.
#
# Usage:
#   scripts/demo.sh [--keep] [--import-source <path>]
#
#   --keep             keep the demo vault after the run (default: remove it)
#   --import-source    claude-mem DB to preview (default: ~/.claude-mem/claude-mem.db)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VAULT="/private/tmp/seahorse-demo-$(date +%s)"
KEEP=0
IMPORT_SOURCE="${HOME}/.claude-mem/claude-mem.db"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep) KEEP=1; shift ;;
    --import-source) IMPORT_SOURCE="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Resolve the seahorse binary: prefer the repo's venv, fall back to PATH.
if [[ -x "$REPO_DIR/.venv/bin/seahorse" ]]; then
  SEAHORSE="$REPO_DIR/.venv/bin/seahorse"
else
  SEAHORSE="$(command -v seahorse || true)"
fi
if [[ -z "$SEAHORSE" ]]; then
  echo "seahorse not found — run 'uv sync' in the repo or 'pip install seahorse-memory'." >&2
  exit 1
fi

step() { printf '\n\033[1;36m$ %s\033[0m\n' "$*"; "$@"; }

echo "Seahorse demo"
echo "Vault: $VAULT"

step "$SEAHORSE" init "$VAULT"
export SEAHORSE_VAULT="$VAULT"

step "$SEAHORSE" remember "Sergio lives in Madrid" --title home
EP_API="$("$SEAHORSE" remember "The API uses FastAPI with Pydantic v2 contracts" --title api-design 2>/dev/null | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)"
EP_INFRA="$("$SEAHORSE" remember "Deploy target is a single-file SQLite, zero infra" --title infra 2>/dev/null | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)"

step "$SEAHORSE" recall "where does Sergio live?"
step "$SEAHORSE" recall "what stack does the API use?"

step "$SEAHORSE" improve "$EP_API" "The API uses FastAPI with Pydantic v2 contracts and a stdio MCP surface" --reason correction
step "$SEAHORSE" recall "what stack does the API use?"

step "$SEAHORSE" forget "$EP_INFRA" --reason superseded
step "$SEAHORSE" recall "deploy target"

if [[ -f "$IMPORT_SOURCE" ]]; then
  step "$SEAHORSE" import --source "$IMPORT_SOURCE"
else
  echo "  (no claude-mem DB at $IMPORT_SOURCE — skipping import preview)"
fi

step "$SEAHORSE" observe status

echo
echo "Demo complete. Vault: $VAULT"
if [[ "$KEEP" -eq 0 ]]; then
  rm -rf "$VAULT"
  echo "Vault removed (use --keep to retain it)."
fi
