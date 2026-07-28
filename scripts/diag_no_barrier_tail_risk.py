"""Holding without a stop beat every barrier -- is the tail survivable?

The barrier sweep produced a result that reads like an improvement and could be a
trap. Mean net per trade, same 5802 candidates:

  TP5xATR / SL2xATR (today)   +0.0312%
  TP only, no stop            +0.1711%
  SL only, no TP              +0.0756%
  no barrier, hold 72 bars    +0.2645%   PF 1.255

A mean is exactly the statistic that hides ruin. Removing the stop on a SHORT
means the loss is unbounded -- one squeeze can erase hundreds of trades, and the
average will still look fine right up until the account does not. So before the
number is allowed to mean anything, the distribution has to be examined:

  TAIL     worst trade, 1st/5th percentile, share of trades worse than -10%/-20%
  EQUITY   the actual compounded path at a fixed fraction, and its max drawdown
  MARGIN   worst adverse excursion while the position is open, which is what a
           real position gets liquidated on, not the exit price

Reported beside the current barriers so the comparison is like-for-like. If the
no-barrier edge comes with a tail that a real account cannot hold, the honest
conclusion is that the stop is paying for survivability, not destroying value.

Barrier parameters are owner decisions; this measures and adopts nothing.
Read-only, train pool, no holdout, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_no_barrier_tail_risk.py
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
TP_MULT, SL_MULT, HORIZON = 5.0, 2.0, 72
RISK_FRACTIONS = (0.02, 0.05, 0.10)      # fraction of equity per trade


def simulate(ind, i: int, use_tp: bool, use_sl: bool) -> dict | None:
    """One short. Also records the worst adverse excursion while open."""
    ei = i + 1
    if ei >= len(ind):
        return None
    atr = float(ind["atr14"].iloc[i])
    entry = float(ind["open"].iloc[ei])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(entry) or entry <= 0:
        return None
    last = min(ei + HORIZON - 1, len(ind) - 1)
    hi = ind["high"].to_numpy()[ei:last + 1]
    lo = ind["low"].to_numpy()[ei:last + 1]
    cl = ind["close"].to_numpy()[ei:last + 1]
    if len(cl) < 2:
        return None

    tp = entry - TP_MULT * atr if use_tp else -np.inf
    sl = entry + SL_MULT * atr if use_sl else np.inf
    up = int(np.argmax(lo <= tp)) if use_tp and (lo <= tp).any() else len(cl)
    dn = int(np.argmax(hi >= sl)) if use_sl and (hi >= sl).any() else len(cl)
    k = min(up, dn, len(cl) - 1)
    if up < dn:
        ret, why = 1 - tp / entry, "TP"
    elif dn < up:
        ret, why = 1 - sl / entry, "SL"
    else:
        ret, why = 1 - cl[-1] / entry, "TIMEOUT"
    # a short's pain is price going UP: worst excursion = highest high while open
    mae = float(np.max(hi[: k + 1]) / entry - 1)
    return {"ret": ret, "why": why, "mae": mae,
            "t": pd.Timestamp(pd.to_datetime(ind["open_time"], utc=True).iloc[i])}


def equity_path(rows: list[dict], frac: float) -> dict:
    """Compound at a fixed fraction of equity, in chronological order."""
    ordered = sorted(rows, key=lambda r: r["t"])
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in ordered:
        eq *= 1 + frac * (r["ret"] - SWAP_TAKER)
        if eq <= 0:
            return {"frac": frac, "final": 0.0, "max_dd": 1.0, "ruined": True}
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    return {"frac": frac, "final": round(eq, 4), "max_dd": round(mdd, 4),
            "ruined": False}


def describe(name: str, rows: list[dict]) -> dict:
    ret = np.array([r["ret"] for r in rows]) - SWAP_TAKER
    mae = np.array([r["mae"] for r in rows])
    w, l = ret[ret > 0].sum(), ret[ret < 0].sum()
    d = {"variant": name, "n": len(rows),
         "mean_net": round(float(ret.mean()), 6),
         "pf": round(float(w / -l), 3) if l < 0 else None,
         "worst": round(float(ret.min()), 4),
         "p01": round(float(np.percentile(ret, 1)), 4),
         "p05": round(float(np.percentile(ret, 5)), 4),
         "share_lt_-10pct": round(float((ret < -0.10).mean()), 4),
         "share_lt_-20pct": round(float((ret < -0.20).mean()), 4),
         "mae_p95": round(float(np.percentile(mae, 95)), 4),
         "mae_worst": round(float(mae.max()), 4),
         "equity": [equity_path(rows, f) for f in RISK_FRACTIONS]}
    return d


def main() -> int:
    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    series = list_series(bar="15m")
    cache: dict[str, tuple | None] = {}
    sigs = []
    for sym, grp in d.groupby("symbol"):
        if sym not in cache:
            key = ("okx", sym)
            cache[sym] = ((add_indicators(add_mas(load_series(series[key]))),) if key in series else None)
        e = cache[sym]
        if e is None:
            continue
        ind = e[0]
        times = pd.to_datetime(ind["open_time"], utc=True)
        for _, r in grp.iterrows():
            i = int(times.searchsorted(r["t"]))
            if 200 <= i < len(ind) - 2:
                sigs.append((ind, i))
    print(f"候选 {len(sigs)}\n")

    variants = [("现行 TP5/SL2", True, True), ("只止盈无止损", True, False),
                ("只止损无止盈", False, True), ("无障碍持72根", False, False)]
    out = []
    for name, utp, usl in variants:
        rows = [r for r in (simulate(ind, i, utp, usl) for ind, i in sigs) if r]
        out.append(describe(name, rows))

    print(f"{'方案':<16} {'净均值':>9} {'PF':>6} {'最差单笔':>10} {'1%分位':>9} "
          f"{'<-10%占比':>10} {'<-20%占比':>10} {'持仓最大逆行p95':>16}")
    for r in out:
        print(f"{r['variant']:<16} {r['mean_net']*100:>+8.4f}% {str(r['pf']):>6} "
              f"{r['worst']*100:>+9.1f}% {r['p01']*100:>+8.1f}% "
              f"{r['share_lt_-10pct']*100:>9.2f}% {r['share_lt_-20pct']*100:>9.2f}% "
              f"{r['mae_p95']*100:>15.1f}%")

    print(f"\n=== 按固定仓位比例复利(时间顺序,{len(sigs)} 笔) ===")
    print(f"{'方案':<16}" + "".join(f"{f'仓位{f*100:.0f}%':>22}" for f in RISK_FRACTIONS))
    for r in out:
        line = f"{r['variant']:<16}"
        for e in r["equity"]:
            cell = "爆仓" if e["ruined"] else f"{e['final']:.2f}x 回撤{e['max_dd']*100:.0f}%"
            line += f"{cell:>22}"
        print(line)

    base = out[0]
    nb = out[-1]
    verdict = (f"无障碍均值 {nb['mean_net']*100:+.4f}% 是现行的 "
               f"{nb['mean_net']/max(base['mean_net'],1e-9):.1f} 倍,代价是最差单笔 "
               f"{nb['worst']*100:.1f}%(现行 {base['worst']*100:.1f}%)、"
               f"{nb['share_lt_-10pct']*100:.2f}% 的单子亏超 10%。"
               f"止损买的是尾部,不是收益。")
    print(f"\n判读: {verdict}")
    print("注:障碍参数属 owner 决策;本脚本只测不改,且全部为训练池样本内。")

    (PROJECT / "analysis" / "output" / "diag_no_barrier_tail_risk.json").write_text(
        json.dumps({"pool": POOL.name, "n": len(sigs), "horizon": HORIZON,
                    "results": out, "verdict": verdict},
                   indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
