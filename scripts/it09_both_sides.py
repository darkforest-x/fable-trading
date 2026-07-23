"""IT-09: give the LONG side the same full treatment as short, then combine.

Owner: v16 detects both up and down clusters -- why short-only? Because on the
full universe long-above-all-MA lost (0.83) while short-below-all-MA won (1.07).
But that was asymmetric: short got 9 quality features + its own judgment, long
never did. And IT-08 showed the edge is intermittent -- maybe short works in
bear months, long in bull months, and trading BOTH (each with its own judgment)
is more consistent than short-only.

This builds a LONG judgment (long-quality features, above-all-MA candidates) and
a SHORT judgment (short-quality, below-all-MA), each trades its own side, and
reports LONG / SHORT / COMBINED walk-forward. TP5/SL2, maker cost, <2026-05-04.
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
from scripts.short_quality_judgment import all6  # noqa: E402

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
HORIZ, TP_A, SL_B = 72, 5.0, 2.0


def net_dir(ind, i, d):
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
    if d > 0:
        up, dn = e + TP_A * atr, e - SL_B * atr
        ut = np.argmax(hi >= up) if (hi >= up).any() else 10**9; dt = np.argmax(lo <= dn) if (lo <= dn).any() else 10**9
        g = (TP_A * atr / e) if ut <= dt and ut < 10**9 else (-SL_B * atr / e) if dt < 10**9 else cl[-1] / e - 1
    else:
        dn, up = e - TP_A * atr, e + SL_B * atr
        dt = np.argmax(lo <= dn) if (lo <= dn).any() else 10**9; ut = np.argmax(hi >= up) if (hi >= up).any() else 10**9
        g = (TP_A * atr / e) if dt <= ut and dt < 10**9 else (-SL_B * atr / e) if ut < 10**9 else e / cl[-1] - 1
    return g - FORWARD_COST


def quality(ind, i, mamat, side):
    o = float(ind["open"].iloc[i]); c = float(ind["close"].iloc[i])
    h = float(ind["high"].iloc[i]); lo = float(ind["low"].iloc[i]); atr = float(ind["atr14"].iloc[i])
    if atr <= 0:
        return None
    col = mamat[:, i]
    hi20 = ind["high"].to_numpy()[max(0, i - 19):i + 1]; lo20 = ind["low"].to_numpy()[max(0, i - 19):i + 1]
    hi48 = ind["high"].to_numpy()[max(0, i - 47):i + 1]; lo48 = ind["low"].to_numpy()[max(0, i - 47):i + 1]
    vol = float(ind["volume"].iloc[i]); vavg = float(ind["volume"].to_numpy()[max(0, i - 19):i + 1].mean())
    center = float(np.mean(col)); center_prev = float(np.mean(mamat[:, i - 12])) if i >= 12 else center
    ma_roll = (center - center_prev) / (12 * c) if c > 0 else 0.0
    highs = hi20; lows = lo20
    if side > 0:  # LONG quality (breakout up)
        return {"bu_height": (c - col.max()) / atr, "bu_force": (c - o) / atr,
                "bd_range": (h - lo) / atr, "vol_break": vol / vavg if vavg > 0 else 1.0,
                "ma_roll": ma_roll, "higher_lows": float(np.mean(lows[1:] > lows[:-1])) if len(lows) > 1 else 0.0,
                "from_low20": (c - float(lo20.min())) / atr, "room_to_high48": (float(hi48.max()) - c) / atr,
                "runup20": c / float(lo20.min()) - 1 if len(lo20) else 0.0}
    else:  # SHORT quality (breakdown down)
        return {"bd_depth": (col.min() - c) / atr, "bd_force": (o - c) / atr,
                "bd_range": (h - lo) / atr, "vol_break": vol / vavg if vavg > 0 else 1.0,
                "ma_roll": ma_roll, "lower_highs": float(np.mean(highs[1:] < highs[:-1])) if len(highs) > 1 else 0.0,
                "from_high20": (float(hi20.max()) - c) / atr, "room_to_low48": (c - float(lo48.min())) / atr,
                "dd48": c / float(hi20.max()) - 1 if len(hi20) else 0.0}


def pf(x):
    x = np.asarray(x); w, l = x[x > 0].sum(), x[x < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def st(x):
    x = np.asarray(x)
    return {"n": int(len(x)), "PF": pf(x), "mean_bps": round(float(x.mean()) * 1e4, 1)} if len(x) else {"n": 0}


def build(side):
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
            if not np.all(np.isfinite(col)):
                continue
            if side > 0 and not (close[i] > col.max()):
                continue
            if side < 0 and not (close[i] < col.min()):
                continue
            q = quality(ind, i, mamat, side); net = net_dir(ind, i, side)
            if q is None or net is None:
                continue
            row = dict(q)
            for srcf in (broad, geom):
                rr = srcf.iloc[i]
                for c in srcf.columns:
                    row[c] = float(rr[c]) if np.isfinite(rr[c]) else 0.0
            for c in ["btc_ret24", "btc_ret96", "btc_above_ema200", "btc_atr_pct"]:
                row[c] = float(r[c]) if c in r and np.isfinite(r[c]) else 0.0
            row["net"] = net; row["signal_time"] = str(r["t"])
            rows.append(row)
    df = pd.DataFrame(rows).sort_values("signal_time").reset_index(drop=True)
    F = [c for c in df.columns if c not in {"net", "signal_time"}]
    for c in F:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df, F


def judge_wf(df, F, frac=0.2):
    P = {"objective": "regression", "num_leaves": 31, "learning_rate": 0.03, "min_data_in_leaf": 30,
         "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5, "verbose": -1}
    n = len(df); picks = []  # (net, signal_time) chosen by judgment top-frac
    res = []
    for a, b, c in [(0.0, 0.5, 0.65), (0.0, 0.65, 0.8), (0.0, 0.8, 1.0)]:
        tr, te = df.iloc[int(n*a):int(n*b)], df.iloc[int(n*b):int(n*c)]
        if len(tr) < 100 or len(te) < 25:
            res.append({"n": len(te)}); continue
        bo = lgb.train(P, lgb.Dataset(tr[F], label=tr["net"]), num_boost_round=250)
        s = bo.predict(te[F]); order = np.argsort(-s); k = max(int(len(te)*frac), 1)
        chosen = te.iloc[order[:k]]
        res.append({"start": te["signal_time"].iloc[0][:10], "raw": st(te["net"]), "top": st(chosen["net"].to_numpy())})
        for _, rr in chosen.iterrows():
            picks.append((float(rr["net"]), str(rr["signal_time"])))
    return res, picks


def main() -> int:
    dl, Fl = build(+1); ds, Fs = build(-1)
    print(f"LONG candidates(above all MA)={len(dl)}  SHORT candidates(below all MA)={len(ds)}")
    long_wf, long_picks = judge_wf(dl, Fl)
    short_wf, short_picks = judge_wf(ds, Fs)
    # combined: both sides' judgment-picked trades, bucketed by the same 3 periods
    allp = sorted(long_picks + short_picks, key=lambda x: x[1])
    m = len(allp); comb = []
    for a, b in [(0.35, 0.55), (0.55, 0.72), (0.72, 1.0)]:  # rough period alignment
        seg = [p[0] for p in allp[int(m*a):int(m*b)]]
        comb.append(st(np.array(seg)))
    out = {"exit": "TP5/SL2",
           "LONG_side": {"n": len(dl), "wf": long_wf},
           "SHORT_side": {"n": len(ds), "wf": short_wf},
           "COMBINED_both_judged": comb}
    (PROJECT / "analysis" / "output" / "it09_both_sides.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print("LONG :", [f"{w.get('start','?')}:raw{w.get('raw',{}).get('PF')}/top{w.get('top',{}).get('PF')}" for w in long_wf])
    print("SHORT:", [f"{w.get('start','?')}:raw{w.get('raw',{}).get('PF')}/top{w.get('top',{}).get('PF')}" for w in short_wf])
    print("COMBINED both-judged 3 buckets PF:", [c.get("PF") for c in comb], "n:", [c.get("n") for c in comb])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
