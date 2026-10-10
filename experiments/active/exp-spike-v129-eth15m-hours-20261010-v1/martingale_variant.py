"""Doubling-stake overlay on the 15m no-weekend + no-12-16 ledgers (owner 2026-10-10:
去掉周末和12-16点再加上倍投 1000u能变多少).

Identical schemes, stake rules, ruin rule and monthly restarts to
experiments/active/exp-spike-v129-eth15m-weekend-20261010-v1/martingale_no_weekend.py (imported),
only the ledgers differ: run_v1/okx_variant/trades.csv.gz (OKX ETH 15m 2024-02..2026-10) and
run_v1/binance_variant_trades.csv.gz (Binance ETH 15m 2021-09..2026-09).

Run: PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v129-eth15m-hours-20261010-v1/martingale_variant.py
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
BASE_SCRIPT = Path("experiments/active/exp-spike-v129-eth15m-weekend-20261010-v1/martingale_no_weekend.py")


def main() -> None:
    spec = importlib.util.spec_from_file_location("martingale_no_weekend", BASE_SCRIPT)
    nw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nw)
    m = nw.overlay()
    ledgers = {"okx": nw.ledger(HERE / "run_v1" / "okx_variant" / "trades.csv.gz"),
               "binance": nw.ledger(HERE / "run_v1" / "binance_variant_trades.csv.gz")}
    rows, starts = [], []
    for venue, t in ledgers.items():
        months = pd.date_range(t.entry.min().normalize().replace(day=1) + pd.offsets.MonthBegin(1),
                               t.entry.max() - pd.Timedelta(days=60), freq="MS", tz="UTC")
        for base, cap in nw.SCHEMES:
            m.BASE = float(base)
            full = m.simulate(t, cap)
            outcomes = []
            for s in months:
                r = m.simulate(t.loc[t.entry >= s].reset_index(drop=True), cap)
                outcomes.append({"venue": venue, "base": base, "cap": "none" if cap is None else cap, "start": s.strftime("%Y-%m"),
                                 "final": r["final_equity"], "ruined": r["ruined_at"] is not None, "max_dd": r["max_drawdown_pct"]})
            o = pd.DataFrame(outcomes)
            starts.extend(outcomes)
            rows.append({"venue": venue, "base": base, "cap": "固定" if cap == 0 else ("不封顶" if cap is None else f"最多翻{cap}次"),
                         "full_final": full["final_equity"], "full_low": full["lowest_equity"], "full_dd": full["max_drawdown_pct"],
                         "full_ruined": full["ruined_at"], "max_stake": full["max_stake"], "starts": len(o),
                         "ruin_share": round(float(o.ruined.mean()), 3), "median_final": float(o.final.median()),
                         "worst_final": float(o.final.min()), "share_below_1000": round(float((o.final < 1000).mean()), 3)})
    (HERE / "run_v1" / "martingale_variant.json").write_text(
        json.dumps({"schemes": rows, "by_start": starts}, ensure_ascii=False, indent=1, default=str) + "\n")
    pd.set_option("display.width", 220)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
