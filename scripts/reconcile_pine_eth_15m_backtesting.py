#!/usr/bin/env python3
"""Independent Backtesting.py reconciliation for the ETH 15m V9 candidate.

This is a second-engine sensitivity check, not the authoritative Pine replay.
Backtesting.py 0.6.5 cannot attach a stop relative to an unknown next-open fill
before that fill occurs.  The strategy therefore attaches the frozen stop at
the entry bar close, one bar later than the custom engine.  The output measures
the resulting ledger divergence explicitly and must not be presented as Pine
parity or as an alternative result chosen for better performance.

Only the bounded 2025-01-01 through 2026-03-01 final-preholdout prefix is run.
No holdout, model training, production artifact or live action is involved.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import backtesting
import numpy as np
import pandas as pd
from backtesting import Strategy
from backtesting.lib import FractionalBacktest

from scripts.research_pine_eth_15m import (
    CONFIG_PATH,
    PROJECT,
    RESULTS,
    build_feature_frame,
    load_config,
    load_research_frame,
)


FRACTIONAL_UNIT = 0.0001


class V9BacktestingApproximation(Strategy):
    """Replay V9 with the framework's documented one-bar stop limitation."""

    risk_percent = 1.0
    max_leverage = 13.0
    margin_fraction = 1.0 / 13.0
    break_even_trigger = 0.015
    break_even_offset = 0.001

    def init(self) -> None:
        self._closed_seen = 0
        self._trades_to_skip = 0

    def _update_closed_trade_cooldown(self) -> None:
        while self._closed_seen < len(self.closed_trades):
            trade = self.closed_trades[self._closed_seen]
            return_percent = float(trade.pl_pct * 100.0)
            if return_percent > 20.0:
                self._trades_to_skip = 7
            elif return_percent > 2.0:
                self._trades_to_skip = 1
            self._closed_seen += 1

    def _attach_and_manage_stops(self) -> None:
        for trade in self.trades:
            distance = float(trade.tag)
            if trade.sl is None:
                trade.sl = (
                    trade.entry_price - distance
                    if trade.is_long
                    else trade.entry_price + distance
                )
            if trade.is_long and float(self.data.High[-1]) >= trade.entry_price * (
                1.0 + self.break_even_trigger
            ):
                trade.sl = max(float(trade.sl), trade.entry_price * (1.0 + self.break_even_offset))
            elif trade.is_short and float(self.data.Low[-1]) <= trade.entry_price * (
                1.0 - self.break_even_trigger
            ):
                trade.sl = min(float(trade.sl), trade.entry_price * (1.0 - self.break_even_offset))

    def next(self) -> None:
        self._update_closed_trade_cooldown()
        self._attach_and_manage_stops()

        raw_long = bool(self.data.RawLong[-1])
        raw_short = bool(self.data.RawShort[-1])
        raw_signal = raw_long or raw_short
        if raw_signal and self._trades_to_skip > 0:
            self._trades_to_skip -= 1
            return
        if not raw_signal or not bool(self.data.EntryAllowed[-1]):
            return

        stop_distance = float(self.data.StopDistance[-1])
        target_leverage = min(
            self.max_leverage,
            self.risk_percent / 100.0 / max(stop_distance / float(self.data.Close[-1]), 1e-12),
        )
        # Backtesting.py interprets 0<size<1 as a fraction of available
        # leveraged liquidity, hence target leverage / maximum leverage.
        order_fraction = min(0.999999, max(1e-6, target_leverage * self.margin_fraction))
        if raw_long and not self.position.is_long:
            self.buy(size=order_fraction, tag=stop_distance)
        elif raw_short and not self.position.is_short:
            self.sell(size=order_fraction, tag=stop_distance)


def build_framework_frame(frame: pd.DataFrame) -> pd.DataFrame:
    times = pd.to_datetime(frame["open_time"], utc=True)
    mask = (times >= pd.Timestamp("2025-01-01", tz="UTC")) & (
        times < pd.Timestamp("2026-03-01", tz="UTC")
    )
    selected = frame.loc[mask].copy()
    stop_distance = np.minimum(
        selected["atr"].to_numpy(dtype=float) * 4.0,
        selected["close"].to_numpy(dtype=float) * 0.03,
    )
    stop_distance = np.maximum(1, np.rint(stop_distance / 0.01).astype(int)) * 0.01
    data = pd.DataFrame(
        {
            "Open": selected["open"].to_numpy(dtype=float),
            "High": selected["high"].to_numpy(dtype=float),
            "Low": selected["low"].to_numpy(dtype=float),
            "Close": selected["close"].to_numpy(dtype=float),
            "Volume": selected["volume"].to_numpy(dtype=float),
            "RawLong": selected["v9_long"].to_numpy(dtype=bool),
            "RawShort": selected["v9_short"].to_numpy(dtype=bool),
            "EntryAllowed": selected["entry_allowed"].to_numpy(dtype=bool),
            # FractionalBacktest scales only OHLC. Extra price-valued columns
            # must be put into its internal fractional price coordinates here.
            "StopDistance": stop_distance * FRACTIONAL_UNIT,
        },
        index=pd.DatetimeIndex(times.loc[mask]),
    )
    return data


def _json_number(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return number if np.isfinite(number) else None


def run(output: Path = RESULTS) -> dict[str, Any]:
    config = load_config(CONFIG_PATH)
    raw, _ = load_research_frame(config)
    frame = build_feature_frame(raw)
    data = build_framework_frame(frame)
    engine = FractionalBacktest(
        data,
        V9BacktestingApproximation,
        cash=500.0,
        commission=0.001,
        margin=1.0 / 13.0,
        trade_on_close=False,
        hedging=False,
        exclusive_orders=True,
        finalize_trades=True,
        fractional_unit=FRACTIONAL_UNIT,
    )
    stats = engine.run()
    framework_trades = stats["_trades"].copy()
    output.mkdir(parents=True, exist_ok=True)
    framework_trades.to_csv(output / "backtesting_trades.csv", index=False)

    custom = pd.read_csv(output / "trades.csv")
    custom = custom.loc[
        (custom["variant"] == "v9_locked")
        & (custom["split"] == "final_preholdout_2025_202602")
    ].copy()
    custom_entries = set(pd.to_datetime(custom["entry_time"], utc=True))
    framework_entries = set(pd.to_datetime(framework_trades["EntryTime"], utc=True))
    custom["entry_time"] = pd.to_datetime(custom["entry_time"], utc=True)
    custom["exit_time"] = pd.to_datetime(custom["exit_time"], utc=True)
    framework_trades["EntryTime"] = pd.to_datetime(framework_trades["EntryTime"], utc=True)
    framework_trades["ExitTime"] = pd.to_datetime(framework_trades["ExitTime"], utc=True)
    ledger = custom.merge(
        framework_trades,
        left_on="entry_time",
        right_on="EntryTime",
        how="inner",
        validate="one_to_one",
    )
    exit_time_matches = int((ledger["exit_time"] == ledger["ExitTime"]).sum())
    max_entry_price_error = float((ledger["entry_price"] - ledger["EntryPrice"]).abs().max())
    max_exit_price_error = float((ledger["exit_price"] - ledger["ExitPrice"]).abs().max())
    max_return_error_bp = float(
        ((ledger["net_return"] - ledger["ReturnPct"]).abs() * 10_000.0).max()
    )
    same_bar_stops = int(
        ((custom["exit_reason"] == "stop") & (custom["holding_bars"] == 0)).sum()
    )
    independent_passed = bool(
        len(ledger) == len(custom) == len(framework_trades)
        and exit_time_matches == len(custom)
        and max_entry_price_error <= 0.01
        and max_exit_price_error <= 0.01
        and max_return_error_bp <= 0.01
    )
    reconciliation = {
        "framework": "Backtesting.py",
        "framework_version": backtesting.__version__,
        "data_rows": int(len(data)),
        "period": [str(data.index.min()), str(data.index.max())],
        "custom_engine_trades": int(len(custom)),
        "framework_trades": int(len(framework_trades)),
        "entry_time_intersection": int(len(custom_entries & framework_entries)),
        "custom_only_entry_times": int(len(custom_entries - framework_entries)),
        "framework_only_entry_times": int(len(framework_entries - custom_entries)),
        "exit_time_matches": exit_time_matches,
        "max_entry_price_error": max_entry_price_error,
        "max_exit_price_error": max_exit_price_error,
        "max_unit_return_error_bp": max_return_error_bp,
        "custom_same_entry_bar_stops": same_bar_stops,
        "framework_return_percent": _json_number(stats.get("Return [%]")),
        "framework_max_drawdown_percent": abs(float(stats.get("Max. Drawdown [%]"))),
        "framework_profit_factor": _json_number(stats.get("Profit Factor")),
        "framework_commission_paid": _json_number(stats.get("Commissions [$]")),
        "authoritative_for_v9": False,
        "independent_framework_reconciliation_passed": independent_passed,
        "tradingview_parity_passed": False,
        "semantic_limitations": [
            "initial stop becomes active at the entry bar close, not at the entry fill; no same-entry-bar stop occurred in this bounded sample",
            "broker-emulator intrabar sequencing differs from the custom confirmed-bar contract",
            "fractional contract composition and margin accounting differ from TradingView/OKX",
            "results are a sensitivity check and cannot replace a TradingView trade export",
        ],
        "holdout_consumed": False,
    }
    (output / "backtesting_reconciliation.json").write_text(
        json.dumps(reconciliation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return reconciliation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=RESULTS)
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
