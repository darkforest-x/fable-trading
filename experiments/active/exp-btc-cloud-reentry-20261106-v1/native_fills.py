"""NautilusTrader native fills for the BTC 5m cloud re-entry study.

Input bars are indexed by their UTC OPEN time. Each real candle becomes two
native 5m external bars in chronological event order:

1. A zero-range opening bar at the observed open price.
2. The real OHLC bar at ``open + 5m - 1ns``.

This gives Nautilus a native matching event for next-open market orders while
keeping the candle's high/low/close unavailable to the strategy at that open.
The open bar checks already-resting stops before the strategy callback; a gap
through a long stop therefore fills at the open. The later full OHLC bar lets
Nautilus match intrabar stops using its normal OHLC path. No stop or fill price
is computed by this adapter.

The opening event is an explicit zero-spread assumption: bid and ask are not
available in OHLC, so a zero-range bar at the recorded open represents both
sides at that price. Volume is synthetic and unlimited. Fee is OKX taker 5bp
per fill, on actual fill notional. No funding, spread, latency or slippage.

Candidates need UTC ``entry_time`` and ``exit_time`` (bar OPEN times), an
absolute ``initial_stop``, and ``exit_reason`` from the signal builder
(``ema20_downcross_next_open`` or ``dataset_end``). Only the schedule timestamp
is consulted at its matching opening event. Native STOP_MARKET fills override
the scheduled market exit whenever they happen first.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
import time
import warnings

import numpy as np
import pandas as pd
import nautilus_trader
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import (
    BacktestEngineConfig,
    LoggingConfig,
    RiskEngineConfig,
    StrategyConfig,
)
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import InstrumentId, PositionId, Symbol, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.trading.strategy import Strategy

from yoyo.layers.l3_backtest.nautilus_independent import ActualFillFee


ENGINE_VERSION = "1.231.0"
EXECUTION_MINUTES = 5
BAR_NS = EXECUTION_MINUTES * 60 * 1_000_000_000
TAKER_FEE = 0.0005
PRICE_TICK = 0.1
BASE_QUANTITY = 1.0
SYNTHETIC_VOLUME = 1_000_000_000_000.0


@dataclass
class _Trade:
    row: dict
    trade_id: str
    position_id: PositionId
    quantity: Quantity
    stop_order: object | None = None
    entry_price: float = math.nan
    entry_fee: float = 0.0
    exit_reason: str = ""


class _OpenEventStrategy(Strategy):
    """Submit market actions only on zero-range observed-open bars."""

    def __init__(self, instrument, bar_type, candidates, open_prices,
                 close_event_to_open, tick, quantity):
        super().__init__(
            StrategyConfig(oms_type="HEDGING", log_events=False, log_commands=False),
        )
        self.instrument = instrument
        self.bar_type = bar_type
        self.tick = tick
        self.quantity_value = quantity
        self.quantity = instrument.make_qty(quantity)
        self.open_prices = open_prices  # ns -> source 5m open; inputs are causal.
        self.close_event_to_open = close_event_to_open
        self.entries_at: dict[int, list[dict]] = {}
        self.exits_at: dict[int, list[dict]] = {}
        for row in candidates:
            entry_ns = int(row["entry_time"].value)
            exit_ns = int(row["exit_time"].value)
            self.entries_at.setdefault(entry_ns, []).append(row)
            if row["schedule_reason"] in ("cloud_exit", "dataset_end"):
                self.exits_at.setdefault(exit_ns, []).append(row)

        self.active: dict[str, _Trade] = {}
        self.trade_by_id: dict[str, _Trade] = {}
        self.order_map: dict[str, tuple[_Trade, str]] = {}
        self.rows: list[dict] = []
        self.errors: list[str] = []
        self.invalid_open_risk = 0
        self.open_events = 0
        self.full_ohlc_events = 0

    def on_start(self):
        self.subscribe_bars(self.bar_type)

    @staticmethod
    def _stamp(ns):
        return pd.Timestamp(int(ns), unit="ns", tz="UTC")

    def on_bar(self, bar):
        ns = int(bar.ts_event)
        if ns not in self.open_prices:
            self.full_ohlc_events += 1
            return

        self.open_events += 1
        observed_open = float(self.open_prices[ns])
        values = (float(bar.open), float(bar.high), float(bar.low), float(bar.close))
        if any(abs(v - observed_open) > self.tick * 1e-7 for v in values):
            self.errors.append(f"open event exposed non-open prices at {self._stamp(ns)}")
            return

        # If a resting native stop was crossed at this opening event, Nautilus
        # has already delivered its fill before this callback. Exits therefore
        # precede any new entry on the same open.
        for row in self.exits_at.get(ns, ()):
            trade = self.active.get(row["trade_id"])
            if trade is not None:
                self._market_exit(trade, row["schedule_reason"])

        for row in self.entries_at.get(ns, ()):
            price = observed_open
            stop = row["initial_stop"]
            if not math.isfinite(stop) or price <= stop + self.tick * 1e-7:
                self.invalid_open_risk += 1
                self.rows.append(self._skipped_row(row, price))
                continue
            self._market_entry(row)

    def _market_entry(self, row):
        tid = row["trade_id"]
        if tid in self.trade_by_id:
            self.errors.append(f"duplicate entry {tid}")
            return
        trade = _Trade(
            row=row,
            trade_id=tid,
            position_id=PositionId(tid),
            quantity=self.quantity,
        )
        self.active[tid] = trade
        self.trade_by_id[tid] = trade
        order = self.order_factory.market(self.instrument.id, OrderSide.BUY, self.quantity)
        self.order_map[str(order.client_order_id)] = (trade, "entry")
        self.submit_order(order, position_id=trade.position_id)

    def _market_exit(self, trade, reason):
        if trade.exit_reason:
            return
        trade.exit_reason = reason
        if trade.stop_order is not None and not trade.stop_order.is_closed:
            self.cancel_order(trade.stop_order)
        order = self.order_factory.market(
            self.instrument.id,
            OrderSide.SELL,
            trade.quantity,
            reduce_only=True,
        )
        self.order_map[str(order.client_order_id)] = (trade, "scheduled_exit")
        self.submit_order(order, position_id=trade.position_id)

    def on_order_filled(self, event):
        entry = self.order_map.get(str(event.client_order_id))
        if entry is None:
            self.errors.append(f"fill for unknown order {event.client_order_id}")
            return
        trade, phase = entry
        fill_time = self._stamp(event.ts_event)
        fill_price = float(event.last_px)
        fee = float(event.commission)
        if event.last_qty != trade.quantity:
            self.errors.append(f"partial fill {trade.trade_id}: {event.last_qty}")
            return

        if phase == "entry":
            expected = trade.row["entry_time"]
            if fill_time != expected:
                self.errors.append(
                    f"entry time mismatch {trade.trade_id}: {fill_time} != {expected}",
                )
            if abs(fill_price - float(self.open_prices[int(expected.value)])) > self.tick * 1e-7:
                self.errors.append(
                    f"entry not filled at open {trade.trade_id}: {fill_price}",
                )
            if fill_price <= trade.row["initial_stop"] + self.tick * 1e-7:
                # A zero-spread open book must make the pre-submit risk check
                # exact. Fail closed if framework precision ever violates it.
                self.errors.append(f"filled entry with no positive risk {trade.trade_id}")
                return
            trade.entry_price = fill_price
            trade.entry_fee = fee
            stop = self.instrument.make_price(trade.row["initial_stop"])
            order = self.order_factory.stop_market(
                self.instrument.id,
                OrderSide.SELL,
                trade.quantity,
                trigger_price=stop,
                reduce_only=True,
            )
            trade.stop_order = order
            self.order_map[str(order.client_order_id)] = (trade, "hard_stop")
            self.submit_order(order, position_id=trade.position_id)
            return

        if not math.isfinite(trade.entry_price):
            self.errors.append(f"exit before entry fill {trade.trade_id}")
            return
        if phase == "hard_stop":
            trade.exit_reason = "hard_stop"
        elif phase == "scheduled_exit":
            if trade.exit_reason not in ("cloud_exit", "dataset_end"):
                self.errors.append(f"invalid scheduled exit reason {trade.trade_id}")
                return
        else:
            self.errors.append(f"unknown order phase {phase}")
            return

        self.rows.append(self._closed_row(trade, fill_time, fill_price, fee))
        self.active.pop(trade.trade_id, None)

    def on_order_rejected(self, event):
        self.errors.append(f"rejected {event.client_order_id}: {event.reason}")

    def on_order_modify_rejected(self, event):
        self.errors.append(f"modify rejected {event.client_order_id}: {event.reason}")

    def _skipped_row(self, row, open_price):
        return {
            "trade_id": row["trade_id"],
            "entry_time": row["entry_time"],
            "scheduled_exit_time": row["exit_time"],
            "exit_time": pd.NaT,
            "exit_time_resolution": "none",
            "exit_bar_open_time": pd.NaT,
            "entry_price": np.nan,
            "observed_open_price": open_price,
            "exit_price": np.nan,
            "initial_stop": row["initial_stop"],
            "qty": self.quantity_value,
            "entry_fee": 0.0,
            "exit_fee": 0.0,
            "gross_usdt": 0.0,
            "fee_usdt": 0.0,
            "net_usdt": 0.0,
            "gross_r": np.nan,
            "net_r": np.nan,
            "exit_reason": "invalid_open_risk",
        }

    def _closed_row(self, trade, exit_time, exit_price, exit_fee):
        stop = float(trade.row["initial_stop"])
        risk = trade.entry_price - stop
        quantity = float(trade.quantity)
        gross = (exit_price - trade.entry_price) * quantity
        fees = trade.entry_fee + exit_fee
        event_ns = int(exit_time.value)
        if event_ns in self.open_prices:
            resolution = "5m_open"
            bar_open_time = self._stamp(event_ns)
        elif event_ns in self.close_event_to_open:
            resolution = "5m_ohlc_event"
            bar_open_time = self._stamp(self.close_event_to_open[event_ns])
        else:
            resolution = "native_event"
            bar_open_time = pd.NaT
        return {
            "trade_id": trade.trade_id,
            "entry_time": trade.row["entry_time"],
            "scheduled_exit_time": trade.row["exit_time"],
            "exit_time": exit_time,
            "exit_time_resolution": resolution,
            "exit_bar_open_time": bar_open_time,
            "entry_price": trade.entry_price,
            "observed_open_price": trade.entry_price,
            "exit_price": exit_price,
            "initial_stop": stop,
            "qty": quantity,
            "entry_fee": trade.entry_fee,
            "exit_fee": exit_fee,
            "gross_usdt": gross,
            "fee_usdt": fees,
            "net_usdt": gross - fees,
            "gross_r": (exit_price - trade.entry_price) / risk,
            "net_r": (gross - fees) / (risk * quantity),
            "exit_reason": trade.exit_reason,
        }


def _utc_time(value, label):
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware UTC")
    ts = ts.tz_convert("UTC").as_unit("ns")
    return ts


def _prepare_inputs(bars, candidates, tick, quantity):
    if nautilus_trader.__version__ != ENGINE_VERSION:
        raise RuntimeError(f"Expected Nautilus {ENGINE_VERSION}; got {nautilus_trader.__version__}")
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None:
        raise ValueError("Bars must use a timezone-aware UTC open-time DatetimeIndex")
    if bars.empty or not bars.index.is_monotonic_increasing or bars.index.has_duplicates:
        raise ValueError("Bars must be nonempty, unique and chronological")
    if tick <= 0 or quantity <= 0 or not float(quantity).is_integer():
        raise ValueError("Tick must be positive and quantity a positive integer")

    frame = bars[["open", "high", "low", "close"]].copy()
    frame.index = pd.DatetimeIndex(frame.index.tz_convert("UTC")).as_unit("ns")
    if ((frame.index.minute % EXECUTION_MINUTES != 0)
            | (frame.index.second != 0)
            | (frame.index.microsecond != 0)
            | (frame.index.nanosecond != 0)).any():
        raise ValueError("5m open timestamps must align to UTC 5-minute boundaries")
    diffs = np.diff(frame.index.asi8)
    if (diffs <= 0).any() or (diffs % BAR_NS != 0).any():
        raise ValueError("Bars must be unique and any gaps must be whole 5m intervals")
    prices = frame.to_numpy(float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("Invalid OHLC prices")
    o, h, l, c = (frame[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    if ((h < np.maximum(o, c)) | (l > np.minimum(o, c)) | (h < l)).any():
        raise ValueError("Malformed OHLC geometry")
    if (np.abs(prices / tick - np.rint(prices / tick)) > 1e-5).any():
        raise ValueError("OHLC prices are not aligned to the configured price tick")

    required = {"entry_time", "exit_time", "initial_stop", "exit_reason"}
    missing = sorted(required - set(candidates.columns))
    if missing:
        raise ValueError(f"Candidates missing columns: {missing}")
    if candidates.empty:
        raise ValueError("Need at least one candidate")
    rows = candidates.copy().to_dict("records")
    ids = []
    for i, row in enumerate(rows):
        row["entry_time"] = _utc_time(row["entry_time"], "entry_time")
        row["exit_time"] = _utc_time(row["exit_time"], "exit_time")
        if row["entry_time"] not in frame.index or row["exit_time"] not in frame.index:
            raise ValueError(f"Candidate {i} entry/exit must be a supplied 5m bar open")
        if row["entry_time"] >= row["exit_time"]:
            raise ValueError(f"Candidate {i} exit must follow entry")
        raw_id = row.get("trade_id")
        tid = str(raw_id) if raw_id is not None and pd.notna(raw_id) else f"BTC{row['entry_time'].value}"
        if not tid or not tid.isascii() or not tid.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"Invalid native trade id: {tid!r}")
        row["trade_id"] = tid
        if not np.isfinite(row["initial_stop"]):
            # Preserve invalid-risk candidates for a counted no-trade output.
            row["initial_stop"] = math.nan
        else:
            row["initial_stop"] = float(row["initial_stop"])
            if row["initial_stop"] <= 0:
                raise ValueError(f"Candidate {tid} stop must be positive")
            if abs(row["initial_stop"] / tick - round(row["initial_stop"] / tick)) > 1e-5:
                raise ValueError(f"Candidate {tid} stop is not aligned to the configured tick")
        reason = str(row["exit_reason"])
        if reason == "ema20_downcross_next_open":
            row["schedule_reason"] = "cloud_exit"
        elif reason == "dataset_end":
            row["schedule_reason"] = "dataset_end"
        else:
            raise ValueError(f"Unsupported scheduled exit reason for {tid}: {reason}")
        ids.append(tid)
    if len(set(ids)) != len(ids):
        raise ValueError("Candidate trade_id values must be unique")
    rows.sort(key=lambda r: r["entry_time"])
    for previous, current in zip(rows, rows[1:]):
        if current["entry_time"] < previous["exit_time"]:
            raise ValueError("Candidate opportunities overlap; this adapter expects non-overlapping flip cycles")

    return frame, rows


def _make_execution_frame(frame):
    """Convert each 5m candle to an open event and a later full OHLC event."""
    rows = []
    open_prices = {}
    close_event_to_open = {}
    for open_time, source in frame.iterrows():
        open_ns = int(open_time.value)
        close_event_ns = open_ns + BAR_NS - 1
        op = float(source.open)
        open_prices[open_ns] = op
        close_event_to_open[close_event_ns] = open_ns
        rows.append((open_ns, op, op, op, op, SYNTHETIC_VOLUME))
        rows.append((close_event_ns, float(source.open), float(source.high),
                     float(source.low), float(source.close), SYNTHETIC_VOLUME))
    execution = pd.DataFrame(
        rows,
        columns=["time", "open", "high", "low", "close", "volume"],
    ).set_index("time").sort_index(kind="stable")
    execution.index = pd.to_datetime(execution.index, utc=True).as_unit("ns")
    if not execution.index.is_unique:
        raise ValueError("Native open and close event timestamps overlap")
    return execution, open_prices, close_event_to_open


def run_native_fills(
    bars,
    candidates,
    *,
    quantity=BASE_QUANTITY,
    tick=PRICE_TICK,
    taker_fee=TAKER_FEE,
    maker_fee=0.0002,
    symbol="BTC",
):
    """Run one native replay and return one actual-fill row per candidate.

    Parameters
    ----------
    bars : DataFrame
        5m OHLCV indexed by UTC bar OPEN time. ``open`` is used for the
        zero-range opening event; actual H/L/C enter native execution only at
        the candle's final event (one nanosecond before the next open).
    candidates : DataFrame
        Must include ``entry_time``, ``exit_time``, ``initial_stop`` and
        ``exit_reason`` from ``signals.build_candidates``. Scheduled market
        exits occur at the supplied exit bar's open unless the native stop
        filled first. Entry and exit times are never reconstructed from a
        fill-price scan.
    quantity : float
        Fixed base quantity for every candidate (1 BTC by default).
    tick : float
        OKX BTC-USDT-SWAP price increment (0.1 USDT by default).
    taker_fee : float
        Per-side taker fee charged by native fee model on each actual fill.
    maker_fee : float
        Included to keep the native fee model explicit; no LIMIT order is used.
    symbol : str
        Base currency/instrument symbol for the synthetic research venue.
    """
    started = time.perf_counter()
    if (not math.isfinite(float(maker_fee)) or not 0 <= float(maker_fee) < 1
            or not math.isfinite(float(taker_fee)) or not 0 <= float(taker_fee) < 1):
        raise ValueError("Fee rates must be finite and in [0, 1)")
    frame, candidate_rows = _prepare_inputs(bars, candidates, tick, quantity)
    execution, open_prices, close_event_to_open = _make_execution_frame(frame)
    precision = -Decimal(str(tick)).as_tuple().exponent
    venue = Venue("RESEARCH")
    instrument_id = InstrumentId(Symbol(f"{symbol}-USDT"), venue)
    instrument = CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=Symbol(f"{symbol}-USDT"),
        base_currency=Currency.from_str(symbol),
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=precision,
        price_increment=Price(tick, precision),
        size_precision=0,
        size_increment=Quantity.from_int(1),
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal("0"),
        taker_fee=Decimal("0"),
        ts_event=0,
        ts_init=0,
    )
    fee_model = ActualFillFee(maker_fee, taker_fee)
    engine = BacktestEngine(BacktestEngineConfig(
        logging=LoggingConfig(bypass_logging=True),
        risk_engine=RiskEngineConfig(bypass=True),
        run_analysis=False,
    ))
    try:
        engine.add_venue(
            venue,
            OmsType.HEDGING,
            AccountType.MARGIN,
            [Money(1_000_000_000_000, USDT)],
            base_currency=USDT,
            fee_model=fee_model,
            frozen_account=True,
            use_message_queue=False,
            reject_stop_orders=True,
            bar_execution=True,
            liquidity_consumption=False,
        )
        engine.add_instrument(instrument)
        bar_type = BarType.from_str(f"{instrument.id}-5-MINUTE-LAST-EXTERNAL")
        strategy = _OpenEventStrategy(
            instrument,
            bar_type,
            candidate_rows,
            open_prices,
            close_event_to_open,
            tick,
            quantity,
        )
        engine.add_strategy(strategy)
        execution["volume"] = SYNTHETIC_VOLUME
        native_bars = BarDataWrangler(bar_type, instrument).process(execution)
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter("always")
            engine.add_data(native_bars)
            engine.run()
        if strategy.errors:
            raise RuntimeError(f"Native order errors: {strategy.errors[:5]}")
        if strategy.active:
            raise RuntimeError(f"Open trades after replay: {sorted(strategy.active)}")
        if engine.cache.orders_open():
            raise RuntimeError("Open native orders remain after replay")
        if len(strategy.rows) != len(candidate_rows):
            raise RuntimeError(
                f"Expected {len(candidate_rows)} output rows, got {len(strategy.rows)}",
            )
        result = pd.DataFrame(strategy.rows).sort_values("entry_time", kind="stable").reset_index(drop=True)
        audit = {
            "engine": "nautilus_trader",
            "version": ENGINE_VERSION,
            "native_replay_runs": 1,
            "source_bars": len(frame),
            "native_execution_bars": len(native_bars),
            "candidates": len(candidate_rows),
            "fills": int(result.exit_reason.isin(["hard_stop", "cloud_exit", "dataset_end"]).sum()),
            "invalid_open_risk": strategy.invalid_open_risk,
            "open_events": strategy.open_events,
            "full_ohlc_events": strategy.full_ohlc_events,
            "quantity_btc": quantity,
            "price_tick": tick,
            "maker_fee": maker_fee,
            "taker_fee": taker_fee,
            "open_price_assumption": "zero-range native bar at recorded OHLC open; zero spread",
            "intrabar_stop_execution": "NautilusTrader bar_execution OHLC; O-H-L-C fixed path",
            "synthetic_liquidity": "unlimited volume; no impact",
            "latency": "none",
            "funding": "not modeled",
            "slippage": "none",
            "seconds": round(time.perf_counter() - started, 3),
            "runtime_warnings": sorted({str(w.message) for w in emitted}),
        }
        result.attrs["audit"] = audit
        return result
    finally:
        engine.dispose()


__all__ = ["run_native_fills"]
