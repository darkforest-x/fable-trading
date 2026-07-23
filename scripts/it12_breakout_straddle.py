"""IT-12: directionless breakout-straddle entry (fix entry timing + sidestep direction).

The whole session's wall was PICKING the direction. A dense cluster is a
consolidation; the "launch" is the breakout. So don't predict direction -- place
a buy-stop above the consolidation and a sell-stop below it, and let whichever
side breaks first choose the trade. This also fixes the entry-vs-eye mismatch
(Jaccard 0.04): we enter only when the launch actually starts, not blindly at
i+1 open.

Mechanic (causal, realistic stop orders):
  at tip bar i: range = [min low, max high] over last RANGE_BARS bars.
    buy_stop  = range_hi * (1 + BUF*atr/price)   sell_stop = range_lo * (1 - ...)
  for bars i+1..i+TRIG_BARS: first bar whose HIGH>=buy_stop -> enter LONG at
    buy_stop; whose LOW<=sell_stop -> enter SHORT at sell_stop. First touch wins;
    if a bar hits both, treat as ambiguous -> skip (can't tell order intrabar).
  then TP5/SL2 from entry over HORIZON bars. Breakout entry is TAKER -> use taker
  round-trip cost. Walk-forward 3 periods, <2026-05-04.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(PROJECT))

from src.data.loader import list_series, load_series  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.labeling import ATR_PCT_MIN  # noqa: E402

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
HORIZON, TP_A, SL_B = 72, 5.0, 2.0
TAKER_RT = 0.0010  # ~0.05% taker each side; breakout stop-entry is taker


def simulate(ind, i, range_bars, trig_bars, buf):
    atr = float(ind["atr14"].iloc[i]); ap = float(ind["atr_pct"].iloc[i]); px = float(ind["close"].iloc[i])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(ap) or ap < ATR_PCT_MIN or px <= 0:
        return None
    hi = ind["high"].to_numpy(); lo = ind["low"].to_numpy(); cl = ind["close"].to_numpy()
    r0 = max(0, i - range_bars + 1)
    rng_hi = float(hi[r0:i + 1].max()); rng_lo = float(lo[r0:i + 1].min())
    buy_stop = rng_hi + buf * atr; sell_stop = rng_lo - buf * atr
    # find first trigger bar in i+1..i+trig_bars
    entry = None; side = 0; tbar = None
    for j in range(i + 1, min(i + trig_bars, len(ind) - 1) + 1):
        hit_up = hi[j] >= buy_stop; hit_dn = lo[j] <= sell_stop
        if hit_up and hit_dn:
            return {"skip": "ambiguous"}  # both in one bar -> unknown order
        if hit_up:
            entry, side, tbar = buy_stop, 1, j; break
        if hit_dn:
            entry, side, tbar = sell_stop, -1, j; break
    if entry is None:
        return {"skip": "no_breakout"}
    # barriers from entry bar's close onward (enter at stop level on bar tbar)
    last = min(tbar + HORIZON, len(ind) - 1)
    H = hi[tbar:last + 1]; L = lo[tbar:last + 1]; C = cl[tbar:last + 1]
    if len(C) < 4:
        return {"skip": "short_tail"}
    if side > 0:
        up, dn = entry + TP_A * atr, entry - SL_B * atr
        ut = np.argmax(H >= up) if (H >= up).any() else 10**9; dt = np.argmax(L <= dn) if (L <= dn).any() else 10**9
        g = (TP_A * atr / entry) if ut <= dt and ut < 10**9 else (-SL_B * atr / entry) if dt < 10**9 else C[-1] / entry - 1
    else:
        dn, up = entry - TP_A * atr, entry + SL_B * atr
        dt = np.argmax(L <= dn) if (L <= dn).any() else 10**9; ut = np.argmax(H >= up) if (H >= up).any() else 10**9
        g = (TP_A * atr / entry) if dt <= ut and dt < 10**9 else (-SL_B * atr / entry) if ut < 10**9 else entry / C[-1] - 1
    return {"net": g - TAKER_RT, "side": side, "signal_time": str(ind["open_time"].iloc[i])}


def pf(x):
    x = np.asarray(x); w, l = x[x > 0].sum(), x[x < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def st(x):
    x = np.asarray(x)
    return {"n": int(len(x)), "PF": pf(x), "mean_bps": round(float(x.mean()) * 1e4, 1)} if len(x) else {"n": 0}


def run(range_bars, trig_bars, buf):
    v16 = pd.read_csv(PROJECT / "data" / "v16_candidates_100.csv")
    v16["t"] = pd.to_datetime(v16["signal_time"], utc=True)
    rows = []; skips = {"ambiguous": 0, "no_breakout": 0, "short_tail": 0}
    for sym, grp in v16.groupby("symbol"):
        try:
            frame = load_series(list_series(bar="15m")[("okx", sym)])
        except Exception:
            continue
        ind = add_indicators(frame)
        tmap = {str(v): k for k, v in enumerate(pd.to_datetime(frame["open_time"], utc=True))}
        for _, r in grp.iterrows():
            k = tmap.get(str(r["t"]))
            if k is None or k < 60 or k >= len(frame) - HORIZON - trig_bars - 2:
                continue
            res = simulate(ind, k, range_bars, trig_bars, buf)
            if res is None:
                continue
            if "skip" in res:
                skips[res["skip"]] = skips.get(res["skip"], 0) + 1
                continue
            rows.append(res)
    df = pd.DataFrame(rows).sort_values("signal_time").reset_index(drop=True)
    n = len(df); wf = []
    for a, b in [(0.0, 0.5), (0.5, 0.75), (0.75, 1.0)]:
        seg = df.iloc[int(n * a):int(n * b)]
        wf.append({"start": seg["signal_time"].iloc[0][:10] if len(seg) else "?",
                   "all": st(seg["net"].to_numpy()),
                   "long": st(seg[seg["side"] == 1]["net"].to_numpy()),
                   "short": st(seg[seg["side"] == -1]["net"].to_numpy())})
    return {"params": {"range_bars": range_bars, "trig_bars": trig_bars, "buf_atr": buf},
            "n_trades": n, "n_long": int((df["side"] == 1).sum()), "n_short": int((df["side"] == -1).sum()),
            "skips": skips, "walk_forward": wf}


def main() -> int:
    outs = []
    for rb, tb, bf in [(12, 12, 0.25), (16, 8, 0.25), (20, 12, 0.5)]:
        o = run(rb, tb, bf); outs.append(o)
        pfs = [w["all"].get("PF") for w in o["walk_forward"]]
        print(f"range{rb}/trig{tb}/buf{bf}: ALL PF {pfs}  n={o['n_trades']} (L{o['n_long']}/S{o['n_short']}) "
              f"no_breakout={o['skips']['no_breakout']} ambig={o['skips']['ambiguous']}")
    (PROJECT / "analysis" / "output" / "it12_breakout_straddle.json").write_text(
        json.dumps({"cost": "taker 0.10% RT", "runs": outs}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
