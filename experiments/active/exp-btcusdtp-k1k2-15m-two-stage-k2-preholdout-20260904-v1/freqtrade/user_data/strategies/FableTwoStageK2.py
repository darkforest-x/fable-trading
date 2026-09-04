"""Freqtrade 2026.8 implementation of the registered two-stage K2 rule.

Framework contracts follow the official Freqtrade documentation:
https://www.freqtrade.io/en/stable/strategy-101/
https://www.freqtrade.io/en/stable/strategy-callbacks/
https://www.freqtrade.io/en/stable/backtesting/

Signal calculations use completed rows only. Entry-time risk/economic checks
are deliberately deferred to ``confirm_trade_entry`` so the following open is
not read while populating the signal dataframe. This strategy is research-only.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, stoploss_from_absolute


class FableTwoStageK2(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "15m"
    can_short = True
    startup_candle_count = 200
    process_only_new_candles = True

    minimal_roi = {"0": 1000.0}
    stoploss = -0.99
    trailing_stop = False
    use_exit_signal = False
    use_custom_stoploss = True
    use_custom_roi = True

    order_types = {
        "entry": "market",
        "exit": "market",
        "emergency_exit": "market",
        "force_exit": "market",
        "force_entry": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    gap_min = 2
    gap_max = 8
    max_confirmation_delay = 2
    k1_min_body_ratio = 0.65
    k1_min_range_atr = 0.80
    k1_min_close_location = 0.70
    k1_min_cross_depth_atr = -0.05
    k2_min_wick_share = 0.25
    k2_max_body_ratio = 0.50
    k2_min_rejection_close = 0.65
    k2_touch_depth_min = 0.0
    k2_touch_depth_max = 1.50
    risk_atr_min = 0.15
    risk_atr_max = 2.50
    fee_to_risk_max = 1.25
    round_trip_cost = 0.002
    cooldown = timedelta(hours=6)
    target_r = 3.0
    protection_trigger_r = 1.5
    maximum_duration = timedelta(hours=12)

    def bot_start(self, **kwargs: Any) -> None:
        self._last_accepted_time: datetime | None = None
        self._last_k1: dict[str, int | None] = {"long": None, "short": None}

    @staticmethod
    def _pine_rma(values: np.ndarray, length: int) -> np.ndarray:
        array = np.asarray(values, dtype=float)
        output = np.full(array.shape, np.nan, dtype=float)
        for start in range(max(0, len(array) - length + 1)):
            seed = array[start : start + length]
            if np.isfinite(seed).all():
                seed_i = start + length - 1
                output[seed_i] = float(seed.mean())
                for i in range(seed_i + 1, len(array)):
                    value = array[i]
                    output[i] = (
                        output[i - 1]
                        if not np.isfinite(value)
                        else (output[i - 1] * (length - 1) + value) / length
                    )
                break
        return output

    @staticmethod
    def _geometry(dataframe: DataFrame, direction: int) -> dict[str, np.ndarray]:
        open_ = dataframe["open"].to_numpy(dtype=float)
        high = dataframe["high"].to_numpy(dtype=float)
        low = dataframe["low"].to_numpy(dtype=float)
        close = dataframe["close"].to_numpy(dtype=float)
        sma = dataframe["sma40_hl2"].to_numpy(dtype=float)
        atr = dataframe["atr14_pine"].to_numpy(dtype=float)
        ranges = high - low
        bodies = np.abs(close - open_)
        with np.errstate(divide="ignore", invalid="ignore"):
            body_ratio = bodies / ranges
            range_atr = ranges / atr
            if direction > 0:
                k1_close = (close - low) / ranges
                entry_depth = (sma - open_) / atr
                exit_depth = (close - sma) / atr
                wick_share = (np.minimum(open_, close) - low) / ranges
                reject_close = (close - low) / ranges
                touch_depth = (sma - low) / atr
                close_side = (close - sma) / atr
                body_side = np.minimum(open_, close) >= sma
            else:
                k1_close = (high - close) / ranges
                entry_depth = (open_ - sma) / atr
                exit_depth = (sma - close) / atr
                wick_share = (high - np.maximum(open_, close)) / ranges
                reject_close = (high - close) / ranges
                touch_depth = (high - sma) / atr
                close_side = (sma - close) / atr
                body_side = np.maximum(open_, close) <= sma
        return {
            "body_ratio": body_ratio,
            "range_atr": range_atr,
            "k1_close": k1_close,
            "cross_depth": np.minimum(entry_depth, exit_depth),
            "wick_share": wick_share,
            "reject_close": reject_close,
            "touch_depth": touch_depth,
            "close_side": close_side,
            "body_side": body_side,
        }

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        previous_close = dataframe["close"].shift(1)
        true_range = pd.concat(
            [
                dataframe["high"] - dataframe["low"],
                (dataframe["high"] - previous_close).abs(),
                (dataframe["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        dataframe["atr14_pine"] = self._pine_rma(
            true_range.to_numpy(dtype=float), 14
        )
        dataframe["sma40_hl2"] = (
            (dataframe["high"] + dataframe["low"]) / 2.0
        ).rolling(40, min_periods=40).mean()
        dataframe["ma_side"] = np.where(
            (dataframe["high"] + dataframe["low"]) / 2.0
            >= dataframe["sma40_hl2"],
            1,
            -1,
        )
        events = self._event_rows(dataframe)
        dataframe["fable_long"] = 0
        dataframe["fable_short"] = 0
        # Freqtrade resets ``enter_tag`` immediately before
        # ``populate_entry_trend``.  Keep the causal event payload in a
        # strategy-owned column until that hand-off point.
        dataframe["fable_entry_tag"] = ""
        for row in events:
            i = int(row["confirm_i"])
            side = "L" if int(row["direction"]) > 0 else "S"
            tag = (
                f"{side},{row['stop']:.10g},{row['atr']:.10g},"
                f"{row['k1_epoch']},{row['touch_epoch']},{row['delay']}"
            )
            if side == "L":
                dataframe.iat[i, dataframe.columns.get_loc("fable_long")] = 1
            else:
                dataframe.iat[i, dataframe.columns.get_loc("fable_short")] = 1
            existing = str(
                dataframe.iat[i, dataframe.columns.get_loc("fable_entry_tag")]
            )
            dataframe.iat[i, dataframe.columns.get_loc("fable_entry_tag")] = (
                tag if not existing else f"{existing}|{tag}"
            )
        return dataframe

    def _event_rows(self, dataframe: DataFrame) -> list[dict[str, Any]]:
        open_ = dataframe["open"].to_numpy(dtype=float)
        high = dataframe["high"].to_numpy(dtype=float)
        low = dataframe["low"].to_numpy(dtype=float)
        close = dataframe["close"].to_numpy(dtype=float)
        sma = dataframe["sma40_hl2"].to_numpy(dtype=float)
        atr = dataframe["atr14_pine"].to_numpy(dtype=float)
        ma_side = dataframe["ma_side"].to_numpy(dtype=int)
        dates = pd.to_datetime(dataframe["date"], utc=True)
        n = len(dataframe)
        candidates: list[dict[str, Any]] = []
        for direction in (1, -1):
            geo = self._geometry(dataframe, direction)
            finite_k1 = np.logical_and.reduce(
                [
                    np.isfinite(geo["body_ratio"]),
                    np.isfinite(geo["range_atr"]),
                    np.isfinite(geo["k1_close"]),
                    np.isfinite(geo["cross_depth"]),
                ]
            )
            k1 = (
                (direction * (close - open_) > 0.0)
                & (ma_side == direction)
                & finite_k1
                & (geo["body_ratio"] >= self.k1_min_body_ratio)
                & (geo["range_atr"] >= self.k1_min_range_atr)
                & (geo["k1_close"] >= self.k1_min_close_location)
                & (geo["cross_depth"] >= self.k1_min_cross_depth_atr)
            )
            finite_k2 = np.logical_and.reduce(
                [
                    np.isfinite(geo["body_ratio"]),
                    np.isfinite(geo["wick_share"]),
                    np.isfinite(geo["reject_close"]),
                    np.isfinite(geo["touch_depth"]),
                    np.isfinite(geo["close_side"]),
                ]
            )
            touch = (
                finite_k2
                & (geo["wick_share"] >= self.k2_min_wick_share)
                & (geo["body_ratio"] <= self.k2_max_body_ratio)
                & (geo["touch_depth"] >= self.k2_touch_depth_min)
                & (geo["touch_depth"] <= self.k2_touch_depth_max)
                & (geo["close_side"] >= 0.0)
                & geo["body_side"]
            )
            confirm = (
                finite_k2
                & (geo["wick_share"] >= self.k2_min_wick_share)
                & (geo["body_ratio"] <= self.k2_max_body_ratio)
                & (geo["reject_close"] >= self.k2_min_rejection_close)
                & (geo["close_side"] >= 0.0)
                & geo["body_side"]
            )
            wrong = (
                ~np.isfinite(sma)
                | (direction * (close - sma) < 0.0)
                | (ma_side != direction)
            )
            prefix = np.concatenate(([0], np.cumsum(wrong.astype(np.int64))))
            for touch_i in np.flatnonzero(touch):
                for gap in range(self.gap_min, self.gap_max + 1):
                    k1_i = int(touch_i - gap)
                    if k1_i < 0 or not k1[k1_i]:
                        continue
                    if prefix[touch_i] - prefix[k1_i + 1] != 0:
                        continue
                    chosen = -1
                    for delay in range(self.max_confirmation_delay + 1):
                        confirm_i = int(touch_i + delay)
                        if confirm_i >= n:
                            break
                        if dates.iloc[confirm_i] - dates.iloc[touch_i] != timedelta(
                            minutes=15 * delay
                        ):
                            break
                        valid = bool(confirm[confirm_i])
                        if delay > 0:
                            valid = bool(
                                valid
                                and ma_side[confirm_i] == direction
                                and prefix[confirm_i] - prefix[touch_i + 1] == 0
                            )
                        if valid:
                            chosen = delay
                            break
                    if chosen < 0:
                        continue
                    confirm_i = int(touch_i + chosen)
                    quality = float(
                        np.mean(
                            [
                                np.clip(geo["body_ratio"][k1_i], 0.0, 1.0),
                                np.clip(geo["range_atr"][k1_i] / 2.0, 0.0, 1.0),
                                np.clip(geo["k1_close"][k1_i], 0.0, 1.0),
                                np.clip(
                                    (geo["cross_depth"][k1_i] + 0.05) / 0.50,
                                    0.0,
                                    1.0,
                                ),
                            ]
                        )
                    )
                    stop = float(
                        low[touch_i : confirm_i + 1].min()
                        if direction > 0
                        else high[touch_i : confirm_i + 1].max()
                    )
                    candidates.append(
                        {
                            "direction": direction,
                            "k1_i": k1_i,
                            "touch_i": int(touch_i),
                            "confirm_i": confirm_i,
                            "gap": gap,
                            "delay": chosen,
                            "quality": quality,
                            "stop": stop,
                            "atr": float(atr[confirm_i]),
                            "k1_epoch": int(dates.iloc[k1_i].timestamp()),
                            "touch_epoch": int(dates.iloc[touch_i].timestamp()),
                        }
                    )
        candidates.sort(
            key=lambda row: (
                int(row["confirm_i"]),
                -int(row["direction"]),
                int(row["delay"]),
                -float(row["quality"]),
                int(row["gap"]),
                -int(row["touch_i"]),
            )
        )
        selected: list[dict[str, Any]] = []
        seen: set[tuple[int, int]] = set()
        for row in candidates:
            key = (int(row["confirm_i"]), int(row["direction"]))
            if key not in seen:
                seen.add(key)
                selected.append(row)
        return selected

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        dataframe["enter_tag"] = dataframe["fable_entry_tag"]
        dataframe.loc[dataframe["fable_long"].eq(1), "enter_long"] = 1
        dataframe.loc[dataframe["fable_short"].eq(1), "enter_short"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    @staticmethod
    def _tag_part(entry_tag: str | None, side: str) -> dict[str, float | int] | None:
        prefix = "L" if side == "long" else "S"
        for part in str(entry_tag or "").split("|"):
            fields = part.split(",")
            if len(fields) == 6 and fields[0] == prefix:
                return {
                    "stop": float(fields[1]),
                    "atr": float(fields[2]),
                    "k1_epoch": int(fields[3]),
                    "touch_epoch": int(fields[4]),
                    "delay": int(fields[5]),
                }
        return None

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs: Any,
    ) -> bool:
        meta = self._tag_part(entry_tag, side)
        if meta is None:
            return False
        direction = 1 if side == "long" else -1
        risk = direction * (float(rate) - float(meta["stop"]))
        atr = float(meta["atr"])
        risk_atr = risk / atr if atr > 0.0 else float("nan")
        risk_fraction = risk / float(rate) if rate > 0.0 else float("nan")
        fee_to_risk = (
            self.round_trip_cost / risk_fraction
            if risk_fraction > 0.0
            else float("inf")
        )
        if not (
            np.isfinite(risk_atr)
            and self.risk_atr_min <= risk_atr <= self.risk_atr_max
            and fee_to_risk <= self.fee_to_risk_max
        ):
            return False
        if (
            self._last_accepted_time is not None
            and current_time - self._last_accepted_time < self.cooldown
        ):
            return False
        k1_epoch = int(meta["k1_epoch"])
        if self._last_k1[side] == k1_epoch:
            return False
        self._last_accepted_time = current_time
        self._last_k1[side] = k1_epoch
        return True

    def custom_roi(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        trade_duration: int,
        entry_tag: str | None,
        side: str,
        **kwargs: Any,
    ) -> float | None:
        meta = self._tag_part(entry_tag, side)
        if meta is None:
            return None
        direction = -1 if trade.is_short else 1
        risk = direction * (trade.open_rate - float(meta["stop"]))
        if risk <= 0.0:
            return None
        target_price = trade.open_rate + direction * self.target_r * risk
        return float(trade.calc_profit_ratio(target_price))

    def _protection_armed(
        self, pair: str, trade: Trade, current_time: datetime, risk: float
    ) -> bool:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return False
        dates = pd.to_datetime(dataframe["date"], utc=True)
        available = dataframe.loc[
            (dates >= pd.Timestamp(trade.open_date_utc))
            & (dates < pd.Timestamp(current_time))
        ]
        if available.empty:
            return False
        direction = -1 if trade.is_short else 1
        close_r = direction * (available["close"].astype(float) - trade.open_rate) / risk
        return bool(close_r.ge(self.protection_trigger_r).any())

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs: Any,
    ) -> float | None:
        side = "short" if trade.is_short else "long"
        meta = self._tag_part(trade.enter_tag, side)
        if meta is None:
            return None
        direction = -1 if trade.is_short else 1
        stop = float(meta["stop"])
        risk = direction * (trade.open_rate - stop)
        if risk <= 0.0:
            return None
        if self._protection_armed(pair, trade, current_time, risk):
            stop = trade.open_rate * (1.0 + direction * self.round_trip_cost)
        return stoploss_from_absolute(
            stop,
            current_rate=current_rate,
            is_short=trade.is_short,
            leverage=trade.leverage,
        )

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs: Any,
    ) -> str | None:
        if current_time - trade.open_date_utc >= self.maximum_duration:
            return "timeout_12h"
        return None

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs: Any,
    ) -> float:
        return 1.0
