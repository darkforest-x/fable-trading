#!/bin/bash
# 一键把看板部署/更新到 VPS（需已配置 SSH 密钥）
# 用法: bash scripts/deploy_vps.sh
set -euo pipefail
VPS=root@103.214.174.58
DIR=/opt/fable-trading
cd "$(dirname "$0")/.."

rsync -az --exclude='__pycache__' --exclude='*.pyc' \
  src analysis models requirements.txt "$VPS:$DIR/"
rsync -az \
  data/judgment_dataset_v2_expanded.csv data/forward_log.csv \
  data/scored_signals*.csv data/scored_signals*.json \
  "$VPS:$DIR/data/"
rsync -az data/sweep_v3 data/swap_replication data/kline_fetched "$VPS:$DIR/data/"
ssh "$VPS" "systemctl restart fable-dashboard
for i in 1 2 3 4 5 6; do
  if systemctl is-active fable-dashboard >/dev/null \
    && curl -s -o /dev/null -w 'http:%{http_code}\n' http://127.0.0.1:8642/api/overview | grep -q 'http:200'; then
    systemctl is-active fable-dashboard
    echo http:200
    exit 0
  fi
  sleep 2
done
systemctl status fable-dashboard --no-pager
journalctl -u fable-dashboard -n 80 --no-pager
exit 1"
echo "done -> http://103.214.174.58:8642"
