"""VIDYA trend-line retest backtest: ETH/BTC/SOL 15m, matched random control, Length sensitivity.

Question (owner, 2026-10-12): does trading only the retest of the VIDYA trend line
(BigBeluga Volumatic VIDYA, Length 10) with a strong per-trend volume delta make money
on OKX/Binance perpetual 15m after costs, and does it beat random entries in the same
coin, month and trend direction with the same stop distance and exit rule?

Rules and parameters live in yoyo/monitor/vidya_retest.py (STRATEGIES), the same code
the live paper worker runs. Main basis: decision at the confirming close, fill at the
next bar's open, 20bp round trip. Outputs results.json (statistics) and trades.json (every
main-basis trade), both read by the monitor's VIDYA 回踩 history view. ``reproduction`` re-runs the exploratory basis of
2026-10-12 (fill at the confirming close, 0.05%/side + 0.01% slippage) to show the
engine reproduces those numbers exactly (ETH tl-2r 53 trades +0.381R).

Data: Binance USD-M archives (data.binance.vision), 2022-01-01 to 2026-10-10 UTC:
  curl -O https://data.binance.vision/data/futures/um/monthly/klines/ETHUSDT/15m/ETHUSDT-15m-YYYY-MM.zip
(and daily files for the current month); merged to CSV columns
open_time,open,high,low,close,volume,time. data/ is not in git.

Usage:
  PYTHONPATH=. .venv/bin/python experiments/active/exp-vidya-trendline-retest-20261012-v1/build.py \
      --csv ETH=/path/eth.csv --csv BTC=/path/btc.csv --csv SOL=/path/sol.csv
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.monitor.vidya_retest import STRATEGIES, backtest, indicator, summarize

HERE = Path(__file__).resolve().parent
SPLIT = pd.Timestamp("2024-10-01", tz="UTC")
COST = 0.002
RANDOM_PER_TRADE = 20
RANDOM_RESAMPLES = 2000
SEED = 20261012


def load(path):
    df = pd.read_csv(path, parse_dates=["time"])
    return df.sort_values("time").reset_index(drop=True)


def yearly(trades):
    if not len(trades):
        return {}
    return {int(y): round(float(g.r.sum()), 2) for y, g in trades.groupby(trades.entry_time.dt.year)}


def split_stats(trades):
    return {"before_2024_10": summarize(trades[trades.entry_time < SPLIT]),
            "from_2024_10": summarize(trades[trades.entry_time >= SPLIT])}


def trade_records(coin, sid, trades):
    """Main-basis trades for the monitor's history view; times are UTC epoch milliseconds."""
    ms = lambda ts: int(pd.Timestamp(ts).value // 1_000_000)
    rows = []
    for t in trades.itertuples():
        rows.append({"coin": coin, "strategy": sid, "side": t.side, "signal_ms": ms(t.signal_time),
                     "entry_ms": ms(t.entry_time), "exit_ms": ms(t.exit_time), "entry": round(float(t.entry), 8),
                     "exit": round(float(t.exit), 8), "stop": round(float(t.stop), 8),
                     "target": None if t.target is None or t.target != t.target else round(float(t.target), 8),
                     "level": round(float(t.level), 8), "delta": round(float(t.delta), 1), "reason": t.reason,
                     "risk_pct": round(float(t.risk_pct), 3), "r": round(float(t.r), 4),
                     "r_gross": round(float(t.r_gross), 4), "bars": int(t.bars)})
    return rows


def matched_random(df, trades, params, rng):
    """Random entries matched on coin, calendar month, trend side, stop distance and exit rule.

    For every strategy trade, RANDOM_PER_TRADE bars are drawn from the same month whose
    trend state equals the trade side (the strategy only trades with the trend); the
    random trade fills at the next open with the strategy trade's stop distance (in %)
    and is managed by the same exit rule and cost. Returns per-draw R grouped by trade.
    """
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    ind = indicator(o, h, l, c, df["volume"].to_numpy(float), length=params.length,
                    momentum=params.momentum, distance=params.distance)
    up = ind["up"]
    month = df["time"].dt.strftime("%Y-%m").to_numpy()
    index_of = {t: i for i, t in enumerate(df["time"].to_numpy())}
    out = []
    for tr in trades.itertuples():
        i_sig = index_of[tr.signal_time]
        want_up = tr.side == "long"
        pool = np.where((month == month[i_sig]) & (up == want_up))[0]
        pool = pool[(pool != i_sig) & (pool < len(df) - 2)]
        draws = []
        for j in rng.choice(pool, size=min(RANDOM_PER_TRADE, len(pool)), replace=False) if len(pool) else []:
            e = o[j + 1]
            risk = e * tr.risk_pct / 100
            stop = e - risk if want_up else e + risk
            target = (e + params.rr * risk if want_up else e - params.rr * risk) if params.exit_mode == "fixed" else None
            px = None
            for k in range(j + 1, len(df)):
                hit_sl = l[k] <= stop if want_up else h[k] >= stop
                hit_tp = target is not None and (h[k] >= target if want_up else l[k] <= target)
                trend_exit = params.exit_mode == "trend" and not hit_sl and (up[k] != want_up)
                if hit_sl:
                    px = stop
                elif hit_tp:
                    px = target
                elif trend_exit:
                    px = c[k]
                if px is not None:
                    break
            if px is None:
                px = c[-1]
            gross = (px - e) if want_up else (e - px)
            draws.append((gross - COST * (e + px) / 2) / risk)
        out.append(draws)
    return out


def control_summary(trades, draws, rng):
    real = float(trades.r.mean())
    usable = [d for d in draws if d]
    flat = np.concatenate([np.asarray(d) for d in usable]) if usable else np.array([])
    if not len(flat):
        return {"random_mean_r": None, "excess_r": None, "p_value": None}
    # resample one random draw per trade; p = share of random portfolios at least as good
    means = np.array([np.mean([rng.choice(d) for d in usable]) for _ in range(RANDOM_RESAMPLES)])
    return {"random_mean_r": round(float(flat.mean()), 3), "random_win_rate": round(float((flat > 0).mean() * 100), 1),
            "excess_r": round(real - float(flat.mean()), 3),
            "p_value": round(float((means >= real).mean()), 4), "draws": int(len(flat))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="append", required=True, help="COIN=path")
    args = ap.parse_args()
    rng = np.random.default_rng(SEED)
    data = {k: load(v) for k, v in (x.split("=", 1) for x in args.csv)}
    result = {"experiment_id": HERE.name, "basis": {"fill": "next_open", "round_trip_cost": COST, "risk_fraction": 0.01,
              "split": "2024-10-01"}, "strategies": {k: dict(label=v["label"], **asdict(v["params"])) for k, v in STRATEGIES.items()},
              "data": {k: {"bars": int(len(df)), "first": str(df.time.iloc[0]), "last": str(df.time.iloc[-1])} for k, df in data.items()},
              "main": {}, "reproduction": {}, "length_sensitivity": {}}
    trade_rows = []
    for coin, df in data.items():
        for sid, cfg in STRATEGIES.items():
            p = cfg["params"]
            tr = backtest(df, p, cost=COST, fill="next_open")
            trade_rows += trade_records(coin, sid, tr)
            draws = matched_random(df, tr, p, rng) if len(tr) else []
            result["main"][f"{coin}|{sid}"] = {"summary": summarize(tr), "by_year": yearly(tr), **split_stats(tr),
                                               "matched_random": control_summary(tr, draws, rng) if len(tr) else None}
            if coin == "ETH":
                rep = backtest(df, p, cost=0.001, slip=0.0001, fill="close")
                result["reproduction"][sid] = summarize(rep)
                for length in range(8, 15):
                    t2 = backtest(df, replace(p, length=length), cost=COST, fill="next_open")
                    result["length_sensitivity"][f"{sid}|L{length}"] = {"summary": summarize(t2), **split_stats(t2)}
            print(coin, sid, result["main"][f"{coin}|{sid}"]["summary"], result["main"][f"{coin}|{sid}"]["matched_random"])
    (HERE / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    (HERE / "trades.json").write_text(json.dumps(trade_rows, ensure_ascii=False, separators=(",", ":")))
    print("wrote", HERE / "results.json", "and", len(trade_rows), "trades to trades.json")


if __name__ == "__main__":
    main()
