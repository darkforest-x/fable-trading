"""Full TWO-LAYER on the best base: v16 detect + short-below-all-MA + judgment.

Owner: the all-MA breakout test was one layer (traded ALL fires, no judgment).
The best base was SHORT when v16 fires below all 6 MAs (PF ~1.07). Now add the
judgment layer to filter THAT subset to its best decile:

  detection : v16 fires (data/v16_candidates_100.csv)
  direction : close < all 6 MAs -> short candidate (owner rule, profitable side)
  label     : short TP5/SL2 net (original exit)
  judgment  : LightGBM regression on short net, walk-forward
  decision  : top score-decile -> short. Does it clear 1.3 robustly?

Causal, <2026-05-04, maker cost.
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

from src.costs import FORWARD_COST  # noqa: E402
from src.data.loader import list_series, load_series  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.labeling import ATR_PCT_MIN  # noqa: E402
from scripts.broad_features import add_broad_features  # noqa: E402
from scripts.v16_judgment_v2 import geom_features  # noqa: E402

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
HORIZ, TP_A, SL_B = 72, 5.0, 2.0
MA_P = (20, 60, 120)


def short_net(ind, i):
    ei = i + 1
    if ei >= len(ind):
        return None
    atr = float(ind["atr14"].iloc[i]); ap = float(ind["atr_pct"].iloc[i])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(ap) or ap < ATR_PCT_MIN:
        return None
    e = float(ind["open"].iloc[ei])
    if e <= 0:
        return None
    last = min(ei + HORIZ - 1, len(ind) - 1)
    hi = ind["high"].to_numpy()[ei:last + 1]; lo = ind["low"].to_numpy()[ei:last + 1]; cl = ind["close"].to_numpy()[ei:last + 1]
    if len(cl) < 8:
        return None
    dn, up = e - TP_A * atr, e + SL_B * atr
    dt = np.argmax(lo <= dn) if (lo <= dn).any() else 10**9; ut = np.argmax(hi >= up) if (hi >= up).any() else 10**9
    g = (TP_A * atr / e) if dt <= ut and dt < 10**9 else (-SL_B * atr / e) if ut < 10**9 else e / cl[-1] - 1
    return g - FORWARD_COST


def all6(frame):
    c = frame["close"].astype(float); mas = []
    for p in MA_P:
        mas.append(c.rolling(p).mean().to_numpy()); mas.append(c.ewm(span=p, adjust=False).mean().to_numpy())
    return np.vstack(mas), c.to_numpy()


def pf(x):
    x = np.asarray(x); w, l = x[x > 0].sum(), x[x < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def st(x):
    x = np.asarray(x)
    return {"n": int(len(x)), "PF": pf(x), "win": round(float((x > 0).mean()), 3),
            "mean_bps": round(float(x.mean()) * 1e4, 1)} if len(x) else {"n": 0}


def main() -> int:
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
        times = pd.to_datetime(frame["open_time"], utc=True)
        tmap = {str(v): k for k, v in enumerate(times)}
        for _, r in grp.iterrows():
            i = tmap.get(str(r["t"]))
            if i is None or i >= len(frame) - HORIZ - 2:
                continue
            col = mamat[:, i]
            if not np.all(np.isfinite(col)) or not (close[i] < col.min()):  # below ALL MAs = short case
                continue
            sn = short_net(ind, i)
            if sn is None:
                continue
            row = {}
            for src in (broad, geom):
                rr = src.iloc[i]
                for c in src.columns:
                    row[c] = float(rr[c]) if np.isfinite(rr[c]) else 0.0
            for c in ["btc_ret24", "btc_ret96", "btc_above_ema200", "btc_atr_pct"]:
                row[c] = float(r[c]) if c in r and np.isfinite(r[c]) else 0.0
            row["net"] = sn; row["signal_time"] = str(r["t"])
            rows.append(row)
    df = pd.DataFrame(rows).sort_values("signal_time").reset_index(drop=True)
    FEAT = [c for c in df.columns if c not in {"net", "signal_time"}]
    for c in FEAT:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    print(f"short(below-all-MA) v16 candidates: {len(df)} base rate: {st(df['net'].to_numpy())}")

    P = {"objective": "regression", "num_leaves": 31, "learning_rate": 0.03, "min_data_in_leaf": 40,
         "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5, "verbose": -1}
    n = len(df); wf = []
    for a, b, c in [(0.0, 0.5, 0.65), (0.0, 0.65, 0.8), (0.0, 0.8, 1.0)]:
        tr, te = df.iloc[int(n*a):int(n*b)].copy(), df.iloc[int(n*b):int(n*c)].copy()
        bo = lgb.train(P, lgb.Dataset(tr[FEAT], label=tr["net"]), num_boost_round=300)
        te = te.copy(); te["s"] = bo.predict(te[FEAT])
        srt = te.sort_values("s", ascending=False)["net"].to_numpy()
        wf.append({"test_start": te["signal_time"].iloc[0][:10],
                   "raw_short": st(te["net"].to_numpy()),
                   "judgment_top10": st(srt[:max(int(len(te)*.1), 1)]),
                   "judgment_top20": st(srt[:max(int(len(te)*.2), 1)]),
                   "judgment_top30": st(srt[:max(int(len(te)*.3), 1)])})
    out = {"base": "v16 short below-all-MA", "exit": "TP5/SL2", "candidates": len(df), "walk_forward": wf}
    (PROJECT / "analysis" / "output" / "two_layer_short_breakout.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
