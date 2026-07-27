"""How much floating profit does the current exit give back?

Owner's complaint, and it is a design gap rather than a bug: the exit is only
TP 5xATR / SL 2xATR / 72-bar timeout. Nothing closes a trade when the setup that
opened it stops being true. If the MA bundle reverses two bars after entry the
position rides on regardless, and a trade that is +4 ATR in the money can round
trip all the way to -2 ATR and book a stop.

This measures that directly, per trade:

  MFE  maximum favourable excursion -- the best unrealised profit reached before
       the exit, in ATR units
  MAE  maximum adverse excursion, same in the losing direction
  giveback = MFE - realised, i.e. profit that was there and was handed back

Then it prices two exits the project has never tried, on the SAME entries, so
the comparison isolates the exit and nothing else:

  TRAIL   once MFE reaches `arm` ATR, exit if price retraces `give` ATR from the
          best price
  MAFLIP  exit when the fast MA bundle stops being below the slow one, i.e. when
          the reason for the short disappears

Both are strictly causal: they only ever look at bars up to the decision bar.

Read-only, training pool only (ends 2026-05-03), no holdout, no promote.
Barriers themselves are owner decisions -- this measures alternatives, it does
not adopt one.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_mfe_giveback.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.costs import SWAP_TAKER  # noqa: E402
from src.data.loader import list_series, load_series  # noqa: E402
from src.detection.data import ALL_MA_COLS, add_mas  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402

POOL = PROJECT / "data" / "judgment_yolo_short_v6_wide.csv"
TP_MULT, SL_MULT, HORIZON = 5.0, 2.0, 72


def pf(x):
    x = np.asarray(x)
    w, l = x[x > 0].sum(), x[x < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def simulate(ind, ma_fast, ma_slow, i: int, mode: str,
             arm: float = 1.5, give: float = 1.0) -> dict | None:
    """Replay one short from bar i under a given exit rule. Causal throughout."""
    ei = i + 1
    if ei >= len(ind):
        return None
    atr = float(ind["atr14"].iloc[i])
    if not np.isfinite(atr) or atr <= 0:
        return None
    entry = float(ind["open"].iloc[ei])
    if entry <= 0:
        return None
    last = min(ei + HORIZON - 1, len(ind) - 1)
    hi = ind["high"].to_numpy()[ei:last + 1]
    lo = ind["low"].to_numpy()[ei:last + 1]
    cl = ind["close"].to_numpy()[ei:last + 1]
    if len(cl) < 2:
        return None
    tp, sl = entry - TP_MULT * atr, entry + SL_MULT * atr
    best = entry                      # best (lowest) price seen, short side
    mfe = mae = 0.0
    for j in range(len(cl)):
        best = min(best, lo[j])
        mfe = max(mfe, (entry - lo[j]) / atr)
        mae = max(mae, (hi[j] - entry) / atr)
        # baseline barriers first, so every mode shares the same hard stops
        if lo[j] <= tp:
            return {"mode": mode, "ret": 1 - tp / entry, "bars": j, "why": "TP",
                    "mfe": mfe, "mae": mae}
        if hi[j] >= sl:
            return {"mode": mode, "ret": 1 - sl / entry, "bars": j, "why": "SL",
                    "mfe": mfe, "mae": mae}
        if mode == "trail" and mfe >= arm and (hi[j] - best) / atr >= give:
            px = best + give * atr
            return {"mode": mode, "ret": 1 - px / entry, "bars": j, "why": "TRAIL",
                    "mfe": mfe, "mae": mae}
        if mode == "maflip":
            k = ei + j
            if k < len(ma_fast) and np.isfinite(ma_fast[k]) and np.isfinite(ma_slow[k]):
                if ma_fast[k] > ma_slow[k]:        # short thesis gone
                    px = cl[j]
                    return {"mode": mode, "ret": 1 - px / entry, "bars": j,
                            "why": "MAFLIP", "mfe": mfe, "mae": mae}
    return {"mode": mode, "ret": 1 - cl[-1] / entry, "bars": len(cl) - 1,
            "why": "TIMEOUT", "mfe": mfe, "mae": mae}


def main() -> int:
    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    series = list_series(bar="15m")
    cache: dict[str, tuple | None] = {}
    rows: list[dict] = []

    for sym, grp in d.groupby("symbol"):
        if sym not in cache:
            key = ("okx", sym)
            if key in series:
                base = load_series(series[key])
                framed = add_mas(base)
                ind = add_indicators(framed)
                fast = np.nanmean(np.vstack([framed[c].to_numpy(dtype=float)
                                             for c in ("sma20", "ema20")]), axis=0)
                slow = np.nanmean(np.vstack([framed[c].to_numpy(dtype=float)
                                             for c in ("sma120", "ema120")]), axis=0)
                cache[sym] = (ind, pd.to_datetime(framed["open_time"], utc=True), fast, slow)
            else:
                cache[sym] = None
        e = cache[sym]
        if e is None:
            continue
        ind, times, fast, slow = e
        for _, r in grp.iterrows():
            i = int(times.searchsorted(r["t"]))
            if i < 200 or i >= len(ind) - 2:
                continue
            base = simulate(ind, fast, slow, i, "base")
            if base is None:
                continue
            rec = {"symbol": sym, "base_ret": base["ret"], "base_why": base["why"],
                   "mfe": base["mfe"], "mae": base["mae"],
                   "giveback": base["mfe"] - base["ret"] / (base["ret"] / base["mfe"] if base["mfe"] else 1)
                   if False else base["mfe"] - (base["ret"] * 0)}
            # realised in ATR units, for a like-for-like giveback
            atr = float(ind["atr14"].iloc[i]); entry = float(ind["open"].iloc[i + 1])
            rec["realised_atr"] = base["ret"] * entry / atr if atr > 0 else np.nan
            rec["giveback_atr"] = rec["mfe"] - rec["realised_atr"]
            for mode in ("trail", "maflip"):
                alt = simulate(ind, fast, slow, i, mode)
                if alt:
                    rec[f"{mode}_ret"] = alt["ret"]
                    rec[f"{mode}_why"] = alt["why"]
            rows.append(rec)

    x = pd.DataFrame(rows).dropna(subset=["base_ret"])
    print(f"可重放 {len(x)} 笔\n")

    print("=== 浮盈回吐(单位:ATR) ===")
    q = np.percentile(x["mfe"], [25, 50, 75, 90])
    print(f"  MFE 最大浮盈: p25={q[0]:.2f} p50={q[1]:.2f} p75={q[2]:.2f} p90={q[3]:.2f}")
    q = np.percentile(x["giveback_atr"], [25, 50, 75, 90])
    print(f"  回吐 MFE-实现: p25={q[0]:.2f} p50={q[1]:.2f} p75={q[2]:.2f} p90={q[3]:.2f}")
    sl = x[x["base_why"] == "SL"]
    for thr in (1.0, 2.0, 3.0):
        n = int((sl["mfe"] >= thr).sum())
        print(f"  止损单中曾浮盈 >= {thr:.0f} ATR 的: {n}/{len(sl)} = {100*n/len(sl):.1f}%")

    print("\n=== 同一批入场,三种出场对比 ===")
    print(f"{'出场':<10} {'毛均值':>10} {'毛PF':>7} {'净@taker':>11} {'胜率':>8}")
    for mode, col in (("现行 TP/SL", "base_ret"), ("移动止损", "trail_ret"),
                      ("均线反转出", "maflip_ret")):
        if col not in x:
            continue
        r = x[col].dropna().to_numpy()
        print(f"{mode:<10} {r.mean()*100:>+9.3f}% {str(pf(r)):>7} "
              f"{(r.mean()-SWAP_TAKER)*100:>+10.3f}% {100*(r>0).mean():>7.1f}%")

    out = {"n": len(x),
           "mfe_p50": round(float(x["mfe"].median()), 3),
           "giveback_p50": round(float(x["giveback_atr"].median()), 3),
           "sl_with_mfe_ge2": round(float((sl["mfe"] >= 2).mean()), 4),
           "exits": {m: {"gross_mean": round(float(x[c].dropna().mean()), 5),
                         "gross_pf": pf(x[c].dropna().to_numpy()),
                         "net_taker": round(float(x[c].dropna().mean() - SWAP_TAKER), 5)}
                     for m, c in (("base", "base_ret"), ("trail", "trail_ret"),
                                  ("maflip", "maflip_ret")) if c in x}}
    (PROJECT / "analysis" / "output" / "diag_mfe_giveback.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
