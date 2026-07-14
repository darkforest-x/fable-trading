#!/bin/bash
# 队列 #16：H19 因子 IC 筛选（纯CPU, nice降权, 不抢v6的GPU）
set -uo pipefail
cd "$(dirname "$0")/.."
exec >> logs/offline_queue16.log 2>&1
echo "=== queue16 start $(date) ==="
nice -n 15 env PYTHONPATH=. python3 scripts/factor_ic_screen.py
git add analysis/output/factor_ic_screen.json analysis/p2b_factor_ic_report.md src/factors/ scripts/factor_ic_screen.py docs/RESEARCH_AGENDA.md 2>/dev/null
git commit -qm "H19 factor IC screen: 14 causal crypto-safe alpha factors vs dense-launch forward return" && git push -q && echo pushed
PYTHONPATH=. python3 -c "
from src.notify import send
import json
r = json.load(open('analysis/output/factor_ic_screen.json'))
alive = [x for x in r if x.get('class')=='alive']
top = sorted([x for x in r if 'ic' in x], key=lambda x: -abs(x['ic']))[:3]
send('🔬 H19因子筛选完成: '+str(len(alive))+'个存活/14。最强IC: '+', '.join(f\"{x['factor']} {x['ic']:+.3f}\" for x in top))" || true

echo "--- H19后自动接力：存活因子单变量增益测试"
nice -n 15 env PYTHONPATH=. python3 scripts/single_var_feature_gain.py
git add analysis/output/single_var_gain.json analysis/p2b_feature_gain_report.md data/single_var_pool.csv 2>/dev/null
git commit -qm "Single-var feature-gain screen: H19 survivors added one at a time" && git push -q || true
PYTHONPATH=. python3 -c "
from src.notify import send
import json, pathlib
p = pathlib.Path('analysis/output/single_var_gain.json')
if p.exists():
    r = json.loads(p.read_text())
    w = [x for x in r[1:] if x.get('gain_net',0)>0 and x.get('p',1)<0.01]
    send('🧬 单变量特征增益完成: '+str(len(w))+'个因子值得合并(需你拍板)。详见 p2b_feature_gain_report.md')
" || true
echo "=== queue16 done $(date) ==="
