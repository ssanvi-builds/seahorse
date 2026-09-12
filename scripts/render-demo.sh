#!/usr/bin/env bash
#
# render-demo.sh — render examples/demo-clip/demo.tape to MP4 + GIF, fully
# scripted (no manual steps). A fresh disposable vault is created per run
# (scripts/demo.sh isolation pattern): the real ~/.claude, the real vault and
# everything outside $BUILD are never touched.
#
# The embedding model must already be cached locally (mE5-small, ~235MB — it
# downloads lazily on the first `remember` otherwise).
#
# Usage:
#   scripts/render-demo.sh [--keep-vault]
#
# Artifacts land in $BUILD (default /tmp/seahorse-clip-render):
#   demo.mp4  H.264 master (assembly input)
#   demo.gif  ≤5MB, README-ready (GitHub camo rejects bigger GIFs)
# If the GIF exceeds 5MB it is re-compressed in place (palettegen/paletteuse).
# With --keep-vault the demo vault (with its two materialized notes) is kept
# under $BUILD/vault for the Obsidian capture take.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TAPE="$REPO_DIR/examples/demo-clip/demo.tape"
BUILD="${SEAHORSE_DEMO_BUILD:-/tmp/seahorse-clip-render}"
VAULT="$BUILD/vault"
GIF_CEILING=$((5 * 1024 * 1024))
KEEP_VAULT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-vault) KEEP_VAULT=1; shift ;;
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

command -v vhs >/dev/null || { echo "vhs not on PATH — sudo pacman -S vhs ttyd" >&2; exit 1; }

mkdir -p "$BUILD"
rm -rf "$VAULT"
"$SEAHORSE" init "$VAULT"
# The CLI writes .md notes via the materialize layer (see [materialize] in
# docs/setup.md); `init` alone does not configure it.
printf '\n[materialize]\nmode = "all"\ndir = "Memory"\n' >> "$VAULT/.seahorse/seahorse.toml"
export SEAHORSE_VAULT="$VAULT"

echo "Rendering tape against vault: $VAULT"
(cd "$REPO_DIR" && vhs examples/demo-clip/demo.tape)

# vhs resolves the tape's relative Output paths against the invocation CWD
# (the repo root here). Always move the fresh render over $BUILD — a stale
# artifact from a previous run must never survive a re-render (nor be what
# the ffprobe report below measures).
for f in demo.mp4 demo.gif; do
  for src in "$REPO_DIR/$f" "$REPO_DIR/examples/demo-clip/$f"; do
    [[ -f "$src" ]] && mv -f "$src" "$BUILD/$f" && break
  done
done
[[ -f "$BUILD/demo.mp4" && -f "$BUILD/demo.gif" ]] || {
  echo "render failed: demo.mp4/demo.gif not found under $BUILD" >&2; exit 1; }

# GIF ceiling: GitHub camo rejects >5MB.
GIF_SIZE=$(stat -c%s "$BUILD/demo.gif")
if (( GIF_SIZE > GIF_CEILING )); then
  echo "demo.gif is $GIF_SIZE bytes (>5MB) — re-compressing"
  ffmpeg -y -loglevel error -i "$BUILD/demo.gif" -vf \
    "fps=15,scale=1200:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=diff_mode=diff" \
    "$BUILD/demo.gif.opt"
  mv "$BUILD/demo.gif.opt" "$BUILD/demo.gif"
fi

echo
echo "Artifacts:"
ls -lh "$BUILD"/demo.mp4 "$BUILD"/demo.gif
for f in "$BUILD"/demo.mp4 "$BUILD"/demo.gif; do
  echo "--- $f"
  ffprobe -v error -select_streams v:0 \
    -show_entries stream=codec_name,width,height,avg_frame_rate \
    -show_entries format=duration,size -of default=noprint_wrappers=1 "$f"
done
echo
if [[ "$KEEP_VAULT" -eq 0 ]]; then
  rm -rf "$VAULT"
  echo "Vault removed (use --keep-vault to retain it)."
fi