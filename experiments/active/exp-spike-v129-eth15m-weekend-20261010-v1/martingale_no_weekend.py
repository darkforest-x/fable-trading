"""Doubling-stake overlay on the 15m no-weekend ledgers (owner 2026-10-10: 去掉周末再加上倍投 1000u 能变多少).

Stake rules are exactly ``experiments/active/exp-spike-v129-eth5m-20261010-v1/martingale_overlay.simulate``:
start 1000U; risk = base x 2^min(losses in a row, cap); net loss doubles, net win resets;
P&L = risk x net R (0.2% round trip included); ruin when the next risk plus open risk exceeds
equity. Ledgers: run_v1/okx_no_weekend/trades.csv.gz (OKX ETH 15m 2024-02..2026-10) and
run_v1/binance_no_weekend_trades.csv.gz (Binance ETH 15m 2021-09..2026-09), closed trades.

Schemes fixed here: fixed 10/20/50U and base 10/20/50U with cap 3, 5 and none. Besides the
full-period run, every scheme is restarted on the first day of each month (entries on or
after it, fresh 1000U) to show how much the outcome depends on the start date.

Run: PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v129-eth15m-weekend-20261010-v1/martingale_no_weekend.py
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OVERLAY = Path("experiments/active/exp-spike-v129-eth5m-20261010-v1/martingale_overlay.py")
SCHEMES = [(10, 0), (20, 0), (50, 0), (10, 3), (10, 5), (10, None), (20, 3), (20, 5), (20, None), (50, 3), (50, 5), (50, None)]


def overlay():
    spec = importlib.util.spec_from_file_location("martingale_overlay", OVERLAY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ledger(path: Path) -> pd.DataFrame:
    t = pd.read_csv(path)
    t = t.loc[~t.censored.astype(bool)]
    return pd.DataFrame({"entry": pd.to_datetime(t.entry_time, utc=True), "exit": pd.to_datetime(t.exit_time, utc=True),
                         "net_r": t.net_r.to_numpy(float)}).sort_values("entry").reset_index(drop=True)


def main() -> None:
    m = overlay()
    ledgers = {"okx": ledger(HERE / "run_v1" / "okx_no_weekend" / "trades.csv.gz"),
               "binance": ledger(HERE / "run_v1" / "binance_no_weekend_trades.csv.gz")}
    rows, starts = [], []
    for venue, t in ledgers.items():
        months = pd.date_range(t.entry.min().normalize().replace(day=1) + pd.offsets.MonthBegin(1),
                               t.entry.max() - pd.Timedelta(days=60), freq="MS", tz="UTC")
        for base, cap in SCHEMES:
            m.BASE = float(base)
            full = m.simulate(t, cap)
            outcomes = []
            for s in months:
                sub = t.loc[t.entry >= s].reset_index(drop=True)
                r = m.simulate(sub, cap)
                outcomes.append({"venue": venue, "base": base, "cap": "none" if cap is None else cap, "start": s.strftime("%Y-%m"),
                                 "final": r["final_equity"], "ruined": r["ruined_at"] is not None, "max_dd": r["max_drawdown_pct"]})
            o = pd.DataFrame(outcomes)
            starts.extend(outcomes)
            rows.append({"venue": venue, "base": base, "cap": "固定" if cap == 0 else ("不封顶" if cap is None else f"最多翻{cap}次"),
                         "full_final": full["final_equity"], "full_low": full["lowest_equity"], "full_dd": full["max_drawdown_pct"],
                         "full_ruined": full["ruined_at"], "max_stake": full["max_stake"], "starts": len(o),
                         "ruin_share": round(float(o.ruined.mean()), 3), "median_final": float(o.final.median()),
                         "worst_final": float(o.final.min()), "share_below_1000": round(float((o.final < 1000).mean()), 3)})
    table = pd.DataFrame(rows)
    (HERE / "run_v1" / "martingale_no_weekend.json").write_text(
        json.dumps({"schemes": rows, "by_start": starts}, ensure_ascii=False, indent=1, default=str) + "\n")
    pd.set_option("display.width", 220)
    print(table.to_string(index=False))


if __name__ == "__main__":
    main()
