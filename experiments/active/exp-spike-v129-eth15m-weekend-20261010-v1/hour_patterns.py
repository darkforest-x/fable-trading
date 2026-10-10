"""Hour-of-day pattern check on the 15m no-weekend ledgers (owner 2026-10-10: 一天中什么时间段容易亏 容易赚).

Descriptive, post-hoc; nothing is changed. Trades are grouped by the Beijing hour of the
signal close (the moment the owner would see it). Samples: OKX ETH 15m 2024-02..2026-10,
Binance ETH 15m 2021-09..2024-01 and Binance 2024-02..2026-09 (same rule, other venue).
Six four-hour blocks are the unit (single hours hold about 5 trades each); single hours are
listed for reference only. A block is called consistent only if block-minus-rest mean net R
has the same sign in all three samples with Welch |t| >= 1 in each (rule fixed here, as in
the 5m pattern audit); with six blocks, about one in eight runs passes one by chance.

Run: PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v129-eth15m-weekend-20261010-v1/hour_patterns.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
SPLIT = pd.Timestamp("2024-02-01T00:00:00Z")
BLOCKS = [(0, 4), (4, 8), (8, 12), (12, 16), (16, 20), (20, 24)]


def load() -> pd.DataFrame:
    frames = []
    for path, venue in ((HERE / "run_v1" / "okx_no_weekend" / "trades.csv.gz", "okx"),
                        (HERE / "run_v1" / "binance_no_weekend_trades.csv.gz", "binance")):
        t = pd.read_csv(path)
        t = t.loc[~t.censored.astype(bool)].copy()
        t["signal_close"] = pd.to_datetime(t.signal_close, utc=True)
        t["sample"] = venue if venue == "okx" else np.where(t.signal_close < SPLIT, "binance_early", "binance_late")
        frames.append(t)
    t = pd.concat(frames, ignore_index=True)
    t["hour"] = t.signal_close.dt.tz_convert("Asia/Shanghai").dt.hour
    t["block"] = (t.hour // 4) * 4
    return t


def stats(g: pd.DataFrame) -> dict:
    r = g.net_r
    return {"n": len(r), "win_rate": float((r > 0).mean()) if len(r) else math.nan, "mean_r": float(r.mean()) if len(r) else math.nan,
            "sum_r": float(r.sum()), "gross_mean_r": float(g.gross_r.mean()) if len(r) else math.nan,
            "stop_share": float((g.exit_reason == "initial_stop").mean()) if len(r) else math.nan,
            "long_sum": float(g.loc[g.side == 1].net_r.sum()), "short_sum": float(g.loc[g.side == -1].net_r.sum())}


def welch(a: pd.Series, b: pd.Series) -> tuple[float, float]:
    if len(a) < 3 or len(b) < 3:
        return math.nan, math.nan
    d = a.mean() - b.mean()
    return float(d), float(d / math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b)))


def main() -> None:
    t = load()
    samples = ["okx", "binance_early", "binance_late"]
    blocks, hours, verdict = [], [], []
    for lo, hi in BLOCKS:
        signs = []
        row = {"block": f"{lo:02d}-{hi:02d}"}
        for s in samples:
            g = t.loc[t["sample"] == s]
            inside, rest = g.loc[g.block == lo], g.loc[g.block != lo]
            st = stats(inside)
            diff, tt = welch(inside.net_r, rest.net_r)
            blocks.append({"block": row["block"], "sample": s, **st, "diff_vs_rest": diff, "t": tt})
            signs.append(np.sign(tt) if np.isfinite(tt) and abs(tt) >= 1 else 0)
        row["consistent"] = bool(signs[0] != 0 and signs.count(signs[0]) == 3)
        row["direction"] = "better" if row["consistent"] and signs[0] > 0 else ("worse" if row["consistent"] else "")
        verdict.append(row)
    for h in range(24):
        for s in samples:
            g = t.loc[(t["sample"] == s) & (t.hour == h)]
            hours.append({"hour": h, "sample": s, **stats(g)})
    out = {"blocks": blocks, "verdict": verdict, "hours": hours, "rule": "same sign and |t|>=1 in all three samples"}
    (HERE / "run_v1" / "hour_patterns.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float) + "\n")
    b = pd.DataFrame(blocks)
    pd.set_option("display.width", 220)
    print(b.pivot_table(index="block", columns="sample", values=["n", "mean_r", "sum_r", "t"], sort=False).round(2).to_string())
    print(pd.DataFrame(verdict).to_string(index=False))
    h = pd.DataFrame(hours)
    print(h.pivot_table(index="hour", columns="sample", values=["n", "sum_r"]).round(1).to_string())


if __name__ == "__main__":
    main()
