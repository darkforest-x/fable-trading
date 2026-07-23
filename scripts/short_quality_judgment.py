"""Short-breakdown QUALITY features + judgment, to rescue period3.

Base: v16 fires below all 6 MAs (short candidates, PF ~1.09 but decays to <1 in
the recent period3). Engineer features that specifically separate a GOOD short
(keeps falling) from a bad one (bounces), and see if the judgment top-decile
stays positive even in period3.

Short-quality features (causal, at the breakdown bar i):
  bd_depth      : how far below the MA bundle, in ATR   (min_MA - close)/atr
  bd_force      : bearish candle body strength           (open-close)/atr
  bd_range      : bar range / atr                        (high-low)/atr
  vol_break     : volume vs 20-bar avg                   (real breakdown?)
  ma_roll       : MA bundle center slope (rolling over?)
  lower_highs20 : fraction of last 20 bars making lower highs (downtrend)
  from_high20   : distance below 20-bar high, in ATR     (already in decline?)
  room_to_low48 : distance above 48-bar low, in ATR      (room to fall)
  dd48          : drawdown from 48-bar high
plus broad + geom + BTC. TP5/SL2, walk-forward.
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


def all6(frame):
    c = frame["close"].astype(float); mas = []
    for p in MA_P:
        mas.append(c.rolling(p).mean().to_numpy()); mas.append(c.ewm(span=p, adjust=False).mean().to_numpy())
    return np.vstack(mas), c.to_numpy()


def short_quality(ind, i, mamat):
    o = float(ind["open"].iloc[i]); c = float(ind["close"].iloc[i])
    h = float(ind["high"].iloc[i]); lo = float(ind["low"].iloc[i])
    atr = float(ind["atr14"].iloc[i])
    if atr <= 0:
        return None
    col = mamat[:, i]
    hi20 = ind["high"].to_numpy()[max(0, i - 19):i + 1]
    lo48 = ind["low"].to_numpy()[max(0, i - 47):i + 1]
    highs = ind["high"].to_numpy()[max(0, i - 19):i + 1]
    lower_highs = float(np.mean(highs[1:] < highs[:-1])) if len(highs) > 1 else 0.0
    center = float(np.mean(col))
    center_prev = float(np.mean(mamat[:, i - 12])) if i >= 12 else center
    vol = float(ind["volume"].iloc[i])
    vavg = float(ind["volume"].to_numpy()[max(0, i - 19):i + 1].mean())
    return {
        "bd_depth": (col.min() - c) / atr,
        "bd_force": (o - c) / atr,
        "bd_range": (h - lo) / atr,
        "vol_break": vol / vavg if vavg > 0 else 1.0,
        "ma_roll": (center - center_prev) / (12 * c) if c > 0 else 0.0,
        "lower_highs20": lower_highs,
        "from_high20": (float(hi20.max()) - c) / atr,
        "room_to_low48": (c - float(lo48.min())) / atr,
        "dd48": c / float(hi20.max()) - 1 if len(hi20) else 0.0,
    }


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
        tmap = {str(v): k for k, v in enumerate(pd.to_datetime(frame["open_time"], utc=True))}
        for _, r in grp.iterrows():
            i = tmap.get(str(r["t"]))
            if i is None or i < 60 or i >= len(frame) - HORIZ - 2:
                continue
            col = mamat[:, i]
            if not np.all(np.isfinite(col)) or not (close[i] < col.min()):
                continue
            sq = short_quality(ind, i, mamat)
            sn = short_net(ind, i)
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
    df = pd.DataFrame(rows).sort_values("signal_time").reset_index(drop=True)
    FEAT = [c for c in df.columns if c not in {"net", "signal_time"}]
    for c in FEAT:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    print(f"short candidates: {len(df)} feats={len(FEAT)} base: {st(df['net'].to_numpy())}")

    P = {"objective": "regression", "num_leaves": 31, "learning_rate": 0.03, "min_data_in_leaf": 40,
         "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5, "verbose": -1}
    n = len(df); wf = []
    for a, b, c in [(0.0, 0.5, 0.65), (0.0, 0.65, 0.8), (0.0, 0.8, 1.0)]:
        tr, te = df.iloc[int(n*a):int(n*b)].copy(), df.iloc[int(n*b):int(n*c)].copy()
        bo = lgb.train(P, lgb.Dataset(tr[FEAT], label=tr["net"]), num_boost_round=300)
        te = te.copy(); te["s"] = bo.predict(te[FEAT])
        srt = te.sort_values("s", ascending=False)["net"].to_numpy()
        wf.append({"test_start": te["signal_time"].iloc[0][:10], "raw": st(te["net"].to_numpy()),
                   "top10": st(srt[:max(int(len(te)*.1), 1)]), "top20": st(srt[:max(int(len(te)*.2), 1)])})
    imp = sorted(zip(FEAT, bo.feature_importance("gain")), key=lambda x: -x[1])[:12]
    out = {"base": "v16 short below-all-MA + short-quality features", "exit": "TP5/SL2",
           "candidates": len(df), "top_features": [{"f": f, "gain": round(float(g), 1)} for f, g in imp],
           "walk_forward": wf}
    (PROJECT / "analysis" / "output" / "short_quality_judgment.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
