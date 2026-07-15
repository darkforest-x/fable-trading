"""Honest A/B: YOLO vs rule candidates ONLY on symbols the detector never
trained on (the 47 frozen-eval symbols). The first A/B was contaminated --
88/101 val symbols were in v6's training images, so YOLO was selecting on
memorized charts while the rule scan (which doesn't learn) was honest.
Same judgment layer, same split discipline, val only.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import load_splits, train_model, evaluate, permutation_pvalue

eval_syms = {s.replace('okx_','') for s in json.load(open('data/eval_frozen_symbols.json'))}
OUT = Path('analysis/output/ab_clean_symbols.json')

def run(csv_path, tag):
    df = pd.read_csv(csv_path, parse_dates=['signal_time'])
    clean = df[df['symbol'].isin(eval_syms)].sort_values('signal_time').reset_index(drop=True)
    if len(clean) < 150:
        return {"tag": tag, "n": len(clean), "note": "样本不足"}
    tmp = Path(f'data/_ab_clean_{tag}.csv'); clean.to_csv(tmp, index=False)
    train, val, _ = load_splits(tmp, horizon_bars=72)
    if len(val) < 40:
        return {"tag": tag, "n": len(clean), "n_val": len(val), "note": "val太小"}
    model = train_model(train, val)
    prob = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    m = evaluate(val['label'].to_numpy(), prob, val['realized_ret'].to_numpy())
    td = m['top_decile']
    return {"tag": tag, "n_pool": len(clean), "n_train": len(train), "n_val": len(val),
            "auc": m['roc_auc'], "p": round(permutation_pvalue(val['label'].to_numpy(), prob), 4),
            "top_n": td['n'], "top_gross": td['mean_realized_ret'],
            "net_maker006": round(td['mean_realized_ret'] - 0.0006, 5),
            "top_win": td['win_rate']}

res = [run('data/judgment_yolo_swap.csv', 'YOLO候选_干净币种'),
       run('data/swap_replication/swap_tp5_sl2.csv', '规则候选_干净币种')]
OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2))
for r in res: print(r, flush=True)
