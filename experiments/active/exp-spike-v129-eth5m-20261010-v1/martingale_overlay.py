"""Doubling-stake (倍投) overlay on frozen trade ledgers -- owner question 2026-10-10.

Owner: "我们之前针对notion任务第44条做的呢 如果结合之前的倍投呢". No entry or exit is
changed; only the stake per trade. Ledgers (frozen, chronological by entry):

* SPIKE V12.9 OKX ETH 5m ordinary signals (this experiment, run_v1, 559 closed).
* Notion #44 HTF-line reversion on OKX ETH (exp-htf-ma-reversion-20261009-v3 and v4):
  the v3 first-year best (5m long, 4x 15m ATR, stop 3%, 5R, Vix Fix, one entry), the v3
  owner-sample-like cell (5m long, 5% from the line, 0.6% stop, 5R, re-entry 3) and the
  v4 first-year best (15m short, 4x 1h ATR, swing stop, 5R, reclaim, one entry).

Policies fixed here before running: start 1000U, base risk 10U (1R); ``fixed`` keeps
10U; ``double`` doubles the stake after every closed net loss and resets after a net
win, no cap; ``cap3`` / ``cap5`` stop doubling at 8x / 32x. A stake is decided at the
entry from trades already closed then (overlapping #44 trades do not see each other's
outcome). Ruin: the stake exceeds current equity -> trading stops. R is net of the
0.2% round trip as in the parents. Earlier evidence: exp-spike-eth-martingale-20260914-v1
(40 bounded schedules on SPIKE ETH, all worse than fixed).

Run: PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v129-eth5m-20261010-v1/martingale_overlay.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
V3 = Path("experiments/active/exp-htf-ma-reversion-20261009-v3/results/trades.csv.gz")
V4 = Path("experiments/active/exp-htf-ma-reversion-20261009-v4/results_eth/trades.csv.gz")
START, BASE = 1000.0, 10.0
POLICIES = {"fixed": 0, "cap3": 3, "cap5": 5, "double": None}


def spike() -> pd.DataFrame:
    t = pd.read_csv(HERE / "run_v1" / "trades.csv.gz")
    t = t.loc[(t.arm == "ordinary") & ~t.censored.astype(bool)]
    return pd.DataFrame({"entry": pd.to_datetime(t.entry_time, utc=True), "exit": pd.to_datetime(t.exit_time, utc=True),
                         "net_r": t.net_r.to_numpy(float)})


def htf(path: Path, **cell) -> pd.DataFrame:
    t = pd.read_csv(path)
    for key, value in cell.items():
        t = t.loc[t[key].astype(str) == str(value)]
    if t.empty:
        raise ValueError(f"cell not found: {cell}")
    entry = pd.to_datetime(t.time, utc=True)
    return pd.DataFrame({"entry": entry, "exit": entry + pd.to_timedelta(t.bars * t.minutes, unit="m"),
                         "net_r": t.net_r.to_numpy(float)})


def simulate(trades: pd.DataFrame, cap: int | None) -> dict:
    trades = trades.sort_values("entry").reset_index(drop=True)
    events = sorted([(e, 1, k) for k, e in enumerate(trades.entry)] + [(x, 0, k) for k, x in enumerate(trades.exit)])
    equity, streak, stake_of, peak, max_dd, max_stake, taken = START, 0, {}, START, 0.0, 0.0, 0
    ruined_at, low = None, START
    for when, kind, k in events:
        if kind == 0:  # exit first at equal timestamps: a close at t is known to an entry at t
            if k not in stake_of:
                continue
            r = trades.net_r[k]
            equity += stake_of[k] * r
            streak = 0 if r > 0 else streak + 1
            peak, low = max(peak, equity), min(low, equity)
            max_dd = max(max_dd, (peak - equity) / peak)
            continue
        if ruined_at is not None:
            continue
        mult = 1 if cap == 0 else 2 ** (streak if cap is None else min(streak, cap))
        stake = BASE * mult
        open_risk = sum(stake_of[j] for j in stake_of if trades.exit[j] > when)
        if stake + open_risk > equity:
            ruined_at = when
            continue
        stake_of[k] = stake
        taken += 1
        max_stake = max(max_stake, stake)
    return {"trades_taken": taken, "final_equity": round(equity, 1), "lowest_equity": round(low, 1),
            "max_drawdown_pct": round(max_dd * 100, 1), "max_stake": max_stake,
            "ruined_at": None if ruined_at is None else ruined_at.tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M")}


def main() -> None:
    ledgers = {
        "SPIKE V12.9 5m 普通信号": spike(),
        "#44 v3 第一年最优（5m 多，4ATR，止损3%，5R）": htf(V3, minutes=5, side=1, vix=True, measure="atr", level=4.0,
                                                    stop="pct3", target_r=5, reentry=0),
        "#44 v3 截图样例（5m 多，离线5%，止损0.6%，5R，可再进）": htf(V3, minutes=5, side=1, vix=False, measure="pct", level=0.05,
                                                        stop="pct0.6", target_r=5, reentry=3),
        "#44 v4 第一年最优（15m 空，4ATR，等收回，5R）": htf(V4, minutes=15, side=-1, vix=False, measure="atr", level=4.0,
                                                     stop="swing0.1", target_r=5, reentry=0, conf="reclaim"),
    }
    out = {}
    for name, trades in ledgers.items():
        r = trades.net_r
        run = best = 0
        for v in r.loc[trades.sort_values("entry").index] < 0:
            run = run + 1 if v else 0
            best = max(best, run)
        out[name] = {"trades": len(trades), "win_rate": round(float((r > 0).mean()), 3), "sum_r": round(float(r.sum()), 1),
                     "longest_loss_streak": best, **{p: simulate(trades, cap) for p, cap in POLICIES.items()}}
    (HERE / "run_v1" / "martingale_overlay.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
