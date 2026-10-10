"""Win rate by Beijing time of day for SPIKE V12.9 5m ordinary signals (owner 2026-10-10:
胜率最高的时间段在什么时候 一天中).

Descriptive, post-hoc. Win = net R > 0 after the 0.2% round trip. Grouping by the Beijing
hour of the signal close in four-hour blocks (single hours listed for reference). Samples:
OKX ETH 5m (run_v1, 2024-02..2026-10) and the Binance ETH 5m baseline ledger of
exp-spike-manual-system-20260925-v1 split at 2024-02-01 (the later part overlaps OKX in time).

Run: PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v129-eth5m-20261010-v1/hour_winrate.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
BINANCE = Path("experiments/active/exp-spike-manual-system-20260925-v1/run_v1/streams/ETHUSDT_5m/trades.csv.gz")
SPLIT = pd.Timestamp("2024-02-01T00:00:00Z")


def load() -> pd.DataFrame:
    o = pd.read_csv(HERE / "run_v1" / "trades.csv.gz")
    o = o.loc[(o.arm == "ordinary") & ~o.censored.astype(bool)].assign(sample="okx")
    b = pd.read_csv(BINANCE)
    b = b.loc[(b.policy == "baseline") & (b.arm == "ordinary") & ~b.censored.astype(bool)].copy()
    t = pd.concat([o, b], ignore_index=True)
    t["signal_close"] = pd.to_datetime(t.signal_close, utc=True)
    t.loc[t["sample"].isna(), "sample"] = np.where(t.loc[t["sample"].isna(), "signal_close"] < SPLIT, "binance_early", "binance_late")
    t["hour"] = t.signal_close.dt.tz_convert("Asia/Shanghai").dt.hour
    t["block"] = (t.hour // 4) * 4
    return t


def main() -> None:
    t = load()
    agg = lambda g: pd.Series({"n": len(g), "win_rate": (g.net_r > 0).mean(), "mean_r": g.net_r.mean(), "sum_r": g.net_r.sum()})
    blocks = t.groupby(["block", "sample"]).apply(agg).reset_index()
    hours = t.groupby(["hour", "sample"]).apply(agg).reset_index()
    overall = t.groupby("sample").apply(agg).reset_index()
    (HERE / "run_v1" / "hour_winrate.json").write_text(json.dumps(
        {"blocks": blocks.to_dict("records"), "hours": hours.to_dict("records"), "overall": overall.to_dict("records")},
        ensure_ascii=False, indent=1, default=float) + "\n")
    pd.set_option("display.width", 220)
    print(overall.round(3).to_string(index=False))
    print(blocks.pivot_table(index="block", columns="sample", values=["n", "win_rate", "mean_r"]).round(3).to_string())
    print(hours.pivot_table(index="hour", columns="sample", values=["n", "win_rate"]).round(2).to_string())


if __name__ == "__main__":
    main()
