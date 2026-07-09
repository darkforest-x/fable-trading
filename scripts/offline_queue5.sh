#!/bin/bash
# 离线队列 #5：等队列4 → tp5对账 → 冻结模型工件 → 前向跟踪首跑 → 自动提交推送
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec >> logs/offline_queue5.log 2>&1
echo "=== queue5 start $(date) ==="
until grep -q "queue4 done" logs/offline_queue4.log 2>/dev/null; do sleep 120; done
echo "--- [1/3] tp5 discrepancy reconciliation"
PYTHONPATH=. python3 scripts/recon_tp5.py
echo "--- [2/3] freeze model artifacts (both contenders)"
PYTHONPATH=. python3 scripts/freeze_model.py
echo "--- [3/3] forward tracker first pass"
PYTHONPATH=. python3 scripts/forward_track.py
git add models analysis/output/v3_portfolio_sim.json src/webapp/static/v3_backtest.html 2>/dev/null
git commit -qm "Offline queue5: frozen artifacts, forward-log first pass, v3 sim outputs

Co-Authored-By: Claude (offline queue)" && git push -q && echo pushed
echo "=== queue5 done $(date) ==="
