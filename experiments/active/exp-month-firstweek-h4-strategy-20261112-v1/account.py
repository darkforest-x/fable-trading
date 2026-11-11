"""Shared-collateral accounting for the frozen Nautilus H4 fills.

This module does not create or replay trades. It consumes baseline.py's already
executed fills.csv, scales each admitted fill to the account's 1% / 5% risk
budget, and marks the resulting positions from the same experiment's replay H1
reader. `baseline` has no drawdown entry gate. `floor30` is a separate account
with a 70% high-water floor; it rejects a standard new position when the
existing positions' fixed-stop stress plus that position's stop loss and fees
would take marked equity below the floor.

Events at one timestamp settle exits first, then consider entries in symbol
order. The baseline writer normalizes intrabar exit times to the containing
hour's close and opening-gap exits to that hour's open. Those timestamps are
used as given; the module does not release an intrabar exit at its trigger
hour's open. `cutoff_mark` is an end-of-sample valuation, reported separately
from ordinary closed trades.
"""

from __future__ import annotations

import argparse
from collections import Counter, OrderedDict
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Callable

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE_REPLAY_DIR = HERE.parent / "exp-month-firstweek-all-replay-20261111-v1"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SOURCE_REPLAY_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_REPLAY_DIR))

HOUR_MS = 3_600_000
INITIAL_EQUITY = 1_000.0
RISK_FRACTION = 0.01
FIXED_STOP_FRACTION = 0.05
MAX_NOTIONAL_FRACTION = RISK_FRACTION / FIXED_STOP_FRACTION
MAX_POSITIONS = 5
TAKER_FEE_RATE = 0.0005  # baseline.py's frozen stop / market fee
MAIN_GROUPS = {"上轨守住", "下轨重新收高"}
VALID_EXIT_REASONS = {"stop", "target", "timeout", "cutoff_mark"}
VARIANTS = ("baseline", "floor30")


def _replay_module():
    """Load the established replay reader and its frozen cutoff."""
    import replay  # imported from exp-month-firstweek-all-replay-20261111-v1

    if int(replay.HOUR) != HOUR_MS:
        raise ValueError(f"replay H1 interval changed: {replay.HOUR}")
    return replay


@dataclass(frozen=True)
class QuoteSeries:
    raw: np.ndarray
    opens: np.ndarray
    closes: np.ndarray


class QuoteBook:
    """Small LRU around replay.read_raw; only active symbols stay pinned."""

    def __init__(self, loader: Callable[[str], np.ndarray], max_cached: int = 12):
        self.loader = loader
        self.max_cached = max(5, int(max_cached))
        self.cache: OrderedDict[str, QuoteSeries] = OrderedDict()
        self.pinned: set[str] = set()

    def _series(self, symbol: str) -> QuoteSeries:
        symbol = str(symbol)
        if symbol in self.cache:
            self.cache.move_to_end(symbol)
            return self.cache[symbol]
        raw = np.asarray(self.loader(symbol), dtype=np.float64)
        if raw.ndim != 2 or raw.shape[1] < 5:
            raise ValueError(f"{symbol}: replay.read_raw must return H1 [ms,open,high,low,close,...]")
        raw = raw[:, :5]
        if len(raw):
            clocks = raw[:, 0]
            if not np.isfinite(clocks).all() or not np.equal(clocks, np.rint(clocks)).all():
                raise ValueError(f"{symbol}: H1 clock must be integer milliseconds")
            opens = clocks.astype(np.int64)
            if (opens % HOUR_MS).any() or (np.diff(opens) <= 0).any():
                raise ValueError(f"{symbol}: H1 quotes must be ordered, unique, hour-aligned")
            px = raw[:, 1:5]
            if not np.isfinite(px).all() or (px <= 0).any():
                raise ValueError(f"{symbol}: H1 OHLC contains non-finite or non-positive values")
            if ((px[:, 1] < np.maximum(px[:, 0], px[:, 3])).any()
                    or (px[:, 2] > np.minimum(px[:, 0], px[:, 3])).any()):
                raise ValueError(f"{symbol}: inconsistent H1 OHLC")
        else:
            opens = np.empty(0, dtype=np.int64)
        series = QuoteSeries(raw=raw, opens=opens, closes=opens + HOUR_MS)
        self.cache[symbol] = series
        self.cache.move_to_end(symbol)
        self._evict()
        return series

    def _evict(self) -> None:
        while len(self.cache) > self.max_cached:
            evicted = False
            for symbol in self.cache:
                if symbol not in self.pinned:
                    del self.cache[symbol]
                    evicted = True
                    break
            if not evicted:
                break

    def pin(self, symbol: str) -> None:
        self.pinned.add(str(symbol))

    def unpin(self, symbol: str) -> None:
        self.pinned.discard(str(symbol))
        self._evict()

    def bar(self, symbol: str, hour_open_ms: int) -> np.ndarray | None:
        series = self._series(symbol)
        i = int(np.searchsorted(series.opens, int(hour_open_ms)))
        if i >= len(series.opens) or int(series.opens[i]) != int(hour_open_ms):
            return None
        return series.raw[i]

    def mark(self, symbol: str, known_at_ms: int) -> tuple[float | None, int | None]:
        """Last H1 close fully known by `known_at_ms`; never reads a partial bar."""
        series = self._series(symbol)
        i = int(np.searchsorted(series.closes, int(known_at_ms), side="right") - 1)
        if i < 0:
            return None, None
        return float(series.raw[i, 4]), int(series.closes[i])


@dataclass
class Position:
    row_id: int
    symbol: str
    side: int
    entry_ms: int
    entry_px: float
    qty: float
    notional: float
    entry_fee: float
    exit_fee: float
    exit_ms: int | None
    exit_px: float | None
    exit_reason: str
    exit_phase: int


def _side_sign(value: Any) -> int:
    text = str(value).strip().lower()
    if text in {"long", "buy", "1", "+1"}:
        return 1
    if text in {"short", "sell", "-1"}:
        return -1
    raise ValueError(f"Unrecognized side: {value!r}")


def _int_ms(value: Any, name: str) -> int:
    number = float(value)
    if not np.isfinite(number) or number != int(number):
        raise ValueError(f"{name} must be a finite integer millisecond timestamp: {value!r}")
    return int(number)


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    number = float(value)
    if not np.isfinite(number) or (number <= 0 if positive else number < 0):
        comparator = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be finite and {comparator}: {value!r}")
    return number


def _validate_fills(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "group", "symbol", "entry_ms", "exit_ms", "entry_px", "exit_px", "qty", "side",
        "fee_u", "entry_fee_u", "exit_fee_u", "exit_reason",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"fills.csv missing required fields: {missing}")
    result = frame.copy().reset_index(drop=True)
    if "sample" not in result:
        result["sample"] = "signal"
    if result["sample"].isna().any() or not result["sample"].astype(str).isin({"signal", "random"}).all():
        raise ValueError("sample must contain only 'signal' or 'random'")
    if result["symbol"].isna().any() or result["group"].isna().any():
        raise ValueError("fills.csv has a blank symbol or group")
    if "trade_id" in result and result.trade_id.notna().any():
        identity = result["trade_id"].astype(str)
        if result.assign(_identity=identity).duplicated(["sample", "_identity"]).any():
            raise ValueError("trade_id must be unique within each sample")
    for ix, row in result.iterrows():
        entry_ms = _int_ms(row.entry_ms, "entry_ms")
        exit_ms = _int_ms(row.exit_ms, "exit_ms")
        if entry_ms % HOUR_MS or exit_ms % HOUR_MS:
            raise ValueError(f"{row.symbol}: normalized entry_ms / exit_ms must lie on H1 boundaries")
        if exit_ms <= entry_ms:
            raise ValueError(f"{row.symbol}: account fills must exit after their H1-open entry")
        _side_sign(row.side)
        _number(row.entry_px, "entry_px", positive=True)
        _number(row.exit_px, "exit_px", positive=True)
        qty = _number(row.qty, "qty", positive=True)
        total_fee = _number(row.fee_u, "fee_u")
        entry_fee = _number(row.entry_fee_u, "entry_fee_u")
        exit_fee = _number(row.exit_fee_u, "exit_fee_u")
        if not np.isclose(total_fee, entry_fee + exit_fee, rtol=1e-7, atol=1e-8):
            raise ValueError(f"row {ix}: fee_u does not equal entry_fee_u + exit_fee_u")
        if str(row.exit_reason) not in VALID_EXIT_REASONS:
            raise ValueError(f"row {ix}: unknown exit_reason {row.exit_reason!r}")
        # Keep the normalized values in the table instead of relying on pandas' inferred types.
        result.at[ix, "entry_ms"] = entry_ms
        result.at[ix, "exit_ms"] = exit_ms
        result.at[ix, "qty"] = qty
    return result


def _position_mark(pos: Position, book: QuoteBook, known_at_ms: int) -> tuple[float, int | None]:
    if known_at_ms <= pos.entry_ms:
        return pos.entry_px, pos.entry_ms
    price, stamp = book.mark(pos.symbol, known_at_ms)
    if price is None or stamp is None or stamp < pos.entry_ms:
        return pos.entry_px, pos.entry_ms
    return price, stamp


def _nav(active: dict[str, Position], cash: float, book: QuoteBook, known_at_ms: int) -> float:
    value = float(cash)
    for pos in active.values():
        mark, _ = _position_mark(pos, book, known_at_ms)
        value += pos.qty * pos.side * (mark - pos.entry_px)
    return float(value)


def _reserved_margin(active: dict[str, Position]) -> float:
    return float(sum(pos.notional for pos in active.values()))


def _all_stopped_nav(active: dict[str, Position], cash: float) -> float:
    """Estimate NAV if all open positions hit their fixed 5% stop now."""
    value = float(cash)
    for pos in active.values():
        stop_px = pos.entry_px * (1.0 - pos.side * FIXED_STOP_FRACTION)
        stop_pnl = pos.qty * pos.side * (stop_px - pos.entry_px)
        stop_exit_fee = pos.qty * stop_px * TAKER_FEE_RATE
        value += stop_pnl - stop_exit_fee
    return float(value)


def _position_hour_prices(pos: Position, bar: np.ndarray, hour_open_ms: int) -> np.ndarray:
    """Observed prices before this position exits, respecting native exit phase."""
    if pos.exit_ms == hour_open_ms + HOUR_MS and pos.exit_px is not None and pos.exit_phase > 0:
        phase = min(int(pos.exit_phase), 4)
        # Keep the same intrabar waypoint order as baseline.py's within_trade:
        # long paths use O-L-H-C; short paths use O-H-L-C. Include only the
        # prefix reported before the native fill, then the fill price itself.
        cols = [1, 3, 2, 4] if pos.side == 1 else [1, 2, 3, 4]
        prices = [pos.entry_px if pos.entry_ms == hour_open_ms else float(bar[1])]
        prices.extend(float(bar[col]) for col in cols[:phase])
        prices.append(float(pos.exit_px))
        return np.asarray(prices, dtype=np.float64)
    start_price = pos.entry_px if pos.entry_ms == hour_open_ms else float(bar[1])
    return np.asarray([start_price, float(bar[2]), float(bar[3]), float(bar[4])], dtype=np.float64)


def _hilo_equity(active: dict[str, Position], cash: float, book: QuoteBook,
                 hour_open_ms: int) -> tuple[float, float, int]:
    """Combine adverse / favorable H1 extremes as a conservative envelope.

    The per-symbol OHLC extreme is observed. Combining different symbols' H/L
    values at one instant is not: the returned pair is a sensitivity bound, not
    a path reconstruction.
    """
    adverse = favorable = float(cash)
    missing = 0
    for pos in active.values():
        bar = book.bar(pos.symbol, hour_open_ms)
        if bar is None:
            price, _ = _position_mark(pos, book, hour_open_ms)
            pnl = pos.qty * pos.side * (price - pos.entry_px)
            adverse += pnl
            favorable += pnl
            missing += 1
        else:
            prices = _position_hour_prices(pos, bar, hour_open_ms)
            pnl = pos.qty * pos.side * (prices - pos.entry_px)
            adverse += float(np.min(pnl))
            favorable += float(np.max(pnl))
    return float(adverse), float(favorable), missing


def _update_close_drawdown(equity: float, peak: float, worst: float) -> tuple[float, float]:
    peak = max(float(peak), float(equity))
    drawdown = 0.0 if peak <= 0 else max(0.0, 1.0 - float(equity) / peak)
    return peak, max(float(worst), drawdown)


def _simulate_sample(frame: pd.DataFrame, book: QuoteBook, cutoff_ms: int,
                     variant: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if variant not in VARIANTS:
        raise ValueError(f"Unknown account variant: {variant}")
    floor_enabled = variant == "floor30"
    rows: list[dict[str, Any]] = []
    for row_id, source in frame.iterrows():
        record = source.to_dict()
        record["_row_id"] = int(row_id)
        record["account_variant"] = variant
        record["account_status"] = "pending"
        record["account_reject_reason"] = ""
        record["account_notional"] = np.nan
        record["account_qty"] = np.nan
        record["account_entry_fee_u"] = np.nan
        record["account_exit_fee_u"] = np.nan
        record["account_realized_gross_u"] = np.nan
        record["account_realized_net_u"] = np.nan
        record["account_final_mark_px"] = np.nan
        rows.append(record)

    def reject(row: dict[str, Any], reason: str) -> None:
        row["account_status"] = "rejected"
        row["account_reject_reason"] = reason

    entries: dict[int, list[dict[str, Any]]] = {}
    exits: dict[int, list[dict[str, Any]]] = {}
    eligible_count = 0
    missing_entry_h1 = 0
    for row in rows:
        if str(row["group"]) not in MAIN_GROUPS:
            reject(row, "group_not_in_main_account")
            continue
        entry_ms = int(row["entry_ms"])
        exit_ms = int(row["exit_ms"])
        if entry_ms >= cutoff_ms:
            reject(row, "entry_at_or_after_account_cutoff")
            continue
        # The sample may contain a trade whose native exit lies beyond the common
        # data cutoff. It remains an account position and is marked at the last
        # fully observed H1 close; the future source exit is not consumed.
        row["_account_exit_ms"] = exit_ms if exit_ms <= cutoff_ms else None
        entry_bar = book.bar(str(row["symbol"]), entry_ms)
        if entry_bar is None:
            reject(row, "no_h1_bar_at_entry")
            missing_entry_h1 += 1
            continue
        row["_side_sign"] = _side_sign(row["side"])
        row["_entry_ms"] = entry_ms
        row["_exit_ms"] = row["_account_exit_ms"]
        row["_exit_px"] = float(row["exit_px"])
        row["_entry_px"] = float(row["entry_px"])
        row["_source_qty"] = float(row["qty"])
        row["_entry_fee_source"] = float(row["entry_fee_u"])
        row["_exit_fee_source"] = float(row["exit_fee_u"])
        row["_source_notional"] = float(row["qty"]) * float(row["entry_px"])
        if row["_source_notional"] <= 0:
            reject(row, "invalid_source_notional")
            continue
        eligible_count += 1
        entries.setdefault(entry_ms, []).append(row)
        if row["_exit_ms"] is not None:
            exits.setdefault(int(row["_exit_ms"]), []).append(row)

    candidates = [row for group in entries.values() for row in group]
    candidates.sort(key=lambda r: (int(r["_entry_ms"]), str(r["symbol"]), int(r["_row_id"])))
    if not candidates:
        skipped = Counter(row["account_reject_reason"] for row in rows if row["account_status"] == "rejected")
        metrics = _empty_metrics(variant, str(frame.iloc[0]["sample"]), len(frame), eligible_count, skipped, missing_entry_h1)
        return rows, [], metrics

    start_ms = min(int(row["_entry_ms"]) for row in candidates)
    if start_ms % HOUR_MS or cutoff_ms % HOUR_MS or cutoff_ms <= start_ms:
        raise ValueError("account interval must be positive and H1-aligned")

    cash = INITIAL_EQUITY
    active: dict[str, Position] = {}
    # Freeze one trade per symbol at a time; native fills themselves remain unchanged.
    row_position: dict[int, Position] = {}
    skip_counts: Counter[str] = Counter()
    accepted_entries = 0
    rejected_entries = 0
    reduced_entries = 0
    min_notional = float("inf")
    min_qty = float("inf")
    total_entry_fees = 0.0
    total_exit_fees = 0.0
    realized_gross = 0.0
    realized_net = 0.0
    max_open_positions = 0
    hwm = INITIAL_EQUITY
    close_peak = INITIAL_EQUITY
    hilo_peak = INITIAL_EQUITY
    max_dd_close = 0.0
    max_dd_hilo = 0.0
    hourly: list[dict[str, Any]] = []

    def update_close_metrics(equity: float) -> None:
        nonlocal hwm, close_peak, max_dd_close
        close_peak, max_dd_close = _update_close_drawdown(equity, close_peak, max_dd_close)
        hwm = max(hwm, float(equity))

    def process_exits(at_ms: int) -> None:
        nonlocal cash, total_exit_fees, realized_gross, realized_net
        for row in sorted(exits.get(at_ms, []), key=lambda r: (str(r["symbol"]), int(r["_row_id"]))):
            pos = row_position.get(int(row["_row_id"]))
            if pos is None:
                continue  # rejected fill or already settled
            exit_px = float(row["_exit_px"])
            gross = pos.qty * pos.side * (exit_px - pos.entry_px)
            exit_fee = float(pos.exit_fee)
            cash += gross - exit_fee
            total_exit_fees += exit_fee
            realized_gross += gross
            realized_net += gross - pos.entry_fee - exit_fee
            active.pop(pos.symbol, None)
            row_position.pop(pos.row_id, None)
            book.unpin(pos.symbol)
            row["account_exit_fee_u"] = exit_fee
            row["account_realized_gross_u"] = gross
            row["account_realized_net_u"] = gross - pos.entry_fee - exit_fee
            row["account_final_mark_px"] = exit_px
            row["account_status"] = (
                "period_end_valuation" if str(row["exit_reason"]) == "cutoff_mark" else "closed"
            )
            row["account_exit_available_ms"] = int(at_ms)

    def process_entries(at_ms: int) -> None:
        nonlocal cash, accepted_entries, rejected_entries, reduced_entries
        nonlocal min_notional, min_qty, total_entry_fees, max_open_positions
        for row in sorted(entries.get(at_ms, []), key=lambda r: (str(r["symbol"]), int(r["_row_id"]))):
            symbol = str(row["symbol"])
            if symbol in active:
                reject(row, "same_symbol_position_open")
                skip_counts["same_symbol_position_open"] += 1
                rejected_entries += 1
                continue
            if len(active) >= MAX_POSITIONS:
                reject(row, "max_positions")
                skip_counts["max_positions"] += 1
                rejected_entries += 1
                continue
            nav_now = _nav(active, cash, book, at_ms)
            hwm_now = max(hwm, nav_now)
            if not np.isfinite(nav_now) or nav_now <= 0:
                reject(row, "nonpositive_equity")
                skip_counts["nonpositive_equity"] += 1
                rejected_entries += 1
                continue
            entry_px = float(row["_entry_px"])
            source_qty = float(row["_source_qty"])
            source_notional = float(row["_source_notional"])
            source_entry_fee = float(row["_entry_fee_source"])
            entry_fee_rate = source_entry_fee / source_notional
            if not np.isfinite(entry_fee_rate) or entry_fee_rate < 0 or entry_fee_rate >= 0.1:
                raise ValueError(f"Invalid entry fee rate for {symbol} at {at_ms}: {entry_fee_rate}")
            desired_notional = nav_now * MAX_NOTIONAL_FRACTION
            reserved = _reserved_margin(active)
            free_collateral = max(0.0, nav_now - reserved)
            collateral_limited = free_collateral / (1.0 + entry_fee_rate)
            notional = min(desired_notional, collateral_limited)
            if notional <= 0:
                reject(row, "insufficient_collateral")
                skip_counts["insufficient_collateral"] += 1
                rejected_entries += 1
                continue
            if floor_enabled:
                # Keep exactly the same collateral-limited size as baseline;
                # this version adds only a binary entry gate, never resizes a
                # position to force it under the high-water floor.
                floor = hwm_now * 0.70
                stress_before = _all_stopped_nav(active, cash)
                qty = notional / entry_px
                entry_fee = source_entry_fee * (qty / source_qty)
                stop_px = entry_px * (1.0 - int(row["_side_sign"]) * FIXED_STOP_FRACTION)
                stop_loss = notional * FIXED_STOP_FRACTION
                stop_fee = qty * stop_px * TAKER_FEE_RATE
                stress_after = stress_before - entry_fee - stop_loss - stop_fee
                if nav_now <= floor + 1e-9 or stress_after < floor - 1e-9:
                    reason = "at_or_below_70pct_floor" if nav_now <= floor + 1e-9 else "fixed_stop_stress_breaches_floor"
                    reject(row, reason)
                    skip_counts[reason] += 1
                    rejected_entries += 1
                    continue

            qty = notional / entry_px
            scale = qty / source_qty
            entry_fee = source_entry_fee * scale
            exit_fee = float(row["_exit_fee_source"]) * scale
            if notional < desired_notional * (1.0 - 1e-10):
                reduced_entries += 1
                row["account_size_reduction_reason"] = "available_collateral"
            else:
                row["account_size_reduction_reason"] = ""
            cash -= entry_fee
            total_entry_fees += entry_fee
            pos = Position(
                row_id=int(row["_row_id"]), symbol=symbol, side=int(row["_side_sign"]),
                entry_ms=int(row["_entry_ms"]), entry_px=entry_px, qty=qty,
                notional=notional, entry_fee=entry_fee, exit_fee=exit_fee,
                exit_ms=row["_exit_ms"], exit_px=row["_exit_px"] if row["_exit_ms"] is not None else None,
                exit_reason=str(row["exit_reason"]),
                exit_phase=int(float(row.get("exit_phase", 0) or 0)),
            )
            active[symbol] = pos
            row_position[pos.row_id] = pos
            book.pin(symbol)
            accepted_entries += 1
            min_notional = min(min_notional, notional)
            min_qty = min(min_qty, qty)
            max_open_positions = max(max_open_positions, len(active))
            row["account_notional"] = notional
            row["account_qty"] = qty
            row["account_entry_fee_u"] = entry_fee
            row["account_entry_nav_u"] = nav_now
            row["account_margin_reserved_u"] = notional
            row["account_stress_floor_u"] = hwm_now * 0.70 if floor_enabled else np.nan
            row["account_status"] = "accepted_open"

    # Initial boundary: no prior H1 interval is part of this account path.
    process_exits(start_ms)
    update_close_metrics(_nav(active, cash, book, start_ms))
    process_entries(start_ms)
    update_close_metrics(_nav(active, cash, book, start_ms))

    for hour_open in range(start_ms, cutoff_ms, HOUR_MS):
        adverse, favorable, missing_hilo = _hilo_equity(active, cash, book, hour_open)
        hour_end = hour_open + HOUR_MS
        if hour_end > cutoff_ms:
            break
        process_exits(hour_end)
        close_equity = _nav(active, cash, book, hour_end)
        update_close_metrics(close_equity)
        floor_at_close = hwm * 0.70 if floor_enabled else np.nan
        if hour_end < cutoff_ms:
            process_entries(hour_end)
        boundary_equity = _nav(active, cash, book, hour_end)
        update_close_metrics(boundary_equity)
        peak_for_hour = max(hilo_peak, favorable, close_equity, boundary_equity)
        adverse_for_hour = min(adverse, close_equity, boundary_equity)
        dd_hilo = 0.0 if peak_for_hour <= 0 else max(0.0, 1.0 - adverse_for_hour / peak_for_hour)
        max_dd_hilo = max(max_dd_hilo, dd_hilo)
        hilo_peak = peak_for_hour
        stale = 0
        for pos in active.values():
            _, mark_ms = _position_mark(pos, book, hour_end)
            if mark_ms is None or hour_end - mark_ms > HOUR_MS:
                stale += 1
        hourly.append({
            "account_variant": variant,
            "sample": str(frame.iloc[0]["sample"]) if len(frame) else "",
            "hour_open_ms": hour_open,
            "hour_end_ms": hour_end,
            "cash_u": cash,
            "equity_close_u": close_equity,
            "equity_after_boundary_events_u": boundary_equity,
            "h1_adverse_equity_envelope_u": adverse_for_hour,
            "h1_favorable_equity_envelope_u": peak_for_hour,
            "open_positions": len(active),
            "reserved_margin_u": _reserved_margin(active),
            "free_collateral_u": max(0.0, boundary_equity - _reserved_margin(active)),
            "high_water_mark_u": hwm,
            "floor_70pct_u": floor_at_close,
            "drawdown_hourly_close_pct": max_dd_close * 100.0,
            "drawdown_h1_ohlc_envelope_pct": max_dd_hilo * 100.0,
            "h1_missing_extreme_positions": missing_hilo,
            "stale_mark_positions": stale,
        })

    # Any admitted source trade without a completed account exit is valued at the
    # last complete H1 close. No hypothetical exit fee is charged in this case.
    for pos in active.values():
        mark, _ = _position_mark(pos, book, cutoff_ms)
        row = rows[pos.row_id]
        row["account_final_mark_px"] = mark
        row["account_unrealized_gross_u"] = pos.qty * pos.side * (mark - pos.entry_px)
        row["account_status"] = "open_marked_at_cutoff"
        book.unpin(pos.symbol)
    final_equity = _nav(active, cash, book, cutoff_ms)
    if not hourly:
        close_peak, max_dd_close = _update_close_drawdown(final_equity, close_peak, max_dd_close)
    skipped = Counter(row["account_reject_reason"] for row in rows if row["account_status"] == "rejected")
    closed = [row for row in rows if row["account_status"] == "closed"]
    terminal = [row for row in rows if row["account_status"] == "period_end_valuation"]
    open_at_cutoff = [row for row in rows if row["account_status"] == "open_marked_at_cutoff"]
    funded = [row for row in rows if row["account_status"] in {"closed", "period_end_valuation", "open_marked_at_cutoff"}]
    metrics = {
        "account_variant": variant,
        "sample": str(frame.iloc[0]["sample"]) if len(frame) else "",
        "initial_equity_u": INITIAL_EQUITY,
        "final_equity_u": final_equity,
        "final_cash_u": cash,
        "total_return_pct": (final_equity / INITIAL_EQUITY - 1.0) * 100.0,
        "candidate_rows": int(len(frame)),
        "main_group_candidates": int(eligible_count),
        "funded_trade_count": int(len(funded)),
        "accepted_entries": int(accepted_entries),
        "normal_closed_trades": int(len(closed)),
        "period_end_valuation_trades": int(len(terminal)),
        "open_marked_at_cutoff": int(len(open_at_cutoff)),
        "rejected_entries": int(rejected_entries),
        "reduced_entries": int(reduced_entries),
        "minimum_accepted_notional_u": min_notional if np.isfinite(min_notional) else None,
        "minimum_accepted_qty": min_qty if np.isfinite(min_qty) else None,
        "max_open_positions": int(max_open_positions),
        "entry_fees_u": float(total_entry_fees),
        "exit_fees_u": float(total_exit_fees),
        "realized_gross_pnl_u": float(realized_gross),
        "realized_net_pnl_u": float(realized_net),
        "max_drawdown_hourly_close_pct": float(max_dd_close * 100.0),
        "max_drawdown_h1_ohlc_envelope_pct": float(max_dd_hilo * 100.0),
        "hwm_floor_fraction": 0.70 if floor_enabled else None,
        "hwm_floor_breached_at_h1_close": bool(floor_enabled and max_dd_close > 0.30 + 1e-12),
        "rejection_reasons": dict(sorted(skipped.items())),
        "missing_h1_entry_bars": int(missing_entry_h1),
        "accounting_notes": {
            "position_size": "risk budget = 1% of entry NAV; 5% fixed stop implies 20% NAV target notional; 1x fully collateralized and capped at five positions",
            "collateral": "free collateral = marked NAV minus reserved entry notional; no borrowing; baseline may shrink to available collateral",
            "floor30": "uses baseline's identical collateral-limited position sizing; only adds a binary entry gate, rejecting a new account-sized position when all active positions and that position at their fixed 5% stops, including actual entry fee and estimated 5bp taker exit fee, would leave NAV below 70% of the observed H1-close high-water mark",
            "drawdown": "hourly-close drawdown is the observed account NAV path; H1 OHLC envelope stops an exit candle at its native exit_phase and can combine different symbols' extrema that were not simultaneous, so it is only a sensitivity bound",
            "floor_risk": "the account-layer 70% entry floor is not a hard loss limit; opening gaps and fills beyond the fixed stop can breach it",
            "cutoff_mark": "native cutoff_mark rows settle at the account cutoff price and are counted as period-end valuation, not ordinary closed trades",
            "fees": "entry_fee_u and exit_fee_u from baseline fills are scaled linearly by account_qty/source_qty; cutoff_mark exit fees are included because the native framework reports a terminal valuation at its end price",
        },
    }
    return rows, hourly, metrics


def _empty_metrics(variant: str, sample: str, candidate_rows: int, main_candidates: int,
                   skipped: Counter[str], missing_entry_h1: int) -> dict[str, Any]:
    return {
        "account_variant": variant, "sample": sample, "initial_equity_u": INITIAL_EQUITY,
        "final_equity_u": INITIAL_EQUITY, "final_cash_u": INITIAL_EQUITY,
        "total_return_pct": 0.0, "candidate_rows": int(candidate_rows),
        "main_group_candidates": int(main_candidates), "funded_trade_count": 0,
        "accepted_entries": 0, "normal_closed_trades": 0, "period_end_valuation_trades": 0,
        "open_marked_at_cutoff": 0, "rejected_entries": 0, "reduced_entries": 0,
        "minimum_accepted_notional_u": None, "minimum_accepted_qty": None,
        "max_open_positions": 0, "entry_fees_u": 0.0, "exit_fees_u": 0.0,
        "realized_gross_pnl_u": 0.0, "realized_net_pnl_u": 0.0,
        "max_drawdown_hourly_close_pct": 0.0, "max_drawdown_h1_ohlc_envelope_pct": 0.0,
        "hwm_floor_fraction": 0.70 if variant == "floor30" else None,
        "hwm_floor_breached_at_h1_close": False,
        "rejection_reasons": dict(sorted(skipped.items())),
        "missing_h1_entry_bars": int(missing_entry_h1),
    }


def run(fills_path: Path | str | None = None, *, cutoff_ms: int | None = None,
        raw_loader: Callable[[str], np.ndarray] | None = None,
        output_dir: Path | str | None = None) -> dict[str, pd.DataFrame]:
    """Run independent signal/random accounts under both sizing policies.

    `raw_loader` is injectable for a small deterministic check. Production calls
    use the same `replay.read_raw` as baseline.py and the H1 dataset cutoff.
    """
    replay = _replay_module()
    if raw_loader is None:
        raw_loader = replay.read_raw
    if fills_path is None:
        fills_path = Path(replay.OUT) / "h4_strategy" / "fills.csv"
    fills_path = Path(fills_path)
    if not fills_path.is_file():
        raise FileNotFoundError(fills_path)
    frame = _validate_fills(pd.read_csv(fills_path, float_precision="round_trip"))
    if cutoff_ms is None:
        cutoff_ms = int(replay.CUTOFF)
    cutoff_ms = int(cutoff_ms) // HOUR_MS * HOUR_MS
    book = QuoteBook(raw_loader)
    summaries: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    hours: list[dict[str, Any]] = []
    for sample in ("signal", "random"):
        sample_frame = frame[frame["sample"].astype(str).eq(sample)].reset_index(drop=True)
        if sample_frame.empty:
            continue
        for variant in VARIANTS:
            order_rows, hour_rows, metrics = _simulate_sample(sample_frame, book, cutoff_ms, variant)
            summaries.append(metrics)
            orders.extend(order_rows)
            hours.extend(hour_rows)
    summary = pd.DataFrame(summaries)
    order_frame = pd.DataFrame(orders)
    hourly_frame = pd.DataFrame(hours)
    result = {"summary": summary, "orders": order_frame, "hourly": hourly_frame}
    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        for name, table in (("account_summary.csv", summary),
                            ("account_orders.csv", order_frame),
                            ("account_hourly.csv", hourly_frame)):
            tmp = out / (name + ".tmp")
            table.to_csv(tmp, index=False)
            tmp.replace(out / name)
    return result


def self_check() -> None:
    """Small synthetic check of collateral, exit-first ordering, and floor gate."""
    symbols = ["A", "B", "C", "D", "E", "F", "G"]
    quotes = {}
    for symbol in symbols:
        clocks = np.arange(0, 5 * HOUR_MS, HOUR_MS, dtype=np.int64)
        bars = np.column_stack((clocks, np.full((len(clocks), 4), 100.0)))
        quotes[symbol] = bars

    def quote_loader(symbol: str) -> np.ndarray:
        return quotes[symbol]

    def fill(sample: str, symbol: str, entry: int, exit_: int, reason: str = "cutoff_mark") -> dict[str, Any]:
        return {
            "sample": sample, "group": "上轨守住", "symbol": symbol, "side": "long",
            "entry_ms": entry, "exit_ms": exit_, "entry_px": 100.0, "exit_px": 100.0,
            "qty": 1.0, "entry_fee_u": 0.0, "exit_fee_u": 0.0, "fee_u": 0.0,
            "exit_reason": reason,
        }

    records = [
        fill("signal", "A", 0, 2 * HOUR_MS, "target"),
        fill("signal", "B", 0, 4 * HOUR_MS),
        fill("signal", "C", 0, 4 * HOUR_MS),
        fill("signal", "D", 0, 4 * HOUR_MS),
        fill("signal", "A", HOUR_MS, 4 * HOUR_MS, "target"),
        fill("signal", "E", HOUR_MS, 4 * HOUR_MS),
        fill("signal", "G", HOUR_MS, 4 * HOUR_MS),
        fill("signal", "F", 2 * HOUR_MS, 3 * HOUR_MS, "target"),
        fill("signal", "A", 3 * HOUR_MS, 4 * HOUR_MS),
    ]
    checked = _validate_fills(pd.DataFrame(records))
    book = QuoteBook(quote_loader)
    rows, _, base = _simulate_sample(checked, book, 4 * HOUR_MS, "baseline")
    by_symbol = {(str(row["symbol"]), int(row["entry_ms"])): row for row in rows}
    assert np.isclose(by_symbol[("A", 0)]["account_notional"], 200.0)
    assert by_symbol[("E", HOUR_MS)]["account_status"] == "period_end_valuation"
    assert by_symbol[("A", HOUR_MS)]["account_reject_reason"] == "same_symbol_position_open"
    assert by_symbol[("G", HOUR_MS)]["account_reject_reason"] == "max_positions"
    assert by_symbol[("F", 2 * HOUR_MS)]["account_status"] == "closed"
    assert by_symbol[("A", 3 * HOUR_MS)]["account_status"] == "period_end_valuation"
    assert base["max_open_positions"] == MAX_POSITIONS
    assert base["funded_trade_count"] == 7
    assert base["rejected_entries"] == 2
    assert base["rejection_reasons"] == {"max_positions": 1, "same_symbol_position_open": 1}

    floor_records = [
        fill("signal", "A", 0, HOUR_MS, "stop"),
        fill("signal", "B", HOUR_MS, 2 * HOUR_MS, "stop"),
        fill("signal", "C", 2 * HOUR_MS, 3 * HOUR_MS, "stop"),
        fill("signal", "D", 3 * HOUR_MS, 4 * HOUR_MS, "stop"),
    ]
    for row in floor_records[:3]:
        row["exit_px"] = 25.0  # extreme opening gap: the 30% account floor is not a hard stop
        row["exit_phase"] = 0
    floor_rows = _validate_fills(pd.DataFrame(floor_records))
    floor_account_rows, _, floor_metrics = _simulate_sample(floor_rows, QuoteBook(quote_loader), 4 * HOUR_MS, "floor30")
    floor_by_key = {(str(row["symbol"]), int(row["entry_ms"])): row for row in floor_account_rows}
    assert floor_by_key[("D", 3 * HOUR_MS)]["account_reject_reason"] == "at_or_below_70pct_floor"
    assert floor_metrics["hwm_floor_breached_at_h1_close"] is True
    assert floor_metrics["max_drawdown_hourly_close_pct"] > 30.0
    exit_bar = np.array([HOUR_MS, 100.0, 120.0, 80.0, 110.0])
    partial = Position(99, "A", 1, 0, 100.0, 2.0, 200.0, 0.0, 0.0,
                       2 * HOUR_MS, 95.0, "stop", 1)
    gap = Position(100, "A", 1, 0, 100.0, 2.0, 200.0, 0.0, 0.0,
                   2 * HOUR_MS, 95.0, "stop", 0)
    assert np.max(_position_hour_prices(partial, exit_bar, HOUR_MS)) == 100.0
    assert np.max(_position_hour_prices(gap, exit_bar, HOUR_MS)) == 120.0
    print("account self-check passed: 1% sizing, five-position collateral cap, exit-first release, single skip count, and active 70% floor gate")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fills", type=Path, default=None, help="baseline fills.csv; defaults to replay.OUT/h4_strategy/fills.csv")
    parser.add_argument("--cutoff-ms", type=int, default=None, help="override the frozen replay cutoff")
    parser.add_argument("--output-dir", type=Path, default=None, help="optional directory for order and hourly ledgers")
    parser.add_argument("--self-check", action="store_true", help="run only deterministic synthetic accounting assertions")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    result = run(args.fills, cutoff_ms=args.cutoff_ms, output_dir=args.output_dir)
    print(result["summary"].to_json(orient="records", force_ascii=False, indent=2))


if __name__ == "__main__":
    main()
