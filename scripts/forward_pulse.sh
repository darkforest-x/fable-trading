#!/usr/bin/env bash
# Periodic forward-clock tick for mainline gate (data/forward_log.csv).
#
# Prefer YOLO candidates (mainline). If ultralytics/torch missing, fall back to
# rules candidates so the clock still moves on lean VPS hosts.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG=logs/forward_pulse.log
exec >>"$LOG" 2>&1
echo "=== forward_pulse $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3
export PYTHONPATH=.

if ! "$PY" -c "import ultralytics" 2>/dev/null; then
  echo "ultralytics missing → FABLE_CANDIDATE_SOURCE=rules"
  export FABLE_CANDIDATE_SOURCE=rules
else
  export FABLE_CANDIDATE_SOURCE="${FABLE_CANDIDATE_SOURCE:-yolo}"
  echo "candidate_source=$FABLE_CANDIDATE_SOURCE"
fi

# Optional light kline refresh (skip if offline)
if [ "${SKIP_UPDATE_OKX:-0}" != "1" ]; then
  if [ -f scripts/../src/data/update_okx.py ] || [ -f src/data/update_okx.py ]; then
    "$PY" -m src.data.update_okx --bar 15m 2>&1 | tail -20 || echo "update_okx skipped/failed"
  fi
fi

"$PY" scripts/forward_track.py
echo "forward_log lines=$(wc -l < data/forward_log.csv 2>/dev/null || echo 0)"
echo "=== done $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
