"""Sweep the barrier multiples themselves, not just the four corners.

Earlier work compared exactly four exits: TP5xATR/SL2xATR, TP-only, SL-only, and
no barriers at all. The no-barrier corner won by 8.5x on the pooled train
candidates, which answered "are the barriers costing money" (yes) but not the
question that actually matters -- what to use instead. Jumping from 5:2 to no
stop at all skips the entire middle, and the middle is where a usable answer
probably lives: a stop at 4xATR may capture most of the drift while keeping the
tail bounded, which no stop cannot.

So this sweeps the grid:

  SL     2, 3, 4, 6, 8, 12 x ATR, plus none
  TP     5, 8, 12, 20 x ATR, plus none
  HORIZON  72 bars (today's) and 144, since the hold's whole edge is drift and
           drift is a function of time -- a longer horizon may be doing the work
           that "no barriers" appeared to do.

Two things are reported for every cell, because pooled means have already misled
once here: the RECENT QUARTER (2026-02-20 onward, still inside the train pool) is
scored separately, since the no-barrier edge decayed +0.78% -> +0.46% -> +0.055%
across quarters and the recent number is the honest one to plan against. And the
worst single trade is carried alongside the mean, so a cell that wins by removing
the stop cannot hide what it costs when it is wrong.

Barrier parameters are owner decisions; this measures the space and adopts
nothing. Read-only, train pool (<2026-05-04), no holdout, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_barrier_grid.py
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
from src.detection.data import add_mas  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402

POOL = PROJECT / "data" / "judgment_yolo_short_v6_wide.csv"
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
RECENT_FROM = pd.Timestamp("2026-02-20", tz="UTC")   # the Q4 block, train side
SL_GRID = (2.0, 3.0, 4.0, 6.0, 8.0, 12.0, None)
TP_GRID = (5.0, 8.0, 12.0, 20.0, None)
HORIZONS = (72, 144)


def outcomes(ind, i: int, horizon: int) -> np.ndarray | None:
    """Path arrays for one signal, shared across every grid cell."""
    ei = i + 1
    if ei >= len(ind):
        return None
    atr = float(ind["atr14"].iloc[i])
    entry = float(ind["open"].iloc[ei])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(entry) or entry <= 0:
        return None
    last = min(ei + horizon - 1, len(ind) - 1)
    if last - ei + 1 < horizon:
        return None
    return (entry, atr,
            ind["high"].to_numpy()[ei:last + 1],
            ind["low"].to_numpy()[ei:last + 1],
            ind["close"].to_numpy()[ei:last + 1])


def close_trade(entry, atr, hi, lo, cl, sl_m, tp_m) -> float:
    tp = entry - tp_m * atr if tp_m else -np.inf
    sl = entry + sl_m * atr if sl_m else np.inf
    up = int(np.argmax(lo <= tp)) if tp_m and (lo <= tp).any() else len(cl)
    dn = int(np.argmax(hi >= sl)) if sl_m and (hi >= sl).any() else len(cl)
    if up < dn:
        return 1 - tp / entry
    if dn < up:
        return 1 - sl / entry
    return 1 - float(cl[-1]) / entry


def main() -> int:
    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    d = d[d["t"] < HOLDOUT_START]
    series = list_series(bar="15m")
    cache: dict[str, object] = {}
    sigs = []
    for sym, grp in d.groupby("symbol"):
        key = ("okx", sym)
        if sym not in cache:
            cache[sym] = add_indicators(add_mas(load_series(series[key]))) if key in series else None
        ind = cache[sym]
        if ind is None:
            continue
        times = pd.to_datetime(ind["open_time"], utc=True)
        for _, r in grp.iterrows():
            i = int(times.searchsorted(r["t"]))
            if 200 <= i < len(ind) - 2:
                sigs.append((ind, i, r["t"]))
    print(f"训练池候选 {len(sigs)}   近期块 = {str(RECENT_FROM)[:10]} 起\n")

    results = []
    for horizon in HORIZONS:
        paths = []
        for ind, i, t in sigs:
            o = outcomes(ind, i, horizon)
            if o is not None:
                paths.append((o, t))
        if not paths:
            continue
        recent = np.array([t >= RECENT_FROM for _o, t in paths])
        print(f"=== 持仓上限 {horizon} 根 ({horizon*15/60:.0f} 小时) · "
              f"{len(paths)} 笔,其中近期 {int(recent.sum())} 笔 ===")
        print(f"{'止损':>6} {'止盈':>6} {'全池净':>10} {'PF':>6} {'最差单笔':>9} "
              f"| {'近期净':>10} {'近期PF':>7}")
        for sl_m in SL_GRID:
            for tp_m in TP_GRID:
                r = np.array([close_trade(*o, sl_m, tp_m) for o, _t in paths]) - SWAP_TAKER
                rr = r[recent]
                def pf(x):
                    w, l = x[x > 0].sum(), x[x < 0].sum()
                    return round(float(w / -l), 3) if l < 0 else None
                cell = {"horizon": horizon,
                        "sl": sl_m, "tp": tp_m, "n": len(r),
                        "net": round(float(r.mean()), 6), "pf": pf(r),
                        "worst": round(float(r.min()), 4),
                        "recent_n": int(recent.sum()),
                        "recent_net": round(float(rr.mean()), 6) if len(rr) else None,
                        "recent_pf": pf(rr) if len(rr) else None}
                results.append(cell)
                print(f"{('无' if sl_m is None else f'{sl_m:g}x'):>6} "
                      f"{('无' if tp_m is None else f'{tp_m:g}x'):>6} "
                      f"{r.mean()*100:>+9.4f}% {str(pf(r)):>6} {r.min()*100:>+8.1f}% "
                      f"| {rr.mean()*100:>+9.4f}% {str(pf(rr)):>7}")
        print()

    base = next((c for c in results
                 if c["horizon"] == 72 and c["sl"] == 2.0 and c["tp"] == 5.0), None)
    ranked = sorted([c for c in results if c["recent_net"] is not None],
                    key=lambda c: -c["recent_net"])
    def mult(v) -> str:
        return "无" if v is None else f"{v:g}x"

    print("=== 按【近期块】净收益排序,前 8 ===")
    print(f"{'持仓':>5} {'止损':>6} {'止盈':>6} {'近期净':>10} {'近期PF':>7} "
          f"{'全池净':>10} {'最差单笔':>9}")
    for c in ranked[:8]:
        print(f"{c['horizon']:>5} {mult(c['sl']):>6} {mult(c['tp']):>6} "
              f"{c['recent_net']*100:>+9.4f}% {str(c['recent_pf']):>7} "
              f"{c['net']*100:>+9.4f}% {c['worst']*100:>+8.1f}%")

    best = ranked[0]
    vs_base = f" vs 现行 5:2/72 的 {base['recent_net']*100:+.4f}%" if base else ""
    verdict = (f"近期块最优 = 持仓{best['horizon']}根 / 止损{mult(best['sl'])}ATR / "
               f"止盈{mult(best['tp'])}ATR,近期净 {best['recent_net']*100:+.4f}%"
               f"{vs_base},最差单笔 {best['worst']*100:.1f}%")
    print(f"\n判读: {verdict}")
    print("注:全部训练池样本内;障碍参数属 owner 决策,本脚本只测不改。")

    (PROJECT / "analysis" / "output" / "diag_barrier_grid.json").write_text(
        json.dumps({"pool": POOL.name, "recent_from": str(RECENT_FROM)[:10],
                    "baseline": base, "cells": results, "verdict": verdict},
                   indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
