"""Cash-account replay of the frozen VectorBT fixed-stop trade ledgers.

This adapter does not create or re-evaluate fills. It consumes the already
executed per-signal ledger, enforces shared cash/slot limits, and marks open
positions from the repository's merged Binance H1 quote reader.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import census12  # noqa: E402
import scan as scan_module  # noqa: E402

DATA_DIR = ROOT / "data/research/alt-isolated-12h-wicks-20261111/expanded12/strategy_v1"
SIGNALS_PATH = DATA_DIR / "signals.csv.gz"
DEFAULT_LEDGER_5 = DATA_DIR / "fixed_trades_sl5.csv.gz"
DEFAULT_LEDGER_3 = DATA_DIR / "fixed_trades_sl3.csv.gz"
HOUR_MS = census12.HOUR
INITIAL_CASH = 1000.0
MAX_POSITIONS = 5
MAX_NOTIONAL_FRACTION = 0.10
STRATEGIES = {
    "baseline": lambda f: f["baseline"].eq(1),
    "first_wick": lambda f: f["first_wick"].eq(1),
    "retest": lambda f: f["retest"].eq(1),
    "owner_style": lambda f: (f["rank_12h"].eq(1) & f["body_inside"].eq(1)) | f["retest"].eq(1),
}


@dataclass
class Position:
    trade_id: str
    symbol: str
    side: int
    entry_ms: int
    entry_price: float
    notional: float
    quantity: float
    entry_fee: float
    entry_fee_rate: float
    exit_fee_rate: float
    source: dict[str, Any]
    max_mark_age_hours: int = 0
    stale_mark_hours: int = 0


def _finite(value: Any) -> bool:
    try:
        return value is not None and np.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _int_ms(value: Any, name: str) -> int:
    if not _finite(value):
        raise ValueError(f"{name} must be a finite millisecond timestamp: {value!r}")
    result = int(value)
    if result != float(value):
        raise ValueError(f"{name} must be an integer millisecond timestamp: {value!r}")
    return result


def _flag(value: Any) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if _finite(value):
        return bool(int(float(value)))
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return False


def _side(value: Any) -> int:
    text = str(value).strip().lower()
    if text in {"long", "buy", "1", "+1"}:
        return 1
    if text in {"short", "sell", "-1"}:
        return -1
    raise ValueError(f"Unrecognized side: {value!r}")


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    frame.to_csv(tmp, index=False, compression="gzip" if path.suffix == ".gz" else None)
    tmp.replace(path)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n")
    tmp.replace(path)


def _load_quote_cache(symbols: set[str], cutoff_ms: int) -> dict[str, np.ndarray]:
    """Read complete registered H1 histories and retain only fully closed bars."""
    raw_by_symbol: dict[str, np.ndarray] = {}
    for symbol in sorted(symbols):
        raw = census12.read_raw(symbol)
        if not len(raw):
            raw_by_symbol[symbol] = np.empty((0, 5), dtype=np.float64)
            continue
        raw = np.asarray(raw, dtype=np.float64)
        clocks = raw[:, 0].astype(np.int64)
        complete = clocks + HOUR_MS <= cutoff_ms
        raw = raw[complete]
        if not len(raw):
            raw_by_symbol[symbol] = np.empty((0, 5), dtype=np.float64)
            continue
        clocks = raw[:, 0].astype(np.int64)
        if (np.diff(clocks) <= 0).any() or (clocks % HOUR_MS).any():
            raise ValueError(f"{symbol}: merged H1 source is not strictly ordered and hour-aligned")
        px = raw[:, 1:5]
        if not np.isfinite(px).all() or (px <= 0).any():
            raise ValueError(f"{symbol}: merged H1 source has invalid OHLC")
        if (px[:, 1] < np.maximum(px[:, 0], px[:, 3])).any() or (px[:, 2] > np.minimum(px[:, 0], px[:, 3])).any():
            raise ValueError(f"{symbol}: merged H1 source has inconsistent OHLC")
        raw_by_symbol[symbol] = raw
    return raw_by_symbol


class QuoteBook:
    def __init__(self, raw_by_symbol: dict[str, np.ndarray]):
        self.raw = raw_by_symbol
        self.clocks = {s: a[:, 0].astype(np.int64) for s, a in raw_by_symbol.items()}
        self.close_times = {s: c + HOUR_MS for s, c in self.clocks.items()}

    def entry_bar(self, symbol: str, entry_ms: int) -> np.ndarray | None:
        a = self.raw.get(symbol)
        if a is None or not len(a):
            return None
        i = int(np.searchsorted(self.clocks[symbol], entry_ms))
        if i >= len(a) or int(self.clocks[symbol][i]) != entry_ms:
            return None
        return a[i]

    def mark(self, symbol: str, known_at_ms: int) -> tuple[float | None, int | None]:
        """Last H1 close timestamp no later than known_at_ms; never use a partial bar."""
        a = self.raw.get(symbol)
        if a is None or not len(a):
            return None, None
        i = int(np.searchsorted(self.close_times[symbol], known_at_ms, side="right") - 1)
        if i < 0:
            return None, None
        return float(a[i, 4]), int(self.close_times[symbol][i])

    def bar(self, symbol: str, hour_open_ms: int) -> np.ndarray | None:
        a = self.raw.get(symbol)
        if a is None or not len(a):
            return None
        i = int(np.searchsorted(self.clocks[symbol], hour_open_ms))
        if i >= len(a) or int(self.clocks[symbol][i]) != hour_open_ms:
            return None
        return a[i]


def _load_inputs(signals_path: Path, ledger_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    signals = pd.read_csv(signals_path)
    ledger = pd.read_csv(ledger_path)
    required_signals = {"symbol", "month", "side", "entry_ms", "entry_price", "baseline", "first_wick", "retest", "rank_12h", "body_inside"}
    if not required_signals.issubset(signals.columns):
        raise ValueError(f"Signal source missing fields: {sorted(required_signals - set(signals.columns))}")
    required_ledger = {
        "signal_row", "trade_id", "symbol", "month", "side", "entry_ms", "entry_price",
        "exit_ms", "exit_bar_open_ms", "exit_price", "exit_reason", "closed_flag",
        "data_status", "stop_pct", "entry_fee_rate", "exit_fee_rate", "gross_return", "fee_return", "net_return",
    }
    if not required_ledger.issubset(ledger.columns):
        raise ValueError(f"VectorBT ledger missing fields: {sorted(required_ledger - set(ledger.columns))}")
    if "signal_row" in signals.columns:
        if signals.signal_row.duplicated().any():
            raise ValueError("signals.csv.gz has duplicate signal_row values")
    else:
        signals = signals.copy()
        signals["signal_row"] = np.arange(len(signals), dtype=np.int64)
    if ledger.signal_row.isna().any() or ledger.signal_row.duplicated().any():
        raise ValueError("The VectorBT fixed-stop ledger must contain one unique signal_row per source signal")
    if len(ledger) != len(signals) or set(pd.to_numeric(ledger.signal_row, errors="raise").astype(int)) != set(pd.to_numeric(signals.signal_row, errors="raise").astype(int)):
        raise ValueError("The VectorBT ledger must retain exactly one row for every source signal, including unclosed/incomplete rows")
    if ledger.trade_id.isna().any() or ledger.trade_id.astype(str).duplicated().any():
        raise ValueError("trade_id must be non-null and unique in the VectorBT ledger")
    merged = ledger.merge(signals, on="signal_row", how="left", suffixes=("_ledger", "_signal"), validate="one_to_one", indicator=True)
    if not merged["_merge"].eq("both").all():
        raise ValueError("Fixed-stop ledger has signal_row values absent from source signals.csv.gz")
    for field in ("symbol", "month", "side", "entry_ms", "entry_price"):
        lcol, scol = f"{field}_ledger", f"{field}_signal"
        if lcol not in merged or scol not in merged:
            continue
        if field in {"entry_ms"}:
            equal = pd.to_numeric(merged[lcol], errors="coerce").eq(pd.to_numeric(merged[scol], errors="coerce"))
        elif field == "entry_price":
            equal = np.isclose(pd.to_numeric(merged[lcol], errors="coerce"),
                               pd.to_numeric(merged[scol], errors="coerce"), rtol=1e-10, atol=1e-12)
        else:
            equal = merged[lcol].astype(str).eq(merged[scol].astype(str))
        if not equal.all():
            bad = merged.loc[~equal, ["signal_row", lcol, scol]].head(5).to_dict("records")
            raise ValueError(f"Ledger/source signal identity mismatch for {field}: {bad}")
    for field in ("baseline", "first_wick", "retest"):
        lcol, scol = f"{field}_ledger", f"{field}_signal"
        if lcol in merged and scol in merged:
            left = pd.to_numeric(merged[lcol], errors="coerce").fillna(0).astype(int)
            right = pd.to_numeric(merged[scol], errors="coerce").fillna(0).astype(int)
            if not left.eq(right).all():
                raise ValueError(f"Ledger/source selector mismatch for {field}")
    if "owner_style" in ledger.columns:
        signal_owner = (signals.rank_12h.eq(1) & signals.body_inside.eq(1)) | signals.retest.eq(1)
        owner_by_row = pd.Series(signal_owner.to_numpy(), index=signals.signal_row)
        expected = merged.signal_row.map(owner_by_row).map(_flag)
        actual_col = "owner_style_ledger" if "owner_style_ledger" in merged else "owner_style"
        actual = merged[actual_col].map(_flag)
        if not expected.eq(actual).all():
            raise ValueError("Ledger owner_style selector disagrees with the source rank/body/retest rule")
    # Use the untouched signal-side fields as the account's admission selectors.
    for field in ("symbol", "month", "side", "entry_ms", "baseline", "first_wick", "retest", "rank_12h", "body_inside"):
        scol = f"{field}_signal" if f"{field}_signal" in merged else field
        if scol in merged:
            merged[field] = merged[scol]
    merged["entry_price"] = merged["entry_price_ledger"]
    if len(merged) != len(ledger):
        raise AssertionError("Ledger/source join changed the ledger row count")
    return signals, merged


def _make_candidates(frame: pd.DataFrame, quote_book: QuoteBook, effective_end_ms: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in frame.to_dict("records"):
        symbol = str(row["symbol"])
        entry_ms = _int_ms(row["entry_ms"], "entry_ms")
        if entry_ms >= effective_end_ms:
            row["_candidate_status"] = "entry_at_or_after_cutoff"
            candidates.append(row)
            continue
        entry_price = float(row["entry_price"])
        if not np.isfinite(entry_price) or entry_price <= 0:
            row["_candidate_status"] = "invalid_entry_price"
            candidates.append(row)
            continue
        if entry_ms % HOUR_MS:
            raise ValueError(f"{symbol} entry_ms is not at the H1 open: {entry_ms}")
        entry_bar = quote_book.entry_bar(symbol, entry_ms)
        if entry_bar is None:
            row["_candidate_status"] = "no_merged_h1_entry_bar"
            candidates.append(row)
            continue
        if not np.isclose(float(entry_bar[1]), entry_price, rtol=1e-10, atol=1e-12):
            raise ValueError(f"{symbol} entry price conflicts with merged H1 open at {entry_ms}: {entry_price} vs {entry_bar[1]}")
        entry_fee_rate = float(row["entry_fee_rate"])
        if not (np.isfinite(entry_fee_rate) and 0 <= entry_fee_rate < 0.1):
            raise ValueError(f"Invalid per-trade entry fee rate for trade_id={row['trade_id']}")
        row["_side_sign"] = _side(row["side"])
        row["_entry_ms"] = entry_ms
        row["_entry_price"] = entry_price
        row["_entry_fee_rate"] = entry_fee_rate
        row["_closed_source"] = _flag(row["closed_flag"])
        exit_fee_rate = float(row["exit_fee_rate"]) if _finite(row.get("exit_fee_rate")) else np.nan
        if row["_closed_source"] and not (np.isfinite(exit_fee_rate) and 0 <= exit_fee_rate < 0.1):
            raise ValueError(f"Invalid per-trade exit fee rate for closed trade_id={row['trade_id']}")
        # An open-at-cutoff position pays no hypothetical exit fee.
        row["_exit_fee_rate"] = exit_fee_rate if np.isfinite(exit_fee_rate) else 0.0
        row["_exit_available_ms"] = None
        if row["_closed_source"]:
            if not (_finite(row.get("exit_ms")) and _finite(row.get("exit_price"))):
                raise ValueError(f"Closed VectorBT trade lacks exit time/price: trade_id={row['trade_id']}")
            exit_ms = _int_ms(row["exit_ms"], "exit_ms")
            exit_price = float(row["exit_price"])
            if exit_price <= 0 or not np.isfinite(exit_price):
                raise ValueError(f"Invalid exit price for trade_id={row['trade_id']}")
            bar_open = row.get("exit_bar_open_ms")
            # OHLCSTX reports its trigger H1 bar, not an intrabar event time.
            # Release cash only once that candle is complete. A 45d close exit
            # is already stamped at the candle close and releases then.
            if _finite(bar_open) and int(float(bar_open)) == exit_ms:
                exit_available_ms = exit_ms + HOUR_MS
                row["_exit_clock_basis"] = "trigger_bar_close"
            else:
                exit_available_ms = exit_ms
                row["_exit_clock_basis"] = "recorded_close_time"
            row["_exit_available_ms"] = exit_available_ms
            row["_exit_price"] = exit_price
            row["_exit_ms"] = exit_ms
            row["_native_closed_before_cutoff"] = exit_available_ms <= effective_end_ms
        else:
            row["_native_closed_before_cutoff"] = False
            row["_exit_clock_basis"] = "open_or_censored"
        if row.get("_native_closed_before_cutoff") is False:
            row["_account_open_at_cutoff"] = True
        else:
            row["_account_open_at_cutoff"] = False
        if row["_closed_source"] and row["_native_closed_before_cutoff"]:
            side_sign = row["_side_sign"]
            gross = side_sign * (row["_exit_price"] / entry_price - 1.0)
            if _finite(row.get("gross_return")) and not np.isclose(gross, float(row["gross_return"]), rtol=1e-8, atol=1e-10):
                raise ValueError(f"Ledger gross_return mismatch for trade_id={row['trade_id']}: {gross} vs {row['gross_return']}")
            fee_return = entry_fee_rate + (exit_price / entry_price * exit_fee_rate)
            if _finite(row.get("fee_return")) and not np.isclose(fee_return, float(row["fee_return"]), rtol=1e-8, atol=1e-10):
                raise ValueError(f"Ledger fee_return mismatch for trade_id={row['trade_id']}: {fee_return} vs {row['fee_return']}")
            if _finite(row.get("net_return")) and not np.isclose(gross - fee_return, float(row["net_return"]), rtol=1e-8, atol=1e-10):
                raise ValueError(f"Ledger net_return mismatch for trade_id={row['trade_id']}")
        row["_candidate_status"] = "eligible"
        candidates.append(row)
    return candidates


def _position_value(position: Position, price: float) -> float:
    return position.notional + position.quantity * position.side * (price - position.entry_price)


def _position_mark(position: Position, quote_book: QuoteBook, known_at_ms: int) -> tuple[float, int | None]:
    # At entry and during the still-forming entry hour, the previous candle's
    # close is stale relative to the known entry fill; keep the mark at entry.
    if position.entry_ms <= known_at_ms < position.entry_ms + HOUR_MS:
        return position.entry_price, position.entry_ms
    price, stamp = quote_book.mark(position.symbol, known_at_ms)
    return (position.entry_price if price is None else price), stamp


def _snapshot(active: dict[str, Position], cash: float, quote_book: QuoteBook, known_at_ms: int,
              hour_open_ms: int | None = None, adverse: bool = False) -> tuple[float, int, int]:
    value = cash
    missing = 0
    stale_max = 0
    for pos in active.values():
        mark, stamp = _position_mark(pos, quote_book, known_at_ms)
        if stamp is None:
            missing += 1
        else:
            stale_max = max(stale_max, max(0, known_at_ms - stamp) // HOUR_MS)
        if adverse and hour_open_ms is not None and pos.entry_ms <= hour_open_ms:
            bar = quote_book.bar(pos.symbol, hour_open_ms)
            if bar is not None:
                mark = float(bar[3] if pos.side == 1 else bar[2])
        value += _position_value(pos, mark)
    return float(value), int(missing), int(stale_max)


def _favorable_snapshot(active: dict[str, Position], cash: float, quote_book: QuoteBook,
                        known_at_ms: int, hour_open_ms: int) -> float:
    value = cash
    for pos in active.values():
        mark, _ = _position_mark(pos, quote_book, known_at_ms)
        bar = quote_book.bar(pos.symbol, hour_open_ms) if pos.entry_ms <= hour_open_ms else None
        if bar is not None:
            mark = float(bar[2] if pos.side == 1 else bar[3])
        value += _position_value(pos, mark)
    return float(value)


def _account_one(candidates: list[dict[str, Any]], strategy_name: str, stop_name: str,
                 quote_book: QuoteBook, effective_end_ms: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    eligible = [x for x in candidates if x.get("_candidate_status") == "eligible"]
    eligible.sort(key=lambda x: (int(x["_entry_ms"]), str(x["symbol"]), str(x["side"]), str(x["trade_id"])))
    if eligible:
        first_hour = int(eligible[0]["_entry_ms"]) // HOUR_MS * HOUR_MS
    else:
        return [], [], {"stop": stop_name, "strategy": strategy_name, "signals": len(candidates), "eligible_entries": 0,
                        "accepted_entries": 0, "final_equity_usdt": INITIAL_CASH, "status": "no_eligible_entry"}

    entries: dict[int, list[dict[str, Any]]] = defaultdict(list)
    exits: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in eligible:
        entries[int(item["_entry_ms"])].append(item)
        if item.get("_native_closed_before_cutoff") and _finite(item.get("_exit_available_ms")):
            exits[int(item["_exit_available_ms"])].append(item)
    event_times = sorted(set(entries) | set(exits))
    event_cursor = 0
    cash = INITIAL_CASH
    active: dict[str, Position] = {}
    by_trade: dict[str, Position] = {}
    order_records: list[dict[str, Any]] = []
    hourly_records: list[dict[str, Any]] = []
    accepted = 0
    rejected_slots = 0
    rejected_symbol = 0
    rejected_cash = 0
    total_entry_fees = 0.0
    total_exit_fees = 0.0
    realized_gross = 0.0
    realized_net = 0.0
    max_open = 0
    close_peak = INITIAL_CASH
    hilo_peak = INITIAL_CASH
    max_dd_close = 0.0
    max_dd_hilo = 0.0
    stale_trade_ids: set[str] = set()
    stale_position_hours = 0
    max_mark_age_hours = 0
    last_hour_close = first_hour

    for hour_open in range(first_hour, effective_end_ms, HOUR_MS):
        hour_end = hour_open + HOUR_MS
        hour_adverse = float("inf")
        hour_favorable = float("-inf")
        hour_missing_mark = 0
        hour_stale_max = 0

        def sample_bounds(at_ms: int) -> None:
            nonlocal hour_adverse, hour_favorable, hour_missing_mark, hour_stale_max
            adverse_value, missing, stale = _snapshot(active, cash, quote_book, at_ms, hour_open, adverse=True)
            favorable_value = _favorable_snapshot(active, cash, quote_book, at_ms, hour_open)
            hour_adverse = min(hour_adverse, adverse_value)
            hour_favorable = max(hour_favorable, favorable_value)
            hour_missing_mark = max(hour_missing_mark, missing)
            hour_stale_max = max(hour_stale_max, stale)

        sample_bounds(hour_open)
        while event_cursor < len(event_times) and event_times[event_cursor] <= hour_end:
            t = event_times[event_cursor]
            event_cursor += 1
            if t < hour_open:
                # The preceding bucket may be empty for this account's roster,
                # but all events still need to be consumed exactly once.
                continue
            sample_bounds(t)
            # Closed H1 exits release cash at the candle close. For shared
            # timestamps, settle exits first; same-time entries then use only
            # cash known to be available at that boundary.
            for item in sorted(exits.get(t, []), key=lambda x: (str(x["symbol"]), str(x["trade_id"]))):
                trade_id = str(item["trade_id"])
                pos = by_trade.pop(trade_id, None)
                if pos is None:
                    continue  # The account did not admit this source signal.
                active.pop(pos.symbol, None)
                exit_price = float(item["_exit_price"])
                gross_pnl = pos.quantity * pos.side * (exit_price - pos.entry_price)
                exit_fee = (pos.quantity * exit_price) * pos.exit_fee_rate
                cash += pos.notional + gross_pnl - exit_fee
                total_exit_fees += exit_fee
                realized_gross += gross_pnl
                realized_net += gross_pnl - pos.entry_fee - exit_fee
                item["_account_exit_ms"] = t
                item["_account_exit_price"] = exit_price
                item["_account_exit_fee"] = exit_fee
                item["_account_realized_gross_pnl"] = gross_pnl
                item["_account_realized_net_pnl"] = gross_pnl - pos.entry_fee - exit_fee
                item["_account_status"] = "closed"
                sample_bounds(t)
            for item in sorted(entries.get(t, []), key=lambda x: (str(x["symbol"]), str(x["side"]), str(x["trade_id"]))):
                symbol = str(item["symbol"])
                reason = None
                if symbol in active:
                    rejected_symbol += 1
                    reason = "same_symbol_position_open"
                elif len(active) >= MAX_POSITIONS:
                    rejected_slots += 1
                    reason = "max_positions"
                else:
                    side = int(item["_side_sign"])
                    entry_price = float(item["_entry_price"])
                    nav, _, _ = _snapshot(active, cash, quote_book, t)
                    if not np.isfinite(nav) or nav <= 0 or cash <= 0:
                        rejected_cash += 1
                        reason = "nonpositive_nav_or_cash"
                    else:
                        desired_notional = nav * MAX_NOTIONAL_FRACTION
                        fee_rate = float(item["_entry_fee_rate"])
                        notional = min(desired_notional, cash / (1.0 + fee_rate))
                        if notional <= 0:
                            rejected_cash += 1
                            reason = "insufficient_cash"
                        else:
                            fee = notional * fee_rate
                            pos = Position(
                                trade_id=str(item["trade_id"]), symbol=symbol, side=side,
                                entry_ms=t, entry_price=entry_price, notional=notional,
                                quantity=notional / entry_price, entry_fee=fee,
                                entry_fee_rate=fee_rate, exit_fee_rate=float(item["_exit_fee_rate"]), source=item,
                            )
                            cash -= notional + fee
                            active[symbol] = pos
                            by_trade[pos.trade_id] = pos
                            total_entry_fees += fee
                            accepted += 1
                            max_open = max(max_open, len(active))
                            item["_account_entry_notional"] = notional
                            item["_account_quantity"] = pos.quantity
                            item["_account_entry_fee"] = fee
                            item["_account_entry_nav"] = nav
                            item["_account_status"] = "accepted_open"
                            sample_bounds(t)
                if reason:
                    item["_account_status"] = "rejected"
                    item["_account_reject_reason"] = reason
            sample_bounds(t)

        # Any trades with an entry/exit timestamp exactly at the final account
        # endpoint have already been consumed. Marks use only fully closed H1.
        close_equity, missing, stale_hours = _snapshot(active, cash, quote_book, hour_end)
        if not np.isfinite(hour_adverse):
            hour_adverse = close_equity
        if not np.isfinite(hour_favorable):
            hour_favorable = close_equity
        hour_adverse = min(hour_adverse, close_equity)
        hour_favorable = max(hour_favorable, close_equity)
        peak_before = close_peak
        if peak_before > 0:
            dd_close = max(0.0, 1.0 - close_equity / max(close_peak, close_equity))
            dd_hilo = max(0.0, 1.0 - hour_adverse / max(hilo_peak, hour_favorable, close_equity))
        else:
            dd_close = float("nan")
            dd_hilo = float("nan")
        if np.isfinite(dd_close):
            max_dd_close = max(max_dd_close, dd_close)
        if np.isfinite(dd_hilo):
            max_dd_hilo = max(max_dd_hilo, dd_hilo)
        close_peak = max(close_peak, close_equity)
        hilo_peak = max(hilo_peak, hour_favorable, close_equity)
        for pos in active.values():
            _, mark_ms = _position_mark(pos, quote_book, hour_end)
            age = 0 if mark_ms is None else max(0, hour_end - mark_ms) // HOUR_MS
            pos.max_mark_age_hours = max(pos.max_mark_age_hours, int(age))
            max_mark_age_hours = max(max_mark_age_hours, int(age))
            if age > 1:
                pos.stale_mark_hours += 1
                stale_position_hours += 1
                stale_trade_ids.add(pos.trade_id)
        timestamp = pd.to_datetime(hour_end, unit="ms", utc=True).isoformat()
        hourly_records.append({
            "stop": stop_name, "strategy": strategy_name, "hour_end_ms": hour_end,
            "hour_end_utc": timestamp, "cash_usdt": cash, "equity_close_usdt": close_equity,
            "adverse_hilo_equity_bound_usdt": hour_adverse,
            "favorable_hilo_equity_bound_usdt": hour_favorable,
            "open_positions": len(active), "missing_mark_positions": missing,
            "hour_adverse_missing_bar_positions": hour_missing_mark,
            "max_mark_age_hours": max(stale_hours, hour_stale_max),
            "stale_mark_position_hours_to_date": stale_position_hours,
            "positions_seen_with_stale_marks": len(stale_trade_ids),
            "drawdown_close_pct": dd_close * 100 if np.isfinite(dd_close) else np.nan,
            "drawdown_hilo_bound_pct": dd_hilo * 100 if np.isfinite(dd_hilo) else np.nan,
        })
        last_hour_close = hour_end

    for item in candidates:
        if item.get("_candidate_status") != "eligible":
            item["_account_status"] = "not_entered"
            item["_account_reject_reason"] = item.get("_candidate_status")
        elif item.get("_account_status") is None:
            item["_account_status"] = "not_entered"
            item["_account_reject_reason"] = "entry_after_account_cutoff"
        if item.get("_account_status") == "accepted_open" and str(item["trade_id"]) in by_trade:
            pos = by_trade[str(item["trade_id"])]
            final_mark, mark_ms = quote_book.mark(pos.symbol, last_hour_close)
            if final_mark is None:
                final_mark = pos.entry_price
            item["_account_final_mark_ms"] = mark_ms
            item["_account_final_mark_price"] = final_mark
            item["_account_unrealized_gross_pnl"] = pos.quantity * pos.side * (final_mark - pos.entry_price)
            item["_account_status"] = "open_marked_at_cutoff"
            item["_account_entry_notional"] = pos.notional
            item["_account_quantity"] = pos.quantity
            item["_account_entry_fee"] = pos.entry_fee

    final_equity = hourly_records[-1]["equity_close_usdt"] if hourly_records else INITIAL_CASH
    final_cash = hourly_records[-1]["cash_usdt"] if hourly_records else INITIAL_CASH
    final_open = len(active)
    if hourly_records:
        close_eq = np.asarray([r["equity_close_usdt"] for r in hourly_records], dtype=np.float64)
        adverse_eq = np.asarray([r["adverse_hilo_equity_bound_usdt"] for r in hourly_records], dtype=np.float64)
        favorable_eq = np.asarray([r["favorable_hilo_equity_bound_usdt"] for r in hourly_records], dtype=np.float64)
        all_peaks = np.maximum.accumulate(np.r_[INITIAL_CASH, close_eq])[1:]
        final_dd_close = float(np.max(np.maximum(0.0, 1.0 - close_eq / np.maximum(all_peaks, close_eq))))
        previous_hilo_peaks = np.maximum.accumulate(np.r_[INITIAL_CASH, favorable_eq[:-1]])
        final_dd_hilo = float(np.max(np.maximum(0.0, 1.0 - adverse_eq / np.maximum.reduce([previous_hilo_peaks, favorable_eq, close_eq]))))
    else:
        final_dd_close = 0.0
        final_dd_hilo = 0.0
    closes = sum(1 for x in candidates if x.get("_account_status") == "closed")
    wins = sum(1 for x in candidates if x.get("_account_status") == "closed" and float(x.get("_account_realized_net_pnl", 0.0)) > 0)
    end_marks = []
    for pos in active.values():
        mark, stamp = quote_book.mark(pos.symbol, last_hour_close)
        age = None if stamp is None else max(0, last_hour_close - stamp) // HOUR_MS
        if pos.entry_ms <= last_hour_close < pos.entry_ms + HOUR_MS:
            age = 0
        end_marks.append({"trade_id": pos.trade_id, "symbol": pos.symbol, "mark_age_hours": age,
                          "stale_at_cutoff": bool(age is not None and age > 1),
                          "mark_missing_at_cutoff": stamp is None,
                          "max_mark_age_during_hold_hours": pos.max_mark_age_hours,
                          "stale_mark_position_hours": pos.stale_mark_hours})
    metrics = {
        "stop": stop_name, "strategy": strategy_name, "initial_cash_usdt": INITIAL_CASH,
        "max_position_notional_fraction_of_nav": MAX_NOTIONAL_FRACTION,
        "max_simultaneous_positions": MAX_POSITIONS, "leverage": 1.0,
        "candidate_signals": len(candidates), "eligible_with_h1_entry": len(eligible),
        "accepted_entries": accepted, "closed_positions": closes, "open_at_cutoff": final_open,
        "rejected_same_symbol": rejected_symbol, "rejected_position_cap": rejected_slots,
        "rejected_cash_or_nav": rejected_cash, "max_positions_seen": max_open,
        "positions_with_any_stale_mark": len(stale_trade_ids),
        "stale_mark_position_hours": stale_position_hours,
        "max_mark_age_hours_during_account": max_mark_age_hours,
        "open_positions_stale_at_cutoff": sum(bool(x["stale_at_cutoff"]) for x in end_marks),
        "open_positions_mark_missing_at_cutoff": sum(bool(x["mark_missing_at_cutoff"]) for x in end_marks),
        "open_positions_stale_24h_at_cutoff": sum(bool(x["mark_age_hours"] is not None and x["mark_age_hours"] >= 24) for x in end_marks),
        "open_positions_stale_7d_at_cutoff": sum(bool(x["mark_age_hours"] is not None and x["mark_age_hours"] >= 168) for x in end_marks),
        "entry_fees_usdt": total_entry_fees, "exit_fees_usdt": total_exit_fees,
        "realized_gross_pnl_usdt": realized_gross, "realized_net_pnl_usdt": realized_net,
        "final_cash_usdt": final_cash, "final_equity_usdt": final_equity,
        "total_return_pct": (final_equity / INITIAL_CASH - 1.0) * 100.0,
        "max_drawdown_hourly_close_pct": final_dd_close * 100.0,
        "max_drawdown_hilo_stress_bound_pct": final_dd_hilo * 100.0,
        "within_30pct_hourly_close_dd": bool(final_dd_close <= 0.30),
        "within_30pct_hilo_stress_bound": bool(final_dd_hilo <= 0.30),
        "closed_win_rate_pct": (100.0 * wins / closes) if closes else None,
        "cutoff_hour_end_ms": last_hour_close, "open_position_mark_ages": end_marks,
        "drawdown_note": "Hourly close drawdown is the observed close-marked account path. H1 H/L stress uses each held symbol's adverse and favorable extremes and may combine extrema that did not occur at the same time; it is a conservative envelope, not a replayed fill path or guaranteed 30% cap.",
        "stale_mark_note": "A mark age above one hour is reported separately. It can reflect an observed H1 gap, a delisting, or source coverage ending; the adapter does not infer which and does not synthesize post-delisting prices.",
    }
    # Emit one row for every source event, including admission rejects, so the
    # cash/slot decision is auditable without altering the native trade ledger.
    for item in candidates:
        item["stop"] = stop_name
        item["strategy"] = strategy_name
        item.pop("_merge", None)
    return candidates, hourly_records, metrics


def run(ledger_path: Path, stop_name: str, signals_path: Path = SIGNALS_PATH,
        expected_stop_pct: float | None = None) -> dict[str, Any]:
    if not ledger_path.is_file():
        raise FileNotFoundError(ledger_path)
    signals, joined = _load_inputs(signals_path, ledger_path)
    if expected_stop_pct is None:
        expected_stop_pct = 5.0 if "sl5" in stop_name.lower() else 3.0 if "sl3" in stop_name.lower() else None
    if expected_stop_pct not in {3.0, 5.0}:
        raise ValueError("Pass --stop-pct 3 or 5, or use a stop-name containing sl3/sl5")
    stop_values = pd.to_numeric(joined["stop_pct"], errors="coerce")
    if stop_values.isna().any() or not np.isclose(stop_values.to_numpy(dtype=np.float64), expected_stop_pct / 100.0).all():
        raise ValueError(f"The supplied ledger does not consistently use a {expected_stop_pct:g}% fixed stop")
    signal_symbols = set(signals.symbol.astype(str))
    cutoff_ms = int(scan_module.CUTOFF)
    effective_end_ms = cutoff_ms // HOUR_MS * HOUR_MS
    quote_book = QuoteBook(_load_quote_cache(signal_symbols, cutoff_ms))
    account_orders: list[dict[str, Any]] = []
    account_hourly: list[dict[str, Any]] = []
    metrics_rows: list[dict[str, Any]] = []
    group_audits: dict[str, Any] = {}
    for strategy_name, selector in STRATEGIES.items():
        selected = joined.loc[selector(joined)].copy()
        candidates = _make_candidates(selected, quote_book, effective_end_ms)
        orders, hourly, metrics = _account_one(candidates, strategy_name, stop_name, quote_book, effective_end_ms)
        account_orders.extend(orders)
        account_hourly.extend(hourly)
        metrics_rows.append(metrics)
        group_audits[strategy_name] = {
            "source_signals": int(selector(signals).sum()),
            "ledger_rows": int(len(selected)),
            "source_closed_rows": int(selected.closed_flag.map(_flag).sum()),
            "data_status_counts": {str(k): int(v) for k, v in selected.data_status.fillna("<null>").value_counts().items()},
            "metrics": metrics,
        }
    orders_frame = pd.DataFrame(account_orders)
    hourly_frame = pd.DataFrame(account_hourly)
    metrics_frame = pd.DataFrame(metrics_rows)
    prefix = f"account_{stop_name}"
    _atomic_csv(orders_frame, DATA_DIR / f"{prefix}_positions.csv.gz")
    _atomic_csv(hourly_frame, DATA_DIR / f"{prefix}_hourly.csv.gz")
    _atomic_csv(metrics_frame, DATA_DIR / f"{prefix}_metrics.csv")
    result = {
        "status": "complete", "stop_name": stop_name,
        "stop_pct": expected_stop_pct,
        "source_signals": str(signals_path.relative_to(ROOT)),
        "source_vectorbt_ledger": str(ledger_path.relative_to(ROOT)),
        "source_h1_reader": "experiments/active/exp-alt-isolated-12h-firstweek-wicks-20261111-v1/census12.py::read_raw",
        "source_h1_cutoff_exclusive_ms": cutoff_ms,
        "effective_last_complete_h1_close_ms": effective_end_ms,
        "account_policy": {
            "initial_cash_usdt": INITIAL_CASH, "max_single_position_notional_fraction_of_nav": MAX_NOTIONAL_FRACTION,
            "max_open_positions": MAX_POSITIONS, "same_symbol_concurrency": 1,
            "entry_priority": "entry_ms, then symbol ascending, side text, trade_id",
            "cash_sharing": True, "leverage": 1.0,
            "entry_cashflow": "reserve notional plus VectorBT ledger entry fee",
            "closed_exit_cashflow": "release collateral plus ledger execution PnL less exit fee",
            "intrabar_exit_cash_available": "H1 trigger-bar close (exit_bar_open_ms + 1h), not that bar's open",
            "terminal_open_positions": "remain open and are marked at the last fully closed merged H1 close; no terminal liquidation or exit fee",
        },
        "strategy_definitions": {
            "baseline": "source baseline == 1 (normal touch; all source rows are baseline)",
            "first_wick": "source first_wick == 1",
            "retest": "source retest == 1",
            "owner_style": "(rank_12h == 1 and body_inside == 1) or retest == 1; post-photo exploratory rule, not independently validated",
        },
        "drawdown_interpretation": "Hourly close max drawdown is the realized marked-to-market account history. H1 OHLC stress uses the worst held-symbol extreme during each hour; different symbols' extremes are not assumed synchronous. The stress bound is an envelope and is not a guaranteed maximum-drawdown limit.",
        "groups": group_audits,
        "outputs": {
            "positions": str((DATA_DIR / f"{prefix}_positions.csv.gz").relative_to(ROOT)),
            "hourly": str((DATA_DIR / f"{prefix}_hourly.csv.gz").relative_to(ROOT)),
            "metrics": str((DATA_DIR / f"{prefix}_metrics.csv").relative_to(ROOT)),
        },
    }
    _atomic_json(DATA_DIR / f"{prefix}_audit.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False), flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_5)
    parser.add_argument("--stop-name", default="sl5")
    parser.add_argument("--stop-pct", type=float, choices=[3.0, 5.0])
    parser.add_argument("--signals", type=Path, default=SIGNALS_PATH)
    args = parser.parse_args()
    run(args.ledger, args.stop_name, args.signals, args.stop_pct)


if __name__ == "__main__":
    main()
