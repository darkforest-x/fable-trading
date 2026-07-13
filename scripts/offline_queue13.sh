#!/bin/bash
set -uo pipefail
cd "$(dirname "$0")/.."
exec >> logs/offline_queue13.log 2>&1
echo "=== queue13 start $(date) ==="
nice -n 15 env PYTHONPATH=. python3 scripts/mtf_30m_deep.py
git add analysis/output/mtf_30m_deep.json 2>/dev/null
git commit -qm "30m deep grid (tp x horizon, val only)" && git push -q && echo pushed
PYTHONPATH=. python3 -c "
from src.notify import send
import json
rows = json.load(open('analysis/output/mtf_30m_deep.json'))
best = max(rows, key=lambda r: r.get('top_net_maker', -1))
send(f\"📐 30m深挖(12格): 最佳 {best['config']} 净@maker {best['top_net_maker']} AUC {best['val_auc']} p {best['perm_p']}\")" || true
echo "=== queue13 done $(date) ==="
