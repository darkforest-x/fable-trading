#!/bin/bash
# 决定性A/B：v7h检测器(留出30%币种) → 在106个从未训练的币种上 YOLO vs 规则
set -uo pipefail
cd "$(dirname "$0")/.."
exec >> logs/ab_decisive.log 2>&1
PY=.venv/bin/python
echo "=== 决定性A/B start $(date) ==="

echo "--- [1/3] 训练 v7h (留出30%币种, patience=8 提速)"
caffeinate -i $PY -m src.detection.train --data datasets/dense_owner_v7h/data.yaml \
  --model runs/detect/runs/detect/owner_v7_chain/weights/best.pt \
  --epochs 60 --patience 8 --name owner_v7_holdout

echo "--- [2/3] 用 v7h 在留出币种上生成YOLO候选(conf降到0.20增样本)"
PYTHONPATH=. $PY - <<'PYEOF'
import json, sys
from pathlib import Path
sys.path.insert(0, '.')
import scripts.yolo_candidate_source as ycs
ycs.CONF = 0.20   # 降阈值 → 更多候选 → 更大样本
holdout = set(json.load(open('data/ab_holdout_symbols.json')))
# 只扫留出币种
orig = ycs.iter_series
def only_holdout(**kw):
    for s, sym, f in orig(**kw):
        if f"okx_{sym}" in holdout or sym in holdout:
            yield s, sym, f
ycs.iter_series = only_holdout
sys.argv = ['x', '--weights', 'runs/detect/runs/detect/owner_v7_holdout/weights/best.pt',
            '--out', 'data/judgment_yolo_holdout.csv']
ycs.main()
PYEOF

echo "--- [3/3] 同一判断层对比(top-30%口径,样本够才判定)"
PYTHONPATH=. $PY - <<'PYEOF'
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, '.')
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import load_splits, train_model, permutation_pvalue

holdout = {s.replace('okx_','') for s in json.load(open('data/ab_holdout_symbols.json'))}
def run(csv, tag, q=0.70):
    df = pd.read_csv(csv, parse_dates=['signal_time'])
    df = df[df['symbol'].isin(holdout)] if 'rule' in tag else df
    if len(df) < 200: return {"tag": tag, "n": len(df), "note": "样本不足"}
    tmp = Path(f'data/_abd_{tag}.csv'); df.sort_values('signal_time').to_csv(tmp, index=False)
    train, val, _ = load_splits(tmp, horizon_bars=72)
    m = train_model(train, val)
    prob = m.predict(val[FEATURE_COLUMNS], num_iteration=m.best_iteration)
    thr = np.quantile(prob, q)          # top-30% 而非 top-10% → 3倍样本
    sel = val[prob >= thr]
    net = sel['realized_ret'] - 0.0006
    from sklearn.metrics import roc_auc_score
    return {"tag": tag, "n_pool": len(df), "n_val": len(val), "top_n": len(sel),
            "auc": round(roc_auc_score(val['label'], prob), 4),
            "p": round(permutation_pvalue(val['label'].to_numpy(), prob), 4),
            "net_maker": round(float(net.mean()), 5), "win": round(float((net>0).mean()), 3)}
res = [run('data/judgment_yolo_holdout.csv','yolo_holdout'),
       run('data/swap_replication/swap_tp5_sl2.csv','rule_holdout')]
Path('analysis/output/ab_decisive.json').write_text(json.dumps(res, ensure_ascii=False, indent=2))
for r in res: print(r, flush=True)
ok = all(r.get('top_n',0) >= 50 for r in res)
print("样本充足,判定有效" if ok else "⚠️样本仍不足,判定无效")
PYEOF
git add analysis/output/ab_decisive.json data/ab_holdout_symbols.json scripts/ab_decisive.sh 2>/dev/null
git commit -qm "Decisive A/B: v7-holdout detector on 106 never-trained symbols, top-30% bucket" && git push -q
PYTHONPATH=. python3 -c "
from src.notify import send
import json
r=json.load(open('analysis/output/ab_decisive.json'))
send('⚔️ 决定性A/B(106个干净币种,检测器从未见过):\n' + '\n'.join(f\"{x['tag']}: AUC {x.get('auc')} 净@maker {x.get('net_maker')} 胜率 {x.get('win')} ({x.get('top_n')}笔)\" for x in r))" || true
echo "=== 决定性A/B done $(date) ==="
