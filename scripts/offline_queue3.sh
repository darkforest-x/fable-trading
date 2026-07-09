#!/bin/bash
# 离线队列 #3（07-09 晨）：SWAP 上的赢家叠加验证 → 多时间框架首轮 → 数据审计
# 启动：caffeinate -i nohup bash scripts/offline_queue3.sh &
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec >> logs/offline_queue3.log 2>&1
echo "=== queue3 start $(date) ==="

echo "--- [1/3] H1 x H9 stack on the SWAP mainline"
PYTHONPATH=. python3 scripts/swap_h1h9_stack.py

echo "--- [2/3] multi-timeframe first pass (1H/30m/5m)"
PYTHONPATH=. python3 scripts/mtf_first_pass.py

echo "--- [3/3] data-quality audit"
PYTHONPATH=. python3 scripts/data_audit.py

echo "=== queue3 done $(date) ==="
