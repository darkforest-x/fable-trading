"""Native one-hour replay for early / confirmed 12H EMA-cloud entries.

The input frame is indexed by UTC hour OPEN. Each source hour is represented
by two native Nautilus bars: a flat open print at ``open_ms + 1ms`` followed by
the original OHLC at ``open_ms + 1h``. This makes opening-gap decisions and
intrahour stop matching distinct native phases while retaining the source
candle's actual OHLC.

``crosses.start_ms`` is the first hour-open timestamp at which a pair of
confirmed 12H EMA values is known. ``cross`` is the price at which that prior
confirmed EMA pair would strictly reverse; ``prev_spread`` is EMA20 - EMA50
from the same confirmed pair, used only to reproduce Pine's floating-point
epsilon. The strategy never rejects an entry because the previous EMA spread
has the opposite sign. It applies the V1.9 strict price-cross rule instead.

Trades use independent HEDGING positions and native market / STOP_MARKET
orders. The protective order is the nearer adverse barrier between the
original fixed stop (inclusive equality) and the current cloud threshold
(strict crossing, rounded to the first tradable tick beyond the level).
Confirmed-threshold updates and scheduled invalidations are applied after the
preceding hour closes and before the next open-only bar is matched. Each entry
is sized from its actual entry-to-original-stop distance to approximately the
requested USDT risk. Fees use actual fill notionals: market and stop-market
fills pay taker; no funding, spread, slippage, or shared-capital constraints
are simulated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
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
HOUR_MS = 3_600_000
HOUR_NS = HOUR_MS * 1_000_000
OPEN_OFFSET_NS = 1_000_000
EMA20_ALPHA = 2.0 / 21.0
EMA50_ALPHA = 2.0 / 51.0
EMA_ALPHA_GAP = EMA20_ALPHA - EMA50_ALPHA
SIZE_PRECISION = 8
SIZE_INCREMENT = Quantity.from_str("0.00000001")


@dataclass
class _Trade:
    row: dict
    position_id: PositionId
    quantity: Quantity
    expected_entry: float
    current_cross_start_ms: int
    cross: float
    prev_spread: float
    eps: float
    stop_order: object = None
    stop_trigger: float = float("nan")
    stop_kind: str = "fixed_stop"
    entry_px: float = float("nan")
    entry_fee: float = 0.0
    pending_exit_reason: str = ""
    fills: list = field(default_factory=list)


class CloudHourlyStrategy(Strategy):
    def __init__(self, instrument, bar_type, frame, entries, crosses, tick):
        super().__init__(StrategyConfig(oms_type="HEDGING", log_events=False, log_commands=False))
        self.instrument, self.bar_type, self.frame = instrument, bar_type, frame
        self.tick, self.eps_tick = tick, tick * 1e-7
        self.open_ns = frame.index.as_unit("ns").asi8
        self.close_ns = self.open_ns + HOUR_NS
        self.open_event_ns = self.open_ns + OPEN_OFFSET_NS
        self.event_phase = {}
        for i, (opening, opening_event, closing) in enumerate(
                zip(self.open_ns, self.open_event_ns, self.close_ns)):
            self.event_phase[int(opening_event)] = (i, "open")
            self.event_phase[int(closing)] = (i, "intrabar")
        self.by_entry = {}
        for row in entries:
            self.by_entry.setdefault(int(row["entry_i"]), []).append(row)
        self.cross_rows = {
            int(row["start_ms"]): (float(row["cross"]), float(row["prev_spread"]))
            for row in crosses
        }
        self.cross_starts = np.asarray(sorted(self.cross_rows), dtype=np.int64)
        self.active: dict[str, _Trade] = {}
        self.order_map = {}
        self.rows, self.errors = [], []
        self.max_open = 0

    def on_start(self):
        self.subscribe_bars(self.bar_type)

    def _asof_cross(self, open_ms):
        k = int(np.searchsorted(self.cross_starts, int(open_ms), side="right")) - 1
        if k < 0:
            return -1, float("nan"), float("nan")
        start = int(self.cross_starts[k])
        cross, spread = self.cross_rows[start]
        return start, cross, spread

    @staticmethod
    def _ema_epsilon(cross, spread):
        if not (math.isfinite(cross) and math.isfinite(spread)):
            return 0.0
        # Given crossPrice and EMA20-EMA50, recover the two previous confirmed
        # EMA values, then use the exact V1.9 epsilon expression.
        prev50 = cross + (1.0 - EMA20_ALPHA) * spread / EMA_ALPHA_GAP
        prev20 = prev50 + spread
        return (np.finfo(np.float64).eps * max(1.0, abs(prev20), abs(prev50))
                * 8.0 / EMA_ALPHA_GAP)

    def _set_cross(self, trade, start_ms):
        value = self.cross_rows.get(int(start_ms))
        if value is None or int(start_ms) <= trade.current_cross_start_ms:
            return False
        trade.current_cross_start_ms = int(start_ms)
        trade.cross, trade.prev_spread = value
        trade.eps = self._ema_epsilon(trade.cross, trade.prev_spread)
        return True

    def _strict_flip_at(self, trade, price):
        return (math.isfinite(trade.cross)
                and (float(price) - trade.cross) * trade.row["side"] < -trade.eps)

    def _fixed_stop_at(self, trade, price):
        return (float(price) - trade.row["stop"]) * trade.row["side"] <= 0.0

    def _fixed_tick(self, stop, side):
        """First tradable tick at/beyond a fixed, inclusive stop threshold."""
        units = (math.floor(float(stop) / self.tick + 1e-10) if side > 0
                 else math.ceil(float(stop) / self.tick - 1e-10))
        return float(units * self.tick)

    def _opening_reason(self, trade, price):
        fixed = self._fixed_stop_at(trade, price)
        flip = self._strict_flip_at(trade, price)
        # V1.9 keeps the fixed-stop precedence when both opening barriers have
        # already been crossed. Equality at the EMA level is not a flip.
        if fixed:
            return "fixed_stop"
        if flip:
            return "cloud_flip"
        return ""

    def _strict_tick(self, cross, side, eps):
        """First exchange tick strictly beyond a cloud threshold."""
        if side > 0:
            units = math.floor((cross - eps) / self.tick + 1e-12)
        else:
            units = math.ceil((cross + eps) / self.tick - 1e-12)
        return float(units * self.tick)

    def _effective_stop(self, trade):
        fixed = float(trade.row["stop"])
        cross = trade.cross
        if not math.isfinite(cross):
            return self._fixed_tick(fixed, trade.row["side"]), "fixed_stop"
        # V1.9 chooses the nearer same-direction adverse barrier. Keep this
        # raw-price comparison for exact equality priority; tick rounding only
        # determines the native trigger price for a strict cloud crossing.
        if (cross - fixed) * trade.row["side"] > 0.0:
            return self._strict_tick(cross, trade.row["side"], trade.eps), "cloud_flip"
        return self._fixed_tick(fixed, trade.row["side"]), "fixed_stop"

    def _cancel_stop(self, trade):
        if trade.stop_order is not None and not trade.stop_order.is_closed:
            self.cancel_order(trade.stop_order)
        trade.stop_order = None

    def _submit(self, order, trade, phase):
        key = str(order.client_order_id)
        self.order_map[key] = (trade, phase)
        # ActualFillFee selects by order type; retain the existing metadata
        # convention used by the shared native adapter as well.
        self.fees.entries[key] = trade.expected_entry
        self.submit_order(order, position_id=trade.position_id)

    def _market_exit(self, trade, reason):
        if trade.pending_exit_reason and reason != trade.pending_exit_reason:
            return
        if trade.row["trade_id"] not in self.active:
            return
        trade.pending_exit_reason = reason
        self._cancel_stop(trade)
        side = int(trade.row["side"])
        order = self.order_factory.market(
            self.instrument.id,
            OrderSide.SELL if side > 0 else OrderSide.BUY,
            trade.quantity,
            reduce_only=True,
        )
        self._submit(order, trade, "exit")

    def _place_or_modify_stop(self, trade, *, boundary_close=None):
        if trade.pending_exit_reason or trade.row["trade_id"] not in self.active:
            return
        trigger, kind = self._effective_stop(trade)
        # A level that was only learned at this close must not backdate an exit
        # into the just-completed hour. Leave it unarmed until the next open,
        # where the opening print decides between an open exit and a new stop.
        if boundary_close is not None:
            if (kind == "fixed_stop" and self._fixed_stop_at(trade, boundary_close)) or (
                    kind == "cloud_flip" and self._strict_flip_at(trade, boundary_close)):
                self._cancel_stop(trade)
                trade.stop_trigger, trade.stop_kind = trigger, kind
                return
        trade.stop_kind, trade.stop_trigger = kind, trigger
        if trade.stop_order is None:
            side = int(trade.row["side"])
            order = self.order_factory.stop_market(
                self.instrument.id,
                OrderSide.SELL if side > 0 else OrderSide.BUY,
                trade.quantity,
                trigger_price=self.instrument.make_price(trigger),
                reduce_only=True,
            )
            trade.stop_order = order
            self._submit(order, trade, "stop")
        elif abs(float(trade.stop_order.trigger_price) - trigger) > self.eps_tick:
            self.modify_order(trade.stop_order, trigger_price=self.instrument.make_price(trigger))

    def _new_trade(self, row):
        start, cross, spread = self._asof_cross(int(row["entry_ms"]))
        eps = self._ema_epsilon(cross, spread)
        distance = abs(float(row["entry_price"]) - float(row["stop"]))
        quantity = self.instrument.make_qty(Decimal(str(row["risk_usdt"])) / Decimal(str(distance)),
                                            round_down=True)
        state = _Trade(
            row=row,
            position_id=PositionId(str(row["trade_id"])),
            quantity=quantity,
            expected_entry=float(row["entry_price"]),
            current_cross_start_ms=start,
            cross=cross,
            prev_spread=spread,
            eps=eps,
        )
        self.active[row["trade_id"]] = state
        self.max_open = max(self.max_open, len(self.active))
        side = int(row["side"])
        order = self.order_factory.market(
            self.instrument.id,
            OrderSide.BUY if side > 0 else OrderSide.SELL,
            quantity,
        )
        self._submit(order, state, "entry")

    def _handle_open(self, i):
        open_ms = int(self.open_ns[i] // 1_000_000)
        opening = float(self.frame.open.iloc[i])
        for trade in list(self.active.values()):
            if trade.pending_exit_reason:
                self._market_exit(trade, trade.pending_exit_reason)
                continue
            if (int(trade.row["fail_at_ms"]) != np.iinfo(np.int64).max
                    and int(trade.row["fail_at_ms"]) <= open_ms):
                self._market_exit(trade, "scheduled_fail")
                continue
            # Normally boundary updates are applied on the preceding actual
            # OHLC close, before this flat opening bar can match old protection.
            # This fallback covers a trade beginning at the first supplied bar.
            start, _, _ = self._asof_cross(open_ms)
            if start > trade.current_cross_start_ms:
                self._set_cross(trade, start)
                self._cancel_stop(trade)
            if trade.stop_order is None:
                reason = self._opening_reason(trade, opening)
                if reason:
                    self._market_exit(trade, reason)
                else:
                    self._place_or_modify_stop(trade)

        for row in self.by_entry.get(i, ()):
            self._new_trade(row)

    def _handle_completed_hour(self, i):
        close_ns = int(self.close_ns[i])
        if i == len(self.frame) - 1:
            for trade in list(self.active.values()):
                self._market_exit(trade, "data_end")
            return
        next_open_ns = int(self.open_ns[i + 1])
        next_open_ms = next_open_ns // 1_000_000
        expected_ns = int(self.open_ns[i] + HOUR_NS)
        for trade in list(self.active.values()):
            if next_open_ns != expected_ns:
                # Unknown hours end the observable path. Cancel stale protection
                # after the last known OHLC, then mark out at the first new open.
                self._cancel_stop(trade)
                trade.pending_exit_reason = "data_gap"
                continue
            if (int(trade.row["fail_at_ms"]) != np.iinfo(np.int64).max
                    and int(trade.row["fail_at_ms"]) <= next_open_ms):
                self._cancel_stop(trade)
                trade.pending_exit_reason = "scheduled_fail"
                continue
            if self._set_cross(trade, next_open_ms):
                close = float(self.frame.close.iloc[i])
                self._place_or_modify_stop(trade, boundary_close=close)

    def on_bar(self, bar):
        i, phase = self.event_phase[bar.ts_event]
        if phase == "open":
            self._handle_open(i)
        else:
            self._handle_completed_hour(i)

    def on_order_filled(self, event):
        trade, phase = self.order_map[str(event.client_order_id)]
        row = trade.row
        event_i, event_phase = self.event_phase[event.ts_event]
        price, fee = float(event.last_px), float(event.commission)
        if event.last_qty != trade.quantity:
            self.errors.append(f"partial fill {row['trade_id']} ({phase})")
        trade.fills.append(dict(phase=phase, index=event_i, price=price, fee=fee))

        if phase == "entry":
            if event_phase != "open" or event_i != row["entry_i"]:
                self.errors.append(f"entry outside scheduled open {row['trade_id']}")
            if abs(price - trade.expected_entry) > max(self.eps_tick, 4 * math.ulp(price)):
                self.errors.append(f"entry price mismatch {row['trade_id']}")
            trade.entry_px, trade.entry_fee = price, fee
            reason = self._opening_reason(trade, price)
            if reason:
                self._market_exit(trade, reason)
            else:
                self._place_or_modify_stop(trade)
            return

        if phase == "stop":
            if event_phase == "open":
                # V1.9 gives an opening fixed-stop trigger priority when both
                # the fixed and strict EMA threshold are already through.
                reason = self._opening_reason(trade, price) or trade.stop_kind
            else:
                reason = "cloud_flip" if trade.stop_kind == "cloud_flip" else "fixed_stop"
        else:
            reason = trade.pending_exit_reason or "scheduled_exit"
        if not trade.pending_exit_reason:
            trade.pending_exit_reason = reason
        side = int(row["side"])
        gross = side * (price - trade.entry_px) * float(trade.quantity)
        fees = trade.entry_fee + fee
        target_risk = float(row["risk_usdt"])
        actual_risk = abs(trade.entry_px - float(row["stop"])) * float(trade.quantity)
        out_phase = "close" if reason == "data_end" else event_phase
        out = dict(
            trade_id=row["trade_id"], side=side,
            entry_ms=int(row["entry_ms"]),
            exit_hour_ms=int(self.open_ns[event_i] // 1_000_000),
            exit_phase=out_phase,
            entry_px=trade.entry_px, exit_px=price, quantity=float(trade.quantity),
            entry_fee=trade.entry_fee, exit_fee=fee, fee_usdt=fees,
            gross_usdt=gross, net_usdt=gross-fees,
            target_risk_usdt=target_risk, actual_risk_usdt=actual_risk,
            risk_rounding_error_usdt=actual_risk-target_risk,
            fixed_stop=float(row["stop"]), exit_reason=reason,
            cloud_cross=trade.cross, cloud_cross_start_ms=trade.current_cross_start_ms,
            framework_position_id=str(event.position_id),
        )
        self.rows.append(out)
        self.active.pop(row["trade_id"], None)

    def on_order_rejected(self, event):
        self.errors.append(f"rejected {event.client_order_id}: {event.reason}")

    def on_order_denied(self, event):
        self.errors.append(f"denied {event.client_order_id}: {event.reason}")

    def on_order_modify_rejected(self, event):
        self.errors.append(f"modify rejected {event.client_order_id}: {event.reason}")

    def on_order_cancel_rejected(self, event):
        self.errors.append(f"cancel rejected {event.client_order_id}: {event.reason}")


def _cross_rows(crosses: pd.DataFrame):
    required = {"start_ms", "cross", "prev_spread"}
    if not required <= set(crosses.columns):
        raise ValueError(f"crosses needs columns {sorted(required)}")
    if crosses.empty:
        return []
    data = crosses.loc[:, ["start_ms", "cross", "prev_spread"]].copy()
    data["start_ms"] = pd.to_numeric(data.start_ms, errors="raise").astype("int64")
    data["cross"] = pd.to_numeric(data.cross, errors="coerce")
    data["prev_spread"] = pd.to_numeric(data.prev_spread, errors="coerce")
    if data.start_ms.duplicated().any() or not data.start_ms.is_monotonic_increasing:
        raise ValueError("cross starts must be unique and ascending")
    if (data.cross.notna() != data.prev_spread.notna()).any():
        raise ValueError("cross and prev_spread must both be finite or both be missing")
    if not np.isfinite(data.loc[data.cross.notna(), ["cross", "prev_spread"]].to_numpy()).all():
        raise ValueError("nonfinite EMA cross input")
    return data.to_dict("records")


def run_cloud_hourly(frame: pd.DataFrame, entries: pd.DataFrame, crosses: pd.DataFrame,
                     tick: float = 0.01, risk_usdt: float = 100.0, *, symbol: str = "ETH",
                     maker: float = 0.0002, taker: float = 0.0005):
    """Run entries with native hourly open, OHLC, fixed-stop and EMA-cross fills.

    Args:
        frame: UTC-aware 1H OHLC indexed by the source candle OPEN.
        entries: ``trade_id, side, entry_ms, entry_price, stop, fail_at_ms``.
          Each entry is a market order at the exact source-hour open; ``stop``
          remains the original reference stop and defines the risk denominator.
          ``fail_at_ms`` is an optional scheduled invalidation, executed at the
          first observed hourly open at or after that timestamp.
        crosses: ``start_ms, cross, prev_spread``. Each row becomes active from
          that hour open, after the preceding source bar has closed.
        tick: exchange price increment.
        risk_usdt: target loss at the original stop per independent trade.
        symbol: base asset used to construct the synthetic perpetual.
        maker/taker: actual per-fill commission rates.

    Returns ``(trades, audit)`` with one row per closed native position.
    """
    started = time.perf_counter()
    if nautilus_trader.__version__ != ENGINE_VERSION:
        raise RuntimeError(f"Expected Nautilus {ENGINE_VERSION}; revalidate before upgrading")
    if not math.isfinite(tick) or tick <= 0 or not math.isfinite(risk_usdt) or risk_usdt <= 0:
        raise ValueError("tick and risk_usdt must be positive and finite")
    if any(not math.isfinite(x) or x < 0 or x >= 1 for x in (maker, taker)):
        raise ValueError("invalid maker/taker rate")
    needed = {"open", "high", "low", "close"}
    if not needed <= set(frame.columns):
        raise ValueError("frame needs open/high/low/close")
    if (frame.empty or frame.index.tz is None or not frame.index.is_monotonic_increasing
            or frame.index.has_duplicates):
        raise ValueError("hourly frame must be nonempty, UTC-aware, unique and chronological")
    frame = frame.loc[:, ["open", "high", "low", "close"]].copy()
    frame.index = frame.index.as_unit("ns")
    if str(frame.index.tz) != "UTC":
        frame.index = frame.index.tz_convert("UTC")
    prices = frame.to_numpy(dtype=np.float64)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("Invalid hourly OHLC")
    if ((np.abs(prices / tick - np.rint(prices / tick)) > 1e-5).any()
            or (prices[:, 1] < np.maximum(prices[:, 0], prices[:, 3])).any()
            or (prices[:, 2] > np.minimum(prices[:, 0], prices[:, 3])).any()
            or (prices[:, 2] > prices[:, 1]).any()):
        raise ValueError("Hourly OHLC is invalid or off the price tick grid")

    cross_rows = _cross_rows(crosses)
    if entries.empty:
        return pd.DataFrame(), dict(engine="nautilus_trader", version=ENGINE_VERSION,
                                    positions=0, fills=0, bars_total=2*len(frame),
                                    maker=maker, taker=taker, risk_usdt=risk_usdt,
                                    seconds=round(time.perf_counter()-started, 3))
    required_entries = {"trade_id", "side", "entry_ms", "entry_price", "stop", "fail_at_ms"}
    if not required_entries <= set(entries.columns):
        raise ValueError(f"entries needs columns {sorted(required_entries)}")
    prepared = entries.copy()
    if prepared.trade_id.isna().any() or prepared.trade_id.astype(str).duplicated().any():
        raise ValueError("trade_id must be nonempty and unique in each native run")
    prepared["trade_id"] = prepared.trade_id.astype(str)
    opens_ms = frame.index.asi8 // 1_000_000
    by_ms = {int(t): i for i, t in enumerate(opens_ms)}
    prepared["entry_ms"] = pd.to_numeric(prepared["entry_ms"], errors="raise").astype("int64")
    raw_fail = pd.to_numeric(prepared["fail_at_ms"], errors="coerce")
    if np.isinf(raw_fail.to_numpy(dtype=np.float64)).any():
        raise ValueError("fail_at_ms cannot be infinite")
    finite_fail = raw_fail.dropna().to_numpy(dtype=np.float64)
    if len(finite_fail) and not np.equal(finite_fail, np.floor(finite_fail)).all():
        raise ValueError("finite fail_at_ms values must be integer UTC milliseconds")
    prepared["fail_at_ms"] = raw_fail.fillna(np.iinfo(np.int64).max).astype("int64")
    for col in ("side", "entry_price", "stop"):
        prepared[col] = pd.to_numeric(prepared[col], errors="raise")
    prepared["risk_usdt"] = float(risk_usdt)
    indices = []
    for row in prepared.to_dict("records"):
        i = by_ms.get(int(row["entry_ms"]))
        if i is None:
            raise ValueError(f"Entry open is missing from hourly frame: {row['trade_id']}")
        if row["side"] not in (-1, 1):
            raise ValueError(f"Invalid side for {row['trade_id']}")
        if not row["side"] * (row["entry_price"] - row["stop"]) > 0:
            raise ValueError(f"Original stop must be adverse to {row['trade_id']} entry")
        if (int(row["fail_at_ms"]) != np.iinfo(np.int64).max
                and int(row["fail_at_ms"]) <= int(row["entry_ms"])):
            raise ValueError(f"fail_at_ms must follow entry for {row['trade_id']}")
        if abs(float(frame.open.iloc[i]) - float(row["entry_price"])) > tick * 1e-7:
            raise ValueError(f"Entry price is not the exact hourly open for {row['trade_id']}")
        distance = abs(float(row["entry_price"]) - float(row["stop"]))
        if distance <= 0 or float(Decimal(str(risk_usdt)) / Decimal(str(distance))) < 1e-8:
            raise ValueError(f"Risk sizing rounds below the 1e-8 quantity increment for {row['trade_id']}")
        indices.append(i)
    prepared["entry_i"] = indices
    if prepared.entry_i.duplicated().any():
        # Multiple same-time positions are valid, including opposing sides.
        pass

    # start_ms denotes when the confirmed 12H pair is first usable; exact H1
    # open alignment avoids applying a newly confirmed level to the prior hour.
    for row in cross_rows:
        start_ms = int(row["start_ms"])
        if start_ms % HOUR_MS:
            raise ValueError("12H EMA cross start_ms must align to a UTC hour")
    decimals = max(0, -Decimal(str(tick)).as_tuple().exponent)
    venue = Venue("RESEARCH")
    inst_symbol = Symbol(f"{symbol}-USDT")
    instrument = CryptoPerpetual(
        instrument_id=InstrumentId(inst_symbol, venue), raw_symbol=inst_symbol,
        base_currency=Currency.from_str(symbol), quote_currency=USDT, settlement_currency=USDT,
        is_inverse=False, price_precision=decimals, price_increment=Price(tick, decimals),
        size_precision=SIZE_PRECISION, size_increment=SIZE_INCREMENT,
        margin_init=Decimal("0"), margin_maint=Decimal("0"),
        maker_fee=Decimal("0"), taker_fee=Decimal("0"), ts_event=0, ts_init=0,
    )

    # The first event in each pair is a flat bar one millisecond after the
    # source open; the second is the exact source OHLC at the source close.
    synthetic_values = np.empty((2 * len(frame), 5), dtype=np.float64)
    synthetic_ns = np.empty(2 * len(frame), dtype=np.int64)
    synthetic_values[0::2, 0] = prices[:, 0]
    synthetic_values[0::2, 1] = prices[:, 0]
    synthetic_values[0::2, 2] = prices[:, 0]
    synthetic_values[0::2, 3] = prices[:, 0]
    synthetic_values[0::2, 4] = 1e12
    synthetic_values[1::2, :4] = prices
    synthetic_values[1::2, 4] = 1e12
    synthetic_ns[0::2] = frame.index.asi8 + OPEN_OFFSET_NS
    synthetic_ns[1::2] = frame.index.asi8 + HOUR_NS
    if (np.diff(synthetic_ns) <= 0).any():
        raise ValueError("Synthetic event bars are not strictly chronological")
    execution_frame = pd.DataFrame(synthetic_values[:, :4], columns=["open", "high", "low", "close"],
                                   index=pd.to_datetime(synthetic_ns, utc=True))
    execution_frame["volume"] = 1e12
    strategy_frame = frame.copy()
    fees = ActualFillFee(maker, taker)
    engine = BacktestEngine(BacktestEngineConfig(
        logging=LoggingConfig(bypass_logging=True),
        risk_engine=RiskEngineConfig(bypass=True),
        run_analysis=False,
    ))
    try:
        engine.add_venue(
            venue, OmsType.HEDGING, AccountType.MARGIN, [Money(1_000_000_000_000, USDT)],
            base_currency=USDT, fee_model=fees, frozen_account=True,
            use_message_queue=True, reject_stop_orders=True,
            bar_execution=True, liquidity_consumption=False,
        )
        engine.add_instrument(instrument)
        bar_type = BarType.from_str(f"{instrument.id}-1-HOUR-LAST-EXTERNAL")
        strategy = CloudHourlyStrategy(instrument, bar_type, strategy_frame,
                                       prepared.to_dict("records"), cross_rows, tick)
        strategy.fees = fees
        engine.add_strategy(strategy)
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter("always")
            engine.add_data(BarDataWrangler(bar_type, instrument).process(execution_frame))
            engine.run()
        if strategy.errors:
            raise RuntimeError(f"Native cloud-hourly order errors: {strategy.errors[:5]}")
        positions = engine.cache.positions()
        trades = pd.DataFrame(strategy.rows)
        if (strategy.active or len(trades) != len(prepared) or len(positions) != len(prepared)
                or any(position.is_open for position in positions)):
            raise RuntimeError("Missing or open independent cloud-hourly positions")
        if engine.cache.orders_open():
            raise RuntimeError("Cloud-hourly run left orphaned orders")
        realized = {str(position.id): float(position.realized_pnl) for position in positions}
        expected_pnl = dict(zip(trades.framework_position_id, trades.net_usdt))
        for position_id, pnl in realized.items():
            if not math.isclose(pnl, expected_pnl[position_id], rel_tol=1e-8, abs_tol=1e-6):
                raise RuntimeError(f"Native PnL mismatch for {position_id}: {pnl} vs {expected_pnl[position_id]}")
        audit = dict(
            engine="nautilus_trader", version=ENGINE_VERSION,
            execution_minutes=60, source_bars=len(frame), native_event_bars=2*len(frame),
            positions=len(positions), fills=2*len(trades),
            simultaneous_positions_max=strategy.max_open,
            open_only_phase="flat open print at source open + 1ms; market entries and open exits fill there",
            intrabar_phase="native original 1H OHLC at source open + 1h",
            cross_start_semantics="UTC hour open at which latest confirmed 12H EMA pair becomes available",
            ema_flip="strict cross with V1.9 epsilon; equality is not a flip; trigger rounded to first adverse tick",
            fixed_stop="inclusive equality; native stop-market",
            maker=maker, taker=taker, target_risk_usdt=risk_usdt,
            quantity_precision=SIZE_PRECISION,
            funding_slippage_spread="not modeled",
            shared_account_capacity="not modeled; independent HEDGING positions with synthetic unlimited liquidity",
            actual_risk_usdt_range=[float(trades.actual_risk_usdt.min()), float(trades.actual_risk_usdt.max())],
            risk_rounding_error_max_abs_usdt=float(trades.risk_rounding_error_usdt.abs().max()),
            runtime_warnings=sorted({str(w.message) for w in emitted}),
            seconds=round(time.perf_counter()-started, 3),
        )
        return trades, audit
    finally:
        engine.dispose()
