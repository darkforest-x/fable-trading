"""Build the OKX ETH-USDT-SWAP 5m series for exp-htf-ma-reversion-20261009-v3.

Owner 2026-10-09: 你用 ethusdt.p 近两年的数据回测一下 找到最优参数. The owner's chart is OKX
ETHUSDT.P, so this run uses OKX bars instead of v1/v2's frozen Binance series:

  1. official OKX 1m archive months (data/kline_lowtf_20260922/okx_ETH_USDT_SWAP_1m_1402560.csv,
     2023-12-31 16:00Z .. 2026-08-31 15:59Z) plus the confirmed API 1m tail of the same pull
     (okx_api_ETH_USDT_SWAP_1m_20260831T1600_20260922T1400.csv), aggregated to complete UTC 5m
     buckets (all five minutes present);
  2. OKX history-candles 5m fetched 2026-10-09 ~14:03Z
     (data/research/htf_ma_reversion_okx_tail_20261009/okx_ETH_USDT_SWAP_5m_5799.csv) for bars after
     the last aggregated bucket; the overlap is compared bar by bar and reported, not blended;
  3. bars whose close is after the fetch time are dropped (unconfirmed).

Output: data/research/htf_ma_reversion_okx_eth_5m_20261009/ETHUSDT.csv.gz (ts, open, high, low,
close, volume) and receipt.json with source hashes, row counts and the overlap check. Volume is
carried for format parity only; the strategy does not read it.

Run: .venv/bin/python experiments/active/exp-htf-ma-reversion-20261009-v3/build_series.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ARCHIVE = Path("data/kline_lowtf_20260922/okx_ETH_USDT_SWAP_1m_1402560.csv")
API_1M = Path("data/kline_lowtf_20260922/okx_api_ETH_USDT_SWAP_1m_20260831T1600_20260922T1400.csv")
TAIL_5M = Path("data/research/htf_ma_reversion_okx_tail_20261009/okx_ETH_USDT_SWAP_5m_5799.csv")
FETCHED_AT = pd.Timestamp("2026-10-09T14:03:00Z")
OUT = Path("data/research/htf_ma_reversion_okx_eth_5m_20261009")
COLS = ["ts", "open", "high", "low", "close", "volume"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def five_minute(one: pd.DataFrame) -> pd.DataFrame:
    """Complete UTC 5m buckets from 1m rows (all five minutes present)."""
    one = one.drop_duplicates("ts").sort_values("ts")
    bucket = one.ts.to_numpy() // 300_000 * 300_000
    g = one.assign(bucket=bucket).groupby("bucket", sort=True)
    bars = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                 volume=("volume", "sum"), n=("ts", "size"))
    bars = bars.loc[bars.n == 5].drop(columns="n")
    return bars.rename_axis("ts").reset_index()


def main() -> None:
    one = pd.concat([pd.read_csv(p, usecols=COLS) for p in (ARCHIVE, API_1M)], ignore_index=True)
    agg = five_minute(one)
    tail = pd.read_csv(TAIL_5M, usecols=COLS).drop_duplicates("ts").sort_values("ts")
    tail = tail.loc[tail.ts + 300_000 <= FETCHED_AT.value // 10**6]
    both = agg.merge(tail, on="ts", suffixes=("_agg", "_api"))
    diffs = {k: float(np.abs(both[f"{k}_agg"] - both[f"{k}_api"]).max()) for k in ("open", "high", "low", "close")}
    series = pd.concat([agg, tail.loc[tail.ts > agg.ts.max()]], ignore_index=True).sort_values("ts")
    step = np.diff(series.ts.to_numpy())
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / "ETHUSDT.csv.gz"
    series.to_csv(out, index=False, compression={"method": "gzip", "mtime": 0})
    receipt = {
        "sources": {str(p): sha256(p) for p in (ARCHIVE, API_1M, TAIL_5M)},
        "rows_1m": len(one), "rows_5m_from_1m": len(agg), "rows_5m_tail_used": int((tail.ts > agg.ts.max()).sum()),
        "rows": len(series), "missing_5m_slots": int((step // 300_000 - 1).clip(min=0).sum()),
        "first": pd.Timestamp(int(series.ts.iloc[0]), unit="ms", tz="UTC").isoformat(),
        "last": pd.Timestamp(int(series.ts.iloc[-1]), unit="ms", tz="UTC").isoformat(),
        "overlap_bars": len(both), "overlap_max_abs_diff": diffs,
        "output": str(out), "output_sha256": sha256(out),
    }
    (OUT / "receipt.json").write_text(json.dumps(receipt, indent=1) + "\n")
    print(json.dumps(receipt, indent=1))


if __name__ == "__main__":
    main()
