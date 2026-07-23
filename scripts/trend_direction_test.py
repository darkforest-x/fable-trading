"""Direction by TREND rules (owner: direction is easy — trade with the trend).

Dense cluster = consolidation; the intuitive human rule is trade the direction
of the prevailing trend (flag/pennant continues the trend). Test simple causal
direction rules and compare to the oracle ceiling (2.68) and always-long/short.

Rules at signal bar i (causal): long if condition else short, then TP3/SL1.
  ema200  : close > EMA200
  ema120  : close > EMA120
  slope55 : EMA55 rising over last 12 bars
  mom24   : ret over last 24 bars > 0
  mom48   : ret over last 48 bars > 0
  combo   : close>EMA200 AND ret24>0 -> long ; close<EMA200 AND ret24<0 -> short ; else skip

<2026-05-04, maker cost.
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
from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.labeling import ATR_PCT_MIN  # noqa: E402

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
FAST_MAX, FULL_MAX, MIN_DENSE, MIN_GAP = 0.0028, 0.0055, 5, 18
HORIZ, TP_A, SL_B = 72, 3.0, 1.0


def _tpsl(entry, atr, hi, lo, cl, direction):
    if direction > 0:
        up, dn = entry + TP_A * atr, entry - SL_B * atr
        ut = np.argmax(hi >= up) if (hi >= up).any() else 10**9
        dt = np.argmax(lo <= dn) if (lo <= dn).any() else 10**9
        g = (TP_A * atr / entry) if ut <= dt and ut < 10**9 else \
            (-SL_B * atr / entry) if dt < 10**9 else (cl[-1] / entry - 1)
    else:
        dn, up = entry - TP_A * atr, entry + SL_B * atr
        dt = np.argmax(lo <= dn) if (lo <= dn).any() else 10**9
        ut = np.argmax(hi >= up) if (hi >= up).any() else 10**9
        g = (TP_A * atr / entry) if dt <= ut and dt < 10**9 else \
            (-SL_B * atr / entry) if ut < 10**9 else (entry / cl[-1] - 1)
    return g - FORWARD_COST


def stats(x):
    x = np.asarray([v for v in x if v is not None])
    if not len(x):
        return {"n": 0}
    w, l = x[x > 0].sum(), x[x < 0].sum()
    return {"n": int(len(x)), "win": round(float((x > 0).mean()), 3),
            "PF": round(float(w / -l), 3) if l < 0 else None,
            "mean_bps": round(float(x.mean()) * 1e4, 1)}


def main() -> int:
    acc = {r: [] for r in ["ema200", "ema120", "slope55", "mom24", "mom48", "combo",
                           "always_long", "always_short", "oracle"]}
    for src, sym, frame in iter_series(bar="15m", min_bars=500):
        if src != "okx" or not sym.endswith("_USDT_SWAP") or is_stockish(sym) or is_eval_symbol(sym):
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        frame = frame[times < HOLDOUT_START].reset_index(drop=True)
        if len(frame) < 500:
            continue
        ema = add_mas(frame); ind = add_indicators(frame)
        c = frame["close"].astype(float)
        ema200 = c.ewm(span=200, adjust=False).mean().to_numpy()
        ema120 = c.ewm(span=120, adjust=False).mean().to_numpy()
        ema55 = c.ewm(span=55, adjust=False).mean().to_numpy()
        cn = c.to_numpy()
        fast = pd.to_numeric(ema["fast_spread"], errors="coerce").to_numpy()
        full = pd.to_numeric(ema["full_spread"], errors="coerce").to_numpy()
        dense = (fast <= FAST_MAX) & (full <= FULL_MAX)
        run = 0; last_sig = -10**9
        for i in range(210, len(frame) - HORIZ - 2):
            run = run + 1 if dense[i] else 0
            if not (run == MIN_DENSE and i - last_sig >= MIN_GAP):
                continue
            entry_i = i + 1
            atr = float(ind["atr14"].iloc[i]); atr_pct = float(ind["atr_pct"].iloc[i])
            if not np.isfinite(atr) or atr <= 0 or not np.isfinite(atr_pct) or atr_pct < ATR_PCT_MIN:
                continue
            entry = float(ind["open"].iloc[entry_i])
            if entry <= 0:
                continue
            last = min(entry_i + HORIZ - 1, len(ind) - 1)
            hi = ind["high"].to_numpy()[entry_i:last + 1]
            lo = ind["low"].to_numpy()[entry_i:last + 1]
            cl = ind["close"].to_numpy()[entry_i:last + 1]
            if len(cl) < 8:
                continue
            last_sig = i
            L = _tpsl(entry, atr, hi, lo, cl, +1)
            S = _tpsl(entry, atr, hi, lo, cl, -1)
            acc["always_long"].append(L); acc["always_short"].append(S)
            acc["oracle"].append(max(L, S))
            ret24 = cn[i] / cn[i - 24] - 1 if i >= 24 else 0.0
            ret48 = cn[i] / cn[i - 48] - 1 if i >= 48 else 0.0
            slope55 = ema55[i] - ema55[i - 12] if i >= 12 else 0.0
            acc["ema200"].append(L if cn[i] > ema200[i] else S)
            acc["ema120"].append(L if cn[i] > ema120[i] else S)
            acc["slope55"].append(L if slope55 > 0 else S)
            acc["mom24"].append(L if ret24 > 0 else S)
            acc["mom48"].append(L if ret48 > 0 else S)
            if cn[i] > ema200[i] and ret24 > 0:
                acc["combo"].append(L)
            elif cn[i] < ema200[i] and ret24 < 0:
                acc["combo"].append(S)
    out = {"exit": "TP3/SL1", "n": len(acc["always_long"]),
           "rules": {r: stats(v) for r, v in acc.items()}}
    (PROJECT / "analysis" / "output" / "trend_direction_test.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
