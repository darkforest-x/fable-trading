#!/bin/bash
# 离线队列 #2（与 offline_pipeline.sh 并存，等它的拉取阶段结束后才动网络）：
#   等所有 fetch_okx 进程消失且稳定 3 分钟 → 资金费率历史 → 多时间框架数据
#   （1H 全池 → 30m 全池 → 5m 主流 15 币 200 天）→ H1/H2 出场变体 sweep
# 启动：caffeinate -i nohup bash scripts/offline_queue2.sh &
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec >> logs/offline_queue2.log 2>&1
echo "=== queue2 start $(date) ==="

echo "--- waiting until no fetch_okx process for 3 consecutive minutes"
quiet=0
while [ $quiet -lt 3 ]; do
  if pgrep -f "src.data.fetch_okx" >/dev/null; then quiet=0; else quiet=$((quiet+1)); fi
  sleep 60
done

echo "--- [1/4] funding-rate history"
PYTHONPATH=. python3 -m src.data.fetch_funding

SPOT=$(python3 -c "from src.data.fetch_okx import DEFAULT_SYMBOLS; print(' '.join(DEFAULT_SYMBOLS))")
SWAPS=$(python3 -c "
from src.data.fetch_okx import DEFAULT_SYMBOLS
print(' '.join(sorted({s.replace('_USDT','_USDT_SWAP') if not s.endswith('_SWAP') else s for s in DEFAULT_SYMBOLS})))")
MAJORS_5M="BTC_USDT_SWAP ETH_USDT_SWAP SOL_USDT_SWAP BNB_USDT_SWAP XRP_USDT_SWAP DOGE_USDT_SWAP ADA_USDT_SWAP LINK_USDT_SWAP AVAX_USDT_SWAP TRX_USDT_SWAP LTC_USDT_SWAP DOT_USDT_SWAP TON_USDT_SWAP ARB_USDT_SWAP OP_USDT_SWAP"

echo "--- [2/4] 1H + 30m for the full swap universe"
python3 -m src.data.fetch_okx --bar 1H  --days 400 --symbols $SWAPS
python3 -m src.data.fetch_okx --bar 30m --days 400 --symbols $SWAPS

echo "--- [3/4] 5m for 15 majors (200 days to cap volume)"
python3 -m src.data.fetch_okx --bar 5m --days 200 --symbols $MAJORS_5M

echo "--- [4/4] H1/H2 exit-variant sweep (15m expanded pool, val only)"
PYTHONPATH=. python3 scripts/exit_variants_sweep.py

echo "=== queue2 done $(date) ==="
