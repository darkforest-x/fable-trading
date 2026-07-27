"""Three untested ideas from the 6-MA compression report, on the same entries.

The report proposed three things this project has never tried. All are cheap to
test on the existing 5802-candidate pool, and all are isolated so each one moves
exactly one thing:

  BODY      the breakout bar must be a real body, |close-open|/(high-low) >= 0.5,
            not a long wick. This is an ENTRY FILTER: same exit, fewer trades.
  RETEST    instead of entering at the next open, wait for price to pull back to
            the MA bundle's lower edge and enter there. Fills only if the pullback
            happens within a window; otherwise no trade.
  STRUCT    stop at the MA bundle's upper edge instead of a fixed 2*ATR. Directly
            relevant to today's finding that low-ATR setups get a stop so tight
            that noise takes it out.

Judged on gross PF and net at the taker floor, against the unchanged baseline on
the same candidates. A variant that only lifts the win rate is not an
improvement -- today's MFE work already showed early exits raise win rate while
lowering expectancy, because the +3.6% TP tail carries everything.

Barriers and thresholds are owner decisions; this measures alternatives and
adopts nothing.

Read-only, train pool (ends 2026-05-03), no holdout, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_three_entry_exit_variants.py
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
BODY_MIN = 0.50
RETEST_WAIT = 12          # bars allowed for the pullback to fill
RETEST_TOL = 0.0          # touch of the band's lower edge counts as a fill


def pf(x):
    x = np.asarray(x)
    w, l = x[x > 0].sum(), x[x < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def simulate(ind, ma_lo, ma_hi, i: int, mode: str) -> dict | None:
    """One short from signal bar i. Every mode shares the same TP and horizon."""
    atr = float(ind["atr14"].iloc[i])
    if not np.isfinite(atr) or atr <= 0:
        return None
    o = float(ind["open"].iloc[i]); c = float(ind["close"].iloc[i])
    h = float(ind["high"].iloc[i]); l = float(ind["low"].iloc[i])

    if mode == "body":
        rng = h - l
        if rng <= 0 or abs(c - o) / rng < BODY_MIN:
            return {"skip": True}

    ei = i + 1
    if ei >= len(ind):
        return None
    entry = float(ind["open"].iloc[ei])

    if mode == "retest":
        # wait for price to come back UP to the bundle's lower edge and short there
        filled = None
        for j in range(ei, min(ei + RETEST_WAIT, len(ind))):
            band = ma_lo[j]
            if np.isfinite(band) and float(ind["high"].iloc[j]) >= band * (1 - RETEST_TOL):
                filled = (j, float(band))
                break
        if filled is None:
            return {"skip": True}
        ei, entry = filled[0] + 1, filled[1]
        if ei >= len(ind):
            return None
    if entry <= 0:
        return None

    last = min(ei + HORIZON - 1, len(ind) - 1)
    hi = ind["high"].to_numpy()[ei:last + 1]
    lo = ind["low"].to_numpy()[ei:last + 1]
    cl = ind["close"].to_numpy()[ei:last + 1]
    if len(cl) < 2:
        return None

    tp = entry - TP_MULT * atr
    if mode == "struct":
        # stop rides the bundle's upper edge rather than a fixed 2*ATR
        stop_series = ma_hi[ei:last + 1]
    else:
        stop_series = np.full(len(cl), entry + SL_MULT * atr)

    for j in range(len(cl)):
        if lo[j] <= tp:
            return {"ret": 1 - tp / entry, "why": "TP", "bars": j}
        s = stop_series[j]
        if np.isfinite(s) and hi[j] >= s:
            return {"ret": 1 - float(s) / entry, "why": "SL", "bars": j}
    return {"ret": 1 - cl[-1] / entry, "why": "TIMEOUT", "bars": len(cl) - 1}


def main() -> int:
    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    series = list_series(bar="15m")
    cache: dict[str, tuple | None] = {}
    modes = ("base", "body", "retest", "struct")
    rows: dict[str, list] = {m: [] for m in modes}
    skipped = {m: 0 for m in modes}

    for sym, grp in d.groupby("symbol"):
        if sym not in cache:
            key = ("okx", sym)
            if key in series:
                framed = add_mas(load_series(series[key]))
                ind = add_indicators(framed)
                ma = np.vstack([framed[c].to_numpy(dtype=float)
                                for c in ALL_MA_COLS if c in framed.columns])
                cache[sym] = (ind, pd.to_datetime(framed["open_time"], utc=True),
                              np.nanmin(ma, axis=0), np.nanmax(ma, axis=0))
            else:
                cache[sym] = None
        e = cache[sym]
        if e is None:
            continue
        ind, times, ma_lo, ma_hi = e
        for _, r in grp.iterrows():
            i = int(times.searchsorted(r["t"]))
            if i < 200 or i >= len(ind) - 2:
                continue
            for m in modes:
                out = simulate(ind, ma_lo, ma_hi, i, m)
                if out is None:
                    continue
                if out.get("skip"):
                    skipped[m] += 1
                    continue
                rows[m].append(out)

    print(f"候选 {len(d)}   基线成交 {len(rows['base'])}\n")
    print(f"{'方案':<22} {'成交':>7} {'跳过':>7} {'胜率':>8} {'毛PF':>7} {'净@taker':>11}")
    out_rows = []
    names = {"base": "现行(下根开盘/2ATR止损)", "body": "实体质量 >=50%",
             "retest": "回踩均线束入场", "struct": "结构止损(均线束上沿)"}
    for m in modes:
        rs = rows[m]
        if not rs:
            print(f"{names[m]:<22} {0:>7}  无成交")
            continue
        ret = np.array([x["ret"] for x in rs])
        wr = float(np.mean([x["why"] == "TP" for x in rs]))
        out_rows.append({"mode": m, "n": len(rs), "skipped": skipped[m],
                         "win_tp": round(wr, 4), "gross_pf": pf(ret),
                         "net_taker": round(float(ret.mean() - SWAP_TAKER), 5)})
        print(f"{names[m]:<22} {len(rs):>7} {skipped[m]:>7} {wr*100:>7.1f}% "
              f"{str(pf(ret)):>7} {(ret.mean()-SWAP_TAKER)*100:>+10.3f}%")

    base = next((r for r in out_rows if r["mode"] == "base"), None)
    better = [r for r in out_rows
              if r["mode"] != "base" and base and r["net_taker"] > base["net_taker"]]
    verdict = ("胜过基线的方案: " + ", ".join(f"{names[r['mode']]}({r['net_taker']*100:+.3f}%)"
                                          for r in better)
               if better else "三条都没有胜过现行方案")
    print(f"\n判读: {verdict}")
    print("注:只看胜率上升不算改进 —— 今天 MFE 分析已证明提前出场会抬胜率、降期望。")

    (PROJECT / "analysis" / "output" / "diag_three_entry_exit_variants.json").write_text(
        json.dumps({"pool": POOL.name, "n_candidates": len(d),
                    "body_min": BODY_MIN, "retest_wait": RETEST_WAIT,
                    "results": out_rows, "verdict": verdict},
                   indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
