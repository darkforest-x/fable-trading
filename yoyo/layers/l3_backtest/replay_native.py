"""Interactive historical replay backed by the pinned NautilusTrader engine.

The engine consumes only the bar currently supplied by an ``advance`` command. Each
OHLC candle is expanded into four monotone synthetic bars (open-high-low-close)
so native orders observe an explicit path without consulting later candles.
Streaming batches use ``BacktestEngine.run(streaming=True)`` followed by
``clear_data()``; the pinned 1.231.0 engine documents that this pauses the run
without finalizing it. This is the same bounded streaming pattern used by
``nautilus_independent.run_native``. See the official API guide at
https://nautilustrader.io/docs/nightly/concepts/backtesting/apis-and-runs/ and
the installed 1.231.0 ``BacktestEngine.run`` docstring for the pinned contract.

This adapter is an isolated, single-symbol paper account. It has no network or
file access, uses a 1x notional reserve, and never creates a liquidation order.
Nautilus performs all fills and fee accounting; this module only validates the
user protocol, maintains the cash-reserve view, and translates native events.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import hashlib
import json
import re
import sys
from typing import Any

import nautilus_trader
import numpy as np
import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, RiskEngineConfig, StrategyConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId, PositionId, Symbol, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.trading.strategy import Strategy

from .nautilus_independent import ActualFillFee, ENGINE_VERSION, IndependentLimitFill


_ID_RE = re.compile(r"^[A-Za-z0-9]{32}$")
_SYMBOL_RE = re.compile(r"^([A-Z0-9]+)-USDT-SWAP$")
_ZERO = Decimal("0")
_ONE = Decimal("1")


def _decimal(value: Any, name: str) -> Decimal:
    """Parse finite decimal input without accepting bools or locale forms."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{name} must be a finite decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be a finite decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be a finite decimal")
    return result


def _json_signature(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _id(value: Any, name: str = "id") -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{name} must be exactly 32 ASCII letters or digits")
    return value


@dataclass
class _Position:
    id: str
    side: str
    entry: Decimal
    quantity: Decimal
    remaining: Decimal
    opened: int
    stop: Decimal | None
    target: Decimal | None
    entry_fee: Decimal
    fees: Decimal
    realized_gross: Decimal = _ZERO
    exit_fees: Decimal = _ZERO
    exit_quantity: Decimal = _ZERO
    exit_value: Decimal = _ZERO
    closed: int | None = None
    close_reason: str | None = None
    generation: int = 0
    stop_order_id: str | None = None
    target_order_id: str | None = None


class _ReplayStrategy(Strategy):
    """Thin strategy bridge; the owning ReplayEngine consumes native events."""

    def __init__(self, owner: "ReplayEngine", instrument: CryptoPerpetual, bar_type: BarType):
        super().__init__(StrategyConfig(oms_type="HEDGING", log_events=False, log_commands=False))
        self.owner = owner
        self.instrument = instrument
        self.bar_type = bar_type

    def on_start(self):
        self.subscribe_bars(self.bar_type)

    def on_order_filled(self, event):
        self.owner._on_fill(event)

    def on_order_canceled(self, event):
        self.owner._on_canceled(event)

    def on_order_rejected(self, event):
        self.owner._on_rejected(event)

    def on_order_denied(self, event):
        self.owner._on_rejected(event)


class ReplayEngine:
    """One historical replay account, driven synchronously by JSON commands.

    Config requires ``symbol``, ``cash``, ``period_seconds``,
    ``price_precision`` and ``size_precision``. Optional ``maker_fee`` and
    ``taker_fee`` default to the project's current Lv1 OKX rates (0.02%/0.05%).
    """

    def __init__(self, config: dict):
        if not isinstance(config, dict):
            raise ValueError("config must be an object")
        if nautilus_trader.__version__ != ENGINE_VERSION:
            raise RuntimeError(f"Replay requires NautilusTrader {ENGINE_VERSION}; got {nautilus_trader.__version__}")
        self.config = dict(config)
        symbol = self.config.get("symbol")
        match = _SYMBOL_RE.fullmatch(symbol) if isinstance(symbol, str) else None
        if not match:
            raise ValueError("symbol must use BASE-USDT-SWAP form")
        self.base = match.group(1)
        self.period_seconds = self._positive_int(self.config.get("period_seconds"), "period_seconds")
        self.price_precision = self._precision(self.config.get("price_precision"), "price_precision")
        self.size_precision = self._precision(self.config.get("size_precision"), "size_precision")
        self.price_step = _ONE.scaleb(-self.price_precision)
        self.size_step = _ONE.scaleb(-self.size_precision)
        self.starting_cash = _decimal(self.config.get("cash"), "cash")
        if self.starting_cash <= 0:
            raise ValueError("cash must be positive")
        if self.starting_cash != self.starting_cash.quantize(Decimal("0.00000001")):
            raise ValueError("cash exceeds USDT's 8-decimal precision")
        self.maker_fee = _decimal(self.config.get("maker_fee", "0.0002"), "maker_fee")
        self.taker_fee = _decimal(self.config.get("taker_fee", "0.0005"), "taker_fee")
        if not (_ZERO <= self.maker_fee < _ONE and _ZERO <= self.taker_fee < _ONE):
            raise ValueError("maker_fee and taker_fee must be in [0, 1)")
        if self.period_seconds % 60:
            raise ValueError("period_seconds must be a whole number of minutes")

        self.venue = Venue("REPLAY")
        instrument_symbol = Symbol(f"{self.base}-USDT")
        self.instrument = CryptoPerpetual(
            instrument_id=InstrumentId(instrument_symbol, self.venue),
            raw_symbol=instrument_symbol,
            base_currency=Currency.from_str(self.base),
            quote_currency=USDT,
            settlement_currency=USDT,
            is_inverse=False,
            price_precision=self.price_precision,
            price_increment=Price(self.price_step, self.price_precision),
            size_precision=self.size_precision,
            size_increment=Quantity(self.size_step, self.size_precision),
            margin_init=Decimal("0"),
            margin_maint=Decimal("0"),
            maker_fee=_ZERO,
            taker_fee=_ZERO,
            ts_event=0,
            ts_init=0,
        )
        self.bar_type = BarType.from_str(f"{self.instrument.id}-{self.period_seconds // 60}-MINUTE-LAST-EXTERNAL")

        self._fee_model = ActualFillFee(self.maker_fee, self.taker_fee)
        self._fill_model = IndependentLimitFill()
        self._engine = BacktestEngine(BacktestEngineConfig(
            logging=LoggingConfig(bypass_logging=True),
            risk_engine=RiskEngineConfig(bypass=True),
            run_analysis=False,
        ))
        self._engine.add_venue(
            self.venue,
            OmsType.HEDGING,
            AccountType.MARGIN,
            [Money(self.starting_cash, USDT)],
            base_currency=USDT,
            default_leverage=Decimal("1"),
            fee_model=self._fee_model,
            fill_model=self._fill_model,
            frozen_account=False,
            use_message_queue=False,
            reject_stop_orders=False,
            support_gtd_orders=True,
            support_contingent_orders=True,
            use_position_ids=True,
            use_reduce_only=True,
            bar_execution=True,
            bar_adaptive_high_low_ordering=False,
            liquidity_consumption=False,
        )
        self._engine.add_instrument(self.instrument)
        self._strategy = _ReplayStrategy(self, self.instrument, self.bar_type)
        self._engine.add_strategy(self._strategy)

        self._cash = self.starting_cash
        self._fees = _ZERO
        self._last_price: Decimal | None = None
        self._clock: int | None = None
        self._last_bar_open: int | None = None
        self._seeded = False
        self._closed = False
        self._failure: str | None = None
        self._current_bar_close: int | None = None
        self._current_bar_open: int | None = None
        self._positions: dict[str, _Position] = {}
        self._user_orders: dict[str, dict] = {}
        self._pending_entries: dict[str, dict] = {}
        self._native_meta: dict[str, dict] = {}
        self._native_orders: dict[str, object] = {}
        self._commands: dict[str, tuple[str, dict]] = {}
        self._fills: list[dict] = []
        self._closed_rows: list[dict] = []
        self._errors: list[str] = []
        self._internal_ids: set[str] = set()

    @staticmethod
    def _positive_int(value, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _precision(value, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 12:
            raise ValueError(f"{name} must be an integer from 0 to 12")
        return value

    def _price(self, value: Any, name: str) -> Decimal:
        price = _decimal(value, name)
        if price <= 0:
            raise ValueError(f"{name} must be positive")
        if price != price.quantize(self.price_step):
            raise ValueError(f"{name} exceeds configured price precision")
        if Decimal(str(float(price))).quantize(self.price_step) != price:
            raise ValueError(f"{name} cannot be represented at configured precision by Nautilus")
        return price

    def _quantity(self, value: Decimal) -> Quantity:
        value = value.quantize(self.size_step)
        if Decimal(str(float(value))).quantize(self.size_step) != value:
            raise ValueError("quantity cannot be represented at configured precision by Nautilus")
        return Quantity(value, self.size_precision)

    def _native_price(self, value: Decimal) -> Price:
        return Price(value, self.price_precision)

    def _fmt_price(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return f"{value:.{self.price_precision}f}"

    def _fmt_quantity(self, value: Decimal) -> str:
        return f"{value:.{self.size_precision}f}"

    @staticmethod
    def _fmt_money(value: Decimal) -> str:
        return f"{value:.8f}"

    @staticmethod
    def _money_value(value) -> Decimal:
        return value.as_decimal() if hasattr(value, "as_decimal") else _decimal(value, "native money")

    def _ledger_failure(self, message: str):
        self._failure = message
        raise RuntimeError(message)

    def _derived_id(self, position_id: str, role: str, generation: int) -> str:
        base = f"{position_id}:{role}:{generation}"
        value = hashlib.sha256(base.encode("ascii")).hexdigest()[:32]
        salt = 0
        while value in self._internal_ids or value in self._user_orders or value in self._commands:
            salt += 1
            value = hashlib.sha256(f"{base}:{salt}".encode("ascii")).hexdigest()[:32]
        self._internal_ids.add(value)
        return value

    def _reserve_state(self) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        unrealized = sum(
            (Decimal(1 if p.side == "long" else -1) * (self._last_price - p.entry) * p.remaining
             for p in self._positions.values()),
            _ZERO,
        ) if self._last_price is not None else _ZERO
        equity = self._cash + unrealized
        open_notional = sum((p.entry * p.remaining for p in self._positions.values()), _ZERO)
        pending_notional = sum((row["reserve_notional"] for row in self._pending_entries.values()), _ZERO)
        pending_fees = sum((row["reserve_fee"] for row in self._pending_entries.values()), _ZERO)
        available = min(self._cash, equity) - open_notional - pending_notional - pending_fees
        return equity, available, unrealized, open_notional

    def _native_ledger(self) -> dict:
        if not self._seeded or self._closed:
            equity, _, unrealized, _ = self._reserve_state()
            return {
                "cash": self._fmt_money(self._cash),
                "realized": self._fmt_money(self._cash - self.starting_cash),
                "unrealized": self._fmt_money(unrealized),
                "equity": self._fmt_money(equity),
                "matched": True,
            }
        account = self._engine.portfolio.account(self.venue)
        if account is None:
            self._ledger_failure("Native replay account is missing")
        native_cash = account.balance_total(USDT).as_decimal()
        native_positions = [
            self._engine.cache.position(PositionId(pid))
            for pid in self._positions
        ]
        native_unrealized = _ZERO
        for pos in native_positions:
            if pos is None or not pos.is_open:
                self._ledger_failure("Native position cache disagrees with replay ledger")
            native_unrealized += pos.unrealized_pnl(self._native_price(self._last_price)).as_decimal()
        expected_equity = self._cash + sum(
            (Decimal(1 if pos.side == "long" else -1) * (self._last_price - pos.entry) * pos.remaining
             for pos in self._positions.values()),
            _ZERO,
        )
        native_equity = native_cash + native_unrealized
        tolerance = max(self.price_step, Decimal("0.0000001"))
        if abs(native_cash - self._cash) > tolerance:
            self._ledger_failure(f"Native cash ledger mismatch: {native_cash} != {self._cash}")
        if abs(native_equity - expected_equity) > tolerance:
            self._ledger_failure(f"Native equity ledger mismatch: {native_equity} != {expected_equity}")
        native_realized = _ZERO
        for position in self._engine.cache.positions():
            if position.instrument_id == self.instrument.id and position.realized_pnl is not None:
                native_realized += position.realized_pnl.as_decimal()
        realized = self._cash - self.starting_cash
        if abs(native_realized - realized) > tolerance:
            self._ledger_failure(f"Native realized ledger mismatch: {native_realized} != {realized}")
        return {
            "cash": self._fmt_money(native_cash),
            "realized": self._fmt_money(native_realized),
            "unrealized": self._fmt_money(native_unrealized),
            "equity": self._fmt_money(native_equity),
            "matched": True,
        }

    def snapshot(self) -> dict:
        if self._closed:
            return self._snapshot_data(ledger=self._native_ledger())
        return self._snapshot_data(ledger=self._native_ledger())

    def _snapshot_data(self, *, ledger: dict) -> dict:
        equity, available, _, _ = self._reserve_state()
        positions = []
        for pos in self._positions.values():
            side_sign = Decimal(1 if pos.side == "long" else -1)
            unrealized = side_sign * (self._last_price - pos.entry) * pos.remaining
            positions.append({
                "id": pos.id,
                "side": pos.side,
                "entry": self._fmt_price(pos.entry),
                "quantity": self._fmt_quantity(pos.quantity),
                "remaining": self._fmt_quantity(pos.remaining),
                "notional": self._fmt_money(pos.entry * pos.remaining),
                "entry_notional": self._fmt_money(pos.entry * pos.quantity),
                "stop": self._fmt_price(pos.stop),
                "target": self._fmt_price(pos.target),
                "opened": pos.opened,
                "unrealized": self._fmt_money(unrealized),
                "fees": self._fmt_money(pos.fees),
                "stop_order_id": pos.stop_order_id,
                "target_order_id": pos.target_order_id,
            })
        orders = []
        for row in self._user_orders.values():
            orders.append({
                "id": row["id"],
                "side": row["side"],
                "kind": row["kind"],
                "price": self._fmt_price(row["price"]),
                "notional": self._fmt_money(row["notional"]),
                "quantity": self._fmt_quantity(row["quantity"]),
                "status": row["status"],
                "tag": row["tag"],
                "expires": row["expires"],
                "position_id": row.get("position_id"),
            })
        return {
            "clock": self._clock,
            "price": self._fmt_price(self._last_price),
            "cash": self._fmt_money(self._cash),
            "equity": self._fmt_money(equity),
            "available": self._fmt_money(available),
            "fees": self._fmt_money(self._fees),
            "positions": positions,
            "orders": orders,
            "closed": list(self._closed_rows),
            "fills": list(self._fills),
            "ledger": ledger,
        }

    def command(self, payload: dict) -> dict:
        if self._closed:
            raise RuntimeError("Replay engine is closed")
        if self._failure is not None:
            raise RuntimeError("Replay engine is unusable after a native failure")
        if not isinstance(payload, dict):
            raise ValueError("command must be an object")
        op = payload.get("op")
        if op == "advance":
            self._advance(payload.get("bars"))
        elif op == "order":
            self._order(payload)
        elif op == "orders":
            self._orders(payload.get("items"))
        elif op == "close":
            self._close_position(payload)
        elif op == "cancel":
            self._cancel(payload.get("order_id"))
        elif op == "snapshot":
            pass
        else:
            raise ValueError(f"unsupported operation: {op!r}")
        self._raise_native_errors()
        return self.snapshot()

    def _raise_native_errors(self):
        if self._errors:
            error = self._errors.pop(0)
            self._errors.clear()
            self._failure = error
            raise RuntimeError(error)

    def _advance(self, bars) -> None:
        if not isinstance(bars, list) or not bars:
            raise ValueError("advance bars must be a nonempty array")
        for item in bars:
            bar = self._validate_bar(item)
            if self._last_bar_open is not None and bar["time"] != self._last_bar_open + self.period_seconds:
                raise ValueError("bars must be strictly continuous by period_seconds")
            if self._last_bar_open is None and self._seeded:
                raise RuntimeError("seed state is inconsistent")
            self._advance_one(bar)

    def _validate_bar(self, bar) -> dict:
        if not isinstance(bar, dict):
            raise ValueError("bar must be an object")
        try:
            at = bar["time"]
            if isinstance(at, bool) or not isinstance(at, int) or at < 0:
                raise ValueError("bar time must be a nonnegative UTC second")
            opened, high, low, close = (
                self._price(bar[name], name)
                for name in ("open", "high", "low", "close")
            )
            volume = _decimal(bar.get("volume", "0"), "volume")
            if volume < 0:
                raise ValueError("volume must be nonnegative")
            if high < max(opened, close) or low > min(opened, close) or high < low:
                raise ValueError("invalid OHLC relationship")
            return {"time": at, "open": opened, "high": high, "low": low, "close": close, "volume": volume}
        except KeyError as exc:
            raise ValueError(f"bar is missing {exc.args[0]}") from exc

    def _advance_one(self, bar: dict) -> None:
        at = bar["time"]
        close_time = at + self.period_seconds
        if self._last_bar_open is None:
            # A seed only establishes native market state; it cannot execute an order.
            self._feed_bar(bar, close_time)
            self._last_bar_open = at
            self._clock = close_time
            self._last_price = bar["close"]
            self._seeded = True
            self._raise_native_errors()
            return

        # Expiry is evaluated before the first candle whose interval contains
        # or follows the expiry instant; this avoids guessing a within-bar time.
        for row in list(self._pending_entries.values()):
            if row["expires"] is not None and close_time > row["expires"]:
                self._cancel_native(row["order"], status="expired")
        self._current_bar_open = at
        self._current_bar_close = close_time
        self._feed_bar(bar, close_time)
        self._last_bar_open = at
        self._clock = close_time
        self._last_price = bar["close"]
        self._current_bar_open = None
        self._current_bar_close = None
        self._raise_native_errors()

    def _feed_bar(self, bar: dict, close_time: int) -> None:
        o, h, l, c = (bar[k] for k in ("open", "high", "low", "close"))
        points = [o, h, l, c]
        segment_rows = []
        previous = o
        for point in points:
            segment_rows.append((previous, max(previous, point), min(previous, point), point, Decimal("1000000000000")))
            previous = point
        close_ns = close_time * 1_000_000_000
        index = pd.to_datetime([close_ns - 3, close_ns - 2, close_ns - 1, close_ns], utc=True)
        frame = pd.DataFrame(
            np.asarray(segment_rows, dtype=np.float64),
            columns=["open", "high", "low", "close", "volume"],
            index=index,
        )
        # Pandas 3 exposes read-only block arrays; Nautilus 1.231's wrangler
        # requires writable buffers, so materialize each input column.
        for column in frame.columns:
            frame[column] = np.array(frame[column].to_numpy(), dtype=np.float64, copy=True)
        native_bars = BarDataWrangler(self.bar_type, self.instrument).process(frame)
        try:
            self._engine.add_data(native_bars)
            self._engine.run(streaming=True)
            self._engine.clear_data()
        except Exception as exc:
            self._failure = "native streaming engine failed"
            self._current_bar_open = None
            self._current_bar_close = None
            raise RuntimeError("Native streaming engine failed") from exc

    def _check_id_unused(self, value: str):
        if value in self._user_orders or value in self._commands or value in self._internal_ids:
            raise ValueError(f"id {value} has already been used")

    def _prepare_order(self, payload: dict) -> dict:
        if not self._seeded or self._last_price is None:
            raise ValueError("seed a historical bar before submitting orders")
        if not isinstance(payload, dict):
            raise ValueError("order item must be an object")
        allowed = {"id", "side", "kind", "notional", "price", "stop", "target", "tag", "expires"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"unknown order fields: {sorted(unknown)}")
        order_id = _id(payload.get("id"))
        side = payload.get("side")
        if side not in ("long", "short"):
            raise ValueError("side must be long or short")
        kind = payload.get("kind")
        if kind not in ("market", "limit"):
            raise ValueError("kind must be market or limit")
        notional = _decimal(payload.get("notional"), "notional")
        if notional <= 0:
            raise ValueError("notional must be positive")
        if kind == "market":
            if "price" in payload:
                raise ValueError("market orders cannot specify price")
            price = None
            estimate_price = self._last_price
            fee_rate = self.taker_fee
        else:
            if "price" not in payload:
                raise ValueError("limit orders require price")
            price = self._price(payload["price"], "price")
            if (side == "long" and price >= self._last_price) or (side == "short" and price <= self._last_price):
                raise ValueError("limit must be strictly passive versus the latest closed price")
            estimate_price = price
            fee_rate = self.maker_fee
        quantity = (notional / estimate_price).quantize(self.size_step, rounding=ROUND_DOWN)
        if quantity <= 0:
            raise ValueError("notional is too small for configured size precision")
        stop = self._price(payload["stop"], "stop") if payload.get("stop") is not None else None
        target = self._price(payload["target"], "target") if payload.get("target") is not None else None
        if side == "long":
            if stop is not None and stop >= estimate_price:
                raise ValueError("long stop must be below expected entry")
            if target is not None and target <= estimate_price:
                raise ValueError("long target must be above expected entry")
        else:
            if stop is not None and stop <= estimate_price:
                raise ValueError("short stop must be above expected entry")
            if target is not None and target >= estimate_price:
                raise ValueError("short target must be below expected entry")
        tag = payload.get("tag")
        if tag not in (None, "monday1618"):
            raise ValueError("tag must be null or monday1618")
        expires = payload.get("expires")
        if expires is not None:
            if kind != "limit":
                raise ValueError("expires is only valid for limit orders")
            if isinstance(expires, bool) or not isinstance(expires, int) or expires <= self._clock:
                raise ValueError("expires must be a future UTC second")
        return {
            "id": order_id,
            "side": side,
            "kind": kind,
            "notional": notional,
            "price": price,
            "quantity": quantity,
            "stop": stop,
            "target": target,
            "tag": tag,
            "expires": expires,
            "reserve_notional": notional,
            "reserve_fee": notional * fee_rate,
            "payload": dict(payload),
        }

    def _validate_reserve(self, prepared: list[dict]) -> None:
        equity, available, _, _ = self._reserve_state()
        if equity <= 0:
            raise ValueError("equity is nonpositive; new orders are disabled")
        requested = sum((row["reserve_notional"] + row["reserve_fee"] for row in prepared), _ZERO)
        if requested > available:
            raise ValueError(f"insufficient 1x available capital: need {requested}, available {available}")

    def _order(self, payload: dict) -> None:
        order_id = _id(payload.get("id"))
        signature = _json_signature(payload)
        prior = self._commands.get(order_id)
        if prior:
            if prior[0] != signature:
                raise ValueError("replayed id has a different command payload")
            return
        self._check_id_unused(order_id)
        order_payload = {key: value for key, value in payload.items() if key != "op"}
        row = self._prepare_order(order_payload)
        self._validate_reserve([row])
        self._submit_entry(row, signature)

    def _orders(self, items) -> None:
        if not isinstance(items, list) or not items:
            raise ValueError("orders items must be a nonempty array")
        prepared = []
        signatures = {}
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("order item must be an object")
            order_id = _id(item.get("id"))
            signature = _json_signature(item)
            signatures[order_id] = signature
            row = {"id": order_id}
            if row["id"] in seen:
                raise ValueError("orders batch contains duplicate ids")
            seen.add(row["id"])
            prepared.append(row)
        existing = [row for row in prepared if row["id"] in self._commands]
        if existing:
            if len(existing) != len(prepared) or any(
                self._commands[row["id"]][0] != signatures[row["id"]] for row in existing
            ):
                raise ValueError("orders batch replay must match the original batch exactly")
            return
        for row, item in zip(prepared, items):
            self._check_id_unused(row["id"])
            row.update(self._prepare_order(item))
        self._validate_reserve(prepared)
        # All request geometry and the combined reserve are checked before the
        # first native command is sent, so an invalid side cannot partially land.
        for row in prepared:
            self._submit_entry(row, signatures[row["id"]])

    def _submit_entry(self, row: dict, signature: str) -> None:
        side = OrderSide.BUY if row["side"] == "long" else OrderSide.SELL
        quantity = self._quantity(row["quantity"])
        if row["kind"] == "market":
            order = self._strategy.order_factory.market(
                self.instrument.id, side, quantity,
                client_order_id=ClientOrderId(row["id"]),
            )
        else:
            order = self._strategy.order_factory.limit(
                self.instrument.id, side, quantity,
                price=self._native_price(row["price"]),
                post_only=True,
                client_order_id=ClientOrderId(row["id"]),
            )
        pos_id = PositionId(row["id"])
        public_row = {
            **row,
            "status": "submitted",
            "position_id": row["id"],
            "order": order,
        }
        self._user_orders[row["id"]] = public_row
        self._commands[row["id"]] = (signature, dict(row["payload"]))
        self._native_meta[str(order.client_order_id)] = {
            "role": "entry",
            "position_id": row["id"],
            "user_order_id": row["id"],
        }
        self._native_orders[str(order.client_order_id)] = order
        if row["kind"] == "limit":
            self._pending_entries[row["id"]] = public_row
        self._submit_native(order, pos_id)
        if row["id"] in self._pending_entries and order.is_closed:
            self._pending_entries.pop(row["id"], None)
        elif row["id"] in self._pending_entries and public_row["status"] == "submitted":
            public_row["status"] = "open"
        self._raise_native_errors()

    def _close_position(self, payload: dict) -> None:
        allowed = {"op", "position_id", "fraction", "id"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"unknown close fields: {sorted(unknown)}")
        order_id = _id(payload.get("id"))
        signature = _json_signature(payload)
        prior = self._commands.get(order_id)
        if prior:
            if prior[0] != signature:
                raise ValueError("replayed id has a different command payload")
            return
        position_id = _id(payload.get("position_id"), "position_id")
        pos = self._positions.get(position_id)
        if pos is None:
            raise ValueError("position is not open")
        fraction = _decimal(payload.get("fraction"), "fraction")
        if not _ZERO < fraction <= _ONE:
            raise ValueError("fraction must be greater than 0 and at most 1")
        self._check_id_unused(order_id)
        quantity = pos.remaining if fraction == _ONE else (pos.remaining * fraction).quantize(
            self.size_step, rounding=ROUND_DOWN
        )
        if quantity <= 0 or quantity > pos.remaining:
            raise ValueError("fraction produces an invalid close quantity")
        self._cancel_protections(pos)
        order = self._strategy.order_factory.market(
            self.instrument.id,
            OrderSide.SELL if pos.side == "long" else OrderSide.BUY,
            self._quantity(quantity),
            reduce_only=True,
            client_order_id=ClientOrderId(order_id),
        )
        self._commands[order_id] = (signature, dict(payload))
        self._native_meta[order_id] = {
            "role": "manual_close",
            "position_id": position_id,
            "user_order_id": order_id,
        }
        self._native_orders[order_id] = order
        self._submit_native(order, PositionId(position_id))
        self._raise_native_errors()

    def _cancel(self, order_id: Any) -> None:
        value = _id(order_id, "order_id")
        row = self._user_orders.get(value)
        if row is None:
            raise ValueError("unknown entry order")
        if row["status"] in ("canceled", "expired"):
            return
        if row["status"] != "open":
            raise ValueError("only pending entry orders can be canceled")
        self._cancel_native(row["order"], status="canceled")

    def _cancel_native(self, order, *, status: str | None = None) -> None:
        key = str(order.client_order_id)
        meta = self._native_meta.get(key, {})
        if not order.is_closed:
            try:
                self._strategy.cancel_order(order)
            except Exception as exc:
                self._failure = "native order cancellation failed"
                raise RuntimeError("Native order cancellation failed") from exc
        user_id = meta.get("user_order_id")
        if user_id in self._pending_entries:
            self._pending_entries.pop(user_id, None)
        if status is not None and user_id in self._user_orders:
            self._user_orders[user_id]["status"] = status

    def _cancel_protections(self, pos: _Position) -> None:
        for key in (pos.stop_order_id, pos.target_order_id):
            if key:
                order = self._native_orders.get(key)
                if order is not None and not order.is_closed:
                    self._cancel_native(order, status=None)
        pos.stop_order_id = None
        pos.target_order_id = None

    def _submit_protections(self, pos: _Position) -> None:
        pos.generation += 1
        if pos.remaining <= 0:
            return
        qty = self._quantity(pos.remaining)
        exit_side = OrderSide.SELL if pos.side == "long" else OrderSide.BUY
        if pos.stop is not None:
            order_id = self._derived_id(pos.id, "stop", pos.generation)
            order = self._strategy.order_factory.stop_market(
                self.instrument.id,
                exit_side,
                qty,
                trigger_price=self._native_price(pos.stop),
                reduce_only=True,
                client_order_id=ClientOrderId(order_id),
            )
            pos.stop_order_id = order_id
            self._native_meta[order_id] = {"role": "stop", "position_id": pos.id}
            self._native_orders[order_id] = order
            self._submit_native(order, PositionId(pos.id))
        if pos.target is not None:
            order_id = self._derived_id(pos.id, "target", pos.generation)
            order = self._strategy.order_factory.limit(
                self.instrument.id,
                exit_side,
                qty,
                price=self._native_price(pos.target),
                post_only=True,
                reduce_only=True,
                client_order_id=ClientOrderId(order_id),
            )
            pos.target_order_id = order_id
            self._native_meta[order_id] = {"role": "target", "position_id": pos.id}
            self._native_orders[order_id] = order
            self._submit_native(order, PositionId(pos.id))
        self._raise_native_errors()

    def _submit_native(self, order, position_id: PositionId) -> None:
        try:
            self._strategy.submit_order(order, position_id=position_id)
        except Exception as exc:
            self._failure = "native order submission failed"
            raise RuntimeError("Native order submission failed") from exc

    def _on_fill(self, event) -> None:
        client_id = str(event.client_order_id)
        meta = self._native_meta.get(client_id)
        if meta is None:
            self._errors.append(f"unmapped native fill: {client_id}")
            return
        role = meta["role"]
        position_id = meta["position_id"]
        price = event.last_px.as_decimal()
        quantity = event.last_qty.as_decimal()
        fee = event.commission.as_decimal() if event.commission is not None else _ZERO
        filled_time = self._current_bar_close if self._current_bar_close is not None else self._clock
        if filled_time is None:
            self._errors.append("native fill occurred before replay clock was seeded")
            return
        if role == "entry":
            row = self._user_orders[position_id]
            pos = _Position(
                id=position_id,
                side=row["side"],
                entry=price,
                quantity=quantity,
                remaining=quantity,
                opened=filled_time,
                stop=row["stop"],
                target=row["target"],
                entry_fee=fee,
                fees=fee,
            )
            if position_id in self._positions:
                self._errors.append(f"duplicate native position id: {position_id}")
                return
            self._positions[position_id] = pos
            self._cash -= fee
            self._fees += fee
            row["status"] = "filled"
            self._pending_entries.pop(position_id, None)
            self._submit_protections(pos)
            reason = "entry"
        else:
            pos = self._positions.get(position_id)
            if pos is None:
                self._errors.append(f"native exit without open position: {position_id}")
                return
            if quantity <= 0 or quantity > pos.remaining:
                self._errors.append(f"invalid native close quantity for {position_id}")
                return
            if role in ("stop", "target"):
                self._cancel_protections(pos)
            gross = Decimal(1 if pos.side == "long" else -1) * (price - pos.entry) * quantity
            self._cash += gross - fee
            self._fees += fee
            pos.realized_gross += gross
            pos.exit_fees += fee
            pos.fees += fee
            pos.exit_quantity += quantity
            pos.exit_value += price * quantity
            pos.remaining -= quantity
            reason = "stop" if role == "stop" else "target" if role == "target" else "manual"
            if role == "manual_close":
                self._user_orders[meta["user_order_id"]] = {
                    "id": meta["user_order_id"],
                    "side": pos.side,
                    "kind": "market",
                    "price": None,
                    "notional": pos.entry * quantity,
                    "quantity": quantity,
                    "status": "filled",
                    "tag": None,
                    "expires": None,
                    "position_id": position_id,
                }
            if pos.remaining == 0:
                pos.closed = filled_time
                pos.close_reason = reason
                self._finalize_position(pos)
            else:
                self._submit_protections(pos)

        order_side = "buy" if event.order_side == OrderSide.BUY else "sell"
        self._fills.append({
            "id": client_id,
            "position_id": position_id,
            "side": self._positions[position_id].side if position_id in self._positions else (
                self._closed_rows[-1]["side"] if self._closed_rows and self._closed_rows[-1]["id"] == position_id else None
            ),
            "order_side": order_side,
            "price": self._fmt_price(price),
            "quantity": self._fmt_quantity(quantity),
            "fee": self._fmt_money(fee),
            "role": role,
            "reason": reason,
            "time": filled_time,
        })

    def _finalize_position(self, pos: _Position) -> None:
        self._cancel_protections(pos)
        self._positions.pop(pos.id, None)
        total_fees = pos.entry_fee + pos.exit_fees
        net_pnl = pos.realized_gross - total_fees
        avg_exit = pos.exit_value / pos.exit_quantity if pos.exit_quantity else pos.entry
        self._closed_rows.append({
            "id": pos.id,
            "side": pos.side,
            "entry": self._fmt_price(pos.entry),
            "exit": self._fmt_price(avg_exit),
            "quantity": self._fmt_quantity(pos.quantity),
            "opened": pos.opened,
            "closed": pos.closed,
            "gross_pnl": self._fmt_money(pos.realized_gross),
            "net_pnl": self._fmt_money(net_pnl),
            "fees": self._fmt_money(total_fees),
            "reason": pos.close_reason,
        })

    def _on_canceled(self, event) -> None:
        key = str(event.client_order_id)
        meta = self._native_meta.get(key, {})
        user_id = meta.get("user_order_id")
        if user_id in self._pending_entries:
            self._pending_entries.pop(user_id, None)
            row = self._user_orders.get(user_id)
            if row and row["status"] in ("submitted", "open"):
                row["status"] = "canceled"

    def _on_rejected(self, event) -> None:
        key = str(getattr(event, "client_order_id", "unknown"))
        meta = self._native_meta.get(key, {})
        user_id = meta.get("user_order_id")
        if user_id in self._pending_entries:
            self._pending_entries.pop(user_id, None)
        if user_id in self._user_orders:
            self._user_orders[user_id]["status"] = "rejected"
        reason = getattr(event, "reason", None) or getattr(event, "message", None) or "native order rejected"
        self._errors.append(f"Nautilus rejected order {key}: {reason}")

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._engine.dispose()
        finally:
            self._closed = True


def _serve_jsonl() -> None:
    engine: ReplayEngine | None = None
    for raw in sys.stdin:
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("request must be a JSON object")
            if payload.get("op") == "init":
                if engine is not None:
                    engine.close()
                engine = ReplayEngine(payload.get("config"))
                state = engine.snapshot()
            elif engine is None:
                raise RuntimeError("first request must initialize the replay engine")
            elif payload.get("op") == "shutdown":
                state = engine.snapshot()
                engine.close()
                engine = None
            else:
                state = engine.command(payload)
            sys.stdout.write(json.dumps({"ok": True, "state": state}, separators=(",", ":")) + "\n")
            sys.stdout.flush()
        except Exception as exc:  # JSONL protocol errors must not kill the worker.
            sys.stdout.write(json.dumps({"ok": False, "error": _rpc_error(exc)}, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    if engine is not None:
        engine.close()


def _rpc_error(exc: Exception) -> str:
    """Keep worker-facing errors brief, actionable and localized."""
    message = str(exc)
    if isinstance(exc, ValueError):
        if message.startswith("insufficient 1x available capital"):
            return "1倍本金可用余额不足；请降低名义金额或减少挂单"
        if message.startswith("limit must be strictly passive"):
            return "限价单必须严格挂在最新收盘价的被动一侧"
        if message.startswith("equity is nonpositive"):
            return "模拟权益不为正，已停止接受新委托"
        if message.startswith("seed a historical bar"):
            return "请先推进一根历史K线，再提交委托"
        if message.startswith("bars must be strictly continuous"):
            return "历史K线时间不连续，请从上一根K线后按周期继续"
        if message.startswith("bar time") or message.startswith("invalid OHLC") or message.startswith("bar is missing"):
            return "历史K线时间或OHLCV数据无效"
        if message.startswith("side must") or message.startswith("kind must"):
            return "委托方向只能为long/short，类型只能为market/limit"
        if message.startswith("long stop") or message.startswith("short stop") or message.startswith("long target") or message.startswith("short target"):
            return "止损和止盈价格必须相对入场价处于正确方向"
        if message.startswith("fraction"):
            return "平仓比例必须大于0且不超过1，并能表示为当前数量精度"
        if message.startswith("expires"):
            return "限价单到期时间必须是晚于当前模拟时钟的UTC秒"
        if message.startswith("id ") or message.startswith("position_id") or message.startswith("order_id"):
            return "委托ID必须为32位稳定标识，且持仓或挂单必须存在"
        if message.startswith("symbol must"):
            return "交易对格式须为 BASE-USDT-SWAP"
        if message.startswith("cash must") or message.startswith("insufficient"):
            return "模拟本金必须为正，且新委托不能超过1倍可用本金"
        return "请求参数无效，请检查模拟订单、价格、资金和历史时间"
    if isinstance(exc, RuntimeError):
        if message == "Replay engine is closed":
            return "模拟引擎已关闭"
        if message == "first request must initialize the replay engine":
            return "首条请求必须初始化模拟引擎"
        return "模拟撮合账本核对失败"
    return "模拟回放执行失败"


if __name__ == "__main__":
    _serve_jsonl()
