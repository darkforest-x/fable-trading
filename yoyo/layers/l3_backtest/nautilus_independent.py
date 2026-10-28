"""Independent close-entry trades executed by NautilusTrader 1.231.0.

The caller supplies causal close-time features and frozen entries. Only completed
management closes update protection; resting STOP_MARKET orders are matched by
Nautilus before on_bar. Market entries/exits submitted in on_bar fill at that
close. HEDGING position IDs preserve simultaneous same-symbol long/short trades.
Source: https://nautilustrader.io/docs/latest/concepts/backtesting/bar-execution/
and the installed 1.231.0 engine.pyx (APIs differ from later online examples).

Scope: independent signal research, synthetic unlimited liquidity, no funding,
no latency/spread, original-entry-notional round-trip cost. Frozen account and
large balance prevent cross-trade funding constraints; account returns are NOT
reported. OHLC ordering is O-H-L-C. Optional fixed targets use native resting
limit orders; stop/target collisions follow that path, not a stop-first oracle.
MFE excludes stop-hit bars, preserving the conservative historical definition.
Optional one-time partial realization is a 5m CLOSE market order, never an
intrabar target. Remaining shares keep the same protection/management path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
import math
import time
import warnings

import numpy as np
import pandas as pd
import nautilus_trader
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FeeModel, FillModel
from nautilus_trader.config import (
    BacktestEngineConfig, LoggingConfig, RiskEngineConfig, StrategyConfig,
)
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import BarType, BookOrder
from nautilus_trader.model.enums import AccountType, BookType, OmsType, OrderSide, OrderType
from nautilus_trader.model.identifiers import InstrumentId, PositionId, Symbol, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.trading.strategy import Strategy

ENGINE_VERSION = "1.231.0"


@dataclass(frozen=True)
class ExitPolicy:
    """Management inputs are current completed-bar ATR and confirmed H1 side."""

    trail_arm_r: float = 5.0
    trail_atr_multiple: float = 4.0
    breakeven_r: float | None = None
    opposite_hour_exit: bool = True
    round_trip_cost: float = .002
    partial_close_r: float | None = None
    partial_fraction: float = .5
    fixed_target_r: float | None = None
    time_breakeven_minutes: int | None = None
    rsi_scaleout: bool = False
    rsi_skip_events: int = 0
    confirmed_pivot_exit: bool = False
    structural_pivot_stop: bool = False
    strict_pivot_ties: bool = False
    pivot_minutes: int = 15
    target_from_entry: bool = False
    target_breakeven: bool = False
    breakeven_includes_cost: bool = True
    breakeven_on_touch: bool = False
    intrabar_path: str | None = None
    fill_maker_fee: float | None = None
    fill_taker_fee: float | None = None
    reclaim_next_bar: bool = False
    # Process each synthetic intrabar segment monotonically (adaptive bar
    # ordering). Without it a rising segment is replayed open-high-low-close
    # and revisits its low after its high (owner 2026-10-25 OLHC short check).
    monotone_segments: bool = False
    # Reclaim with a resting initial stop (the entry's stop) from the fill
    # until the confirmation close: a reclaim moves it to entry, a failed
    # reclaim cancels it before the market exit (owner 2026-10-25 backstop).
    reclaim_backstop: bool = False

    def __post_init__(self):
        actual = (self.fill_maker_fee, self.fill_taker_fee)
        if any(v is not None for v in actual):
            if (any(v is None or not math.isfinite(v) or not 0 <= v < 1 for v in actual)
                    or self.round_trip_cost != 0):
                raise ValueError("Actual-fill fees require both rates and zero legacy round-trip cost")
        if self.target_breakeven and not self.target_from_entry:
            raise ValueError("Target breakeven requires per-entry targets and parent groups")
        if self.reclaim_next_bar and (not self.target_from_entry or self.intrabar_path is None
                or self.opposite_hour_exit or self.breakeven_r is not None
                or self.time_breakeven_minutes is not None or self.target_breakeven
                or self.breakeven_on_touch or self.breakeven_includes_cost):
            raise ValueError("Reclaim requires standalone targets, explicit path and entry-price protection")
        if self.intrabar_path not in (None, "OHLC", "OLHC"):
            raise ValueError("Intrabar path must be OHLC or OLHC")
        if self.monotone_segments and self.intrabar_path is None:
            raise ValueError("Monotone segments require an explicit intrabar path")
        if self.reclaim_backstop and not self.reclaim_next_bar:
            raise ValueError("Backstop requires the reclaim rule")
        if self.breakeven_on_touch and (self.breakeven_r is None or self.intrabar_path is None):
            raise ValueError("Touch breakeven requires an R trigger and explicit intrabar path")
        if self.breakeven_on_touch and self.breakeven_includes_cost:
            raise ValueError("Touch breakeven currently supports entry-price protection only")
        if self.intrabar_path is not None and ((self.fixed_target_r is None and not self.target_from_entry)
                or self.opposite_hour_exit or self.partial_close_r is not None
                or self.time_breakeven_minutes is not None or self.rsi_scaleout
                or self.confirmed_pivot_exit or self.structural_pivot_stop):
            raise ValueError("Explicit intrabar path supports standalone fixed-target management")
        pivot_management = self.confirmed_pivot_exit or self.structural_pivot_stop
        if self.confirmed_pivot_exit and self.structural_pivot_stop:
            raise ValueError("Pivot market exits and structural stops are separate policies")
        if self.pivot_minutes not in (5, 15) or (self.pivot_minutes != 15 and not pivot_management):
            raise ValueError("Pivot timeframe must be 5m or 15m and requires pivot exits")
        if self.target_from_entry and (self.fixed_target_r is not None
                or self.partial_close_r is not None or self.rsi_scaleout
                or pivot_management):
            raise ValueError("Per-entry targets cannot combine with another target or reduction policy")
        if self.strict_pivot_ties and not pivot_management:
            raise ValueError("Strict pivot ties require confirmed pivot exits")
        if pivot_management and (self.opposite_hour_exit
                or self.breakeven_r is not None or self.time_breakeven_minutes is not None
                or self.partial_close_r is not None or self.fixed_target_r is not None
                or self.rsi_scaleout):
            raise ValueError("Confirmed pivot exits require standalone initial-stop management")
        if (not isinstance(self.rsi_skip_events, int)
                or self.rsi_skip_events not in (0,1)
                or (self.rsi_skip_events and not self.rsi_scaleout)):
            raise ValueError("RSI skip requires an enabled RSI policy and zero or one event")
        if self.time_breakeven_minutes is not None:
            if (not isinstance(self.time_breakeven_minutes, int)
                    or self.time_breakeven_minutes <= 0
                    or self.time_breakeven_minutes % 5):
                raise ValueError("Timed BE must be a positive whole number of 5m bars")
            if self.breakeven_r is not None:
                raise ValueError("Time and R breakeven triggers cannot be combined")
        if self.rsi_scaleout and (self.partial_close_r is not None or self.fixed_target_r is not None):
            raise ValueError("RSI reductions cannot combine with other partial/target policies")
        values = [self.trail_arm_r, self.trail_atr_multiple]
        if self.breakeven_r is not None:
            values.append(self.breakeven_r)
        if self.partial_close_r is not None:
            values.append(self.partial_close_r)
        if self.fixed_target_r is not None:
            values.append(self.fixed_target_r)
            if self.partial_close_r is not None:
                raise ValueError("Fixed target and partial reduction cannot be combined")
        if not all(math.isfinite(x) and x > 0 for x in values):
            raise ValueError("R and ATR parameters must be finite and positive")
        if not math.isfinite(self.round_trip_cost) or self.round_trip_cost < 0:
            raise ValueError("Invalid cost")
        if not math.isfinite(self.partial_fraction) or not 0 < self.partial_fraction < 1:
            raise ValueError("Partial fraction must be strictly between zero and one")
        if Fraction(str(self.partial_fraction)).denominator > 10000:
            raise ValueError("Partial fraction requires excessive research sizing")


def confirmed_pivots(frame, execution_minutes=5, strict=False, pivot_minutes=15):
    """Return 5m or 15m 2/2 extremes only at their confirmation close.

    Inputs are completed OHLC indexed by close time. Positive shifts inspect
    only the current and previous four completed pivot candles. Pivot timestamps
    identify the center candle OPEN, never its confirmation or backdated fill.
    Default ties select the latest equal extreme; strict mode excludes ties
    on both sides for sensitivity because TradingView does not document ties.
    """
    if pivot_minutes not in (5, 15) or execution_minutes <= 0 or pivot_minutes % execution_minutes:
        raise ValueError("Pivot timeframe must contain whole execution candles")
    rule = f"{pivot_minutes}min"
    quarter = frame.resample(rule, closed="right", label="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"})
    count = frame.close.resample(rule, closed="right", label="right").count()
    quarter.loc[count != pivot_minutes // execution_minutes] = np.nan
    outputs = []
    for column, direction in (("high", 1), ("low", -1)):
        value = direction * quarter[column]
        center = value.shift(2)
        left = ((center > value.shift(3)) & (center > value.shift(4))) if strict else (
            (center >= value.shift(3)) & (center >= value.shift(4)))
        valid = left & (center > value.shift(1)) & (center > value)
        outputs.append((direction * center.where(valid)).reindex(frame.index).to_numpy(float))
    at = pd.Series((quarter.index - pd.Timedelta(minutes=3*pivot_minutes)).as_unit("ns").asi8,
                   index=quarter.index).reindex(frame.index).fillna(-1).to_numpy(np.int64)
    return (*outputs, at)


class EntryNotionalFee(FeeModel):
    """Charge half the frozen round-trip cost on original entry notional."""

    def __init__(self, cost):
        super().__init__()
        self.rate = Decimal(str(cost)) / 2
        self.entries = {}

    def get_commission(self, order, fill_qty, fill_px, instrument):
        entry = Decimal(str(self.entries[str(order.client_order_id)]))
        return Money(entry * fill_qty.as_decimal() * self.rate, USDT)


class ActualFillFee(FeeModel):
    """Research resting limits pay maker; market/stop fills pay taker notional."""

    def __init__(self, maker, taker):
        super().__init__()
        self.maker, self.taker = Decimal(str(maker)), Decimal(str(taker))
        self.entries = {}  # Existing submission metadata contract.

    def get_commission(self, order, fill_qty, fill_px, instrument):
        rate = self.maker if order.order_type == OrderType.LIMIT else self.taker
        return Money(fill_px.as_decimal() * fill_qty.as_decimal() * rate, USDT)


class IndependentLimitFill(FillModel):
    """Keep native limit triggering independent across simultaneous positions.

    Nautilus1.231.0 determine_limit_price_and_volume mutates shared quotes to a
    maker's limit price until the entire matching iteration finishes. A second
    crossed target can then be delayed. Its documented simulated-book hook
    bypasses that mutation. Native matching still decides WHEN a limit executes;
    this hook supplies only its limit-price fill, with synthetic full liquidity.
    Market/later-stop orders retain the default model. Limit-entry protection
    can register a stop crossing price for that entry bar only. Native matching
    determines the trigger; no future bars are inspected.
    """

    def __init__(self):
        super().__init__()
        self.entry_bar_stops = {}

    def get_orderbook_for_fill_simulation(self, instrument, order, best_bid, best_ask):
        stop_price = self.entry_bar_stops.get(str(order.client_order_id))
        if order.order_type != OrderType.LIMIT and stop_price is None:
            return None
        book = OrderBook(instrument_id=instrument.id, book_type=BookType.L2_MBP)
        side = OrderSide.SELL if order.side == OrderSide.BUY else OrderSide.BUY
        price = order.price if stop_price is None else instrument.make_price(stop_price)
        book.add(BookOrder(side=side, price=price,
                           size=order.quantity, order_id=1), 0, 0)
        return book


@dataclass
class TradeState:
    source: dict
    position_id: PositionId
    quantity: Quantity
    stop: float
    be_price: float
    peak: float = 0.
    arm_i: int = -1
    be_trigger_i: int = -1
    be_effective_i: int = -1
    stop_kind: int = 0
    deferred: int = 0
    stop_order: object = None
    target_order: object = None
    target_price: float = 0.
    fees: float = 0.
    fills: list = field(default_factory=list)
    remaining: int = 0
    partial_i: int = -1
    partial_price: float = float("nan")
    partial_weight: float = 0.
    rsi_count: int = 0
    rsi_first_i: int = -1
    rsi_skipped: int = 0
    rsi_seen: int = 0
    structural_updates: int = 0
    reclaim_due_ns: int = -1
    reclaim_i: int = -1
    reclaim_status: str = "not_applicable"

    def __post_init__(self):
        self.remaining = int(self.quantity)


class IndependentCloseStrategy(Strategy):
    def __init__(self, instrument, bar_type, frame, atr, hour, entries, policy, fees,
                 protection_tick, execution_minutes=5, rsi_side=None, entry_order_mode="market",
                 entry_fill_model=None, point_phases=None, entry_updates=None):
        super().__init__(StrategyConfig(
            oms_type="HEDGING", log_events=False, log_commands=False,
        ))
        self.instrument = instrument
        self.bar_type = bar_type
        self.frame = frame
        self.prices = frame[["open", "high", "low", "close"]].to_numpy()
        self.atr, self.hour = np.asarray(atr), np.asarray(hour)
        self.execution_minutes = execution_minutes
        self.entry_order_mode = entry_order_mode
        self.pending, self.opportunity_rows = {}, []
        # Reclaim scale-in legs (entry column 'parent'): they share the parent's
        # confirmation clock and are cancelled when the parent closes unfilled.
        self.children, self.reclaim_due_by_trade = {}, {}
        if "parent" in entries:
            for tid, parent in zip(entries.trade_id, entries.parent):
                if isinstance(parent, str) and parent:
                    self.children.setdefault(parent, []).append(tid)
        self.pending_updates = {}
        self.pending_update_rows = []
        if entry_updates is not None:
            for update in entry_updates.to_dict('records'):
                self.pending_updates.setdefault(int(update['bar_i']), []).append(update)
        self.entry_fill_model = entry_fill_model
        self.entering_limit_i = None
        self.rsi_side = np.zeros(len(frame)) if rsi_side is None else np.asarray(rsi_side)
        self.be_clock = frame.index.minute % 5 == 0
        self.policy, self.fees = policy, fees
        self.pivots = confirmed_pivots(frame, execution_minutes, policy.strict_pivot_ties, policy.pivot_minutes) if (policy.confirmed_pivot_exit or policy.structural_pivot_stop) else None
        self.tick = protection_tick
        self.eps = self.tick * 1e-7
        self.indices = {int(t): i for i, t in enumerate(frame.index.asi8)}
        self.point_phases = {} if point_phases is None else point_phases
        self.indices.update({t: i for t, (i, _) in self.point_phases.items()})
        # Pyramid legs (entry column 'pyramid_parent', owner 2026-10-27 滚仓): stop
        # entries in the trade's favour at 'entry', armed only once the parent's
        # reclaim succeeds; each fill moves the stop of the parent and of every
        # filled leg to its 'ratchet' price (tighten only); legs share the
        # parent's target and end. Unarmed or unfilled legs are opportunities.
        self.pyramids, self.pyr_pending, self.pyr_armed, self.pyr_active = {}, {}, set(), {}
        if "pyramid_parent" in entries:
            for row in entries.to_dict("records"):
                parent = row.get("pyramid_parent")
                if isinstance(parent, str) and parent:
                    self.pyramids.setdefault(parent, []).append(row)
            for legs in self.pyramids.values():
                legs.sort(key=lambda r: int(r["pyramid_step"]))
        pyramid_ids = {r["trade_id"] for legs in self.pyramids.values() for r in legs}
        self.by_entry = {}
        for row in entries.to_dict("records"):
            if row["trade_id"] not in pyramid_ids:
                self.by_entry.setdefault(int(row["entry_i"]), []).append(row)
        self.active, self.order_map = {}, {}
        self.rows, self.fill_rows, self.errors = [], [], []
        self.protection_updates = []
        self.max_open = self.hedged_bars = 0

    def on_start(self):
        self.subscribe_bars(self.bar_type)

    def _submit(self, order, state, phase):
        key = str(order.client_order_id)
        self.order_map[key] = (state, phase, order.quantity)
        self.fees.entries[key] = state.source["entry"]
        self.submit_order(order, position_id=state.position_id)

    def _rests(self, row):
        """A resting limit entry: the run's mode, unless the row asks for a completed-close market entry."""
        return self.entry_order_mode == "limit" and row.get("order_mode", "limit") != "market"

    def _market(self, state, entering=False, reason=0, quantity=None):
        side = state.source["side"] * (1 if entering else -1)
        order = self.order_factory.market(
            self.instrument.id, OrderSide.BUY if side > 0 else OrderSide.SELL,
            (state.quantity if entering else self.instrument.make_qty(
                state.remaining if quantity is None else quantity)), reduce_only=not entering,
        )
        self._submit(order, state, "entry" if entering else reason)

    def _submit_protective_stop(self, state):
        side = state.source["side"]
        order = self.order_factory.stop_market(
            self.instrument.id, OrderSide.SELL if side > 0 else OrderSide.BUY,
            self.instrument.make_qty(state.remaining),
            trigger_price=self.instrument.make_price(state.stop),
            reduce_only=True,
        )
        state.stop_order = order
        if self.entering_limit_i is not None:
            # A bar update can jump directly from above the buy limit to below
            # its stop. Immediate protection must model the intervening stop
            # crossing, not fill at the synthetic low. On a genuine opening gap
            # retain the adverse open. Native matching alone decides triggering.
            opened = self.prices[self.entering_limit_i, 0]
            price = min(opened, state.stop) if side > 0 else max(opened, state.stop)
            self.entry_fill_model.entry_bar_stops[str(order.client_order_id)] = price
        self._submit(order, state, "stop")

    def _submit_target(self, state):
        if self.policy.fixed_target_r is None and not self.policy.target_from_entry:
            return
        row = state.source
        if self.policy.target_from_entry:
            target = row["target_price"]
            if not np.isfinite(target):
                return
            state.target_price = float(target)
        else:
            raw = (row["entry"]+row["side"]*self.policy.fixed_target_r*row["risk"])/self.tick
            state.target_price = (math.ceil(raw-1e-8) if row["side"] > 0
                                  else math.floor(raw+1e-8))*self.tick
        # A short target below zero is unreachable, never clipped to an easier TP.
        if state.target_price <= 0:
            return
        order = self.order_factory.limit(
            self.instrument.id, OrderSide.SELL if row["side"] > 0 else OrderSide.BUY,
            self.instrument.make_qty(state.remaining),
            price=self.instrument.make_price(state.target_price), reduce_only=True,
        )
        state.target_order = order
        self._submit(order, state, "target")

    def _cancel_target(self, state):
        if state.target_order is not None and not state.target_order.is_closed:
            self.cancel_order(state.target_order)

    def _cancel_stop(self, state):
        if state.stop_order is not None and not state.stop_order.is_closed:
            self.cancel_order(state.stop_order)

    def _protect_target_siblings(self, filled, i):
        """A native TP fill immediately protects only its parent's live legs.

        No completed-bar OHLC or future target is inspected. Synchronous order
        amendments allow a later segment of this same bar to stop the runner.
        Each runner covers its own original entry-notional fee; earlier profit
        is not used to relax protection below cost-adjusted entry.
        """
        for state in list(self.active.values()):
            if (state.remaining <= 0 or state.source["parent_id"] != filled.source["parent_id"]
                    or state.be_effective_i >= 0):
                continue
            if state.be_trigger_i < 0:
                state.be_trigger_i = i
            if (self.policy.fill_taker_fee is not None
                    and state.source["side"]*(filled.target_price-state.be_price) < self.tick-self.eps):
                # A tiny TP1 can lie inside the remaining leg's fee breakeven.
                # Keep its original stop until a later observed close/open point
                # makes the new stop executable; never filter this entry away.
                state.deferred += 1
                continue
            state.be_effective_i = i
            if state.source["side"]*(state.be_price-state.stop) > self.eps:
                state.stop, state.stop_kind = state.be_price, 1
                self.modify_order(state.stop_order,
                                  trigger_price=self.instrument.make_price(state.stop))

    def _pyramid_opportunity(self, src, status, i, submitted=-1):
        self.pyr_armed.add(src["trade_id"])
        self.opportunity_rows.append(dict(trade_id=src["trade_id"], status=status, submitted_i=submitted,
                                          fill_i=-1, expire_i=int(src["end_i"]), cancelled_i=i))

    def _arm_pyramids(self, parent_state, i, close):
        """Submit the parent's pyramid stop entries at its successful reclaim close."""
        for src in self.pyramids.get(parent_state.source["trade_id"], ()):
            row, side = dict(src), int(src["side"])
            trigger = float(row["entry"])
            if side*(trigger-close) < self.tick-self.eps:
                # Already beyond this level at the decision close: no late add.
                self._pyramid_opportunity(row, "pyramid_passed", i, i)
                continue
            self.pyr_armed.add(row["trade_id"])
            state = TradeState(row, PositionId(row["trade_id"]), self.instrument.make_qty(max(1, int(1000/trigger))),
                               parent_state.stop, parent_state.stop)
            order = self.order_factory.stop_market(
                self.instrument.id, OrderSide.BUY if side > 0 else OrderSide.SELL, state.quantity,
                trigger_price=self.instrument.make_price(trigger))
            self.pyr_pending[row["trade_id"]] = (state, order)
            self._submit(order, state, "pyr_entry")

    def _fill_pyramid(self, state, i, price):
        row, side = state.source, int(state.source["side"])
        parent_id = row["pyramid_parent"]
        self.pyr_pending.pop(row["trade_id"])
        # Returns are per this leg's own notional at its actual (possibly gapped) fill.
        row.update(trigger=row["entry"], entry=price, risk=price, signal_entry_i=i, entry_i=i,
                   entry_time=str(self.frame.index[i]), signal_entry_time=str(self.frame.index[i]),
                   entry_interval_start=str(self.frame.index[i]-pd.Timedelta(minutes=self.execution_minutes)))
        self.active[row["trade_id"]] = state
        self.opportunity_rows.append(dict(trade_id=row["trade_id"], status="filled", submitted_i=i,
                                          fill_i=i, expire_i=int(row["end_i"])))
        parent = self.active.get(parent_id)
        group = ([parent] if parent is not None else []) + [self.active[t] for t in self.pyr_active.get(parent_id, ()) if t in self.active]
        self.pyr_active.setdefault(parent_id, set()).add(row["trade_id"])
        stop = parent.stop if parent is not None else state.stop
        ratchet = float(row["ratchet"])
        if side*(ratchet-stop) > self.eps:
            stop = ratchet
            for member in group:
                member.stop, member.stop_kind = stop, 10
                if member.stop_order is not None and not member.stop_order.is_closed:
                    self.modify_order(member.stop_order, trigger_price=self.instrument.make_price(stop))
        # A leg filling after a deeper leg already ratcheted the group joins that stop.
        state.stop, state.stop_kind = stop, (10 if abs(stop-ratchet) <= self.eps or (parent is not None and parent.stop_kind == 10) else 1)
        self._submit_protective_stop(state)
        if state.remaining > 0:
            self._submit_target(state)

    def on_order_filled(self, event):
        state, phase, requested = self.order_map[str(event.client_order_id)]
        i = self.indices[event.ts_event]
        price = float(event.last_px)
        state.fees += float(event.commission)
        fill = dict(trade_id=state.source["trade_id"], phase=str(phase),
                    bar_i=i, ts_ns=event.ts_event, price=price,
                    quantity=float(event.last_qty), commission=float(event.commission),
                    position_id=str(event.position_id),
                    client_order_id=str(event.client_order_id))
        self.fill_rows.append(fill)
        state.fills.append(fill)
        if event.last_qty != requested:
            raise RuntimeError(
                f"Partial fill for {state.source['trade_id']} ({phase}): "
                f"got {event.last_qty}, requested {requested}, bar {i}"
            )
        if phase == "pyr_entry":
            self._fill_pyramid(state, i, price)
            return
        if phase == "entry":
            if self._rests(state.source):
                row = state.source
                if not row["entry_i"] < i <= row["expire_i"]:
                    raise RuntimeError("Limit filled before submission or after expiry")
                if abs(price-row["entry"]) > max(self.eps, 2*math.ulp(price), 2*math.ulp(row["entry"])):
                    raise RuntimeError("Limit fill differs from conservative resting price")
                row["signal_entry_i"] = row["entry_i"]
                row["signal_entry_time"] = str(self.frame.index[row["entry_i"]])
                row["entry_i"], row["entry_time"] = i, str(self.frame.index[i])
                # Intrabar time is unknown: preserve the interval, and require
                # exit pivot centers to begin after its conservative close bound.
                row["entry_interval_start"] = str(self.frame.index[i]-pd.Timedelta(minutes=self.execution_minutes))
                self.pending.pop(row["trade_id"])
                self.active[row["trade_id"]] = state
                self.opportunity_rows.append(dict(trade_id=row["trade_id"], status="filled",
                    submitted_i=row["signal_entry_i"], fill_i=i, expire_i=row["expire_i"]))
            elif i != state.source["entry_i"] or abs(price-state.source["entry"]) > max(
                    self.eps, 2*math.ulp(price), 2*math.ulp(state.source["entry"])):
                # Fixed-point native Price -> float may differ by one ULP at
                # large notionals; keep tolerance far below one market tick.
                raise RuntimeError("Entry did not fill at the signal bar close")
            if self.policy.reclaim_next_bar:
                # Fill-derived clock only. The touch timeframe candle and its
                # immediate successor must both close before protection exists.
                minutes = int(state.source["reclaim_minutes"])
                opened = self.frame.index[i]-pd.Timedelta(minutes=self.execution_minutes)
                due = opened.floor(f"{minutes}min")+pd.Timedelta(minutes=2*minutes)
                row = state.source
                parent = row.get("parent")
                if isinstance(parent, str) and parent in self.reclaim_due_by_trade:
                    due = pd.Timestamp(self.reclaim_due_by_trade[parent], tz="UTC")
                self.reclaim_due_by_trade[row["trade_id"]] = due.value
                # Scale-in legs may only fill up to the parent's decision close;
                # afterwards the parent is at its breakeven or closed.
                due_i = int(self.frame.index.searchsorted(due))
                for child in self.children.get(row["trade_id"], ()):
                    if child in self.pending:
                        src = self.pending[child][0].source
                        src["expire_i"] = min(int(src["expire_i"]), max(due_i, i))
                state.reclaim_due_ns = due.value
                state.reclaim_status = "pending"
                # Missing pre-confirmation observations make the path unknown,
                # including scheduled session closures. This administrative
                # boundary is censored, never scored as an executed stop/TP.
                # Only timestamps are examined; no future price is consumed.
                end = min(len(self.frame)-1, int(self.frame.index.searchsorted(due)))
                times = self.frame.index.asi8[i:end+1]
                gaps = np.flatnonzero(np.diff(times) != pd.Timedelta(minutes=self.execution_minutes).value)
                if len(gaps):
                    boundary = i+int(gaps[0])
                    if boundary < state.source["end_i"]:
                        state.source["end_i"] = boundary
                        state.source["censor_reason"] = "missing_preconfirmation_interval"
                if self.policy.reclaim_backstop:
                    # A completed-close market entry has no intrabar entry bar to model.
                    self.entering_limit_i = i if self._rests(state.source) else None
                    self._submit_protective_stop(state)
                    self.entering_limit_i = None
            else:
                self.entering_limit_i = i if self._rests(state.source) else None
                self._submit_protective_stop(state)
                self.entering_limit_i = None
            # Synchronous protection can close a just-filled limit immediately.
            # The callback then resumes here with zero remaining exposure.
            if state.remaining > 0:
                self._submit_target(state)
            return
        state.remaining -= int(event.last_qty)
        if phase == "rsi_partial":
            if state.rsi_count >= 5 or state.remaining < 0:
                raise RuntimeError("RSI reductions exceeded the original position")
            if abs(price-self.prices[i, 3]) > self.eps:
                raise RuntimeError("RSI reduction did not fill at the confirmed close")
            state.rsi_count += 1
            if state.rsi_first_i < 0:
                state.rsi_first_i = i
            if state.remaining > 0:
                self._submit_protective_stop(state)
                return
            phase = 6
        if phase == "partial":
            if state.partial_i >= 0 or state.remaining <= 0:
                raise RuntimeError("Duplicate partial or exhausted runner")
            if abs(price-self.prices[i, 3]) > self.eps:
                raise RuntimeError("Partial did not fill at the completed 5m close")
            state.partial_i, state.partial_price = i, price
            state.partial_weight = float(event.last_qty)/float(state.quantity)
            # The old stop was cancelled before reducing. Protect only the
            # remaining shares, at the unchanged/ratcheted management price.
            self._submit_protective_stop(state)
            return
        if state.remaining != 0:
            raise RuntimeError("Final exit left a nonzero runner")
        if phase == "stop":
            reason = (13 if state.stop_kind == 10 else 9 if state.stop_kind == 4 else 8 if state.stop_kind == 3
                      else 5 if state.stop_kind == 1 else 2 if state.arm_i >= 0 else 1)
        elif phase == "target":
            # The native matcher cancels all remaining reduce-only orders for
            # this closed HEDGING position after dispatching the fill. Sending
            # another cancellation here races that native cleanup.
            reason = 4
            if self.policy.target_breakeven:
                self._protect_target_siblings(state, i)
        else:
            reason = phase
        row = dict(state.source)
        risk, entry, side = row["risk"], row["entry"], row["side"]
        gross = side*(price-entry)/risk
        if state.partial_i >= 0:
            gross = (state.partial_weight*side*(state.partial_price-entry)/risk
                     + (1-state.partial_weight)*gross)
        if self.policy.rsi_scaleout:
            gross = sum(f["quantity"]*side*(f["price"]-entry)/risk
                        for f in state.fills if f["phase"] != "entry")/float(state.quantity)
        # Native bar events carry close timestamps. Preserve a separate logical
        # open timestamp for gap exits without pretending intrabar time is known.
        point_phase = self.point_phases.get(event.ts_event, (i, 0))[1]
        gap = (phase == "stop" and point_phase == 0 and state.be_effective_i != i
               and side*(self.prices[i, 0]-state.stop) < -self.eps)
        row.update(
            exit_i=i, exit_price=price, reason=int(reason), peak_r=state.peak,
            arm_i=state.arm_i, protection=state.stop,
            steps=i-int(row["entry_i"]), ambiguous=int(
                state.target_price > 0 and
                self.prices[i, 2] <= min(state.stop, state.target_price)+self.eps and
                self.prices[i, 1] >= max(state.stop, state.target_price)-self.eps),
            be_trigger_i=state.be_trigger_i, be_effective_i=state.be_effective_i,
            be_price=state.be_price, stop_kind=state.stop_kind,
            deferred_closes=state.deferred, gross_r=gross,
            fee_r=self.policy.round_trip_cost*entry/risk,
            net_r=gross-self.policy.round_trip_cost*entry/risk,
            net_notional_bp=(gross*risk/entry-self.policy.round_trip_cost)*10000,
            exit_time=str(self.frame.index[i]-pd.Timedelta(minutes=self.execution_minutes if gap else 0)),
            framework_exit_time=str(self.frame.index[i]),
            framework_position_id=str(event.position_id),
            framework_commission=state.fees, quantity=float(state.quantity),
        )
        if self.policy.fill_taker_fee is not None:
            fee_r = state.fees/(float(state.quantity)*risk)
            row.update(fee_r=fee_r, net_r=gross-fee_r,
                       net_notional_bp=(gross-fee_r)*risk/entry*10000)
        if self.policy.intrabar_path:
            row.update(intrabar_path=self.policy.intrabar_path, exit_path_phase=point_phase,
                       simulated_event_ns=int(event.ts_event))
        if self.policy.partial_close_r is not None:
            row.update(partial_i=state.partial_i, partial_price=state.partial_price,
                       partial_weight=state.partial_weight,
                       partial_time=str(self.frame.index[state.partial_i])
                       if state.partial_i >= 0 else "",
                       runner_gross_r=side*(price-entry)/risk)
        if self.policy.rsi_scaleout:
            row.update(rsi_partial_count=state.rsi_count,rsi_first_i=state.rsi_first_i,
                       rsi_skipped_unprofitable=state.rsi_skipped,
                       rsi_seen=state.rsi_seen,
                       rsi_realized_weight=state.rsi_count/5,
                       runner_gross_r=side*(price-entry)/risk)
        if self.policy.confirmed_pivot_exit:
            pivot_price = self.pivots[0 if side == 1 else 1][i] if reason == 7 else float("nan")
            row.update(pivot_price=pivot_price,
                       pivot_open_time=str(pd.Timestamp(self.pivots[2][i], tz="UTC")) if reason == 7 else "",
                       pivot_reference_r=side*(pivot_price-entry)/risk)
        if self.policy.structural_pivot_stop:
            row["structural_updates"] = state.structural_updates
        if self.policy.reclaim_next_bar:
            row.update(reclaim_due_ns=state.reclaim_due_ns,
                       reclaim_i=state.reclaim_i, reclaim_status=state.reclaim_status,
                       normalization="entry_notional_not_initial_risk")
        self.rows.append(row)
        del self.active[row["trade_id"]]
        for child in self.children.get(row["trade_id"], ()):
            if child in self.pending:
                self._cancel_pending(child, i, "parent_closed")
        for src in self.pyramids.get(row["trade_id"], ()):
            if src["trade_id"] in self.pyr_pending:
                _, order = self.pyr_pending.pop(src["trade_id"])
                self.cancel_order(order)
                self._pyramid_opportunity(src, "parent_closed", i, i)
            elif src["trade_id"] not in self.pyr_armed:
                self._pyramid_opportunity(src, "pyramid_not_armed", i)

    def on_order_rejected(self, event):
        self.errors.append(str(event))

    def on_order_denied(self, event):
        self.errors.append(str(event))

    def on_order_modify_rejected(self, event):
        self.errors.append(str(event))

    def on_order_cancel_rejected(self, event):
        self.errors.append(str(event))

    def _cancel_pending(self, key, i, status):
        state, order = self.pending.pop(key)
        self.cancel_order(order)
        self.opportunity_rows.append(dict(trade_id=key, status=status,
            submitted_i=state.source['entry_i'], fill_i=-1,
            expire_i=state.source['expire_i'], cancelled_i=i))
        for src in self.pyramids.get(key, ()):
            if src["trade_id"] not in self.pyr_armed:
                self._pyramid_opportunity(src, "pyramid_not_armed", i)

    def _update_pending(self, i):
        """Apply amendments only after their calculation candle has closed.

        Native matching of the old resting order has already finished. A
        marketable revision is cancelled rather than filled retrospectively
        at the newly calculated moving average.
        """
        for update in self.pending_updates.get(i, []):
            key = update['trade_id']
            if key not in self.pending:
                continue
            if update.get('action') == 'cancel':
                self._cancel_pending(key, i, str(update['status']))
                continue
            state, order = self.pending[key]
            side = state.source['side']
            entry, stop = float(update['entry']), float(update['stop'])
            if side*(self.prices[i, 3]-entry) < self.tick-self.eps:
                self._cancel_pending(key, i, 'amendment_not_resting')
                continue
            state.source.update(entry=entry, stop=stop, risk=side*(entry-stop),
                                last_amendment_i=i)
            state.stop = stop
            self.pending_update_rows.append(dict(update))
            if abs(float(order.price)-entry) > self.eps:
                self.modify_order(order, price=self.instrument.make_price(entry))

    def on_bar(self, bar):
        i = self.indices[bar.ts_event]
        if self.policy.intrabar_path:
            # Native matching precedes this callback at each OHLC segment.
            # The boundary lies beyond the favorable approach to the limit;
            # reaching it cancels only entries that are still pending.
            for key, (state, _) in list(self.pending.items()):
                boundary = state.source.get('cancel_boundary', float('nan'))
                if np.isfinite(boundary) and state.source['side']*(float(bar.close)-boundary) >= -self.eps:
                    self._cancel_pending(key, i, 'returned_to_monday_range')
            # Only the current path endpoint is visible here. The full parent
            # bar's features/entries are consumed at its final close, not early.
            if self.policy.breakeven_on_touch:
                price = float(bar.close)
                for state in list(self.active.values()):
                    r = state.source
                    if (i > r["entry_i"] and state.be_trigger_i < 0
                            and r["side"]*(price-r["entry"])+self.eps
                            >= self.policy.breakeven_r*r["risk"]):
                        state.be_trigger_i = state.be_effective_i = i
                        state.stop, state.stop_kind = state.be_price, 1
                        self.modify_order(state.stop_order,
                            trigger_price=self.instrument.make_price(state.stop))
            if self.point_phases[bar.ts_event][1] != 3:
                return
        if self.entry_fill_model is not None:
            # The current bar has finished matching. Later gap stops must use
            # their own native price, never the entry bar's override.
            self.entry_fill_model.entry_bar_stops.clear()
        _, high, low, close = self.prices[i]
        p = self.policy
        quarter = np.isfinite(self.atr[i]) and self.atr[i] > 0
        # Native stops have already executed. Only surviving positions observe
        # this bar's favorable extreme and then update close-derived protection.
        for state in list(self.active.values()):
            row = state.source
            entry, side, risk = row["entry"], row["side"], row["risk"]
            if i > int(row["entry_i"]):
                state.peak = max(state.peak, side*((high if side > 0 else low)-entry)/risk)
            if row.get("fill_close_exit") is True or row.get("fill_close_exit") is np.True_:
                if i == int(row["entry_i"]):
                    # Decided by the caller from the fill bar's completed close
                    # (e.g. a score that reads that bar); the position survived
                    # its own bar's native target and backstop.
                    self._cancel_target(state)
                    self._cancel_stop(state)
                    self._market(state, reason=14)
                    continue
            if (p.reclaim_next_bar and state.reclaim_status == "pending"
                    and self.frame.index[i].value >= state.reclaim_due_ns):
                # Source gaps are bounded by the caller. Never silently confirm
                # on a later candle when the scheduled decision close is absent.
                if self.frame.index[i].value != state.reclaim_due_ns:
                    raise RuntimeError("Missing exact reclaim decision close")
                state.reclaim_i = i
                ref = row.get("reclaim_ref", float("nan"))
                ref = entry if not (isinstance(ref, (int, float)) and math.isfinite(ref)) else float(ref)
                offset = row.get("be_offset_bp", 0.)
                offset = float(offset) if isinstance(offset, (int, float)) and math.isfinite(offset) else 0.
                be_raw = ref*(1+side*offset/1e4)/self.tick
                be = (math.ceil(be_raw-1e-8) if side > 0 else math.floor(be_raw+1e-8))*self.tick if offset else ref
                if side*(close-ref)+self.eps >= self.tick and side*(close-be)+self.eps < self.tick:
                    # Reclaimed, but the close has not cleared the fee-covering
                    # breakeven price: a stop there would already be crossed.
                    state.reclaim_status = "reclaimed_inside_be"
                    self._cancel_target(state)
                    self._cancel_stop(state)
                    self._market(state, reason=12)
                    continue
                if side*(close-ref)+self.eps >= self.tick:
                    state.reclaim_status = "reclaimed"
                    state.stop, state.stop_kind = be, 1
                    state.be_trigger_i = state.be_effective_i = i
                    if state.stop_order is not None and not state.stop_order.is_closed:
                        self.modify_order(state.stop_order, trigger_price=self.instrument.make_price(state.stop))
                    else:
                        self._submit_protective_stop(state)
                    self._arm_pyramids(state, i, close)
                else:
                    state.reclaim_status = "failed"
                    self._cancel_target(state)
                    self._cancel_stop(state)
                    self._market(state, reason=11)
                    continue
            old_stop = state.stop
            if (p.target_breakeven and p.fill_taker_fee is not None
                    and state.be_trigger_i >= 0 and state.be_effective_i < 0):
                if side*(close-state.be_price)+self.eps >= self.tick:
                    state.be_effective_i = i
                    state.stop, state.stop_kind = state.be_price, 1
                else:
                    state.deferred += 1
            update_i = row.get("stop_update_i", -1)
            stop_now = False
            if (np.isfinite(update_i) and i == int(update_i)
                    and side*(row["stop_update"]-state.stop) > self.eps):
                # One tightening to a price first known at this close (e.g. the
                # touch bar's wick extreme); it cannot fill retrospectively.
                # A close already at that price is a triggered stop: market out.
                state.stop, state.stop_kind = row["stop_update"], 4
                stop_now = side*(close-state.stop)+self.eps < self.tick
            if p.time_breakeven_minutes is not None and self.be_clock[i]:
                elapsed=(i-int(row["entry_i"]))*self.execution_minutes
                if state.be_trigger_i < 0 and elapsed >= p.time_breakeven_minutes:
                    state.be_trigger_i = i
                if state.be_trigger_i >= 0 and state.be_effective_i < 0:
                    if side*(close-state.be_price)+self.eps >= self.tick:
                        state.be_effective_i = i
                        if side*(state.be_price-state.stop) > self.eps:
                            state.stop,state.stop_kind=state.be_price,1
                    else:
                        state.deferred += 1
            if quarter and not (p.confirmed_pivot_exit or p.structural_pivot_stop):
                move = side*(close-entry)
                if p.fixed_target_r is None and not p.target_from_entry and state.arm_i < 0 and move+self.eps >= p.trail_arm_r*risk:
                    state.arm_i = i
                if p.breakeven_r is not None and not p.breakeven_on_touch:
                    if state.be_trigger_i < 0 and move+self.eps >= p.breakeven_r*risk:
                        state.be_trigger_i = i
                    if state.be_trigger_i >= 0 and state.be_effective_i < 0:
                        if side*(close-state.be_price)+self.eps >= self.tick:
                            state.be_effective_i = i
                            if side*(state.be_price-state.stop) > self.eps:
                                state.stop, state.stop_kind = state.be_price, 1
                        else:
                            state.deferred += 1
                if state.arm_i >= 0:
                    raw = (close-side*p.trail_atr_multiple*self.atr[i])/self.tick
                    candidate = (math.floor(raw+1e-8) if side > 0 else math.ceil(raw-1e-8))*self.tick
                    if side*(candidate-state.stop) > 0:
                        state.stop, state.stop_kind = candidate, 2
            if p.structural_pivot_stop:
                # Long protection uses confirmed lows; short protection highs.
                # Center candle must open at/after entry. Two right bars are
                # complete; the amended stop cannot fill retrospectively.
                pivot_price = self.pivots[1 if side == 1 else 0][i]
                center = self.pivots[2][i]
                if (center >= self.frame.index[int(row["entry_i"])].value
                        and np.isfinite(pivot_price) and i < int(row["end_i"])):
                    raw = pivot_price/self.tick
                    candidate = (math.floor(raw+1e-8) if side > 0 else math.ceil(raw-1e-8))*self.tick
                    if (side*(candidate-state.stop) > self.eps
                            and side*(close-candidate)+self.eps >= self.tick):
                        self.protection_updates.append(dict(
                            trade_id=row["trade_id"], bar_i=i, pivot_open_ns=int(center),
                            pivot_price=float(pivot_price), previous_stop=state.stop,
                            stop=candidate, close=float(close)))
                        state.stop, state.stop_kind = candidate, 3
                        state.structural_updates += 1
                        if state.arm_i < 0:
                            state.arm_i = i
            reverse = p.opposite_hour_exit and state.arm_i >= 0 and self.hour[i] == -side
            pivot_exit = False
            if p.confirmed_pivot_exit:
                pivot_price = self.pivots[0 if side == 1 else 1][i]
                pivot_exit = (self.pivots[2][i] >= self.frame.index[int(row["entry_i"])].value
                              and np.isfinite(pivot_price) and side*(pivot_price-entry) > 0)
            if reverse or pivot_exit or stop_now or i == int(row["end_i"]):
                self._cancel_stop(state)
                self._cancel_target(state)
                self._market(state, reason=9 if stop_now else 7 if pivot_exit else 3 if reverse else 0)
            elif p.rsi_scaleout and self.rsi_side[i] == -side:
                # Reduce one fifth of ORIGINAL units, only at a fee-positive
                # opposite strong-diamond close. Losing events consume no slot.
                state.rsi_seen += 1
                if state.rsi_seen <= p.rsi_skip_events:
                    if abs(state.stop-old_stop) > self.eps:
                        self.modify_order(state.stop_order,
                                          trigger_price=self.instrument.make_price(state.stop))
                elif side*(close-entry)-p.round_trip_cost*entry > self.eps:
                    self.cancel_order(state.stop_order)
                    self._market(state, reason="rsi_partial",
                                 quantity=min(int(state.quantity)//5,state.remaining))
                else:
                    state.rsi_skipped += 1
                    if abs(state.stop-old_stop) > self.eps:
                        self.modify_order(state.stop_order,
                                          trigger_price=self.instrument.make_price(state.stop))
            elif (p.partial_close_r is not None and state.partial_i < 0
                  and side*(close-entry)+self.eps >= p.partial_close_r*risk):
                # Old resting stops win before on_bar; terminal close exits
                # above win over redundant partial+final orders at one price.
                self.cancel_order(state.stop_order)
                fraction = Fraction(str(p.partial_fraction))
                units = int(state.quantity)*fraction.numerator//fraction.denominator
                self._market(state, reason="partial", quantity=units)
            elif abs(state.stop-old_stop) > self.eps:
                self.modify_order(state.stop_order,
                                  trigger_price=self.instrument.make_price(state.stop))
        for key, (state, order) in list(self.pending.items()):
            if i >= int(state.source["expire_i"]):
                self._cancel_pending(key, i, 'expired')
        self._update_pending(i)
        for source in self.by_entry.get(i, []):
            row = dict(source)
            side, entry = row["side"], row["entry"]
            be_cost = p.round_trip_cost if p.breakeven_includes_cost else 0.
            be_value = entry+side*be_cost*entry
            if p.fill_taker_fee is not None and p.breakeven_includes_cost:
                # Cover this leg's allocated market entry and stop exit fees.
                be_value = entry*(1+side*p.fill_taker_fee)/(1-side*p.fill_taker_fee)
            be_raw = be_value/self.tick
            be = (math.ceil(be_raw-1e-8) if side > 0 else math.floor(be_raw+1e-8))*self.tick
            units = max(1, int(1000/entry))
            if p.partial_close_r is not None:
                denominator = Fraction(str(p.partial_fraction)).denominator
                units = ((units+denominator-1)//denominator)*denominator
            if p.rsi_scaleout:
                units = ((units+4)//5)*5
            state = TradeState(
                row, PositionId(row["trade_id"]),
                # Research returns are normalized independently of order size.
                # Whole units avoid fractional position roundoff at ~1e9 PEPE:
                # native float exposure can otherwise trim 1e-7 units on exit.
                self.instrument.make_qty(units), row["stop"], be,
            )
            parent = row.get("parent")
            if isinstance(parent, str) and parent in self.reclaim_due_by_trade:
                # The parent already filled (a market parent fills at this close before its legs are
                # submitted): the leg may only rest until the parent's decision close.
                due = pd.Timestamp(self.reclaim_due_by_trade[parent], tz="UTC")
                row["expire_i"] = min(int(row["expire_i"]), max(int(self.frame.index.searchsorted(due)), i+1))
            if self._rests(row):
                order = self.order_factory.limit(self.instrument.id,
                    OrderSide.BUY if side > 0 else OrderSide.SELL, state.quantity,
                    price=self.instrument.make_price(entry))
                self.pending[row["trade_id"]] = (state, order)
                self._submit(order, state, "entry")
            else:
                self.active[row["trade_id"]] = state
                self._market(state, entering=True)
        self.max_open = max(self.max_open, len(self.active))
        self.hedged_bars += int({s.source["side"] for s in self.active.values()} == {-1, 1})


def ohlc_path_frame(frame, path):
    """Expose a stated OHLC path as opening point and three monotone segments.

    These are synthetic execution segments, not observed lower-timeframe bars.
    All timestamps occupy the last four nanoseconds of the original interval;
    only sequence is asserted. Features and entries remain on the real close.
    An opening gap stays a gap; later continuous segments let native matching
    fill a crossed stop at its trigger instead of at an artificial endpoint.
    """
    cols = ["open", "high", "low", "close"] if path == "OHLC" else ["open", "low", "high", "close"]
    points = frame[cols].to_numpy()
    previous = np.column_stack([points[:, 0], points[:, :-1]])
    data = np.column_stack([previous.ravel(), np.maximum(previous, points).ravel(),
                            np.minimum(previous, points).ravel(), points.ravel()])
    times = (frame.index.as_unit("ns").asi8[:, None]+np.arange(-3, 1)).ravel()
    expanded = pd.DataFrame(data, columns=["open", "high", "low", "close"],
                            index=pd.to_datetime(times, utc=True))
    phases = {int(t): (i//4, i%4) for i, t in enumerate(times)}
    return expanded, phases


def run_native(frame, atr, hour, entries, policy, tick=1e-9, symbol="PEPE", market_tick=None,
               *, execution_minutes=5, rsi_side=None, entry_order_mode="market", entry_updates=None,
               batch_bars=None):
    """Execute supplied entries using native orders, fills and HEDGING positions.

    Frame index is UTC close time. ATR is NaN except at completed management
    closes; hour contains confirmed direction only at completed hourly closes.
    Entries need unique trade_id, entry_i, side, entry, stop, risk and end_i.
    No future excursion or expected-exit fields are read by the strategy.
    Optional batch_bars streams the same native engine across bounded chunks;
    open orders and positions survive batch boundaries (1.231.0 run docstring).
    """
    started = time.perf_counter()
    if entry_order_mode not in ("market", "limit"):
        raise ValueError("Unknown entry order mode")
    limit_entry = entry_order_mode == "limit"
    if policy.reclaim_next_bar and not limit_entry and "parent" in entries:
        raise ValueError("Scale-in legs on the reclaim rule require resting limit entries")
    market_rows = (entries.order_mode.astype(str) == "market").to_numpy() if "order_mode" in entries else np.zeros(len(entries), bool)
    if "order_mode" in entries:
        if not (limit_entry and entries.order_mode.astype(str).isin(["limit", "market"]).all()):
            raise ValueError("Per-row order modes need a limit run and values limit / market")
        if "parent" in entries and (market_rows & entries.parent.map(lambda v: isinstance(v, str) and bool(v)).to_numpy()).any():
            raise ValueError("Scale-in legs must rest as limits")
        if "fill_close_exit" in entries and (market_rows & entries.fill_close_exit.astype(bool).to_numpy()).any():
            raise ValueError("Fill-close exits apply to resting entries only")
    if "fill_close_exit" in entries:
        flags = entries.fill_close_exit
        if not (limit_entry and policy.reclaim_next_bar and flags.map(lambda v: isinstance(v, (bool, np.bool_))).all()):
            raise ValueError("Fill-close exits need boolean flags on resting reclaim entries")
    if policy.reclaim_backstop:
        st, en, sd = (entries[c].to_numpy(float) for c in ('stop', 'entry', 'side'))
        if not (np.isfinite(st).all() and (sd*(en-st) > 0).all()):
            raise ValueError("Backstop needs a finite stop beyond every entry")
    if any(c in entries for c in ("be_offset_bp", "reclaim_ref", "parent")) and not policy.reclaim_next_bar:
        raise ValueError("Breakeven offsets and scale-in legs require the reclaim rule")
    if "be_offset_bp" in entries:
        off = entries.be_offset_bp.to_numpy(float)
        if not (np.isfinite(off).all() and (off >= 0).all()):
            raise ValueError("Breakeven offset must be a finite non-negative bp value")
    if "parent" in entries:
        by_id = entries.set_index("trade_id")
        for r in entries[entries.parent.map(lambda v: isinstance(v, str) and bool(v))].itertuples():
            if r.parent not in by_id.index:
                raise ValueError("Scale-in leg references an unknown parent")
            par = by_id.loc[r.parent]
            if (par.side != r.side or isinstance(par.get("parent"), str) and par.get("parent")
                    or not r.side*(par.entry-r.entry) > 0
                    or abs(float(r.reclaim_ref)-(float(par.reclaim_ref) if np.isfinite(float(par.get("reclaim_ref", np.nan))) else float(par.entry))) > 1e-12
                    or int(r.entry_i) != int(par.entry_i) or int(r.expire_i) != int(par.expire_i)):
                raise ValueError("Scale-in leg must deepen its parent's side, reference its entry and share its window")
    pyramid = (entries.pyramid_parent.map(lambda v: isinstance(v, str) and bool(v)).to_numpy()
               if "pyramid_parent" in entries else np.zeros(len(entries), bool))
    if pyramid.any():
        if not (policy.reclaim_next_bar and limit_entry and policy.target_from_entry):
            raise ValueError("Pyramid legs require the reclaim rule, resting entries and per-entry targets")
        by_id = entries.set_index("trade_id")
        legs = entries[pyramid]
        if legs.duplicated(["pyramid_parent", "pyramid_step"]).any():
            raise ValueError("Pyramid steps must be unique per parent")
        for r in legs.itertuples():
            if r.pyramid_parent not in by_id.index:
                raise ValueError("Pyramid leg references an unknown parent")
            par = by_id.loc[r.pyramid_parent]
            nested = any(isinstance(par.get(c), str) and par.get(c) for c in ("pyramid_parent", "parent"))
            side = int(par.side)
            if (nested or r.side != side or int(r.end_i) != int(par.end_i) or int(r.entry_i) != int(par.entry_i)
                    or side*(r.entry-par.entry) < tick-tick*1e-7
                    or side*(r.ratchet-par.entry) < -tick*1e-7 or side*(r.entry-r.ratchet) < tick-tick*1e-7
                    or not np.isfinite(r.target_price) or abs(r.target_price-par.target_price) > tick*1e-7
                    or side*(r.target_price-r.entry) < tick-tick*1e-7
                    or any(abs(v/tick-round(v/tick)) > 1e-5 for v in (r.entry, r.ratchet))):
                raise ValueError("Pyramid leg must trigger beyond its primary parent's entry, ratchet between that "
                                 "entry and its trigger, and share target, submission bar and end")
    if limit_entry and policy.target_breakeven:
        raise ValueError("Target sibling breakeven currently requires completed-close entries")
    if limit_entry and not (policy.confirmed_pivot_exit or policy.target_from_entry
                            or policy.fixed_target_r is not None):
        raise ValueError("Resting entry requires initial-stop/pivot or fixed-target management")
    if execution_minutes not in (1,5):
        raise ValueError("Execution resolution must be 1m or 5m")
    if batch_bars is not None and (not isinstance(batch_bars, int) or batch_bars < 2):
        raise ValueError("Streaming batch size must be an integer of at least two bars")
    if policy.rsi_scaleout:
        if (rsi_side is None or len(rsi_side)!=len(frame)
                or not np.isin(rsi_side,[-1,0,1]).all()):
            raise ValueError("RSI policy requires aligned confirmed side events")
    if nautilus_trader.__version__ != ENGINE_VERSION:
        raise RuntimeError(f"Expected Nautilus {ENGINE_VERSION}; revalidate before upgrading")
    if frame.empty or not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError("Bars must be nonempty, unique and chronological")
    if frame.index.tz is None or len(frame) != len(atr) or len(frame) != len(hour):
        raise ValueError("Close-time features must align with timezone-aware bars")
    # Pandas 3 can retain microsecond resolution. Nautilus uses nanoseconds.
    frame = frame.copy()
    frame.index = frame.index.as_unit("ns")
    if entries.empty or not entries.trade_id.is_unique:
        raise ValueError("Need nonempty, uniquely identified independent trades")
    if entry_updates is not None and len(entry_updates):
        if not limit_entry:
            raise ValueError('Pending amendments require limit entries')
        lookup = entries.set_index('trade_id')
        if entry_updates.duplicated(['trade_id', 'bar_i']).any():
            raise ValueError('Duplicate pending amendment at one close')
        for u in entry_updates.to_dict('records'):
            if u['trade_id'] not in lookup.index:
                raise ValueError('Unknown amended trade')
            r = lookup.loc[u['trade_id']]
            if not r.entry_i < u['bar_i'] <= r.expire_i:
                raise ValueError('Amendment must follow submission and precede expiry')
            if u.get('action') != 'cancel':
                if (not np.isfinite([u['entry'], u['stop']]).all() or u['stop'] <= 0
                        or r.side*(u['entry']-u['stop']) < tick-tick*1e-7
                        or r.side*(r.target_price-u['entry']) < tick-tick*1e-7
                        or any(abs(u[c]/tick-round(u[c]/tick)) > 1e-5 for c in ('entry','stop'))):
                    raise ValueError('Invalid pending price geometry')
    if 'cancel_boundary' in entries:
        if not limit_entry or policy.intrabar_path is None:
            raise ValueError('Price boundary cancellation requires explicit path and limit entry')
        if (entries.side*(entries.cancel_boundary-entries.entry) < tick-tick*1e-7).any():
            raise ValueError('Cancellation boundary must be beyond the resting approach')
    if policy.target_breakeven:
        if "parent_id" not in entries or entries.parent_id.isna().any():
            raise ValueError("Target breakeven requires explicit parent IDs")
        shared = ["entry_i", "side", "entry", "stop", "risk", "end_i"]
        if entries.groupby("parent_id")[shared].nunique().gt(1).any().any():
            raise ValueError("Target breakeven siblings must share entry and initial risk")
        cost = policy.round_trip_cost if policy.breakeven_includes_cost else 0.
        # Protection must be feasible at every native target fill.
        if policy.fill_taker_fee is None and (entries.side*(entries.target_price-entries.entry)-cost*entries.entry < 2*tick).any():
            raise ValueError("Target breakeven needs targets beyond cost protection")
    market_tick = tick if market_tick is None else market_tick
    has_targets = policy.fixed_target_r is not None or policy.target_from_entry
    if not 0 < market_tick <= tick or abs(tick/market_tick-round(tick/market_tick)) > 1e-8:
        raise ValueError("Protection tick must be an integer multiple of the market precision")
    precision = -Decimal(str(market_tick)).as_tuple().exponent
    venue = Venue("RESEARCH")
    instrument = CryptoPerpetual(
        instrument_id=InstrumentId(Symbol(f"{symbol}-USDT"), venue),
        raw_symbol=Symbol(f"{symbol}-USDT"),
        base_currency=Currency.from_str(symbol), quote_currency=USDT,
        settlement_currency=USDT, is_inverse=False,
        price_precision=precision, price_increment=Price(market_tick, precision),
        size_precision=0, size_increment=Quantity.from_int(1),
        margin_init=Decimal("0"), margin_maint=Decimal("0"),
        maker_fee=Decimal("0"), taker_fee=Decimal("0"), ts_event=0, ts_init=0,
    )
    prices = frame[["open", "high", "low", "close"]].to_numpy()
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("Invalid OHLC")
    if (np.abs(prices/market_tick-np.rint(prices/market_tick)) > 1e-5).any():
        raise ValueError("OHLC does not lie on the configured tick grid")
    for k, r in enumerate(entries.itertuples()):
        if r.side not in (-1, 1) or not 0 <= r.entry_i < r.end_i < len(frame):
            raise ValueError("Invalid entry direction or bounded interval")
        if pyramid[k]:
            continue  # validated above; stop entries rest beyond the price, not limits
        if limit_entry and not market_rows[k]:
            if (not r.entry_i < r.expire_i <= r.end_i
                    or r.side*(prices[int(r.entry_i), 3]-r.entry) < tick-tick*1e-7
                    or abs(r.entry/market_tick-round(r.entry/market_tick)) > 1e-5):
                raise ValueError("Limit must be favorable, tick-aligned, and have a bounded expiry")
        elif abs(prices[int(r.entry_i), 3]-r.entry) > tick*1e-7:
            raise ValueError("Entry differs from completed close")
        if policy.reclaim_next_bar:
            minutes = getattr(r, "reclaim_minutes", None)
            if (not isinstance(minutes, (int, np.integer)) or minutes <= 0
                    or minutes % execution_minutes or np.isnan(r.stop) == policy.reclaim_backstop
                    or abs(r.risk-r.entry) > tick*1e-7):
                raise ValueError("Reclaim needs whole aligned minutes, an initial stop only as a backstop, "
                                 "and entry-notional normalization")
        elif r.stop <= 0 or r.risk < tick-tick*1e-7 or abs(r.side*(r.entry-r.stop)-r.risk) > tick*1e-7:
            raise ValueError("Invalid initial risk")
        update_i = getattr(r, "stop_update_i", -1)
        if np.isfinite(update_i) and update_i >= 0:
            update = r.stop_update
            if (not r.entry_i < update_i < r.end_i or not update > 0
                    or abs(update/tick-round(update/tick)) > 1e-5):
                raise ValueError("Stop update must be tick-aligned and inside the trade interval")
        if policy.target_from_entry:
            target = getattr(r, "target_price", None)
            if target is None or np.isinf(target):
                raise ValueError("Per-entry target must be a finite price or explicit NaN")
            if np.isfinite(target) and (target <= 0 or r.side*(target-r.entry) < tick-tick*1e-7
                    or abs(target/tick-round(target/tick)) > 1e-5):
                raise ValueError("Per-entry target must be favorable and tick-aligned")
    fee_model = (EntryNotionalFee(policy.round_trip_cost) if policy.fill_taker_fee is None
                 else ActualFillFee(policy.fill_maker_fee, policy.fill_taker_fee))
    fill_model = IndependentLimitFill() if has_targets or limit_entry else None
    engine = BacktestEngine(BacktestEngineConfig(
        logging=LoggingConfig(bypass_logging=True),
        risk_engine=RiskEngineConfig(bypass=True),
        run_analysis=False,
    ))
    try:
        engine.add_venue(
            venue, OmsType.HEDGING, AccountType.MARGIN,
            [Money(1_000_000_000, USDT)], base_currency=USDT,
            fee_model=fee_model, frozen_account=True,
            # Fill callbacks must protect an intrabar entry immediately. The
            # default command queue waits until the whole bar has completed.
            use_message_queue=not (limit_entry or policy.target_breakeven),
            reject_stop_orders=not limit_entry,
            fill_model=fill_model,
            bar_execution=True, bar_adaptive_high_low_ordering=policy.monotone_segments,
            liquidity_consumption=False,
        )
        engine.add_instrument(instrument)
        bar_type = BarType.from_str(f"{instrument.id}-{execution_minutes}-MINUTE-LAST-EXTERNAL")
        execution_frame, point_phases = ((None, None) if batch_bars is not None else
            ohlc_path_frame(frame, policy.intrabar_path)
            if policy.intrabar_path else (frame.copy(), None))
        strategy = IndependentCloseStrategy(
            instrument, bar_type, frame, atr, hour, entries, policy, fee_model, tick,
            execution_minutes,rsi_side,entry_order_mode,fill_model if limit_entry else None,point_phases,entry_updates,
        )
        engine.add_strategy(strategy)
        # Synthetic liquidity deliberately decouples each independent entry
        # from other entries. These are unit returns, not capacity estimates.
        prepared = time.perf_counter()
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter("always")
            if batch_bars is None:
                execution_frame["volume"] = 1_000_000_000_000.
                engine.add_data(BarDataWrangler(bar_type, instrument).process(execution_frame))
                prepared = time.perf_counter()
                engine.run()
            else:
                for start in range(0, len(frame), batch_bars):
                    chunk = frame.iloc[start:start+batch_bars]
                    if policy.intrabar_path:
                        execution_frame, local_phases = ohlc_path_frame(chunk, policy.intrabar_path)
                        phases = {t:(start+i, phase) for t,(i,phase) in local_phases.items()}
                    else:
                        execution_frame, phases = chunk.copy(), {}
                    strategy.point_phases = phases
                    strategy.indices = {int(t):start+i for i,t in enumerate(chunk.index.asi8)}
                    strategy.indices.update({t:i for t,(i,_) in phases.items()})
                    execution_frame["volume"] = 1_000_000_000_000.
                    engine.add_data(BarDataWrangler(bar_type, instrument).process(execution_frame))
                    engine.run(streaming=True)
                    engine.clear_data()
                engine.end()
        if strategy.errors:
            raise RuntimeError(f"Native order errors: {strategy.errors[:3]}")
        expired = sum(r["status"] == "expired" for r in strategy.opportunity_rows)
        cancelled = sum(r['status'] not in ('filled', 'expired') for r in strategy.opportunity_rows)
        expected_positions = len(entries)-expired-cancelled
        if strategy.active or strategy.pending or len(strategy.rows) != expected_positions:
            raise RuntimeError("Missing or open independent trades")
        positions = engine.cache.positions()
        if len(positions) != expected_positions or any(p.is_open for p in positions):
            raise RuntimeError("Native position count/closure mismatch")
        if (has_targets or limit_entry) and engine.cache.orders_open():
            raise RuntimeError("Fixed target left an orphaned protective order")
        rows, fills = pd.DataFrame(strategy.rows), pd.DataFrame(strategy.fill_rows)
        if len(rows) and (policy.partial_close_r is not None or has_targets or policy.rsi_scaleout or policy.confirmed_pivot_exit or policy.structural_pivot_stop):
            realized = {str(p.id): float(p.realized_pnl) for p in positions}
            native_pnl = rows.framework_position_id.map(realized).to_numpy()
            expected_pnl = (rows.net_r*rows.risk*rows.quantity).to_numpy()
            np.testing.assert_allclose(native_pnl, expected_pnl, atol=1e-6, rtol=1e-10,
                                       err_msg="Native position PnL differs")
        audit = dict(
            engine="nautilus_trader", version=ENGINE_VERSION,
            market_tick=market_tick, protection_tick=tick,
            research_quantity=("Whole units; round up to the partial fraction denominator "
                               "when enabled. Returns normalized per original risk"),
            partial_execution="one completed 5m close reduction" if policy.partial_close_r is not None else "disabled",
            native_partial_accounting_checked=len(rows) if policy.partial_close_r is not None else 0,
            fixed_target_execution=(f"resting limit; {policy.intrabar_path or 'OHLC'}; independent limit-price book; native reduce-only sibling cleanup" if has_targets else "disabled"),
            native_fixed_target_accounting_checked=len(rows) if has_targets else 0,
            target_from_entry=policy.target_from_entry,
            target_breakeven=policy.target_breakeven,
            breakeven_includes_cost=policy.breakeven_includes_cost,
            fill_maker_fee=policy.fill_maker_fee, fill_taker_fee=policy.fill_taker_fee,
            breakeven_on_touch=policy.breakeven_on_touch,
            reclaim_next_bar=policy.reclaim_next_bar,
            intrabar_path=policy.intrabar_path,
            synthetic_segments_per_bar=4 if policy.intrabar_path else 1,
            positions=len(positions), fills=len(fills),
            execution_minutes=execution_minutes,
            streaming_batch_bars=batch_bars,
            rsi_scaleout="five original20percent reductions at fee-positive opposite confirmed strong closes" if policy.rsi_scaleout else "disabled",
            rsi_skip_events=policy.rsi_skip_events,
            timed_be_minutes=policy.time_breakeven_minutes,
            confirmed_pivot_exit=policy.confirmed_pivot_exit,
            structural_pivot_stop=policy.structural_pivot_stop,
            structural_execution=(f"{policy.pivot_minutes}m 2/2 opposite pivot ratchets exact stop; post-entry centers only; confirmation-close amendment effective next bar; no profit gate" if policy.structural_pivot_stop else "disabled"),
            pivot_execution=f"{policy.pivot_minutes}m 2/2 confirmation-close market order; initial stop first; no fill at retrospective extreme" if policy.confirmed_pivot_exit else "disabled",
            strict_pivot_ties=policy.strict_pivot_ties,
            max_simultaneous_positions=strategy.max_open,
            bars_with_both_directions=strategy.hedged_bars,
            prepare_seconds=prepared-started,
            replay_seconds=time.perf_counter()-prepared,
            runtime_warnings=sorted({str(w.message) for w in emitted}),
            accounting=("independent entry-notional fractions; no initial risk stop; not R or portfolio returns"
                        if policy.reclaim_next_bar else "independent original-risk R; frozen account; not portfolio returns"),
        )
        if limit_entry:
            audit.update(entry_order_mode="limit", opportunities=strategy.opportunity_rows,
                cancelled=cancelled, pending_amendments=strategy.pending_update_rows,
                expired=expired, entry_fill="Native resting limit after signal close; fixed limit price even on favorable gaps; OHLC order O-H-L-C; protection follows configured policy (reclaim mode has no initial stop); any entry-bar stop book uses stop/adverse open, later stops use native model; expiry after last eligible bar matching", entry_bar_mfe="excluded")
        if policy.structural_pivot_stop:
            audit["protection_updates"] = strategy.protection_updates
        return rows, fills, audit
    finally:
        engine.dispose()
