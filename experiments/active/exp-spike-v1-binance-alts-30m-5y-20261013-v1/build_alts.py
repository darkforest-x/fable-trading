"""SPIKE 强劲爆发 V1 on 30 Binance USD-M alts, 30m, 2021-10-13..2026-10-01: ledger, controls, summary.

Owner 2026-10-13: "多测试一些山寨币呢". The universe, data and window are in config.json; everything
that decides a trade or a control is imported unchanged from the OKX ETH/BTC study's build.py
(evaluate_bars: the two-year study's _trade_rows ledger with this window, trail_exit for the event and
20 matched random entries, exit kinds; block: summary statistics and month-block sign-flip p).
Bars are the archive's 30m klines as published (no resampling); V1 runs over the bars present.

Usage (after fetch_alts.py):
  PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v1-binance-alts-30m-5y-20261013-v1/build_alts.py
"""
from __future__ import annotations

import importlib.util
import json
import math
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import market_sync_shock as v1
from yoyo.evaluation import spike_v10_4_study as meta_source

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "config.json").read_text())
OUT = HERE / "results_v1"
DATA = Path(CFG["data_dir"])
OKX_BUILD = HERE.parent / "exp-spike-v1-okx-30m-5y-20261013-v1" / "build.py"
_spec = importlib.util.spec_from_file_location("spike_v1_okx_build", OKX_BUILD)
okx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(okx)


def inferred_tick(bars: pd.DataFrame) -> float:
    prices = np.unique(np.round(bars[["open", "high", "low", "close"]].to_numpy(float).ravel(), 12))
    gaps = np.diff(prices)
    return float(gaps[gaps > 0].min())


def run_alt(symbol: str) -> dict:
    audit = json.loads((DATA / "audits" / f"{symbol}.json").read_text())
    path = Path(audit["output_path"])
    raw = pd.read_csv(path, usecols=["ts", "open", "high", "low", "close", "volume"])
    raw = raw.loc[raw.ts + 30 * 60_000 <= pd.Timestamp(CFG["end"]).value // 10**6]
    bars = raw.set_index(pd.to_datetime(raw.ts, unit="ms", utc=True)).drop(columns="ts")
    meta = meta_source.symbol_meta().get(symbol, {})
    tick = float(meta.get("tick", math.nan))
    tick_source = "exchange_info"
    if not (math.isfinite(tick) and tick > 0):
        tick, tick_source = inferred_tick(bars), "inferred_min_price_gap"
    item = {"venue": "binance", "symbol": symbol, "asset": meta.get("asset", symbol.removesuffix("USDT")), "tick": tick}
    receipt = {"symbol": symbol, "path": str(path), "sha256": audit["output_sha256"], "bars": len(bars),
               "first_bar": bars.index[0].isoformat(), "last_bar": bars.index[-1].isoformat(),
               "non_bar_gaps": audit.get("non_bar_gaps"), "months_missing": len(audit["months_missing"]),
               "tick": tick, "tick_source": tick_source}
    if len(bars) < 400:
        return {"symbol": symbol, "ledger": pd.DataFrame(), "controls": pd.DataFrame(), "receipt": {**receipt, "skipped": "too few bars"}}
    got = okx.evaluate_bars(symbol, bars, item, CFG)
    led = got["ledger"]
    if len(led):
        led.insert(0, "sym", symbol)
        led["period"] = np.where(pd.to_datetime(led.signal_close_time, utc=True) < pd.Timestamp(CFG["split"]), "before", "after")
    return {"symbol": symbol, "ledger": led, "controls": got["controls"], "receipt": {**receipt, **got["signals"]}}


def main():
    if not v1._committed([Path(__file__), HERE / "config.json", OKX_BUILD]):
        raise SystemExit("commit build_alts.py, config.json and the OKX build.py before generating results")
    ranking = json.loads((DATA / "ranking.json").read_text())
    universe = ranking["universe"]
    OUT.mkdir(exist_ok=True)
    with ProcessPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(run_alt, universe))
    ledgers = [r["ledger"] for r in results if len(r["ledger"])]
    trades = pd.concat(ledgers, ignore_index=True)
    controls = pd.concat([r["controls"] for r in results if len(r["controls"])], ignore_index=True)
    trades.to_csv(OUT / "alt_trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    controls.to_csv(OUT / "alt_controls.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    per_coin = {}
    for r in results:
        led = r["ledger"]
        per_coin[r["symbol"]] = {"receipt": r["receipt"], "full": okx.block(led, CFG) if len(led) else {"trades": 0},
                                 "before": okx.block(led[led.period == "before"], CFG) if len(led) else {"trades": 0},
                                 "after": okx.block(led[led.period == "after"], CFG) if len(led) else {"trades": 0},
                                 "censored_open": int(led.censored.astype(bool).sum()) if len(led) else 0}
    pooled = {p: okx.block(trades if p == "full" else trades[trades.period == p], CFG) for p in ("full", "before", "after")}
    closed = trades.loc[~trades.censored.astype(bool)]
    summary = {"generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
               "git_head": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
               "config": CFG, "ranking": {k: ranking[k] for k in ("rank_month", "listed", "candidates", "with_rank_month_data", "universe")},
               "pooled": pooled, "per_coin": per_coin,
               "exit_kinds": closed.exit_kind.value_counts().to_dict(),
               "sim_vs_ledger_max_abs_diff_r": round(float((closed.sim_net_r - closed.net_r).abs().max()), 4)}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=str))
    print("pooled", json.dumps(pooled, ensure_ascii=False))
    for s, v in per_coin.items():
        f = v["full"]
        print(f"{s:14s} n={f.get('trades', 0):3d} avg={f.get('avg_r')} sum={f.get('sum_r')} excess={f.get('excess_r')}")


if __name__ == "__main__":
    main()
