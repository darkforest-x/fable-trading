"""IT-10: causal regime side-selection + separate long/short judgments.

IT-09: long and short are complementary (short wins periods 1-2, long wins the
recent period3). So pick the SIDE by market regime and trade it with that side's
own judgment:
  BTC bull (above its 200-bar SMA) -> trade LONG candidates via long judgment
  BTC bear                          -> trade SHORT candidates via short judgment
per period, keep each side's judgment top-frac, combine. Walk-forward.

Differs from IT-05 (which failed): IT-05 used ONE judgment on mixed long+short
net and a crude per-candidate BTC-200EMA flag. Here each side has its own
judgment trained on its own net, and the regime picks which side is active.
TP5/SL2, maker cost, <2026-05-04.
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

from src.data.loader import list_series, load_series  # noqa: E402
from scripts.it09_both_sides import build, pf, st  # noqa: E402

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")


def btc_bull_map():
    """time -> 1 if BTC close > its 200-bar SMA (bull regime), else 0. Causal."""
    btc = load_series(list_series(bar="15m")[("okx", "BTC_USDT_SWAP")])
    c = btc["close"].astype(float)
    bull = (c > c.rolling(200).mean()).astype(float)
    t = pd.to_datetime(btc["open_time"], utc=True).astype(str)
    return {tt: float(v) for tt, v in zip(t, bull)}


def train_judge(df, F, tr_mask):
    P = {"objective": "regression", "num_leaves": 31, "learning_rate": 0.03, "min_data_in_leaf": 30,
         "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5, "verbose": -1}
    tr = df[tr_mask]
    if len(tr) < 100:
        return None
    return lgb.train(P, lgb.Dataset(tr[F], label=tr["net"]), num_boost_round=250)


def main() -> int:
    bull = btc_bull_map()
    dl, Fl = build(+1); ds, Fs = build(-1)
    for d in (dl, ds):
        d["t"] = pd.to_datetime(d["signal_time"], utc=True)
        d["bull"] = d["signal_time"].map(lambda s: bull.get(str(pd.Timestamp(s)), 0.0))
    # align periods by global time across both sides
    tmin = min(dl["t"].min(), ds["t"].min()); tmax = max(dl["t"].max(), ds["t"].max())
    edges = [tmin + (tmax - tmin) * q for q in (0.0, 0.65, 0.8, 1.0)]  # train<.65, test .65-.8, .8-1.0
    frac = 0.2
    res = []
    for lo, hi in [(edges[1], edges[2]), (edges[2], edges[3])]:
        # train each side's judgment on data before `lo`
        jl = train_judge(dl, Fl, dl["t"] < lo); js = train_judge(ds, Fs, ds["t"] < lo)
        trades = []
        # long side, bull bars, in [lo,hi)
        tel = dl[(dl["t"] >= lo) & (dl["t"] < hi) & (dl["bull"] == 1)]
        if jl is not None and len(tel):
            s = jl.predict(tel[Fl]); k = max(int(len(tel) * frac), 1)
            trades += list(tel["net"].to_numpy()[np.argsort(-s)][:k])
        # short side, bear bars
        tes = ds[(ds["t"] >= lo) & (ds["t"] < hi) & (ds["bull"] == 0)]
        if js is not None and len(tes):
            s = js.predict(tes[Fs]); k = max(int(len(tes) * frac), 1)
            trades += list(tes["net"].to_numpy()[np.argsort(-s)][:k])
        res.append({"test_window": f"{str(lo)[:10]}..{str(hi)[:10]}",
                    "regime_selected": st(np.array(trades)),
                    "n_long_bull": int(len(tel)), "n_short_bear": int(len(tes))})
    out = {"config": "IT-10 regime side-select (BTC>SMA200->long, else short) + separate judgments",
           "exit": "TP5/SL2", "long_cands": len(dl), "short_cands": len(ds), "walk_forward": res}
    (PROJECT / "analysis" / "output" / "it10_regime_side_select.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
