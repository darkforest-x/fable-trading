#!/usr/bin/env bash
# Periodic forward-clock tick for mainline gate (data/forward_log.csv).
#
# Production candidate provenance is fail-closed: only the validated YOLO path
# may discover new rows. Missing detector dependencies/weights mean
# detector=none (zero discovery), never a silent switch to legacy rules.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG=logs/forward_pulse.log
exec >>"$LOG" 2>&1
echo "=== forward_pulse $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3
export PYTHONPATH=.

export FABLE_RUNTIME_MODE=production
export FABLE_CANDIDATE_SOURCE=yolo
if ! "$PY" -c "import ultralytics" 2>/dev/null; then
  echo "ultralytics missing → detector=none (production discovery disabled)"
fi
echo "runtime_mode=$FABLE_RUNTIME_MODE candidate_source=$FABLE_CANDIDATE_SOURCE"

# Optional tip-only mainline (default unchanged = live 6-window).
#   FABLE_YOLO_MODE=tip          # pure tip window only
#   TIP_CONF=0.22                # tip-window conf floor (other live windows stay 0.30)
#   FABLE_YOLO_RIGHT_BIAS=1      # within min_gap prefer rightmost box
# Rollback: unset the three vars (or set FABLE_YOLO_MODE=live).
echo "yolo_mode=${FABLE_YOLO_MODE:-live} tip_conf=${TIP_CONF:-off} right_bias=${FABLE_YOLO_RIGHT_BIAS:-0}"

# Optional light kline refresh (skip if offline). SWAP-only: mainline universe;
# full-universe update is a separate daily job.
if [ "${SKIP_UPDATE_OKX:-0}" != "1" ]; then
  if [ -f scripts/../src/data/update_okx.py ] || [ -f src/data/update_okx.py ]; then
    echo "update_okx --swap-only --bar 15m"
    "$PY" -m src.data.update_okx --bar 15m --swap-only 2>&1 | tail -25 || echo "update_okx skipped/failed"
  fi
fi

echo "forward_track start $(date -u +%Y-%m-%dT%H:%M:%SZ)"
forward_track_ok=0
if "$PY" scripts/forward_track.py; then
  forward_track_ok=1
  echo "forward_log lines=$(wc -l < data/forward_log.csv 2>/dev/null || echo 0)"
else
  forward_track_status=$?
  echo "forward_track failed status=$forward_track_status → executor blocked"
fi

# (v12 shadow removed 2026-07-23 — pre-v16 detectors are deleted per iron
# rule 12; no shadow may run a banned model.)

# Real-tip data engine (v17 training distribution). Light side-step: no YOLO,
# own budget, writes only data/real_tip_collect/. Never blocks the pulse.
if [ "${FABLE_COLLECT_REAL_TIPS:-1}" = "1" ]; then
  echo "real_tip_collect start $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  "$PY" scripts/collect_real_tips_pulse.py 2>&1 | tail -3 || echo "real_tip_collect skipped/failed"
fi

# Immediately try to trade fresh open rows only after this pulse completed its
# forward-log refresh. A failed refresh must not dispatch against stale state.
if [ "$forward_track_ok" -eq 1 ]; then
  echo "executor --once (post-pulse)"
  "$PY" -m src.execution --once 2>&1 | tail -5 || echo "executor once failed/skipped"
else
  echo "executor --once skipped: forward_track did not complete"
fi

echo "=== done $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
