#!/bin/bash
# 离线队列 #4：等队列 3 结束 → v3 全家桶组合回测（SWAP 主线）→ 部署 VPS 看板
# 启动：caffeinate -i nohup bash scripts/offline_queue4.sh &
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec >> logs/offline_queue4.log 2>&1
echo "=== queue4 start $(date) ==="

echo "--- waiting for queue3 to finish"
until grep -q "queue3 done" logs/offline_queue3.log 2>/dev/null; do sleep 120; done

echo "--- [1/2] v3 stack portfolio sim (SWAP mainline, val window)"
PYTHONPATH=. python3 scripts/v3_portfolio_sim.py

echo "--- [2/2] deploy dashboard (incl. v3_backtest.html) to VPS"
bash scripts/deploy_vps.sh
curl -s -o /dev/null -w "vps v3 page http:%{http_code}\n" http://103.214.174.58:8642/v3_backtest.html

echo "=== queue4 done $(date) ==="
