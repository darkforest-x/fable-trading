"""Funding-rate cost helpers for OKX perpetual-swap backtests.

Source data is written by `src.data.fetch_funding` from OKX public
funding-rate-history. For this long-only strategy, positive realized funding
rates are a cost and negative realized rates are a rebate. A trade is charged
for settlements with `entry_time < funding_time <= exit_time`, matching the
assumption that the position is held through that funding timestamp.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd
from numpy.typing import NDArray

PROJECT_DIR: Final = Path(__file__).resolve().parents[2]
FUNDING_DIR: Final = PROJECT_DIR / "data" / "funding"
BAR: Final = pd.Timedelta(minutes=15)
MAKER_FEE_ROUND_TRIP: Final = 0.0004


@dataclass(frozen=True)
class FundingSeries:
    symbol: str
    settlement_ms: NDArray[np.int64]
    long_cost: NDArray[np.float64]


def load_funding_series(symbol: str, *, funding_dir: Path = FUNDING_DIR) -> FundingSeries | None:
    path = funding_dir / f"{symbol}.csv"
    if not path.exists():
        return None
    frame = pd.read_csv(path)
    if frame.empty:
        return None
    funding_rate = pd.to_numeric(frame["funding_rate"], errors="coerce")
    if "realized_rate" in frame.columns:
        realized_rate = pd.to_numeric(frame["realized_rate"], errors="coerce")
        long_cost = realized_rate.fillna(funding_rate)
    else:
        long_cost = funding_rate
    times = pd.to_numeric(frame["funding_time_ms"], errors="coerce")
    valid = times.notna() & long_cost.notna()
    if not valid.any():
        return None
    ordered = pd.DataFrame(
        {"funding_time_ms": times[valid].astype("int64"), "long_cost": long_cost[valid].astype("float64")}
    ).sort_values("funding_time_ms")
    return FundingSeries(
        symbol=symbol,
        settlement_ms=ordered["funding_time_ms"].to_numpy(dtype=np.int64),
        long_cost=ordered["long_cost"].to_numpy(dtype=np.float64),
    )


def cumulative_long_funding_cost(
    series: FundingSeries,
    *,
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
) -> float | None:
    entry_ms = _timestamp_ms(entry_time)
    exit_ms = _timestamp_ms(exit_time)
    if exit_ms < int(series.settlement_ms[0]) or entry_ms > int(series.settlement_ms[-1]):
        return None
    held_settlements = (series.settlement_ms > entry_ms) & (series.settlement_ms <= exit_ms)
    return float(series.long_cost[held_settlements].sum())


def funding_costs_for_trades(
    trades: pd.DataFrame,
    *,
    funding_dir: Path = FUNDING_DIR,
    bar: pd.Timedelta = BAR,
) -> pd.Series:
    cache: dict[str, FundingSeries | None] = {}
    costs: list[float] = []
    for row in trades[["symbol", "signal_time", "exit_offset"]].itertuples(index=False):
        symbol = str(row.symbol)
        if symbol not in cache:
            cache[symbol] = load_funding_series(symbol, funding_dir=funding_dir)
        series = cache[symbol]
        if series is None:
            costs.append(float("nan"))
            continue
        signal_time = pd.Timestamp(row.signal_time)
        entry_time = signal_time + bar
        exit_time = entry_time + int(row.exit_offset) * bar
        cost = cumulative_long_funding_cost(series, entry_time=entry_time, exit_time=exit_time)
        costs.append(float("nan") if cost is None else cost)
    return pd.Series(costs, index=trades.index, name="funding_cost")


def _timestamp_ms(value: pd.Timestamp) -> int:
    ts = value.tz_localize("UTC") if value.tzinfo is None else value.tz_convert("UTC")
    return int(ts.timestamp() * 1000)
