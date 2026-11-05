"""Close-entry trades with a per-bar protective stop schedule, NautilusTrader 1.231.0.

Built for the signal-center cloud score study (exp-signal-center-score-20261106-v1):
the reference plan exits at the first of a fixed stop and the intrabar EMA20/50
cross of the signal timeframe. The cross price for the next timeframe bar is a
known level once a timeframe bar closes, so the exit is a native STOP_MARKET
order whose trigger is amended at every completed timeframe close. The existing
``nautilus_independent`` adapter only supports a single stop move.

Execution semantics (frame index = UTC bar CLOSE time, as in nautilus_independent):
- Entries are market orders submitted in ``on_bar`` of ``entry_i``; they fill at
  that bar's close (bar execution, message queue).
- Resting stops are matched by Nautilus inside each bar (O-H-L-C path) before
  ``on_bar``; an opening gap beyond the trigger fills at the open.
- At a schedule close the caller supplies the next level. If that level is
  already at/through the current close the trade exits with a market order at
  that close (the reference plan exits at the next open; on continuous 5m data
  the difference is the close-to-open gap).
- A schedule level is valid until ``expiry_ns``; a bar closing after it (data or
  timeframe gap) forces a market exit at that close. The last frame bar forces
  a market exit and marks the trade censored.
Fees: ActualFillFee (maker for LIMIT, taker otherwise; here every fill is taker).
Liquidity is synthetic and unlimited; no funding, latency or spread. Returns are
independent per-trade unit returns, not account returns.
Source: https://nautilustrader.io/docs/latest/concepts/backtesting/bar-execution/
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import math
import time
import warnings

import numpy as np
import pandas as pd
import nautilus_trader
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, RiskEngineConfig, StrategyConfig
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
NOTIONAL = 100.0


@dataclass
class _Trade:
    row: dict
    position_id: PositionId
    quantity: Quantity
    level: float = float("nan")
    level_kind: str = ""
    expiry_ns: int = 0
    stop_order: object = None
    entry_px: float = float("nan")
    entry_fee: float = 0.0
    exit_reason: str = ""
    updates: int = 0
    fills: list = field(default_factory=list)


class StopScheduleStrategy(Strategy):
    def __init__(self, instrument, bar_type, closes_ns, last_close, entries, schedules, tick):
        super().__init__(StrategyConfig(oms_type="HEDGING", log_events=False, log_commands=False))
        self.instrument, self.bar_type, self.tick = instrument, bar_type, tick
        self.eps = tick * 1e-7
        self.indices = {int(t): i for i, t in enumerate(closes_ns)}
        self.closes = last_close
        self.last_i = len(closes_ns) - 1
        self.by_entry = {}
        for row in entries:
            self.by_entry.setdefault(int(row["entry_i"]), []).append(row)
        # schedules: key -> (bar index array, level array, expiry_ns array)
        self.schedules = schedules
        self.update_at = {}
        for key, (idx, _, _) in schedules.items():
            for k, i in enumerate(idx):
                self.update_at.setdefault(int(i), []).append((key, k))
        self.active, self.order_map = {}, {}
        self.rows, self.errors = [], []

    def on_start(self):
        self.subscribe_bars(self.bar_type)

    # --- helpers -------------------------------------------------------
    def _level(self, trade, key, k):
        side, fixed = trade.row["side"], trade.row["stop"]
        cross = float(self.schedules[key][1][k])
        if not math.isfinite(cross):
            return fixed, "fixed"
        if side > 0:
            raw = math.floor(cross / self.tick + 1e-7) * self.tick
            return (raw, "flip") if raw > fixed + self.eps else (fixed, "fixed")
        raw = math.ceil(cross / self.tick - 1e-7) * self.tick
        return (raw, "flip") if raw < fixed - self.eps else (fixed, "fixed")

    def _through(self, side, level, price):
        return price <= level + self.eps if side > 0 else price >= level - self.eps

    def _submit(self, order, trade, phase):
        self.order_map[str(order.client_order_id)] = (trade, phase)
        self.submit_order(order, position_id=trade.position_id)

    def _market_exit(self, trade, reason):
        if trade.exit_reason:
            return
        trade.exit_reason = reason
        if trade.stop_order is not None and not trade.stop_order.is_closed:
            self.cancel_order(trade.stop_order)
        side = trade.row["side"]
        order = self.order_factory.market(self.instrument.id, OrderSide.SELL if side > 0 else OrderSide.BUY,
                                          trade.quantity, reduce_only=True)
        self._submit(order, trade, "exit")

    def _apply(self, trade, level, kind, expiry_ns, price):
        trade.level, trade.level_kind, trade.expiry_ns = level, kind, expiry_ns
        side = trade.row["side"]
        if self._through(side, level, price):
            self._market_exit(trade, f"{kind}_at_close")
            return
        trigger = self.instrument.make_price(level)
        if trade.stop_order is None:
            order = self.order_factory.stop_market(
                self.instrument.id, OrderSide.SELL if side > 0 else OrderSide.BUY,
                trade.quantity, trigger_price=trigger, reduce_only=True)
            trade.stop_order = order
            self._submit(order, trade, "stop")
        elif abs(float(trade.stop_order.trigger_price) - level) > self.eps:
            trade.updates += 1
            self.modify_order(trade.stop_order, trigger_price=trigger)

    # --- events --------------------------------------------------------
    def on_bar(self, bar):
        i = self.indices[bar.ts_event]
        price = self.closes[i]
        updates = {key: k for key, k in self.update_at.get(i, ())}
        for tid, trade in list(self.active.items()):
            if trade.exit_reason or math.isnan(trade.entry_px):
                continue
            if i == self.last_i:
                self._market_exit(trade, "data_end")
                continue
            key = trade.row["schedule"]
            if key in updates:
                k = updates[key]
                level, kind = self._level(trade, key, k)
                self._apply(trade, level, kind, int(self.schedules[key][2][k]), price)
            elif bar.ts_event > trade.expiry_ns:
                self._market_exit(trade, "gap")
        if i == self.last_i:
            return
        for row in self.by_entry.get(i, ()):
            side = row["side"]
            qty = self.instrument.make_qty(max(1, round(NOTIONAL / row["entry"])))
            trade = _Trade(row, PositionId(row["trade_id"]), qty)
            self.active[row["trade_id"]] = trade
            order = self.order_factory.market(self.instrument.id, OrderSide.BUY if side > 0 else OrderSide.SELL, qty)
            self._submit(order, trade, "entry")

    def on_order_filled(self, event):
        trade, phase = self.order_map[str(event.client_order_id)]
        i = self.indices[event.ts_event]
        price = float(event.last_px)
        fee = float(event.commission)
        if event.last_qty != trade.quantity:
            self.errors.append(f"partial fill {trade.row['trade_id']}")
        trade.fills.append((phase, i, price, fee))
        row = trade.row
        if phase == "entry":
            if i != row["entry_i"] or abs(price - row["entry"]) > max(self.eps, 4 * math.ulp(price)):
                self.errors.append(f"entry off close {row['trade_id']}")
            trade.entry_px, trade.entry_fee = price, fee
            key = row["schedule"]
            idx = self.schedules[key][0]
            k = int(np.searchsorted(idx, i))
            if k >= len(idx) or idx[k] != i:
                self.errors.append(f"no schedule at entry {row['trade_id']}")
                self._market_exit(trade, "no_schedule")
                return
            level, kind = self._level(trade, key, k)
            self._apply(trade, level, kind, int(self.schedules[key][2][k]), price)
            return
        # exit fill (stop or market)
        if phase == "stop":
            trade.exit_reason = f"{trade.level_kind}_stop"
        side = row["side"]
        gross = side * (price - trade.entry_px) / trade.entry_px
        fees = (trade.entry_fee + fee) / (trade.entry_px * float(trade.quantity))
        self.rows.append(dict(
            trade_id=row["trade_id"], side=side, entry_i=row["entry_i"], exit_i=i,
            entry_px=trade.entry_px, exit_px=price, exit_reason=trade.exit_reason,
            stop_updates=trade.updates, gross_bp=gross * 1e4, fee_bp=fees * 1e4,
            net_bp=(gross - fees) * 1e4, censored=trade.exit_reason == "data_end",
            framework_position_id=str(event.position_id)))
        self.active.pop(row["trade_id"], None)

    def on_order_rejected(self, event):
        self.errors.append(f"rejected {event.client_order_id}: {event.reason}")

    def on_order_modify_rejected(self, event):
        self.errors.append(f"modify rejected {event.client_order_id}: {event.reason}")


def run_schedule(frame, entries, schedules, tick, symbol="X", taker=0.0005, maker=0.0002,
                 batch_bars=50_000):
    """Run independent close-entry trades whose stops follow ``schedules``.

    frame: 5m OHLC, tz-aware UTC CLOSE-time index, prices on the ``tick`` grid.
    entries: trade_id, entry_i, side (+1/-1), entry (== close at entry_i),
      stop (fixed, tick aligned, beyond entry), schedule (key into schedules).
    schedules: key -> (bar_i int array sorted, next-bar cross level array,
      expiry_ns array). Only levels known at each close are supplied; the
      strategy never reads later bars. Returns (trades DataFrame, audit dict).
    """
    started = time.perf_counter()
    if nautilus_trader.__version__ != ENGINE_VERSION:
        raise RuntimeError(f"Expected Nautilus {ENGINE_VERSION}")
    if frame.empty or frame.index.tz is None or not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError("Bars must be nonempty, unique, chronological and tz-aware")
    frame = frame[["open", "high", "low", "close"]].copy()
    frame.index = frame.index.as_unit("ns")
    prices = frame.to_numpy()
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("Invalid OHLC")
    if (np.abs(prices / tick - np.rint(prices / tick)) > 1e-5).any():
        raise ValueError("OHLC not on tick grid")
    rows = entries.to_dict("records")
    ids = [r["trade_id"] for r in rows]
    if not rows or len(set(ids)) != len(ids):
        raise ValueError("Need unique trades")
    closes = prices[:, 3]
    for r in rows:
        i = int(r["entry_i"])
        if r["side"] not in (-1, 1) or not 0 <= i < len(frame):
            raise ValueError("Bad entry")
        if abs(closes[i] - r["entry"]) > tick * 1e-7:
            raise ValueError("Entry differs from completed close")
        if not r["side"] * (r["entry"] - r["stop"]) > 0 or abs(r["stop"] / tick - round(r["stop"] / tick)) > 1e-5:
            raise ValueError("Stop must be tick aligned and beyond entry")
        if r["schedule"] not in schedules:
            raise ValueError("Unknown schedule")
    close_ns = frame.index.asi8
    for key, (idx, levels, expiry) in schedules.items():
        idx = np.asarray(idx)
        if (len(idx) != len(levels) or len(idx) != len(expiry) or (np.diff(idx) <= 0).any()
                or idx.min(initial=0) < 0 or idx.max(initial=0) >= len(frame)
                or (np.asarray(expiry) <= close_ns[idx]).any()):
            raise ValueError(f"Schedule {key} must be sorted, in range and expire after its close (ns)")
    decimals = -Decimal(str(tick)).as_tuple().exponent
    venue = Venue("RESEARCH")
    instrument = CryptoPerpetual(
        instrument_id=InstrumentId(Symbol(f"{symbol}-USDT"), venue), raw_symbol=Symbol(f"{symbol}-USDT"),
        base_currency=Currency.from_str(symbol), quote_currency=USDT, settlement_currency=USDT,
        is_inverse=False, price_precision=decimals, price_increment=Price(tick, decimals),
        size_precision=0, size_increment=Quantity.from_int(1),
        margin_init=Decimal("0"), margin_maint=Decimal("0"),
        maker_fee=Decimal("0"), taker_fee=Decimal("0"), ts_event=0, ts_init=0)
    fee_model = ActualFillFee(maker, taker)
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True),
                                                 risk_engine=RiskEngineConfig(bypass=True), run_analysis=False))
    try:
        engine.add_venue(venue, OmsType.HEDGING, AccountType.MARGIN, [Money(1_000_000_000_000, USDT)],
                         base_currency=USDT, fee_model=fee_model, frozen_account=True,
                         use_message_queue=True, reject_stop_orders=True, bar_execution=True,
                         liquidity_consumption=False)
        engine.add_instrument(instrument)
        bar_type = BarType.from_str(f"{instrument.id}-5-MINUTE-LAST-EXTERNAL")
        strategy = StopScheduleStrategy(instrument, bar_type, frame.index.asi8, closes, rows, schedules, tick)
        engine.add_strategy(strategy)
        entry_at = sorted(strategy.by_entry)
        start, fed = 0, 0
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            while start < len(frame):
                if not strategy.active and not engine.cache.orders_open():
                    nxt = int(np.searchsorted(entry_at, start))
                    if nxt == len(entry_at):
                        break
                    start = max(start, entry_at[nxt])
                chunk = frame.iloc[start:start + batch_bars].copy()
                chunk["volume"] = 1e12
                engine.add_data(BarDataWrangler(bar_type, instrument).process(chunk))
                engine.run(streaming=True)
                engine.clear_data()
                fed += len(chunk)
                start += len(chunk)
            engine.end()
        if strategy.errors:
            raise RuntimeError(f"Native order errors: {strategy.errors[:3]}")
        out = pd.DataFrame(strategy.rows)
        positions = engine.cache.positions()
        if strategy.active or len(out) != len(rows) or len(positions) != len(rows) or any(p.is_open for p in positions):
            raise RuntimeError("Open or missing trades after run")
        if engine.cache.orders_open():
            raise RuntimeError("Orphaned orders")
        audit = dict(engine="nautilus_trader", version=ENGINE_VERSION, tick=tick, trades=len(out),
                     bars_total=len(frame), bars_fed=fed, maker=maker, taker=taker,
                     seconds=round(time.perf_counter() - started, 3))
        return out, audit
    finally:
        engine.dispose()
