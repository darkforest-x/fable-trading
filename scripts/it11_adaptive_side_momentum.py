"""IT-11: follow the flipping regime by the strategy's OWN recent side performance.

IT-09 proved long/short are complementary but at a multi-week timescale; IT-10
showed a per-bar BTC-trend gate can't pick the side (lands ~1.0). This tries a
SLOW, self-referential regime signal: at decision time t, look at the trailing
K-day realized net of each side's already-CLOSED trades (entry+72bar < t, so
causal), and trade whichever side has been winning lately (strategy momentum).
Each side still filtered by its own judgment (trained on data < period start).

If this also lands ~1.0, the honest read is the per-decision edge is not
robustly recoverable and the wall is real. <2026-05-04, TP5/SL2, maker cost.
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

from scripts.it09_both_sides import build, st  # noqa: E402

HORIZON_BARS = 72
BAR_MIN = 15
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")


def train_judge(df, F, mask):
    P = {"objective": "regression", "num_leaves": 31, "learning_rate": 0.03, "min_data_in_leaf": 30,
         "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5, "verbose": -1}
    tr = df[mask]
    if len(tr) < 100:
        return None
    return lgb.train(P, lgb.Dataset(tr[F], label=tr["net"]), num_boost_round=250)


def main() -> int:
    for K_days in (14, 21, 30):
        dl, Fl = build(+1); ds, Fs = build(-1)
        for d in (dl, ds):
            d["t"] = pd.to_datetime(d["signal_time"], utc=True)
            d["close_t"] = d["t"] + pd.Timedelta(minutes=BAR_MIN * (HORIZON_BARS + 1))
        # merged timeline of closed-trade outcomes for the regime signal
        closed = pd.concat([
            dl[["close_t", "net"]].assign(side=1),
            ds[["close_t", "net"]].assign(side=-1)]).sort_values("close_t").reset_index(drop=True)
        Kd = pd.Timedelta(days=K_days)

        def side_now(t):
            w = closed[(closed["close_t"] < t) & (closed["close_t"] >= t - Kd)]
            if len(w) < 20:
                return 0  # not enough history -> skip
            ml = w[w["side"] == 1]["net"].mean() if (w["side"] == 1).any() else -9
            msh = w[w["side"] == -1]["net"].mean() if (w["side"] == -1).any() else -9
            if max(ml, msh) <= 0:
                return 0
            return 1 if ml >= msh else -1

        tmin = min(dl["t"].min(), ds["t"].min()); tmax = max(dl["t"].max(), ds["t"].max())
        edges = [tmin + (tmax - tmin) * q for q in (0.0, 0.65, 0.8, 1.0)]
        frac = 0.2
        res = []
        for lo, hi in [(edges[1], edges[2]), (edges[2], edges[3])]:
            jl = train_judge(dl, Fl, dl["t"] < lo); js = train_judge(ds, Fs, ds["t"] < lo)
            trades = []
            for d, F, model, side in [(dl, Fl, jl, 1), (ds, Fs, js, -1)]:
                te = d[(d["t"] >= lo) & (d["t"] < hi)].copy()
                if model is None or not len(te):
                    continue
                te = te[te["t"].map(side_now) == side]
                if not len(te):
                    continue
                s = model.predict(te[F]); k = max(int(len(te) * frac), 1)
                trades += list(te["net"].to_numpy()[np.argsort(-s)][:k])
            res.append({"test_window": f"{str(lo)[:10]}..{str(hi)[:10]}", "sel": st(np.array(trades))})
        print(f"K={K_days}d:", [f"{r['test_window']}:PF{r['sel'].get('PF')}(n{r['sel'].get('n')})" for r in res])
        if K_days == 21:
            (PROJECT / "analysis" / "output" / "it11_adaptive_side.json").write_text(
                json.dumps({"K_days": 21, "walk_forward": res}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
