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
python3 - <<'PY'
from pathlib import Path
import pandas as pd
try:
    from src.judgment.forward_types import FORWARD_COLUMNS, PROTOCOL_VERSION
except Exception as exc:
    print('import fail', exc)
    raise
pd.DataFrame(columns=list(FORWARD_COLUMNS)).to_csv('data/forward_log.csv', index=False)
print('forward_log empty protocol', PROTOCOL_VERSION)
from src.judgment.frozen import load_runtime_artifact
a = load_runtime_artifact()
assert a is not None, 'no runtime artifact'
print('ACTIVE', a.relative_model_path, 'side', a.config.side, 'thr', a.threshold)
assert a.config.side == 'short', a.config.side
PY

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
