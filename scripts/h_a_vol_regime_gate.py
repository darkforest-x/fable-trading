"""H-A: does the short edge live in a specific VOLATILITY regime (all 3 periods)?

Lab IT-06 showed btc_atr_pct / vol dominate. Hypothesis: shorts work when market
volatility is elevated. Test DIAGNOSTICALLY (not a curve-fit gate): bucket the
v16 short-below-all-MA candidates by a CAUSAL BTC-vol regime (btc_atr_pct vs its
trailing 500-bar median), and report base rate + judgment top-decile PER bucket
PER walk-forward period. A principled gate exists only if one bucket is robustly
>=1.3 across ALL three periods -- not just better in the weak one.

Causal, <2026-05-04, TP5/SL2, maker cost.
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
from scripts.short_quality_judgment import short_quality, short_net, all6  # noqa: E402

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")


def btc_vol_regime():
    """time -> 1 if BTC atr_pct above its trailing 500-bar median (high-vol) else 0."""
    try:
        btc = load_series(list_series(bar="15m")[("okx", "BTC_USDT_SWAP")])
    except Exception:
        return {}
    h = btc["high"].astype(float); l = btc["low"].astype(float); c = btc["close"].astype(float)
    atrp = ((h - l) / c).rolling(14).mean()
    med = atrp.rolling(500, min_periods=100).median()
    hi = (atrp > med).astype(float)
    t = pd.to_datetime(btc["open_time"], utc=True).astype(str)
    return {tt: float(v) for tt, v in zip(t, hi)}


def pf(x):
    x = np.asarray(x); w, l = x[x > 0].sum(), x[x < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def st(x):
    x = np.asarray(x)
    return {"n": int(len(x)), "PF": pf(x), "mean_bps": round(float(x.mean()) * 1e4, 1)} if len(x) else {"n": 0}


def main() -> int:
    reg = btc_vol_regime()
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
            row["hivol"] = reg.get(str(r["t"]), 0.0)
            row["net"] = sn; row["signal_time"] = str(r["t"])
            rows.append(row)
    df = pd.DataFrame(rows).sort_values("signal_time").reset_index(drop=True)
    FEAT = [c for c in df.columns if c not in {"net", "signal_time", "hivol"}]
    for c in FEAT:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    def wf_judge(d):
        n = len(d); res = []
        if n < 200:
            return [{"n": n, "note": "too few"}]
        P = {"objective": "regression", "num_leaves": 31, "learning_rate": 0.03, "min_data_in_leaf": 30,
             "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5, "verbose": -1}
        for a, b, c in [(0.0, 0.5, 0.65), (0.0, 0.65, 0.8), (0.0, 0.8, 1.0)]:
            tr, te = d.iloc[int(n*a):int(n*b)], d.iloc[int(n*b):int(n*c)]
            if len(tr) < 100 or len(te) < 30:
                res.append({"n": len(te), "raw": st(te["net"]), "top20": {"n": 0}}); continue
            bo = lgb.train(P, lgb.Dataset(tr[FEAT], label=tr["net"]), num_boost_round=250)
            s = bo.predict(te[FEAT]); srt = te["net"].to_numpy()[np.argsort(-s)]
            res.append({"start": te["signal_time"].iloc[0][:10], "raw": st(te["net"]),
                        "top20": st(srt[:max(int(len(te)*.2), 1)])})
        return res

    out = {"config": "v16 short + short-quality judgment, split by BTC-vol regime",
           "n_total": len(df), "n_hivol": int(df["hivol"].sum()), "n_lovol": int((df["hivol"] == 0).sum()),
           "ALL": wf_judge(df.reset_index(drop=True)),
           "HI_VOL_only": wf_judge(df[df["hivol"] == 1].reset_index(drop=True)),
           "LO_VOL_only": wf_judge(df[df["hivol"] == 0].reset_index(drop=True))}
    (PROJECT / "analysis" / "output" / "h_a_vol_regime_gate.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
