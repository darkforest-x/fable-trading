"""Freqtrade 2026.8 execution bridge for frozen V1 ETH stop paths.

This adapter is deliberately not production-tradable.  It consumes precomputed,
causal stop paths from ``run_eth_stops.py``.  A path's price at candle open was
known at the preceding close.  Returning that absolute price from
``custom_stoploss`` avoids Freqtrade's default backtest behavior of calculating
the callback against a candle high (low for short) and then testing the same
candle's opposite extreme.  Inputs contain no credentials and no exchange I/O.
"""
from __future__ import annotations

import os
from pathlib import Path
import pandas as pd
from pandas import DataFrame
from freqtrade.strategy import CategoricalParameter, IStrategy, stoploss_from_absolute

PLAN = Path(os.environ.get("ETH_FT_PLAN_DIR", Path(__file__).resolve().parents[1] / "plans"))


class _FrozenV1Base(IStrategy):
    INTERFACE_VERSION = 3
    can_short = False
    minimal_roi = {"0": 100.0}
    stoploss = -0.99
    trailing_stop = False
    use_custom_stoploss = True
    use_exit_signal = False
    process_only_new_candles = True
    startup_candle_count = 0
    timeframe = "30m"
    _axis = "baseline"

    def bot_start(self, **kwargs) -> None:
        tf = {"30m": "30m", "1h": "60m", "4h": "240m"}[self.timeframe]
        signals = pd.read_csv(PLAN / f"v1_signals_{tf}.csv", parse_dates=["signal_bar_open", "entry_time"])
        self._signals = set(pd.to_datetime(signals.signal_bar_open, utc=True))
        choices = ["baseline"] if self._axis == "baseline" else [f"initial_{x:.1f}" for x in (1.5, 2., 2.5, 3.)] if self._axis == "initial" else [f"trail_{x:.1f}" for x in (3.,4.,5.)]
        self._stops = {}
        for choice in choices:
            plan = pd.read_csv(PLAN / f"v1_stops_{tf}_{choice}.csv", parse_dates=["entry_time", "current_time"])
            self._stops[choice] = {(pd.Timestamp(a, tz="UTC") if pd.Timestamp(a).tzinfo is None else pd.Timestamp(a).tz_convert("UTC"), pd.Timestamp(b, tz="UTC") if pd.Timestamp(b).tzinfo is None else pd.Timestamp(b).tz_convert("UTC")): float(s) for a,b,s in plan[["entry_time","current_time","active_stop"]].itertuples(index=False,name=None)}

    def _choice(self) -> str:
        return "baseline"

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dates = pd.to_datetime(dataframe["date"], utc=True)
        dataframe.loc[dates.isin(self._signals), "enter_long"] = 1
        dataframe.loc[dates.isin(self._signals), "enter_tag"] = "frozen_v1"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs):
        entry = pd.Timestamp(trade.open_date_utc)
        entry = entry.tz_localize("UTC") if entry.tzinfo is None else entry.tz_convert("UTC")
        now = pd.Timestamp(current_time)
        now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
        stop = self._stops.get(self._choice(), {}).get((entry, now))
        if stop is None:
            return None
        return stoploss_from_absolute(stop, current_rate, is_short=False, leverage=trade.leverage)


class FrozenV1BaselineBridge(_FrozenV1Base):
    """Fixed 2ATR floor / 4ATR trailing baseline, for parity only."""


class FrozenV1InitialBridge(_FrozenV1Base):
    _axis = "initial"
    initial_floor = CategoricalParameter([1.5, 2.0, 2.5, 3.0], default=2.0, space="buy", optimize=True, load=False)
    def _choice(self) -> str: return f"initial_{float(self.initial_floor.value):.1f}"


class FrozenV1TrailBridge(_FrozenV1Base):
    _axis = "trail"
    trail_atr = CategoricalParameter([3.0, 4.0, 5.0], default=4.0, space="buy", optimize=True, load=False)
    def _choice(self) -> str: return f"trail_{float(self.trail_atr.value):.1f}"
