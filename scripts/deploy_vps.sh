#!/bin/bash
# 一键把看板部署/更新到 VPS（需已配置 SSH 密钥）
# 用法: bash scripts/deploy_vps.sh
set -euo pipefail
VPS=root@103.214.174.58
DIR=/opt/fable-trading
cd "$(dirname "$0")/.."

rsync -az --exclude='__pycache__' --exclude='*.pyc' \
  src analysis requirements.txt "$VPS:$DIR/"
rsync -az \
  data/judgment_dataset_v2_expanded.csv data/scored_signals.csv \
  data/scored_signals_meta.json data/kline_fetched \
  "$VPS:$DIR/data/"
ssh "$VPS" "systemctl restart fable-dashboard && sleep 2 && systemctl is-active fable-dashboard \
  && curl -s -o /dev/null -w 'http:%{http_code}\n' http://127.0.0.1:8642/api/overview"
echo "done -> http://103.214.174.58:8642"
