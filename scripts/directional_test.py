"""Layer-1 only screens (dense = compression); it does NOT pick long/short.

Owner (2026-07-23): every prior test baked in LONG. But a dense MA cluster is a
coiled spring that can break either way -- and the measured drift is DOWN
(-40bps), so long-only may have tested the wrong side. Direction is a separate
decision (layer 2 / breakout / discretion). Test it:

  LONG      : buy next open, TP3*ATR up / SL1*ATR down
  SHORT     : sell next open, TP3*ATR down / SL1*ATR up
  BREAKOUT  : within +6 bars, first close beyond the pre-entry 20-bar range ->
              trade that direction (causal direction confirmation)
  ORACLE    : whichever of long/short won (CEILING -- unreachable, diagnostic):
              if oracle is high but long/short/breakout thin -> the edge exists
              but lives in DIRECTION PREDICTION (the missing layer-2 job).

Dense-rule candidates, <2026-05-04, maker cost. Reports PF/mean per direction.
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
    """direction +1 long / -1 short. First-touch TP3*ATR vs SL1*ATR."""
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


def outcomes(ind, i):
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
    if len(cl) < 8:
        return None
    L = _tpsl(entry, atr, hi, lo, cl, +1)
    S = _tpsl(entry, atr, hi, lo, cl, -1)
    # breakout: pre-entry 20-bar range (causal, bars < entry_i)
    prehi = float(ind["high"].to_numpy()[max(0, i - 19):i + 1].max())
    prelo = float(ind["low"].to_numpy()[max(0, i - 19):i + 1].min())
    bdir = 0; bentry = None
    for j in range(entry_i, min(entry_i + 6, len(ind))):
        c = float(ind["close"].iloc[j])
        if c > prehi:
            bdir = +1; bentry = j; break
        if c < prelo:
            bdir = -1; bentry = j; break
    B = None
    if bdir != 0 and bentry + 1 < len(ind):
        be = float(ind["open"].iloc[bentry + 1])
        ll = min(bentry + 1 + HORIZ - 1, len(ind) - 1)
        bh = ind["high"].to_numpy()[bentry + 1:ll + 1]
        bl = ind["low"].to_numpy()[bentry + 1:ll + 1]
        bc = ind["close"].to_numpy()[bentry + 1:ll + 1]
        if len(bc) >= 4 and be > 0:
            B = _tpsl(be, atr, bh, bl, bc, bdir)
    return {"long": L, "short": S, "breakout": B, "oracle": max(L, S)}


def stats(x):
    x = np.asarray([v for v in x if v is not None])
    if not len(x):
        return {"n": 0}
    w, l = x[x > 0].sum(), x[x < 0].sum()
    return {"n": int(len(x)), "win": round(float((x > 0).mean()), 3),
            "PF": round(float(w / -l), 3) if l < 0 else None,
            "mean_bps": round(float(x.mean()) * 1e4, 1)}


def main() -> int:
    L, S, B, O = [], [], [], []
    for src, sym, frame in iter_series(bar="15m", min_bars=500):
        if src != "okx" or not sym.endswith("_USDT_SWAP") or is_stockish(sym) or is_eval_symbol(sym):
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        frame = frame[times < HOLDOUT_START].reset_index(drop=True)
        if len(frame) < 500:
            continue
        ema = add_mas(frame); ind = add_indicators(frame)
        fast = pd.to_numeric(ema["fast_spread"], errors="coerce").to_numpy()
        full = pd.to_numeric(ema["full_spread"], errors="coerce").to_numpy()
        dense = (fast <= FAST_MAX) & (full <= FULL_MAX)
        run = 0; last_sig = -10**9
        for i in range(210, len(frame) - HORIZ - 2):
            run = run + 1 if dense[i] else 0
            if run == MIN_DENSE and i - last_sig >= MIN_GAP:
                o = outcomes(ind, i)
                if o:
                    L.append(o["long"]); S.append(o["short"])
                    B.append(o["breakout"]); O.append(o["oracle"])
                    last_sig = i
    out = {"exit": "TP3xATR/SL1xATR", "n_signals": len(L),
           "LONG": stats(L), "SHORT": stats(S),
           "BREAKOUT_confirmed": stats(B), "ORACLE_ceiling": stats(O)}
    (PROJECT / "analysis" / "output" / "directional_test.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
