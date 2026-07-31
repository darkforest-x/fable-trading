#!/bin/bash
# Deploy short-protocol P0 fix to VPS + archive polluted forward_log once.
# Usage: bash scripts/deploy_vps_short_protocol.sh
# Never pushes data/kline_fetched or enables job executor.
set -euo pipefail
VPS="${VPS:-root@206.237.14.112}"
DIR=/opt/fable-trading
cd "$(dirname "$0")/.."

echo "==> rsync code (src scripts tests models analysis docs requirements)"
rsync -az --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' \
  src scripts tests models analysis docs requirements.txt \
  "$VPS:$DIR/"

# Optional judgment tables if present (not klines)
ssh "$VPS" "mkdir -p $DIR/data $DIR/models"
for f in \
  data/judgment_yolo_swap_v10.csv \
  data/judgment_v10_wide.csv \
  data/judgment_yolo_swap_v11.csv \
  data/forward_log_pre_short_protocol_20260731.csv
do
  if [ -f "$f" ]; then
    rsync -az "$f" "$VPS:$DIR/data/"
  fi
done

# ACTIVE pointer
if [ -f models/ACTIVE ]; then
  rsync -az models/ACTIVE models/ACTIVE_PREV "$VPS:$DIR/models/" 2>/dev/null || rsync -az models/ACTIVE "$VPS:$DIR/models/"
fi

echo "==> VPS: archive old forward_log + reset empty schema + restart dashboard"
ssh "$VPS" "set -euo pipefail
cd $DIR
export PYTHONPATH=.
if [ -f data/forward_log.csv ] && [ ! -f data/forward_log_pre_short_protocol_20260731.csv ]; then
  cp -a data/forward_log.csv data/forward_log_pre_short_protocol_20260731.csv
  echo archived remote forward_log
elif [ -f data/forward_log_pre_short_protocol_20260731.csv ]; then
  echo archive already present
fi
# Empty log without requiring pandas (VPS may use app venv later)
HEADER='source,symbol,signal_time,detected_at,status,score,threshold,model_path,dataset_sha256,signal_i,entry_time,entry_price,maker_filled,outcome,label,exit_offset,exit_time,realized_ret,atr_pct,dense_run_len,tier,size_mult,side'
printf '%s\n' "\$HEADER" > data/forward_log.csv
echo "forward_log empty protocol short_v10_p0fix_20260731"
cat models/ACTIVE
# Optional import check if app venv exists
for PY in .venv/bin/python venv/bin/python python3; do
  if [ -x \"\$PY\" ] || command -v \"\$PY\" >/dev/null 2>&1; then
    \"\$PY\" - <<'PY' 2>/dev/null && break || true
try:
    from src.judgment.frozen import load_runtime_artifact
    a = load_runtime_artifact()
    print('ACTIVE load', a.relative_model_path, a.config.side, a.threshold)
except Exception as exc:
    print('runtime import skip:', type(exc).__name__, exc)
PY
  fi
done

UNIT=/etc/systemd/system/fable-dashboard.service
if [ -f \"\$UNIT\" ]; then
  if grep -q '^Environment=ENABLE_JOB_EXECUTOR=' \"\$UNIT\"; then
    sed -i 's/^Environment=ENABLE_JOB_EXECUTOR=.*/Environment=ENABLE_JOB_EXECUTOR=0/' \"\$UNIT\"
  else
    if ! grep -q 'ENABLE_JOB_EXECUTOR' \"\$UNIT\"; then
      sed -i '/^\[Service\]/a Environment=ENABLE_JOB_EXECUTOR=0' \"\$UNIT\"
    fi
  fi
  systemctl daemon-reload
  systemctl restart fable-dashboard || true
fi
# restart forward timer if present
systemctl restart fable-forward.timer 2>/dev/null || true
systemctl list-timers --all 2>/dev/null | grep -i fable || true
wc -l data/forward_log.csv data/forward_log_pre_short_protocol_20260731.csv 2>/dev/null || true
"

echo "done -> http://206.237.14.112:8642  (ENABLE_JOB_EXECUTOR=0)"
echo "See docs/vps_deploy_short_protocol_20260731.md"
