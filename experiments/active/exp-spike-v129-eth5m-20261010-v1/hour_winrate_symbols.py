"""Win rate by Beijing time of day on other symbols (owner 2026-10-10: 其他币种呢).

Descriptive, post-hoc; nothing is changed. Win = net R > 0 after the 0.2% round trip;
blocks are four Beijing hours of the signal close.

* 5m: BTCUSDT from the exp-spike-manual-system-20260925-v1 baseline ledger (Binance,
  2021-09..2026-09; same V12.6/V12.8 5m port as the ETH run), split at 2024-02-01.
* 15m: every Binance USD-M symbol of exp-spike-v128-entry-clock-long-20260923-v1/retest_v1
  (V12.8 = V12.9 on 15m), ordinary arm v9_both, baseline policy, in-window closed trades,
  2022-11..2026-09, split at that run's 2025-01-01.

For 15m the table is pooled over symbols, shown for six majors, and per symbol (>= 40
trades) as which block has the highest win rate, to show whether one block wins broadly.

Run: PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v129-eth5m-20261010-v1/hour_winrate_symbols.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
BTC5 = Path("experiments/active/exp-spike-manual-system-20260925-v1/run_v1/streams/BTCUSDT_5m/trades.csv.gz")
LONG = Path("experiments/active/exp-spike-v128-entry-clock-long-20260923-v1/retest_v1/streams")
SPLIT5, SPLIT15 = pd.Timestamp("2024-02-01T00:00:00Z"), pd.Timestamp("2025-01-01T00:00:00Z")
MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT"]
COLS = ["symbol", "signal_close", "net_r", "side"]


def blockify(t: pd.DataFrame) -> pd.DataFrame:
    t = t.copy()
    t["signal_close"] = pd.to_datetime(t.signal_close, utc=True)
    t["block"] = (t.signal_close.dt.tz_convert("Asia/Shanghai").dt.hour // 4) * 4
    t["win"] = t.net_r > 0
    return t


def table(t: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    g = t.groupby(by)
    return pd.DataFrame({"n": g.size(), "win_rate": g.win.mean(), "mean_r": g.net_r.mean()}).reset_index()


def main() -> None:
    btc = pd.read_csv(BTC5)
    btc = btc.loc[(btc.policy == "baseline") & (btc.arm == "ordinary") & ~btc.censored.astype(bool), COLS]
    btc = blockify(btc)
    btc["period"] = np.where(btc.signal_close < SPLIT5, "2021-09..2024-01", "2024-02..2026-09")
    frames, skipped = [], []
    for folder in sorted(LONG.glob("*_15m")):
        path = folder / "serial_trades.csv.gz"
        if not path.exists():
            continue
        wanted = set(COLS) | {"arm", "policy", "in_window", "censored"}
        t = pd.read_csv(path, usecols=lambda c: c in wanted)
        if not wanted <= set(t.columns):
            skipped.append(folder.name)  # streams with no trade rows carry a reduced header
            continue
        t = t.loc[(t.arm == "v9_both") & (t.policy == "baseline") & t.in_window.astype(bool) & ~t.censored.astype(bool), COLS]
        if len(t):
            frames.append(t)
    m = blockify(pd.concat(frames, ignore_index=True))
    m["period"] = np.where(m.signal_close < SPLIT15, "2022-11..2024-12", "2025-01..2026-09")
    pooled = table(m, ["period", "block"])
    majors = table(m.loc[m.symbol.isin(MAJORS)], ["symbol", "block"])
    per = table(m, ["symbol", "block"])
    big = per.groupby("symbol").n.sum()
    per = per.loc[per.symbol.isin(big[big >= 40].index)]
    best = per.loc[per.groupby("symbol").win_rate.idxmax()].block.value_counts().sort_index()
    worst = per.loc[per.groupby("symbol").win_rate.idxmin()].block.value_counts().sort_index()
    sym_overall = m.groupby("symbol").win.mean()
    above = per.assign(diff=per.win_rate - per.symbol.map(sym_overall)).groupby("block")["diff"].apply(lambda d: float((d > 0).mean()))
    out = {"btc_5m": table(btc, ["period", "block"]).to_dict("records"),
           "btc_5m_overall": table(btc, ["period"]).to_dict("records"),
           "pooled_15m": pooled.to_dict("records"), "pooled_15m_overall": table(m, ["period"]).to_dict("records"),
           "majors_15m": majors.to_dict("records"), "symbols_15m": int(m.symbol.nunique()), "trades_15m": int(len(m)),
           "symbols_ge40": int(len(big[big >= 40])), "skipped_streams": skipped, "best_block_counts": best.to_dict(), "worst_block_counts": worst.to_dict(),
           "share_symbols_block_above_own_avg": above.to_dict()}
    (HERE / "run_v1" / "hour_winrate_symbols.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float) + "\n")
    pd.set_option("display.width", 220)
    print("BTC 5m"); print(table(btc, ["period", "block"]).pivot_table(index="block", columns="period", values=["n", "win_rate", "mean_r"]).round(3))
    print(table(btc, ["period"]).round(3))
    print(f"\n15m all symbols: {m.symbol.nunique()} symbols, {len(m)} trades; skipped {len(skipped)} streams without trade columns")
    print(pooled.pivot_table(index="block", columns="period", values=["n", "win_rate", "mean_r"]).round(3))
    print(table(m, ["period"]).round(3))
    print(majors.pivot_table(index="block", columns="symbol", values="win_rate").round(2))
    print(majors.groupby("symbol").n.sum())
    print("best block counts (symbols >= 40 trades):", best.to_dict(), "worst:", worst.to_dict())
    print("share of symbols whose block win rate beats their own average:", {k: round(v, 2) for k, v in above.items()})


if __name__ == "__main__":
    main()
