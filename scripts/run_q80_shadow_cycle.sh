#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT_DIR="$ROOT/output/offline_tasks"
LOCK_DIR="${TMPDIR:-/tmp}/fable-q80-shadow-cycle.lock"
PYTHON_BIN="${FABLE_PYTHON:-python3}"
SITE_PACKAGES="${FABLE_SITE_PACKAGES:-}"

if [[ -z "$SITE_PACKAGES" ]]; then
  for candidate in \
    "$ROOT/.venv/lib/python3.9/site-packages" \
    "$ROOT/../fable-trading/.venv/lib/python3.9/site-packages"; do
    if [[ -d "$candidate" ]]; then
      SITE_PACKAGES="$candidate"
      break
    fi
  done
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  printf 'q80 shadow cycle skipped: lock exists at %s\n' "$LOCK_DIR"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT HUP INT TERM

mkdir -p "$OUTPUT_DIR"
export PYTHONPATH="$ROOT${SITE_PACKAGES:+:$SITE_PACKAGES}${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT"

printf 'q80 shadow cycle start: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
"$PYTHON_BIN" -c "import lightgbm, numpy, pandas"
"$PYTHON_BIN" -m src.data.update_okx --bar 15m
"$PYTHON_BIN" scripts/forward_threshold_shadow.py > "$OUTPUT_DIR/q80_shadow_latest.json.tmp"
mv "$OUTPUT_DIR/q80_shadow_latest.json.tmp" "$OUTPUT_DIR/q80_shadow_latest.json"
printf 'q80 shadow cycle complete: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
