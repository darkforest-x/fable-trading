"""Causal aggregation helpers for official OKX one-minute archives.

Inputs are already validated one-minute rows from one named archive month.
Only complete, evenly spaced groups are emitted; no future row is used outside
the bar being formed. Supported outputs are research-only 5m and 15m bars.
"""
from __future__ import annotations

from typing import Final

import pandas as pd


ARCHIVE_BAR_MINUTES: Final[dict[str, int]] = {"5m": 5, "15m": 15}


def aggregate_complete_ohlcv(
    frame: pd.DataFrame,
    *,
    bar: str,
) -> tuple[pd.DataFrame, int]:
    """Aggregate validated 1m OHLCV rows into complete UTC-aligned bars.

    Reads ``open_time`` in epoch milliseconds plus OHLC and ``vol``. Each
    output only uses the exact ``bar`` minutes beginning at its UTC bucket;
    incomplete or non-contiguous buckets are dropped and counted.
    """

    if bar not in ARCHIVE_BAR_MINUTES:
        expected = ", ".join(sorted(ARCHIVE_BAR_MINUTES))
        raise ValueError(f"unsupported archive output bar {bar!r}; expected {expected}")
    minutes = ARCHIVE_BAR_MINUTES[bar]
    bucket_ms = minutes * 60_000
    work = frame.copy()
    work["bucket_ms"] = (work["open_time"].astype("int64") // bucket_ms) * bucket_ms
    grouped = work.groupby("bucket_ms", sort=True)
    counts = grouped.size()
    first_ts = grouped["open_time"].min()
    last_ts = grouped["open_time"].max()
    complete = (counts == minutes) & ((last_ts - first_ts) == (minutes - 1) * 60_000)
    complete_buckets = set(int(value) for value in counts.index[complete])
    kept = work[work["bucket_ms"].isin(complete_buckets)]
    aggregated = (
        kept.groupby("bucket_ms", sort=True)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("vol", "sum"),
        )
        .reset_index()
        .rename(columns={"bucket_ms": "ts"})
    )
    aggregated["open_time"] = pd.to_datetime(aggregated["ts"], unit="ms", utc=True)
    aggregated = aggregated[["ts", "open", "high", "low", "close", "volume", "open_time"]]
    return aggregated, int((~complete).sum())
