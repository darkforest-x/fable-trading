"""Independent TP/SL paths for first-week 12h wick events.

Each event/target pair is an isolated one-unit position. OHLC barrier ordering
and gap fills use :mod:`monday_wick_r_engine`; VectorBT records entry/exit fills
and charges fees. Untriggered positions remain open and are reported censored.
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np
import vectorbt as vbt

from yoyo.evaluation.monday_wick_r_engine import first_hit


HOUR_MS = 60 * 60 * 1000
ENTRY_TAKER_FEE = 0.0005
EXIT_TAKER_FEE = 0.0005
EXIT_MAKER_FEE = 0.0002
R_TARGETS = (1, 2, 3, 5, 6, 7, 10, 20)
TARGETS = tuple(f"{risk}R" for risk in R_TARGETS) + ("near", "mid", "far")


def _event_targets(event: dict[str, Any]) -> tuple[float, float, float, dict[str, float]]:
    """Return side, entry, risk, and target prices for one event."""
    try:
        side = float(event["side"])
        entry = float(event["entry"])
        stop = float(event["stop"])
        week_high = float(event["week_high"])
        week_low = float(event["week_low"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("event requires numeric side, entry, stop, week_high, and week_low") from exc

    values = np.asarray([side, entry, stop, week_high, week_low], dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError("event prices and side must be finite")
    if side not in (-1.0, 1.0):
        raise ValueError("event side must be +1 or -1")
    if entry <= 0.0 or stop <= 0.0 or week_low <= 0.0 or week_high < week_low:
        raise ValueError("event prices must be positive and week_high >= week_low")

    risk = abs(entry - stop)
    if risk <= 0.0 or side * (entry - stop) <= 0.0:
        raise ValueError("stop must be strictly adverse to entry")

    prices = {f"{multiple}R": entry + side * multiple * risk for multiple in R_TARGETS}
    midpoint = (week_high + week_low) / 2.0
    if side > 0:
        prices.update(near=week_low, mid=midpoint, far=week_high)
    else:
        prices.update(near=week_high, mid=midpoint, far=week_low)
    return side, entry, risk, prices


def _entry_index(event: dict[str, Any], row: int, n_bars: int) -> int:
    value = event.get("entry_i")
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"events[{row}].entry_i must be an integer index")
    try:
        index = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"events[{row}].entry_i must be an integer index") from exc
    if index != value or not 0 <= index < n_bars:
        raise ValueError(f"events[{row}].entry_i is outside raw")
    return index


def _native_arrays(source_ohlc: np.ndarray):
    """Add the sentinel required by ``first_hit`` and its output buffers."""
    ohlc = np.empty((len(source_ohlc) + 1, 4), dtype=np.float64)
    ohlc[0] = source_ohlc[0]
    ohlc[1:] = source_ohlc
    o, h, low, c = (np.ascontiguousarray(ohlc[:, i:i + 1]) for i in range(4))
    po = np.zeros_like(o)
    ty = np.zeros(o.shape, dtype=np.int64)
    scratch = np.zeros(1, dtype=np.int64)
    return o, h, low, c, po, ty, scratch


def _minute_first_hit(
    minute_bars: np.ndarray,
    reference: float,
    stop: float,
    target: float,
    side: float,
) -> tuple[int, float, int, bool] | None:
    """Resolve one H1 candle on its complete minute tape using the same kernel."""
    source_ohlc = np.ascontiguousarray(minute_bars[:, 1:5])
    o, h, low, c, po, ty, scratch = _native_arrays(source_ohlc)
    hit_i, price, kind, ambiguous = first_hit(
        o, h, low, c, 1, len(minute_bars), reference, stop, target, side,
        po, ty, scratch,
    )
    if hit_i < 0:
        return None
    return int(hit_i - 1), float(price), int(kind), bool(ambiguous)


def _validate_minute_tape(minute_raw: np.ndarray, hour_raw: np.ndarray) -> np.ndarray:
    """Require exactly the complete, contiguous 60-minute tape for the H1 bar."""
    minute = np.asarray(minute_raw, dtype=np.float64)
    if minute.shape != (60, 5) or not np.isfinite(minute).all():
        raise ValueError("minute_lookup must return a finite 60 x 5 array or None")
    minute_ts = minute[:, 0].astype(np.int64)
    if not np.array_equal(minute[:, 0], minute_ts.astype(np.float64)):
        raise ValueError("minute_lookup timestamps must be integer milliseconds")
    if minute_ts[0] != int(hour_raw[0]) or minute_ts[-1] != int(hour_raw[0]) + 59 * 60_000:
        raise ValueError("minute_lookup must cover the requested H1 candle exactly")
    if np.any(np.diff(minute_ts) != 60_000):
        raise ValueError("minute_lookup must contain 60 contiguous one-minute bars")
    if np.any(minute[:, 1:] <= 0.0):
        raise ValueError("minute OHLC values must be positive")
    if np.any(minute[:, 2] < np.maximum(minute[:, 1], minute[:, 4])) or np.any(
        minute[:, 3] > np.minimum(minute[:, 1], minute[:, 4])
    ) or np.any(minute[:, 3] > minute[:, 2]):
        raise ValueError("minute_lookup returned inconsistent OHLC rows")
    return minute


def run_exits(
    raw: np.ndarray,
    events: list[dict[str, Any]],
    *,
    minute_lookup: Callable[[Any, np.ndarray], np.ndarray | None] | None = None,
) -> list[dict[str, Any]]:
    """Return independent TP/SL outcomes for fixed-R and weekly-range targets.

    ``raw`` is an ``N x 5`` float array with columns ``open_ts_ms, open, high,
    low, close``. ``entry_i`` is the first hourly bar on which the position is
    active. Scanning stops immediately before the first non-hourly timestamp
    gap after entry, or at the final supplied bar. Months do not otherwise
    limit a path.

    ``near`` is the touched-side weekly boundary, ``far`` is the opposite
    boundary, and ``mid`` is their midpoint. Named targets at or behind entry
    are disabled and reported as ``behind_entry`` without entering a position.
    A nonpositive fixed-R target is also disabled, while its stop remains active.
    If an H1 candle touches both barriers, ``minute_lookup(symbol, hour_row)``
    may provide that candle's complete minute tape for finer first-hit ordering.
    """
    bars = np.asarray(raw, dtype=np.float64)
    if bars.ndim != 2 or bars.shape[1] != 5:
        raise ValueError("raw must be an N x 5 array: open_ts_ms, O, H, L, C")
    if not events:
        return []
    if len(bars) == 0:
        raise ValueError("raw cannot be empty when events are supplied")
    if not np.isfinite(bars).all():
        raise ValueError("raw timestamps and OHLC values must be finite")
    if np.any(bars[:, 1:] <= 0.0):
        raise ValueError("raw OHLC values must be positive")

    timestamps_f = bars[:, 0]
    timestamps_i = timestamps_f.astype(np.int64)
    if not np.array_equal(timestamps_f, timestamps_i.astype(np.float64)):
        raise ValueError("open_ts_ms values must be integer milliseconds")
    if np.any(np.diff(timestamps_i) <= 0):
        raise ValueError("raw timestamps must be strictly increasing")
    if np.any(bars[:, 2] < np.maximum(bars[:, 1], bars[:, 4])) or np.any(
        bars[:, 3] > np.minimum(bars[:, 1], bars[:, 4])
    ) or np.any(bars[:, 3] > bars[:, 2]):
        raise ValueError("raw OHLC rows are inconsistent")

    # The native wrapper reads start-1 as an anchor. Prefixing one sentinel
    # lets a real event begin at raw index zero without changing its bars.
    o, h, low, c, po, ty, scratch = _native_arrays(np.ascontiguousarray(bars[:, 1:5]))

    # A gap is the first bar whose open is not exactly one hour after its
    # predecessor. The prior bar remains part of the observable path.
    gap_after = np.flatnonzero(np.diff(timestamps_i) != HOUR_MS) + 1
    rows: list[dict[str, Any]] = []
    private: list[dict[str, Any]] = []

    for event_row, event in enumerate(events):
        if "event_id" not in event:
            raise ValueError(f"events[{event_row}] is missing event_id")
        entry_i = _entry_index(event, event_row, len(bars))
        side, entry, risk, target_prices = _event_targets(event)
        gap_pos = int(np.searchsorted(gap_after, entry_i + 1, side="left"))
        has_gap = gap_pos < len(gap_after)
        end_i = int(gap_after[gap_pos] - 1) if has_gap else len(bars) - 1
        censor_reason = "gap" if has_gap else "end"

        for target_name in TARGETS:
            target_price = float(target_prices[target_name])
            target_ahead = side * (target_price - entry) > 0.0
            target_possible = target_price > 0.0 and target_ahead
            behind_entry = target_name in ("near", "mid", "far") and not target_ahead

            if behind_entry:
                rows.append({
                    "event_id": event["event_id"],
                    "target": target_name,
                    "target_price": target_price,
                    "outcome": "behind_entry",
                    "ambiguous": False,
                    "exit_ms": None,
                    "exit_price": None,
                    "net_r": None,
                    "net_return": None,
                    "target_possible": False,
                    "censor_reason": None,
                    "resolution": "h1",
                })
                private.append({
                    "entry": entry,
                    "side": side,
                    "risk": risk,
                    "entered": False,
                    "closed": False,
                    "exit_price": float(bars[end_i, 4]),
                    "outcome": "behind_entry",
                    "mark_price": float(bars[end_i, 4]),
                })
                continue

            active_target = target_price if target_possible else np.nan

            hit_i, fill_price, hit_kind, ambiguous = first_hit(
                o, h, low, c,
                entry_i + 1, end_i + 1,
                entry, float(event["stop"]), active_target, side,
                po, ty, scratch,
            )

            if hit_i >= 0 and hit_kind == 4:
                outcome = "tp"
                exit_i = int(hit_i - 1)
                exit_price: float | None = float(fill_price)
                ambiguous_flag = bool(ambiguous)
                resolution = "h1"
            elif hit_i >= 0 and hit_kind == 1:
                outcome = "sl"
                exit_i = int(hit_i - 1)
                exit_price = float(fill_price)
                ambiguous_flag = bool(ambiguous)
                resolution = "h1"
            elif hit_i >= 0:
                raise RuntimeError(f"unexpected first_hit kind {hit_kind}")
            else:
                outcome = "censored"
                exit_i = None
                exit_price = None
                ambiguous_flag = False
                resolution = "h1"
            exit_ms = int(timestamps_i[exit_i]) if exit_i is not None else None

            if hit_i >= 0 and ambiguous and minute_lookup is not None:
                minute_raw = minute_lookup(event["symbol"], bars[exit_i])
                if minute_raw is None:
                    resolution = "h1_fallback"
                else:
                    minute_bars = _validate_minute_tape(minute_raw, bars[exit_i])
                    minute_hit = _minute_first_hit(
                        minute_bars, entry, float(event["stop"]), active_target, side,
                    )
                    if minute_hit is None:
                        raise RuntimeError(
                            f"H1 dual-touch has no minute-level barrier hit: {event['event_id']}"
                        )
                    minute_i, minute_price, minute_kind, minute_ambiguous = minute_hit
                    if minute_kind not in (1, 4):
                        raise RuntimeError(f"unexpected minute first_hit kind {minute_kind}")
                    outcome = "sl" if minute_kind == 1 else "tp"
                    exit_ms = int(minute_bars[minute_i, 0])
                    exit_price = minute_price
                    ambiguous_flag = minute_ambiguous
                    resolution = "1m"

            closed = outcome in ("tp", "sl")
            rows.append({
                "event_id": event["event_id"],
                "target": target_name,
                "target_price": target_price,
                "outcome": outcome,
                "ambiguous": ambiguous_flag,
                "exit_ms": exit_ms,
                "exit_price": exit_price,
                "net_r": None,
                "net_return": None,
                "target_possible": bool(target_possible),
                "censor_reason": censor_reason if not closed else None,
                "resolution": resolution,
            })
            private.append({
                "entry": entry,
                "side": side,
                "risk": risk,
                "entered": True,
                "closed": closed,
                "exit_price": exit_price,
                "outcome": outcome,
                "mark_price": float(bars[end_i, 4]),
            })

    # Use isolated VectorBT columns: each eligible target gets its own one-unit
    # entry, and only triggered scenarios receive a closing order. Open/censored
    # rows retain an open status; behind-entry rows create no orders.
    count = len(rows)
    entries = np.asarray([item["entry"] for item in private], dtype=np.float64)
    sides = np.asarray([item["side"] for item in private], dtype=np.float64)
    entered = np.asarray([item["entered"] for item in private], dtype=bool)
    closed = np.asarray([item["closed"] for item in private], dtype=bool)
    exits = np.asarray([
        item["exit_price"] if item["closed"] else item["mark_price"]
        for item in private
    ], dtype=np.float64)
    exit_fees = np.asarray([
        EXIT_MAKER_FEE if item["outcome"] == "tp" else EXIT_TAKER_FEE
        for item in private
    ], dtype=np.float64)
    sizes = np.vstack((np.where(entered, sides, 0.0), np.where(closed, -sides, 0.0)))
    order_prices = np.vstack((entries, exits))
    close_prices = np.vstack((entries, exits))
    fees = np.vstack((np.where(entered, ENTRY_TAKER_FEE, 0.0), np.where(closed, exit_fees, 0.0)))
    # A short gap stop can cover far above the entry. Keep every isolated
    # column solvent for its largest execution/mark price so VectorBT rejects
    # only invalid orders, not a synthetic initial-cash shortage.
    init_cash = np.maximum.reduce((entries, exits, np.asarray([
        item["mark_price"] for item in private
    ], dtype=np.float64))) * 4.0

    portfolio = vbt.Portfolio.from_orders(
        close_prices,
        size=sizes,
        price=order_prices,
        size_type="amount",
        direction="both",
        fees=fees,
        init_cash=init_cash,
        cash_sharing=False,
        allow_partial=False,
        raise_reject=True,
    )
    records = portfolio.trades.records
    closed_records = records[records.status.eq(1)]
    if len(closed_records) != int(closed.sum()):
        raise RuntimeError("VectorBT closed-trade count does not match triggered outcomes")

    for record in closed_records.itertuples(index=False):
        col = int(record.col)
        pnl = float(record.pnl)
        rows[col]["net_return"] = pnl / private[col]["entry"]
        rows[col]["net_r"] = pnl / private[col]["risk"]

    return rows
