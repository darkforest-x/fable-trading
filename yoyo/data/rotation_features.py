"""Closed-candle features for the unvalidated rotation observation prototype.

Input columns are open/high/low/close/volume/quote_volume and an explicit
confirmed (or OKX confirm) flag. The UTC DatetimeIndex is candle OPEN time.
Availability is open time plus the declared interval; all price/volume checks
and calculations happen AFTER selecting the prefix closed by ``as_of``.

Daily metrics use only completed daily candles: close-to-close returns over
7/30 days, inclusive 20/50 day simple means, inclusive 30 day median quote
volume, and at most 60 completed closes rebased to 100. At least 51 daily
candles are required for an eligible daily snapshot. No ATR, labels, model,
future outcome, cached market state, or order execution is involved.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


INTERVALS = {"1d": pd.Timedelta(days=1), "4h": pd.Timedelta(hours=4),
             "15m": pd.Timedelta(minutes=15)}
BAR_COLUMNS = ("open", "high", "low", "close", "volume", "quote_volume")


def finite_number(value: Any) -> float | None:
    """Return a JSON-safe finite scalar, preserving missing data as None."""
    if isinstance(value, (bool, np.bool_)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def utc_as_of(as_of: Any) -> pd.Timestamp:
    """Require an explicit aware instant and normalize it to UTC."""
    timestamp = pd.Timestamp(as_of)
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise ValueError("as_of must be an explicit timezone-aware timestamp")
    return timestamp.tz_convert("UTC")


def closed_prefix(bars: pd.DataFrame, *, as_of: Any, interval: str) -> pd.DataFrame:
    """Select available candles, then reject malformed or discontinuous data.

    Required values are finite, prices positive, volumes nonnegative, and OHLC
    geometry valid. Zero volume is a fact, never a substitute for missing data.
    Every available row needs an explicit true/1 confirmation. An unfinished
    row and arbitrary malformed future OHLCV values cannot affect the snapshot.
    Gaps, duplicate/reversed timestamps and off-grid timestamps are not repaired.
    """
    if interval not in INTERVALS:
        raise ValueError(f"unsupported interval: {interval!r}")
    instant = utc_as_of(as_of)
    if not isinstance(bars, pd.DataFrame) or not isinstance(bars.index, pd.DatetimeIndex):
        raise ValueError("bars must have a UTC DatetimeIndex of candle open times")
    if str(bars.index.tz) not in {"UTC", "Etc/UTC", "UTC+00:00"}:
        raise ValueError("bars index must use UTC")
    if bars.index.hasnans:
        raise ValueError("candle timestamps cannot be missing")
    duration = INTERVALS[interval]
    prefix = bars.loc[bars.index + duration <= instant].copy()
    if not prefix.columns.is_unique:
        raise ValueError("duplicate candle column names")
    missing = set(BAR_COLUMNS) - set(prefix.columns)
    if missing:
        raise ValueError(f"missing candle columns: {sorted(missing)}")
    flags = [name for name in ("confirmed", "confirm") if name in prefix]
    if not flags:
        raise ValueError("explicit confirmed or confirm column is required")
    if prefix.empty:
        return prefix
    if not prefix.index.is_unique or not prefix.index.is_monotonic_increasing:
        raise ValueError("closed candle timestamps must be unique and increasing")
    if any(stamp != stamp.floor(duration) for stamp in prefix.index):
        raise ValueError("closed candle timestamps must lie on the UTC interval grid")
    if len(prefix) > 1 and not (prefix.index[1:] - prefix.index[:-1] == duration).all():
        raise ValueError("closed candle prefix has a gap or an irregular interval")
    for name in flags:
        # Explicit membership avoids bool('0') and bool(nan), both of which are True.
        confirmed = prefix[name].map(
            lambda value: isinstance(value, (str, bool, np.bool_, int, float, np.number))
            and not pd.isna(value) and value in (True, 1, "1")
        )
        if not confirmed.all():
            raise ValueError(f"closed candles contain an unconfirmed {name} value")
    try:
        numeric = prefix.loc[:, BAR_COLUMNS].apply(pd.to_numeric, errors="raise").astype(float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("closed candle OHLCV must be numeric") from exc
    if not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("closed candle OHLCV must be finite")
    if (numeric.loc[:, ("open", "high", "low", "close")] <= 0).any().any():
        raise ValueError("closed candle prices must be positive")
    if (numeric.loc[:, ("volume", "quote_volume")] < 0).any().any():
        raise ValueError("closed candle volumes must be nonnegative")
    if ((numeric.low > numeric[["open", "close"]].min(axis=1))
            | (numeric.high < numeric[["open", "close"]].max(axis=1))
            | (numeric.high < numeric.low)).any():
        raise ValueError("closed candle OHLC geometry is invalid")
    for name in BAR_COLUMNS:
        prefix[name] = numeric[name]
    return prefix


def daily_metrics(bars: pd.DataFrame, *, as_of: Any) -> dict:
    """Compute the documented daily windows from a validated available prefix."""
    frame = closed_prefix(bars, as_of=as_of, interval="1d")
    output = {
        "state": "insufficient_data", "as_of": utc_as_of(as_of).isoformat(),
        "close": None, "last_bar_at": None, "return_7d": None,
        "return_30d": None, "sma20": None, "sma50": None,
        "above_sma20": None, "above_sma50": None,
        "median_quote_volume_30d": None, "sparkline": [], "reasons": [],
    }
    if not frame.empty:
        output["close"] = finite_number(frame.close.iloc[-1])
        output["last_bar_at"] = (frame.index[-1] + INTERVALS["1d"]).isoformat()
        tail = frame.close.iloc[-60:]
        output["sparkline"] = [
            {"time": (stamp + INTERVALS["1d"]).isoformat(),
             "value": finite_number(value / tail.iloc[0] * 100)}
            for stamp, value in tail.items()
        ]
    if len(frame) < 51:
        output["reasons"].append(f"need_51_completed_daily_candles:have_{len(frame)}")
        return output
    close = frame.close.iloc[-1]
    sma20 = frame.close.iloc[-20:].mean()
    sma50 = frame.close.iloc[-50:].mean()
    output.update({
        "state": "ok", "return_7d": finite_number(close / frame.close.iloc[-8] - 1),
        "return_30d": finite_number(close / frame.close.iloc[-31] - 1),
        "sma20": finite_number(sma20), "sma50": finite_number(sma50),
        "above_sma20": bool(close > sma20), "above_sma50": bool(close > sma50),
        "median_quote_volume_30d": finite_number(frame.quote_volume.iloc[-30:].median()),
    })
    if output["median_quote_volume_30d"] == 0:
        output["reasons"].append("zero_median_quote_volume")
    return output


def market_regime(metrics_by_symbol: dict, *, btc_symbol: str = "BTCUSDT",
                  eth_symbol: str = "ETHUSDT") -> dict:
    """Describe an observed regime with explicit, unvalidated thresholds.

    Breadth excludes BTC/ETH, insufficient and mismatched-date observations.
    Supportive: BTC positive over 30d and above SMA50, ETH/BTC nonnegative,
    and breadth >= 0.60. Defensive: BTC negative and below SMA50, or breadth
    <= 0.35. Other complete observations are mixed. Missing benchmarks or an
    empty breadth denominator mean unknown. These are not return forecasts.
    """
    result = {
        "state": "unknown", "btc_return_7d": None, "btc_return_30d": None,
        "eth_btc_return_30d": None, "breadth_above_sma20": None,
        "coverage_count": 0, "observed_at": None, "reasons": [],
        "heuristic": True,
    }
    btc = metrics_by_symbol.get(btc_symbol) or {}
    eth = metrics_by_symbol.get(eth_symbol) or {}
    for symbol, metrics in ((btc_symbol, btc), (eth_symbol, eth)):
        if metrics.get("state") != "ok" or finite_number(metrics.get("return_30d")) is None:
            result["reasons"].append(f"missing_or_insufficient_benchmark:{symbol}")
    if result["reasons"]:
        return result
    observed = btc.get("last_bar_at")
    result["observed_at"] = observed
    if observed != eth.get("last_bar_at"):
        result["reasons"].append("benchmark_observation_dates_differ")
        return result
    eligible = [
        item for symbol, item in metrics_by_symbol.items()
        if symbol not in {btc_symbol, eth_symbol} and item.get("state") == "ok"
        and isinstance(item.get("above_sma20"), (bool, np.bool_))
        and item.get("last_bar_at") == observed
    ]
    result["coverage_count"] = len(eligible)
    result["btc_return_7d"] = finite_number(btc.get("return_7d"))
    result["btc_return_30d"] = finite_number(btc["return_30d"])
    btc_return = result["btc_return_30d"]
    eth_return = finite_number(eth["return_30d"])
    if btc_return <= -1 or eth_return <= -1:
        result["reasons"].append("invalid_benchmark_return")
        return result
    result["eth_btc_return_30d"] = finite_number((1 + eth_return) / (1 + btc_return) - 1)
    if not eligible:
        result["reasons"].append("no_eligible_altcoin_breadth_denominator")
        return result
    breadth = sum(bool(item["above_sma20"]) for item in eligible) / len(eligible)
    result["breadth_above_sma20"] = breadth
    btc_above = btc.get("above_sma50")
    if not isinstance(btc_above, (bool, np.bool_)):
        result["reasons"].append("missing_btc_sma50_state")
        return result
    if (btc_return < 0 and not btc_above) or breadth <= 0.35:
        result["state"] = "defensive"
        result["reasons"].append("btc_negative_below_sma50_or_breadth_at_most_0.35")
    elif btc_return > 0 and btc_above and result["eth_btc_return_30d"] >= 0 and breadth >= 0.60:
        result["state"] = "supportive"
        result["reasons"].append("btc_trend_eth_relative_strength_and_breadth_at_least_0.60")
    else:
        result["state"] = "mixed"
        result["reasons"].append("complete_inputs_with_mixed_trend_and_breadth")
    result["reasons"].append("descriptive_heuristic_not_a_validated_forecast")
    return result
