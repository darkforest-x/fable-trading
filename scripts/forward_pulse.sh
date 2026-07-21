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
"$PY" scripts/forward_track.py
echo "forward_log lines=$(wc -l < data/forward_log.csv 2>/dev/null || echo 0)"

# Optional H-TIP v12 tip-only shadow (never writes mainline log; never promotes).
# Enable on VPS with: FABLE_V12_SHADOW=1 and models/owner_v12_htip.pt present
# (or data/v12_shadow.env written by scripts/enable_v12_shadow_vps.sh).
if [ -f data/v12_shadow.env ]; then
  # shellcheck disable=SC1091
  set -a; . data/v12_shadow.env; set +a
fi
if [ "${FABLE_V12_SHADOW:-0}" = "1" ]; then
  W="${FABLE_V12_WEIGHTS:-models/owner_v12_htip.pt}"
  if [ -f "$W" ] || [ -f runs/detect/runs/detect/owner_v12_htip/weights/best.pt ]; then
    echo "v12_shadow start $(date -u +%Y-%m-%dT%H:%M:%SZ) weights=$W"
    "$PY" scripts/forward_track_v12_shadow.py --weights "$W" 2>&1 | tail -30 \
      || echo "v12_shadow failed/skipped"
    echo "v12_shadow lines=$(wc -l < data/forward_log_v12_shadow.csv 2>/dev/null || echo 0)"
  else
    echo "v12_shadow skipped: weights missing ($W)"
  fi
fi

# Immediately try to trade any fresh open rows — do not wait up to 30s for the
# executor loop. Failures here must never fail the pulse unit.
echo "executor --once (post-pulse)"
"$PY" -m src.execution --once 2>&1 | tail -5 || echo "executor once failed/skipped"

echo "=== done $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
