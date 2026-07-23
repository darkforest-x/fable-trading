"""Two-layer test under the RIGHT exit (TP3/SL1), not TP5/SL2.

Owner (2026-07-23): the multi-exit test was one layer (entry+exit, no judgment).
Every prior judgment layer was trained on TP5/SL2 labels -- the wrong exit
(owner picks drift down; TP5 needs a +5*ATR up-move that never comes). Under a
tight exit (TP3*ATR / SL1*ATR = cut fast, modest target) the owner's picks were
the only positive set (PF 1.048). So test the TWO-LAYER properly:

  detection: rule-dense tips (causal emergence, the tradeable universe)
  judgment : LightGBM trained to predict the TP3/SL1 net return (RIGHT exit)
  decision : top score-decile, walk-forward across periods

If the top decile is robustly >1.3 -> the two-layer works once the exit and the
judgment label are aligned to the signal. If it collapses (like every TP5/SL2
attempt) -> thin persists even with the right exit + same-source judgment.

Caveat: TP3/SL1 was chosen post-hoc as the best of 6 exits; walk-forward is the
guard against that being luck. <2026-05-04 only (holdout untouched).
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
from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows  # noqa: E402
from src.judgment.labeling import ATR_PCT_MIN  # noqa: E402

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
FAST_MAX, FULL_MAX, MIN_DENSE, MIN_GAP = 0.0028, 0.0055, 5, 18
HORIZ = 72
TP_A, SL_B = 3.0, 1.0   # the tight exit that was the only positive one
F = list(FEATURE_COLUMNS)


def tight_net(ind, i):
    entry_i = i + 1
    if entry_i >= len(ind):
        return None
    atr = float(ind["atr14"].iloc[i]); atr_pct = float(ind["atr_pct"].iloc[i])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(atr_pct) or atr_pct < ATR_PCT_MIN:
        return None
    entry = float(ind["open"].iloc[entry_i])
    if entry <= 0:
        return None
    last = min(entry_i + HORIZ - 1, len(ind) - 1)
    hi = ind["high"].to_numpy()[entry_i:last + 1]
    lo = ind["low"].to_numpy()[entry_i:last + 1]
    cl = ind["close"].to_numpy()[entry_i:last + 1]
    up = entry + TP_A * atr; dn = entry - SL_B * atr
    ut = np.argmax(hi >= up) if (hi >= up).any() else 10**9
    dt = np.argmax(lo <= dn) if (lo <= dn).any() else 10**9
    if ut == dt == 10**9:
        g = cl[-1] / entry - 1
    elif ut <= dt:
        g = TP_A * atr / entry
    else:
        g = -SL_B * atr / entry
    return g - FORWARD_COST


def dense_tips(ema, lo, hi):
    fast = pd.to_numeric(ema["fast_spread"], errors="coerce").to_numpy()
    full = pd.to_numeric(ema["full_spread"], errors="coerce").to_numpy()
    dense = (fast <= FAST_MAX) & (full <= FULL_MAX)
    run = 0; fires = []
    for i in range(len(dense)):
        run = run + 1 if dense[i] else 0
        if run == MIN_DENSE and lo <= i < hi:
            fires.append(i)
    out = []
    for i in fires:
        if not out or i - out[-1] >= MIN_GAP:
            out.append(i)
    return out


def pf(x):
    x = np.asarray(x); w, l = x[x > 0].sum(), x[x < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def stats(x):
    x = np.asarray(x)
    return {"n": int(len(x)), "win": round(float((x > 0).mean()), 3),
            "PF": pf(x), "mean_bps": round(float(x.mean()) * 1e4, 1)} if len(x) else {"n": 0}


def main() -> int:
    rows = []
    for src, sym, frame in iter_series(bar="15m", min_bars=500):
        if src != "okx" or not sym.endswith("_USDT_SWAP") or is_stockish(sym) or is_eval_symbol(sym):
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        frame = frame[times < HOLDOUT_START].reset_index(drop=True)
        if len(frame) < 500:
            continue
        ema = add_mas(frame); ind = add_indicators(frame); feat = add_features(ind)
        tips = dense_tips(ema, 210, len(frame) - HORIZ - 2)
        if not tips:
            continue
        fr = extract_feature_rows(feat, tips)
        t = pd.to_datetime(ind["open_time"], utc=True)
        for pos, i in enumerate(tips):
            net = tight_net(ind, i)
            if net is None:
                continue
            row = {c: float(fr.iloc[pos][c]) for c in F}
            row["net"] = net; row["signal_time"] = str(t.iloc[i])
            rows.append(row)
    df = pd.DataFrame(rows).dropna(subset=["net"]).sort_values("signal_time").reset_index(drop=True)
    for c in F:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    print(f"dense candidates: {len(df)}  base rate (TP3/SL1): {stats(df['net'].to_numpy())}")

    P = {"objective": "regression", "num_leaves": 31, "learning_rate": 0.03,
         "min_data_in_leaf": 50, "feature_fraction": 0.8, "bagging_fraction": 0.8,
         "bagging_freq": 5, "verbose": -1}
    n = len(df)
    wf = []
    for a, b, c in [(0.0, 0.5, 0.65), (0.0, 0.65, 0.8), (0.0, 0.8, 1.0)]:
        tr, te = df.iloc[int(n * a):int(n * b)], df.iloc[int(n * b):int(n * c)]
        bo = lgb.train(P, lgb.Dataset(tr[F], label=tr["net"]), num_boost_round=300)
        te = te.copy(); te["s"] = bo.predict(te[F])
        net = te.sort_values("s", ascending=False)["net"].to_numpy()
        k = max(int(len(te) * .1), 1)
        wf.append({"test_start": te["signal_time"].iloc[0][:10],
                   "raw_TP3SL1": stats(te["net"].to_numpy()),
                   "judgment_top10pct": stats(net[:k])})
    out = {"exit": f"TP{TP_A:g}xATR / SL{SL_B:g}xATR (tight, only positive exit)",
           "candidates": len(df), "walk_forward": wf}
    (PROJECT / "analysis" / "output" / "two_layer_tight_exit.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
