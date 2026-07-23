"""v16 detection + judgment-layer 2.0 (SELECTION + DIRECTION), properly aligned.

Owner (2026-07-24): the v16 backtest was long-only (PF 0.78) and that was the
wrong default -- v16 detects both up and down clusters. The judgment layer's
real job is to (a) select and (b) pick the SIDE (oracle PF 2.68). Reuse v16's
actual detections (data/v16_candidates_100.csv = 4014 real v16 tip fires) and
rebuild judgment properly:

  features : MA-GEOMETRY (slopes / stacking order / price-vs-each-MA / spreads
             -- the directional geometry the eye reads) + broad OHLCV + BTC
  label    : y_long = long TP3/SL1 net > short TP3/SL1 net (which side wins)
  model    : LightGBM binary -> P(long is the better side)
  trade    : predicted side, TP3/SL1, walk-forward
  gates    : direction AUC (is side predictable?), traded PF vs always-long/
             short/oracle. Selection filter on |p-0.5| (conviction) tested too.

Causal, <2026-05-04, maker cost. Reuses v16 fires (no GPU re-scan needed).
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
from src.detection.data import add_mas  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.labeling import ATR_PCT_MIN  # noqa: E402
from scripts.broad_features import add_broad_features  # noqa: E402

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
HORIZ, TP_A, SL_B = 72, 3.0, 1.0
MA_P = (20, 60, 120)


def geom_features(frame: pd.DataFrame) -> pd.DataFrame:
    """MA-geometry: slopes, stacking order, price-vs-MA, pairwise spreads."""
    c = frame["close"].astype(float)
    out: dict[str, pd.Series] = {}
    smas = {p: c.rolling(p).mean() for p in MA_P}
    emas = {p: c.ewm(span=p, adjust=False).mean() for p in MA_P}
    allma = {}
    for p in MA_P:
        allma[f"sma{p}"] = smas[p]; allma[f"ema{p}"] = emas[p]
    for name, ma in allma.items():
        out[f"c_vs_{name}"] = c / ma - 1                      # price above/below (directional)
        out[f"slope_{name}"] = (ma - ma.shift(12)) / (12 * c)  # MA rising/falling (directional)
    # stacking order: fraction of MA pairs in bullish order (fast>slow)
    keys = list(allma.keys())
    bull = None
    npair = 0
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            pi = int(keys[i].replace("sma", "").replace("ema", ""))
            pj = int(keys[j].replace("sma", "").replace("ema", ""))
            if pi == pj:
                continue
            fast, slow = (allma[keys[i]], allma[keys[j]]) if pi < pj else (allma[keys[j]], allma[keys[i]])
            b = (fast > slow).astype(float)
            bull = b if bull is None else bull + b
            npair += 1
    out["ma_bull_stack"] = (bull / npair) if npair else pd.Series(0.5, index=c.index)  # 1=all bullish stacked
    # spread of the bundle (compression) + its recent change (expanding up/down?)
    bundle = pd.concat(list(allma.values()), axis=1)
    out["ma_bundle_spread"] = (bundle.max(axis=1) - bundle.min(axis=1)) / c
    out["ma_center_slope"] = (bundle.mean(axis=1) - bundle.mean(axis=1).shift(12)) / (12 * c)
    return pd.DataFrame(out, index=frame.index).replace([np.inf, -np.inf], np.nan)


def tight_net(ind, i, direction):
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
    if direction > 0:
        up, dn = e + TP_A * atr, e - SL_B * atr
        ut = np.argmax(hi >= up) if (hi >= up).any() else 10**9; dt = np.argmax(lo <= dn) if (lo <= dn).any() else 10**9
        g = (TP_A * atr / e) if ut <= dt and ut < 10**9 else (-SL_B * atr / e) if dt < 10**9 else cl[-1] / e - 1
    else:
        dn, up = e - TP_A * atr, e + SL_B * atr
        dt = np.argmax(lo <= dn) if (lo <= dn).any() else 10**9; ut = np.argmax(hi >= up) if (hi >= up).any() else 10**9
        g = (TP_A * atr / e) if dt <= ut and dt < 10**9 else (-SL_B * atr / e) if ut < 10**9 else e / cl[-1] - 1
    return g - FORWARD_COST


def pf(x):
    x = np.asarray([v for v in x if v is not None]); w, l = x[x > 0].sum(), x[x < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def main() -> int:
    cand = pd.read_csv(PROJECT / "data" / "v16_candidates_100.csv")
    cand["t"] = pd.to_datetime(cand["signal_time"], utc=True)
    rows = []
    for sym, grp in cand.groupby("symbol"):
        try:
            frame = load_series(list_series(bar="15m")[("okx", sym)])
        except Exception:
            continue
        ind = add_indicators(frame); broad = add_broad_features(frame); geom = geom_features(frame)
        times = pd.to_datetime(frame["open_time"], utc=True)
        tmap = {str(v): k for k, v in enumerate(times)}
        for _, r in grp.iterrows():
            i = tmap.get(str(r["t"]))
            if i is None or i >= len(frame) - HORIZ - 2:
                continue
            L = tight_net(ind, i, +1); S = tight_net(ind, i, -1)
            if L is None or S is None:
                continue
            row = {}
            for src in (broad, geom):
                rr = src.iloc[i]
                for c in src.columns:
                    row[c] = float(rr[c]) if np.isfinite(rr[c]) else 0.0
            for c in ["btc_ret24", "btc_ret96", "btc_above_ema200", "btc_atr_pct"]:
                row[c] = float(r[c]) if c in r and np.isfinite(r[c]) else 0.0
            row["long_net"] = L; row["short_net"] = S; row["y_long"] = int(L > S)
            row["signal_time"] = str(r["t"])
            rows.append(row)
    df = pd.DataFrame(rows).sort_values("signal_time").reset_index(drop=True)
    FEAT = [c for c in df.columns if c not in {"long_net", "short_net", "y_long", "signal_time"}]
    for c in FEAT:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    print(f"v16 candidates used={len(df)} geom+broad+btc features={len(FEAT)} up-better={df['y_long'].mean():.3f}")

    from sklearn.metrics import roc_auc_score
    P = {"objective": "binary", "num_leaves": 31, "learning_rate": 0.03, "min_data_in_leaf": 40,
         "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5, "verbose": -1}
    n = len(df); wf = []
    for a, b, c in [(0.0, 0.5, 0.65), (0.0, 0.65, 0.8), (0.0, 0.8, 1.0)]:
        tr, te = df.iloc[int(n*a):int(n*b)].copy(), df.iloc[int(n*b):int(n*c)].copy()
        bo = lgb.train(P, lgb.Dataset(tr[FEAT], label=tr["y_long"]), num_boost_round=300)
        te["p"] = bo.predict(te[FEAT])
        auc = float(roc_auc_score(te["y_long"], te["p"])) if te["y_long"].nunique() > 1 else float("nan")
        p = te["p"].to_numpy()
        dirnet = np.where(p >= 0.5, te["long_net"].to_numpy(), te["short_net"].to_numpy())
        conv = np.abs(p - 0.5) >= 0.1
        oracle = np.maximum(te["long_net"].to_numpy(), te["short_net"].to_numpy())
        wf.append({"test_start": te["signal_time"].iloc[0][:10], "dir_AUC": round(auc, 4),
                   "traded_dir_PF": pf(dirnet), "traded_dir_bps": round(float(dirnet.mean())*1e4, 1),
                   "conviction_PF": pf(dirnet[conv]), "conviction_n": int(conv.sum()),
                   "always_long_PF": pf(te["long_net"]), "always_short_PF": pf(te["short_net"]),
                   "oracle_PF": pf(oracle)})
    # top geometry features by gain (last fold)
    imp = sorted(zip(FEAT, bo.feature_importance("gain")), key=lambda x: -x[1])[:12]
    out = {"exit": "TP3/SL1", "v16_candidates": len(df),
           "top_features": [{"f": f, "gain": round(float(g), 1)} for f, g in imp],
           "walk_forward": wf}
    (PROJECT / "analysis" / "output" / "v16_judgment_v2.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
