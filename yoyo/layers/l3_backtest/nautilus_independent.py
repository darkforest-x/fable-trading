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
reported. OHLC ordering is O-H-L-C. No intrabar target orders are supported:
their collision semantics would differ from the historical stop-first oracle.
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
from nautilus_trader.backtest.models import FeeModel
from nautilus_trader.config import (
    BacktestEngineConfig, LoggingConfig, RiskEngineConfig, StrategyConfig,
)
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
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

    def __post_init__(self):
        values = [self.trail_arm_r, self.trail_atr_multiple]
        if self.breakeven_r is not None:
            values.append(self.breakeven_r)
        if self.partial_close_r is not None:
            values.append(self.partial_close_r)
        if not all(math.isfinite(x) and x > 0 for x in values):
            raise ValueError("R and ATR parameters must be finite and positive")
        if not math.isfinite(self.round_trip_cost) or self.round_trip_cost < 0:
            raise ValueError("Invalid cost")
        if not math.isfinite(self.partial_fraction) or not 0 < self.partial_fraction < 1:
            raise ValueError("Partial fraction must be strictly between zero and one")
        if Fraction(str(self.partial_fraction)).denominator > 10000:
            raise ValueError("Partial fraction requires excessive research sizing")


class EntryNotionalFee(FeeModel):
    """Charge half the frozen round-trip cost on original entry notional."""

    def __init__(self, cost):
        super().__init__()
        self.rate = Decimal(str(cost)) / 2
        self.entries = {}

    def get_commission(self, order, fill_qty, fill_px, instrument):
        entry = Decimal(str(self.entries[str(order.client_order_id)]))
        return Money(entry * fill_qty.as_decimal() * self.rate, USDT)


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
    fees: float = 0.
    fills: list = field(default_factory=list)
    remaining: int = 0
    partial_i: int = -1
    partial_price: float = float("nan")
    partial_weight: float = 0.

    def __post_init__(self):
        self.remaining = int(self.quantity)


class IndependentCloseStrategy(Strategy):
    def __init__(self, instrument, bar_type, frame, atr, hour, entries, policy, fees, protection_tick):
        super().__init__(StrategyConfig(
            oms_type="HEDGING", log_events=False, log_commands=False,
        ))
        self.instrument = instrument
        self.bar_type = bar_type
        self.frame = frame
        self.prices = frame[["open", "high", "low", "close"]].to_numpy()
        self.atr, self.hour = np.asarray(atr), np.asarray(hour)
        self.policy, self.fees = policy, fees
        self.tick = protection_tick
        self.eps = self.tick * 1e-7
        self.indices = {int(t): i for i, t in enumerate(frame.index.asi8)}
        self.by_entry = {}
        for row in entries.to_dict("records"):
            self.by_entry.setdefault(int(row["entry_i"]), []).append(row)
        self.active, self.order_map = {}, {}
        self.rows, self.fill_rows, self.errors = [], [], []
        self.max_open = self.hedged_bars = 0

    def on_start(self):
        self.subscribe_bars(self.bar_type)

    def _submit(self, order, state, phase):
        key = str(order.client_order_id)
        self.order_map[key] = (state, phase, order.quantity)
        self.fees.entries[key] = state.source["entry"]
        self.submit_order(order, position_id=state.position_id)

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
        self._submit(order, state, "stop")

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
        if phase == "entry":
            if i != state.source["entry_i"] or abs(price-state.source["entry"]) > self.eps:
                raise RuntimeError("Entry did not fill at the signal bar close")
            self._submit_protective_stop(state)
            return
        state.remaining -= int(event.last_qty)
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
            reason = 5 if state.stop_kind == 1 else 2 if state.arm_i >= 0 else 1
        else:
            reason = phase
        row = dict(state.source)
        risk, entry, side = row["risk"], row["entry"], row["side"]
        gross = side*(price-entry)/risk
        if state.partial_i >= 0:
            gross = (state.partial_weight*side*(state.partial_price-entry)/risk
                     + (1-state.partial_weight)*gross)
        # Native bar events carry close timestamps. Preserve a separate logical
        # open timestamp for gap exits without pretending intrabar time is known.
        gap = phase == "stop" and side*(self.prices[i, 0]-state.stop) < -self.eps
        row.update(
            exit_i=i, exit_price=price, reason=int(reason), peak_r=state.peak,
            arm_i=state.arm_i, protection=state.stop,
            steps=i-int(row["entry_i"]), ambiguous=0,
            be_trigger_i=state.be_trigger_i, be_effective_i=state.be_effective_i,
            be_price=state.be_price, stop_kind=state.stop_kind,
            deferred_closes=state.deferred, gross_r=gross,
            fee_r=self.policy.round_trip_cost*entry/risk,
            net_r=gross-self.policy.round_trip_cost*entry/risk,
            net_notional_bp=(gross*risk/entry-self.policy.round_trip_cost)*10000,
            exit_time=str(self.frame.index[i]-pd.Timedelta(minutes=5 if gap else 0)),
            framework_exit_time=str(self.frame.index[i]),
            framework_position_id=str(event.position_id),
            framework_commission=state.fees, quantity=float(state.quantity),
        )
        if self.policy.partial_close_r is not None:
            row.update(partial_i=state.partial_i, partial_price=state.partial_price,
                       partial_weight=state.partial_weight,
                       partial_time=str(self.frame.index[state.partial_i])
                       if state.partial_i >= 0 else "",
                       runner_gross_r=side*(price-entry)/risk)
        self.rows.append(row)
        del self.active[row["trade_id"]]

    def on_order_rejected(self, event):
        self.errors.append(str(event))

    def on_order_denied(self, event):
        self.errors.append(str(event))

    def on_order_modify_rejected(self, event):
        self.errors.append(str(event))

    def on_order_cancel_rejected(self, event):
        self.errors.append(str(event))

    def on_bar(self, bar):
        i = self.indices[bar.ts_event]
        _, high, low, close = self.prices[i]
        p = self.policy
        quarter = np.isfinite(self.atr[i]) and self.atr[i] > 0
        # Native stops have already executed. Only surviving positions observe
        # this bar's favorable extreme and then update close-derived protection.
        for state in list(self.active.values()):
            row = state.source
            entry, side, risk = row["entry"], row["side"], row["risk"]
            state.peak = max(state.peak, side*((high if side > 0 else low)-entry)/risk)
            old_stop = state.stop
            if quarter:
                move = side*(close-entry)
                if state.arm_i < 0 and move+self.eps >= p.trail_arm_r*risk:
                    state.arm_i = i
                if p.breakeven_r is not None:
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
            reverse = p.opposite_hour_exit and state.arm_i >= 0 and self.hour[i] == -side
            if reverse or i == int(row["end_i"]):
                self.cancel_order(state.stop_order)
                self._market(state, reason=3 if reverse else 0)
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
        for row in self.by_entry.get(i, []):
            side, entry = row["side"], row["entry"]
            be_raw = (entry+side*p.round_trip_cost*entry)/self.tick
            be = (math.ceil(be_raw-1e-8) if side > 0 else math.floor(be_raw+1e-8))*self.tick
            units = max(1, int(1000/entry))
            if p.partial_close_r is not None:
                denominator = Fraction(str(p.partial_fraction)).denominator
                units = ((units+denominator-1)//denominator)*denominator
            state = TradeState(
                row, PositionId(row["trade_id"]),
                # Research returns are normalized independently of order size.
                # Whole units avoid fractional position roundoff at ~1e9 PEPE:
                # native float exposure can otherwise trim 1e-7 units on exit.
                self.instrument.make_qty(units), row["stop"], be,
            )
            self.active[row["trade_id"]] = state
            self._market(state, entering=True)
        self.max_open = max(self.max_open, len(self.active))
        self.hedged_bars += int({s.source["side"] for s in self.active.values()} == {-1, 1})


def run_native(frame, atr, hour, entries, policy, tick=1e-9, symbol="PEPE", market_tick=None):
    """Execute supplied entries using native orders, fills and HEDGING positions.

    Frame index is UTC close time. ATR is NaN except at completed management
    closes; hour contains confirmed direction only at completed hourly closes.
    Entries need unique trade_id, entry_i, side, entry, stop, risk and end_i.
    No future excursion or expected-exit fields are read by the strategy.
    """
    started = time.perf_counter()
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
    market_tick = tick if market_tick is None else market_tick
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
    for r in entries.itertuples():
        if r.side not in (-1, 1) or not 0 <= r.entry_i < r.end_i < len(frame):
            raise ValueError("Invalid entry direction or bounded interval")
        if abs(prices[int(r.entry_i), 3]-r.entry) > tick*1e-7:
            raise ValueError("Entry differs from completed close")
        if r.stop <= 0 or r.risk < tick-tick*1e-7 or abs(r.side*(r.entry-r.stop)-r.risk) > tick*1e-7:
            raise ValueError("Invalid initial risk")
    fee_model = EntryNotionalFee(policy.round_trip_cost)
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
            bar_execution=True, bar_adaptive_high_low_ordering=False,
            liquidity_consumption=False,
        )
        engine.add_instrument(instrument)
        bar_type = BarType.from_str(f"{instrument.id}-5-MINUTE-LAST-EXTERNAL")
        strategy = IndependentCloseStrategy(
            instrument, bar_type, frame, atr, hour, entries, policy, fee_model, tick,
        )
        engine.add_strategy(strategy)
        # Synthetic liquidity deliberately decouples each independent entry
        # from other entries. These are unit returns, not capacity estimates.
        execution_frame = frame.copy()
        execution_frame["volume"] = 1_000_000_000_000.
        engine.add_data(BarDataWrangler(bar_type, instrument).process(execution_frame))
        prepared = time.perf_counter()
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter("always")
            engine.run()
        if strategy.errors:
            raise RuntimeError(f"Native order errors: {strategy.errors[:3]}")
        if strategy.active or len(strategy.rows) != len(entries):
            raise RuntimeError("Missing or open independent trades")
        positions = engine.cache.positions()
        if len(positions) != len(entries) or any(p.is_open for p in positions):
            raise RuntimeError("Native position count/closure mismatch")
        rows, fills = pd.DataFrame(strategy.rows), pd.DataFrame(strategy.fill_rows)
        if policy.partial_close_r is not None:
            realized = {str(p.id): float(p.realized_pnl) for p in positions}
            native_pnl = rows.framework_position_id.map(realized).to_numpy()
            expected_pnl = (rows.net_r*rows.risk*rows.quantity).to_numpy()
            np.testing.assert_allclose(native_pnl, expected_pnl, atol=1e-6, rtol=1e-10,
                                       err_msg="Native partial position PnL differs")
        audit = dict(
            engine="nautilus_trader", version=ENGINE_VERSION,
            market_tick=market_tick, protection_tick=tick,
            research_quantity=("Whole units; round up to the partial fraction denominator "
                               "when enabled. Returns normalized per original risk"),
            partial_execution="one completed 5m close reduction" if policy.partial_close_r is not None else "disabled",
            native_partial_accounting_checked=len(rows) if policy.partial_close_r is not None else 0,
            positions=len(positions), fills=len(fills),
            max_simultaneous_positions=strategy.max_open,
            bars_with_both_directions=strategy.hedged_bars,
            prepare_seconds=prepared-started,
            replay_seconds=time.perf_counter()-prepared,
            runtime_warnings=sorted({str(w.message) for w in emitted}),
            accounting="independent original-risk R; frozen account; not portfolio returns",
        )
        return rows, fills, audit
    finally:
        engine.dispose()
