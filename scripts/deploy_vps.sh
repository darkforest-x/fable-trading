#!/bin/bash
# 一键把看板部署/更新到 VPS（需已配置 SSH 密钥）
# 用法: bash scripts/deploy_vps.sh
set -euo pipefail
VPS=root@103.214.174.58
DIR=/opt/fable-trading
cd "$(dirname "$0")/.."

rsync -az --exclude='__pycache__' --exclude='*.pyc' \
  src analysis models requirements.txt "$VPS:$DIR/"

# Data files (skip missing — e.g. forward_log cleared at YOLO clock reset)
ssh "$VPS" "mkdir -p $DIR/data"
for f in \
  data/judgment_dataset_v2_expanded.csv \
  data/judgment_yolo_swap.csv \
  data/judgment_yolo_swap_v8.csv \
  data/judgment_yolo_swap_v11.csv \
  data/forward_log_rules_pre_yolo_20260715.csv \
  data/scored_signals_swap.csv \
  data/scored_signals_swap_meta.json \
  data/scored_signals_spot.csv \
  data/scored_signals_spot_meta.json \
  data/scored_signals.csv \
  data/scored_signals.json
do
  if [ -f "$f" ]; then
    rsync -az "$f" "$VPS:$DIR/data/"
  fi
done
# NOTE: data/kline_fetched deliberately NOT pushed — the VPS forward pulse
# updates its own klines every 15 min (single writer); pushing the Mac's
# stale copies would roll live data backwards mid-pulse (2026-07-20).
rsync -az data/sweep_v3 data/swap_replication "$VPS:$DIR/data/"

# Hard red line: never leave job executor enabled on public VPS.
ssh "$VPS" "set -euo pipefail
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
fi
systemctl restart fable-dashboard
for i in 1 2 3 4 5 6 7 8 9 10; do
  if systemctl is-active fable-dashboard >/dev/null \
    && curl -s -o /dev/null -w 'http:%{http_code}\n' http://127.0.0.1:8642/api/overview | grep -q 'http:200'; then
    systemctl is-active fable-dashboard
    echo http:200
    systemctl show fable-dashboard -p Environment --value 2>/dev/null | tr ' ' '\n' | grep ENABLE_JOB || true
    # show which freeze the API thinks is active if endpoint exists
    curl -s http://127.0.0.1:8642/api/overview 2>/dev/null | head -c 400 || true
    echo
    exit 0
  fi
  sleep 2
done
systemctl status fable-dashboard --no-pager
journalctl -u fable-dashboard -n 80 --no-pager
exit 1"
echo "done -> http://103.214.174.58:8642"
