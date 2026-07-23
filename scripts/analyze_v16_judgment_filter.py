"""True live-pipeline verdict: apply v11 judgment filter to v16's holdout fires.

The tip-replay backtest was detector-only (PF 0.78, net -2.8). Live adds the
v11 LightGBM filter (keep score>=threshold). Recompute P&L on the judgment-
passing subset. No YOLO rerun — reuse recorded fire bars.
"""
import json
import numpy as np
import pandas as pd
import lightgbm as lgb

from src.data.loader import list_series, load_series
from src.judgment.candidates import add_indicators
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.frozen import DEFAULT_FROZEN_CONFIG, latest_artifact

art = latest_artifact(DEFAULT_FROZEN_CONFIG)
booster = lgb.Booster(model_file=str(art.model_path))
thr = art.threshold
print(f"judgment: {art.relative_model_path} thr={thr:.5f}")

trades = json.load(open("analysis/output/v16_holdout_verdict.json"))["trades"]
by_sym = {}
for t in trades:
    by_sym.setdefault(t["symbol"], []).append(t)

scored = []
for sym, ts in by_sym.items():
    try:
        paths = list_series(bar="15m")[("okx", sym)]
        frame = load_series(paths)
    except Exception:
        continue
    enriched = add_indicators(frame)
    featured = add_features(enriched)
    times = pd.to_datetime(featured["open_time"], utc=True)
    tmap = {str(v): i for i, v in enumerate(times)}
    idxs, keep = [], []
    for t in ts:
        st = str(pd.Timestamp(t["signal_time"]))
        i = tmap.get(st)
        if i is None:
            continue
        idxs.append(i); keep.append(t)
    if not idxs:
        continue
    rows = extract_feature_rows(featured, idxs)
    s = booster.predict(rows[FEATURE_COLUMNS], num_iteration=art.best_iteration)
    for t, sc in zip(keep, s):
        t2 = dict(t); t2["jscore"] = float(sc); scored.append(t2)

net_all = np.array([t["net_ret"] for t in scored])
passed = [t for t in scored if t["jscore"] >= thr]
net_p = np.array([t["net_ret"] for t in passed]) if passed else np.array([])

def stats(net):
    if not len(net): return "n=0"
    w, l = net[net>0].sum(), net[net<0].sum()
    pf = w/-l if l<0 else float('inf')
    return f"n={len(net)} win={np.mean(net>0):.3f} PF={pf:.3f} net={net.sum():.3f} mean={net.mean():.5f}"

print(f"all v16 fires scored: {stats(net_all)}")
print(f"passed v11 judgment (score>={thr:.4f}): {stats(net_p)}")
print(f"pass rate: {len(passed)}/{len(scored)} = {len(passed)/max(len(scored),1):.3f}")
# also try: does a HIGHER judgment cutoff find a profitable subset?
for q in (0.5, 0.7, 0.9, 0.95):
    cut = np.quantile([t["jscore"] for t in scored], q)
    sub = np.array([t["net_ret"] for t in scored if t["jscore"] >= cut])
    print(f"  top {(1-q)*100:.0f}% by jscore (cut={cut:.4f}): {stats(sub)}")
