#!/bin/bash
# 离线队列 #6：深历史拉取（2021 起，从未被选型污染的时段）→ 冻结管道一次性检验
# 预注册判定见 scripts/deep_history_test.py 头部。启动后约 5-7 小时出结果。
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs data/kline_deep
exec >> logs/offline_queue6.log 2>&1
echo "=== queue6 start $(date) ==="
SWAPS=$(python3 -c "
from src.data.fetch_okx import DEFAULT_SYMBOLS
print(' '.join(sorted({s.replace('_USDT','_USDT_SWAP') if not s.endswith('_SWAP') else s for s in DEFAULT_SYMBOLS})))")
echo "--- [1/2] deep fetch: 1650 days of 15m swaps into data/kline_deep"
python3 -m src.data.fetch_okx --bar 15m --days 1650 --symbols $SWAPS --out-dir data/kline_deep
python3 -m src.data.fetch_okx --bar 15m --days 1650 --symbols $SWAPS --out-dir data/kline_deep
echo "--- [2/2] pre-registered one-shot frozen-pipeline test on the pre-2025-06 era"
PYTHONPATH=. python3 scripts/deep_history_test.py
git add analysis/output/deep_history_test.json scripts/deep_history_test.py 2>/dev/null
git commit -qm "Deep-history one-shot test result (pre-registered)" && git push -q && echo pushed
PYTHONPATH=. python3 -c "
from src.notify import send
import json, pathlib
p = pathlib.Path('analysis/output/deep_history_test.json')
if p.exists():
    d = json.loads(p.read_text())
    lines = ['🕰 深历史一次性检验（2021~2025.5，预注册）:']
    for c, r in d.get('configs', {}).items():
        v = r.get('verdict', '?'); a = r.get('aggregate', {})
        lines.append(f\"{c}: {v} | PF {a.get('profit_factor')} | 净 {a.get('net_return_on_capital')}\")
    send('\n'.join(lines))
" || true
echo "=== queue6 done $(date) ==="
