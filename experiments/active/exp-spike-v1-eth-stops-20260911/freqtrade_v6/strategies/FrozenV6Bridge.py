"""Freqtrade 2026.8 bridge for causal SPIKE V6 ETH execution inputs.

The signal list is the V6 close-time output.  Freqtrade shifts it once and
therefore enters at the next candle open.  Each custom stop is the active price
already calculated at the preceding close; it never uses the callback's current
high/low to calculate a new same-candle stop.
"""
from __future__ import annotations

import os
from pathlib import Path
import pandas as pd
from pandas import DataFrame
from freqtrade.strategy import IStrategy, stoploss_from_absolute

PLAN = Path(os.environ.get("V6_FT_PLAN_DIR", Path(__file__).resolve().parents[1] / "plans"))
UNIVERSE = os.environ.get("V6_FT_UNIVERSE", "both")
VARIANT = os.environ.get("V6_FT_VARIANT", "baseline")


def _utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


class FrozenV6Bridge(IStrategy):
    INTERFACE_VERSION = 3
    can_short = True
    minimal_roi = {"0": 100.0}
    stoploss = -0.99
    trailing_stop = False
    use_custom_stoploss = True
    use_exit_signal = False
    process_only_new_candles = True
    startup_candle_count = 0
    timeframe = "30m"

    def bot_start(self, **kwargs) -> None:
        minutes = {"30m": 30, "1h": 60, "4h": 240}[self.timeframe]
        suffix = f"{UNIVERSE}_{minutes}m_{VARIANT}"
        signals = pd.read_csv(PLAN / f"signals_{suffix}.csv", parse_dates=["signal_bar_open", "entry_time"])
        stops = pd.read_csv(PLAN / f"stops_{suffix}.csv", parse_dates=["entry_time", "current_time"])
        self._long_signals = set(_utc(x) for x in signals.loc[signals.side.eq(1), "signal_bar_open"])
        self._short_signals = set(_utc(x) for x in signals.loc[signals.side.eq(-1), "signal_bar_open"])
        self._stops = {(_utc(e), _utc(now), int(side)): float(stop) for e, now, side, stop in stops[["entry_time", "current_time", "side", "active_stop"]].itertuples(index=False, name=None)}

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dates = pd.to_datetime(dataframe["date"], utc=True)
        dataframe.loc[dates.isin(self._long_signals), "enter_long"] = 1
        dataframe.loc[dates.isin(self._short_signals), "enter_short"] = 1
        dataframe.loc[dates.isin(self._long_signals | self._short_signals), "enter_tag"] = "frozen_v6"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs):
        key = (_utc(trade.open_date_utc), _utc(current_time), -1 if trade.is_short else 1)
        absolute = self._stops.get(key)
        if absolute is None:
            return None
        return stoploss_from_absolute(absolute, current_rate, is_short=trade.is_short, leverage=trade.leverage)
