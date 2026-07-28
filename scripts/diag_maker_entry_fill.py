"""Can any leg be maker? Measure fill rate against the 4bp it would save.

Today all three legs are taker: entry is ordType="market", and the OCO carries
tpOrdPx/slOrdPx="-1" so both exits fire at market on trigger. At OKX SWAP rates
that is 0.05% x 2 = 10bp round trip against a holdout gross edge of 11.7bp, so
cost eats 85% of the edge. Maker is 0.02%, so each leg converted saves ~4bp --
the single largest available improvement that requires no new alpha.

The catch is that "saves 4bp" is only true on trades that still happen. A short
entry as maker means resting a SELL above the market, which fills only if price
ticks up first; on a breakdown that runs straight down, the order never fills and
the trade is missed. Missing winners can cost far more than 4bp, so fill rate
alone is not the answer -- what matters is net expectancy over the whole pool,
counting missed trades as zero.

Three legs, measured separately because they behave differently:

  ENTRY   rest a sell at entry*(1+offset) for a few bars. Fills on a wick up.
          Offsets swept from 0 (touch the open) to 20bp.
  TP      a short's take-profit is a BUY below market -- a resting limit is
          natural there, and tpOrdPx="-1" throws that away for nothing. Converting
          it costs no fill risk beyond the trigger already being a limit level.
  SL      a stop chases price by construction and must stay taker. Included only
          to state the floor.

Measured on the same candidates the rest of the work uses, so the numbers compose
with the existing net-per-trade figures.

Cost assumptions are owner decisions (CLAUDE.md escalation rule); this measures
alternatives and adopts nothing. Read-only, train pool, no holdout, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_maker_entry_fill.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.costs import SWAP_MAKER, SWAP_TAKER  # noqa: E402
from src.data.loader import list_series, load_series  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402

POOL = PROJECT / "data" / "judgment_yolo_short_v6_wide.csv"
TP_MULT, SL_MULT, HORIZON = 5.0, 2.0, 72
OFFSETS_BP = (0.0, 1.0, 2.0, 5.0, 10.0, 20.0)
FILL_WAIT = (1, 2, 4)          # bars the resting entry is allowed to sit


def simulate(ind, i: int, offset_bp: float, wait: int) -> dict | None:
    """One short. Entry rests at open*(1+offset); unfilled = no trade."""
    atr = float(ind["atr14"].iloc[i])
    if not np.isfinite(atr) or atr <= 0:
        return None
    ei = i + 1
    if ei >= len(ind):
        return None
    ref = float(ind["open"].iloc[ei])
    if not np.isfinite(ref) or ref <= 0:
        return None
    limit = ref * (1 + offset_bp / 10000.0)

    fill_i = None
    for j in range(ei, min(ei + wait, len(ind))):
        # a resting SELL fills when price trades up to it (offset 0 = the open itself)
        if offset_bp <= 0 and j == ei:
            fill_i = j
            break
        if float(ind["high"].iloc[j]) >= limit:
            fill_i = j
            break
    if fill_i is None:
        return {"filled": False}

    entry = limit
    start = fill_i + 1 if fill_i > ei else ei
    last = min(start + HORIZON - 1, len(ind) - 1)
    if last < start:
        return {"filled": False}
    hi = ind["high"].to_numpy()[start:last + 1]
    lo = ind["low"].to_numpy()[start:last + 1]
    cl = ind["close"].to_numpy()[start:last + 1]
    if len(cl) < 2:
        return {"filled": False}

    tp, sl = entry - TP_MULT * atr, entry + SL_MULT * atr
    up = int(np.argmax(lo <= tp)) if (lo <= tp).any() else len(cl)
    dn = int(np.argmax(hi >= sl)) if (hi >= sl).any() else len(cl)
    if up < dn:
        return {"filled": True, "gross": 1 - tp / entry, "why": "TP"}
    if dn < up:
        return {"filled": True, "gross": 1 - sl / entry, "why": "SL"}
    return {"filled": True, "gross": 1 - cl[-1] / entry, "why": "TIMEOUT"}


def net_of(rows: list[dict], n_all: int, entry_fee: float, tp_maker: bool) -> dict:
    """Net per CANDIDATE (missed fills count as zero), not per filled trade."""
    tot = 0.0
    for r in rows:
        exit_fee = SWAP_MAKER if (tp_maker and r["why"] == "TP") else SWAP_TAKER
        tot += r["gross"] - entry_fee - exit_fee
    return {"n_filled": len(rows), "fill_rate": round(len(rows) / max(n_all, 1), 4),
            "net_per_candidate": round(tot / max(n_all, 1), 6),
            "net_per_filled": round(tot / max(len(rows), 1), 6)}


def main() -> int:
    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    series = list_series(bar="15m")
    cache: dict[str, tuple | None] = {}
    sigs: list[tuple] = []
    for sym, grp in d.groupby("symbol"):
        if sym not in cache:
            key = ("okx", sym)
            if key in series:
                ind = add_indicators(add_mas(load_series(series[key])))
                cache[sym] = (ind, pd.to_datetime(ind["open_time"], utc=True))
            else:
                cache[sym] = None
        e = cache[sym]
        if e is None:
            continue
        ind, times = e
        for _, r in grp.iterrows():
            i = int(times.searchsorted(r["t"]))
            if 200 <= i < len(ind) - 2:
                sigs.append((ind, i))
    n_all = len(sigs)
    print(f"候选 {n_all}   maker {SWAP_MAKER*1e4:.0f}bp/边   taker {SWAP_TAKER*1e4:.0f}bp/边\n")

    # ---- baseline: everything taker, entry at the open (today's executor) ----
    base_rows = [r for r in (simulate(ind, i, 0.0, 1) for ind, i in sigs)
                 if r and r.get("filled")]
    base = net_of(base_rows, n_all, SWAP_TAKER, tp_maker=False)
    print(f"现行(三腿全 taker,市价入场): 成交 {base['fill_rate']*100:.1f}%  "
          f"净/候选 {base['net_per_candidate']*100:+.4f}%")

    # ---- TP leg alone: same entry, TP as a resting limit ----
    tp_only = net_of(base_rows, n_all, SWAP_TAKER, tp_maker=True)
    print(f"只把止盈腿改挂单     : 成交 {tp_only['fill_rate']*100:.1f}%  "
          f"净/候选 {tp_only['net_per_candidate']*100:+.4f}%  "
          f"({(tp_only['net_per_candidate']-base['net_per_candidate'])*1e4:+.2f}bp)")

    # ---- entry as maker, swept over offset and patience ----
    print(f"\n{'入场挂单偏移':>12} {'等待':>5} {'成交率':>8} {'净/成交单':>11} "
          f"{'净/候选':>10} {'vs现行':>9}")
    rows_out = []
    for wait in FILL_WAIT:
        for off in OFFSETS_BP:
            rs = [r for r in (simulate(ind, i, off, wait) for ind, i in sigs)
                  if r and r.get("filled")]
            if not rs:
                continue
            m = net_of(rs, n_all, SWAP_MAKER, tp_maker=True)
            delta = (m["net_per_candidate"] - base["net_per_candidate"]) * 1e4
            rows_out.append({"offset_bp": off, "wait": wait, **m,
                             "delta_bp_vs_base": round(delta, 2)})
            print(f"{off:>11.0f}bp {wait:>5} {m['fill_rate']*100:>7.1f}% "
                  f"{m['net_per_filled']*100:>+10.4f}% "
                  f"{m['net_per_candidate']*100:>+9.4f}% {delta:>+8.2f}bp")

    best = max(rows_out, key=lambda r: r["net_per_candidate"]) if rows_out else None
    if best and best["delta_bp_vs_base"] > 0:
        verdict = (f"最佳 = 入场挂 {best['offset_bp']:.0f}bp 等 {best['wait']} 根 + 止盈挂单:"
                   f"成交率 {best['fill_rate']*100:.1f}%,净/候选 "
                   f"{best['net_per_candidate']*100:+.4f}%,比现行 "
                   f"{best['delta_bp_vs_base']:+.2f}bp")
    else:
        verdict = (f"入场挂单没有净收益(漏单损失 > 省下的 4bp);"
                   f"但只改止盈腿仍值 "
                   f"{(tp_only['net_per_candidate']-base['net_per_candidate'])*1e4:+.2f}bp,零漏单风险")
    print(f"\n判读: {verdict}")
    print("注:止损腿必然 taker(止损追价);成本假设改动属 owner 决策,本脚本只测不改。")

    (PROJECT / "analysis" / "output" / "diag_maker_entry_fill.json").write_text(
        json.dumps({"pool": POOL.name, "n_candidates": n_all,
                    "maker": SWAP_MAKER, "taker": SWAP_TAKER,
                    "baseline_all_taker": base, "tp_maker_only": tp_only,
                    "entry_maker_sweep": rows_out, "verdict": verdict},
                   indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
