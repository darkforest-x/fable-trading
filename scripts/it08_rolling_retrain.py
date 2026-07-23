"""IT-08: monthly ROLLING retrain of the short judgment (adapt to flipping regime).

IT-07 found the winning regime flips over time, so a static model (train 70% /
test 30%) goes stale. Test whether frequent retraining keeps the edge alive:
for each test month M, train on ALL data < M (expanding window), test on M,
take the judgment top-decile short trades. Report per-month PF -- especially the
most recent months (closest to holdout). Robust = late months stay >=1.3.

Reuses v16 short-below-all-MA candidates (caches to data/short_cand_cache.csv on
first run so re-iterations are fast). Causal, <2026-05-04, TP5/SL2, maker cost.
"""
from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(PROJECT))

CACHE = PROJECT / "data" / "short_cand_cache.csv"
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")


def build_cache():
    from src.data.loader import list_series, load_series
    from src.judgment.candidates import add_indicators
    from scripts.broad_features import add_broad_features
    from scripts.v16_judgment_v2 import geom_features
    from scripts.short_quality_judgment import short_quality, short_net, all6
    import numpy as np
    v16 = pd.read_csv(PROJECT / "data" / "v16_candidates_100.csv")
    v16["t"] = pd.to_datetime(v16["signal_time"], utc=True)
    rows = []
    for sym, grp in v16.groupby("symbol"):
        try:
            frame = load_series(list_series(bar="15m")[("okx", sym)])
        except Exception:
            continue
        ind = add_indicators(frame); broad = add_broad_features(frame); geom = geom_features(frame)
        mamat, close = all6(frame)
        tmap = {str(v): k for k, v in enumerate(pd.to_datetime(frame["open_time"], utc=True))}
        for _, r in grp.iterrows():
            i = tmap.get(str(r["t"]))
            if i is None or i < 60 or i >= len(frame) - 74:
                continue
            col = mamat[:, i]
            if not np.all(np.isfinite(col)) or not (close[i] < col.min()):
                continue
            sq = short_quality(ind, i, mamat); sn = short_net(ind, i)
            if sq is None or sn is None:
                continue
            row = dict(sq)
            for srcf in (broad, geom):
                rr = srcf.iloc[i]
                for c in srcf.columns:
                    row[c] = float(rr[c]) if np.isfinite(rr[c]) else 0.0
            for c in ["btc_ret24", "btc_ret96", "btc_above_ema200", "btc_atr_pct"]:
                row[c] = float(r[c]) if c in r and np.isfinite(r[c]) else 0.0
            row["net"] = sn; row["signal_time"] = str(r["t"])
            rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(CACHE, index=False)
    return df


def pf(x):
    x = np.asarray(x); w, l = x[x > 0].sum(), x[x < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def main() -> int:
    df = pd.read_csv(CACHE) if CACHE.exists() else build_cache()
    df["t"] = pd.to_datetime(df["signal_time"], utc=True)
    df = df.sort_values("t").reset_index(drop=True)
    FEAT = [c for c in df.columns if c not in {"net", "signal_time", "t"} and pd.api.types.is_numeric_dtype(df[c])]
    for c in FEAT:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["ym"] = df["t"].dt.strftime("%Y-%m")
    months = sorted(df["ym"].unique())
    P = {"objective": "regression", "num_leaves": 31, "learning_rate": 0.03, "min_data_in_leaf": 30,
         "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5, "verbose": -1}
    print(f"candidates={len(df)} feats={len(FEAT)} months={months}")
    out_months = []
    for m in months:
        tr = df[df["ym"] < m]; te = df[df["ym"] == m]
        if len(tr) < 300 or len(te) < 25:
            continue
        bo = lgb.train(P, lgb.Dataset(tr[FEAT], label=tr["net"]), num_boost_round=250)
        s = bo.predict(te[FEAT]); net = te["net"].to_numpy()[np.argsort(-s)]
        k10 = max(int(len(te)*.1), 1); k20 = max(int(len(te)*.2), 1)
        out_months.append({
            "month": m, "n_test": len(te), "raw_PF": pf(te["net"].to_numpy()),
            "top10_PF": pf(net[:k10]), "top10_n": k10,
            "top20_PF": pf(net[:k20]), "top20_bps": round(float(net[:k20].mean())*1e4, 1)})
    out = {"config": "monthly rolling retrain, short below-all-MA + short-quality judgment",
           "exit": "TP5/SL2", "months": out_months}
    (PROJECT / "analysis" / "output" / "it08_rolling_retrain.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    for m in out_months:
        print(f"  {m['month']}: raw={m['raw_PF']} top10={m['top10_PF']}(n{m['top10_n']}) top20={m['top20_PF']} ({m['top20_bps']}bps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
