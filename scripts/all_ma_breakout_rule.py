"""Owner rule: at the v16 tip, close ABOVE all 6 MAs -> long; BELOW all -> short;
mixed (price tangled in the bundle) -> SKIP. TP3/SL1.

This is a selection+direction rule not tested before: only trade the clean
breakout cases (price clear of the whole MA bundle), skip the ambiguous middle.
Tested on v16's actual detections AND on rule-dense, walk-forward, maker cost.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(PROJECT))

from src.costs import FORWARD_COST  # noqa: E402
from src.data.loader import iter_series, list_series, load_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.labeling import ATR_PCT_MIN  # noqa: E402

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
FAST_MAX, FULL_MAX, MIN_DENSE, MIN_GAP = 0.0028, 0.0055, 5, 18
HORIZ, TP_A, SL_B = 72, 5.0, 2.0  # back to original TP5/SL2
MA_P = (20, 60, 120)


def all_ma(frame):
    c = frame["close"].astype(float)
    mas = []
    for p in MA_P:
        mas.append(c.rolling(p).mean().to_numpy())
        mas.append(c.ewm(span=p, adjust=False).mean().to_numpy())
    return np.vstack(mas), c.to_numpy()  # (6, n), close


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


def st(rows):
    if not rows:
        return {"n": 0}
    net = np.array([r[0] for r in rows]); ts = [r[1] for r in rows]
    w, l = net[net > 0].sum(), net[net < 0].sum()
    out = {"n": len(net), "win": round(float((net > 0).mean()), 3),
           "PF": round(float(w / -l), 3) if l < 0 else None, "mean_bps": round(float(net.mean()) * 1e4, 1)}
    # walk-forward by 3 time buckets
    order = np.argsort(ts); net = net[order]
    thirds = []
    for a, b in [(0, 1/3), (1/3, 2/3), (2/3, 1)]:
        seg = net[int(len(net)*a):int(len(net)*b)]
        if len(seg):
            ww, ll = seg[seg > 0].sum(), seg[seg < 0].sum()
            thirds.append(round(float(ww/-ll), 3) if ll < 0 else None)
    out["thirds_PF"] = thirds
    return out


def run(candidates):
    """candidates: dict symbol -> list of signal bar indices. Returns rule stats."""
    long_r, short_r, skip_n = [], [], 0
    for sym, (ind, mamat, close, ii) in candidates.items():
        t = pd.to_datetime(ind["open_time"], utc=True)
        for i in ii:
            col = mamat[:, i]
            if not np.all(np.isfinite(col)):
                continue
            above = close[i] > col.max()
            below = close[i] < col.min()
            if not (above or below):
                skip_n += 1
                continue
            d = +1 if above else -1
            net = tight_net(ind, i, d)
            if net is None:
                continue
            (long_r if d > 0 else short_r).append((net, str(t.iloc[i])))
    return {"long(above_all)": st(long_r), "short(below_all)": st(short_r),
            "traded_total": st(long_r + short_r), "skipped_middle": skip_n}


def main() -> int:
    # universe A: rule-dense tips ; universe B: v16 actual fires
    v16 = pd.read_csv(PROJECT / "data" / "v16_candidates_100.csv")
    v16["t"] = pd.to_datetime(v16["signal_time"], utc=True)
    v16map = {s: set(g["t"].astype(str)) for s, g in v16.groupby("symbol")}

    dense_cands, v16_cands = {}, {}
    for src, sym, frame in iter_series(bar="15m", min_bars=500):
        if src != "okx" or not sym.endswith("_USDT_SWAP") or is_stockish(sym) or is_eval_symbol(sym):
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        frame = frame[times < HOLDOUT_START].reset_index(drop=True)
        if len(frame) < 500:
            continue
        ema = add_mas(frame); ind = add_indicators(frame)
        mamat, close = all_ma(frame)
        fast = pd.to_numeric(ema["fast_spread"], errors="coerce").to_numpy()
        full = pd.to_numeric(ema["full_spread"], errors="coerce").to_numpy()
        dense = (fast <= FAST_MAX) & (full <= FULL_MAX)
        run_c = 0; ls = -10**9; dtips = []
        for i in range(210, len(frame) - HORIZ - 2):
            run_c = run_c + 1 if dense[i] else 0
            if run_c == MIN_DENSE and i - ls >= MIN_GAP:
                dtips.append(i); ls = i
        if dtips:
            dense_cands[sym] = (ind, mamat, close, dtips)
        if sym in v16map:
            tt = pd.to_datetime(ind["open_time"], utc=True).astype(str)
            tmap = {v: k for k, v in enumerate(tt)}
            vi = [tmap[s] for s in v16map[sym] if s in tmap and tmap[s] < len(frame) - HORIZ - 2]
            if vi:
                v16_cands[sym] = (ind, mamat, close, vi)

    out = {"exit": "TP3/SL1", "rule": "close>all6MA->long; close<all6MA->short; else skip",
           "rule_dense_universe": run(dense_cands), "v16_fire_universe": run(v16_cands)}
    (PROJECT / "analysis" / "output" / "all_ma_breakout_rule.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
