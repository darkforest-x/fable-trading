"""Causal next-open labels and one-position equity for IMACD startup research.

The neutral exit and fixed 20 bp round-trip cost inherit
``imacd_profit_mechanism.outcome``: signal i closes, enter i+1 open, find the
first j > i with side * md[j] <= 0, and exit j+1 open. Positions without an
available exit open are marked at the fold's last close. There is no new
protective stop, profit target, funding ledger, or liquidation model.

OHLC after entry are used only for outcome labels and accounting, never as
features. MFE/MAE use the held bars' high/low and the exit open, excluding that
exit bar's later high/low. ATR-normalized labels use the closed signal ATR[i].
Portfolio equity is sampled at every open/close and each cost deduction; its
drawdown and insolvency flags cannot detect an intrabar high/low excursion.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np
import pandas as pd

COST_BP = 20.0
ENTRY_COST_RATE = EXIT_COST_RATE = COST_BP / 20000.0


def _bars(bars: pd.DataFrame) -> tuple[np.ndarray, pd.Timedelta]:
    """Validate regular open-stamped OHLC; no IO and no source substitution."""
    if not isinstance(bars.index, pd.DatetimeIndex) or len(bars) < 2:
        raise ValueError("bars require at least two datetime-indexed open stamps")
    differences = np.diff(bars.index.asi8)
    if differences[0] <= 0 or not np.all(differences == differences[0]):
        raise ValueError("bar open timestamps must be increasing and gap-free")
    values = bars[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("OHLC must be finite and positive")
    if (values[:, 1] < np.maximum(values[:, 0], values[:, 3])).any() or (
        values[:, 2] > np.minimum(values[:, 0], values[:, 3])
    ).any():
        raise ValueError("invalid OHLC geometry")
    return values, pd.Timedelta(int(differences[0]), unit="ns")


def _range_extremes(
    high: np.ndarray, low: np.ndarray, starts: np.ndarray, stops: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Batch [start, stop) extremes with an O(n)-memory segment tree."""
    size = 1 << (len(high) - 1).bit_length()
    highs = np.full(2 * size, -np.inf)
    lows = np.full(2 * size, np.inf)
    highs[size:size + len(high)] = high
    lows[size:size + len(low)] = low
    begin, end = size // 2, size
    while begin:
        highs[begin:end] = np.maximum(highs[2 * begin:2 * end:2], highs[2 * begin + 1:2 * end:2])
        lows[begin:end] = np.minimum(lows[2 * begin:2 * end:2], lows[2 * begin + 1:2 * end:2])
        end, begin = begin, begin // 2
    left, right = starts + size, stops + size
    result_high = np.full(len(starts), -np.inf)
    result_low = np.full(len(starts), np.inf)
    while np.any(left < right):
        take_left = (left < right) & ((left & 1) == 1)
        take_right = (left < right) & ((right & 1) == 1)
        result_high[take_left] = np.maximum(result_high[take_left], highs[left[take_left]])
        result_low[take_left] = np.minimum(result_low[take_left], lows[left[take_left]])
        right[take_right] -= 1
        result_high[take_right] = np.maximum(result_high[take_right], highs[right[take_right]])
        result_low[take_right] = np.minimum(result_low[take_right], lows[right[take_right]])
        left[take_left] += 1
        left //= 2
        right //= 2
    return result_high, result_low


def outcome_arrays(
    bars: pd.DataFrame,
    features: pd.DataFrame,
    signal_indexes: Iterable[int],
    sides: Iterable[int] | int,
    last: int,
) -> list[dict[str, Any]]:
    """Batch neutral-exit labels; call once per fold for many random controls.

    ``features`` must align with ``bars`` and contain md and atr. The only
    normalization feature read is ATR[i], using OHLC through signal close i.
    The md exit search and future OHLC are outcome data. ``last`` is inclusive.
    Timestamps explicitly distinguish bar opens from decision/mark closes.
    """
    values, step = _bars(bars)
    if not features.index.equals(bars.index):
        raise ValueError("features and bars must have identical indexes")
    if not isinstance(last, (int, np.integer)) or not 0 <= last < len(bars):
        raise ValueError("last must identify an available final fold bar")
    raw_indexes = np.asarray(list(signal_indexes))
    if raw_indexes.ndim != 1 or not np.isfinite(raw_indexes).all():
        raise ValueError("signal_indexes must be finite integers")
    indexes = raw_indexes.astype(np.int64)
    if not np.array_equal(indexes, raw_indexes) or ((indexes < 0) | (indexes >= last)).any():
        raise ValueError("each signal needs an i+1 entry open inside the fold")
    directions = np.asarray(list(sides) if not np.isscalar(sides) else np.full(len(indexes), sides))
    if directions.shape != indexes.shape or not np.isin(directions, [-1, 1]).all():
        raise ValueError("sides must contain one +1/-1 direction per signal")
    directions = directions.astype(np.int64)
    if not len(indexes):
        return []
    md = features["md"].to_numpy(dtype=float)
    atr = features["atr"].to_numpy(dtype=float)[indexes]
    decision_exits = np.full(len(indexes), last, dtype=np.int64)
    has_exit = np.zeros(len(indexes), dtype=bool)
    for side in (-1, 1):
        mask = directions == side
        sequence = np.flatnonzero(md[:last + 1] * side <= 0)
        locations = np.searchsorted(sequence, indexes[mask], side="right")
        found = locations < len(sequence)
        selected = np.flatnonzero(mask)[found]
        decision_exits[selected] = sequence[locations[found]]
        has_exit[selected] = True
    natural = has_exit & (decision_exits + 1 <= last)
    entries = indexes + 1
    exits = np.where(natural, decision_exits + 1, last)
    stops = np.where(natural, exits, last + 1)
    entry_prices = values[entries, 0]
    exit_prices = np.where(natural, values[exits, 0], values[last, 3])
    high, low = _range_extremes(values[:last + 1, 1], values[:last + 1, 2], entries, stops)
    high = np.maximum(high, np.maximum(entry_prices, exit_prices))
    low = np.minimum(low, np.minimum(entry_prices, exit_prices))
    favorable = np.where(directions == 1, high - entry_prices, entry_prices - low)
    adverse = np.where(directions == 1, low - entry_prices, entry_prices - high)
    gross = directions * (exit_prices / entry_prices - 1) * 10000
    valid_atr = np.isfinite(atr) & (atr > 0)
    mfe_atr = np.divide(favorable, atr, out=np.full(len(atr), np.nan), where=valid_atr)
    mae_atr = np.divide(adverse, atr, out=np.full(len(atr), np.nan), where=valid_atr)
    stamps = bars.index
    results = []
    for k, i in enumerate(indexes):
        x = int(exits[k])
        is_natural = bool(natural[k])
        results.append({
            "signal_i": int(i), "side": int(directions[k]),
            "signal_open_time": stamps[i].isoformat(),
            "signal_decision_close_time": (stamps[i] + step).isoformat(),
            "entry_i": int(entries[k]), "entry_open_time": stamps[entries[k]].isoformat(),
            "entry_price": float(entry_prices[k]),
            "exit_i": x, "exit_bar_open_time": stamps[x].isoformat(),
            "exit_decision_close_time": (stamps[decision_exits[k]] + step).isoformat() if is_natural else None,
            "exit_fill_open_time": stamps[x].isoformat() if is_natural else None,
            "boundary_mark_close_time": None if is_natural else (stamps[last] + step).isoformat(),
            "exit_time": (stamps[x] if is_natural else stamps[last] + step).isoformat(),
            "exit_price": float(exit_prices[k]),
            "exit_kind": "natural" if is_natural else "boundary_mark",
            "hold_bars": int(stops[k] - entries[k]),
            "gross_bp": float(gross[k]), "net_bp": float(gross[k] - COST_BP),
            "mfe_bp": float(favorable[k] / entry_prices[k] * 10000),
            "mae_bp": float(adverse[k] / entry_prices[k] * 10000),
            "mfe_atr": float(mfe_atr[k]), "mae_atr": float(mae_atr[k]),
            "signal_atr": float(atr[k]),
        })
    return results


def outcome(bars: pd.DataFrame, features: pd.DataFrame, i: int, side: int, last: int) -> dict[str, Any]:
    """Single-event convenience wrapper; use outcome_arrays for large batches."""
    return outcome_arrays(bars, features, [i], [side], last)[0]


def compound_portfolio(
    bars: pd.DataFrame,
    events: pd.DataFrame | Iterable[Mapping[str, Any]],
    first: int,
    last: int,
) -> tuple[dict[str, Any], pd.DataFrame, list[Any]]:
    """One series, one position, initial equity 1, current-equity entry notional.

    Each accepted entry uses equity immediately before entry as its notional;
    both 10 bp fees are charged against that entry notional, reproducing exactly
    the inherited gross-minus-20-bp trade outcome. This is a synthetic one-times
    notional accounting proxy, not a margined execution or cash-settlement model.

    A natural open exit can precede a new entry at the same open. Boundary marks
    occupy the final close, so no entry at that bar's earlier open is allowed.
    After any sampled equity <= 0, no further entries are accepted. The existing
    position continues to its specified exit; no liquidation or recovery reset
    is invented. Open/close samples can miss intrabar insolvency and drawdown.
    """
    prices, step = _bars(bars)
    if not 0 <= first <= last < len(bars):
        raise ValueError("portfolio fold bounds are invalid")
    rows = events.to_dict("records") if isinstance(events, pd.DataFrame) else [dict(r) for r in events]
    for column in ("symbol", "timeframe", "minutes"):
        if len({str(r[column]) for r in rows if column in r}) > 1:
            raise ValueError("compound_portfolio accepts only one symbol/timeframe")
    for order, row in enumerate(rows):
        row.setdefault("event_id", order)
        for key in ("entry_i", "exit_i"):
            if int(row[key]) != row[key]:
                raise ValueError("entry/exit indexes must be integers")
        a, z = int(row["entry_i"]), int(row["exit_i"])
        if not first <= a <= z <= last or row["side"] not in (-1, 1):
            raise ValueError("invalid event direction or fold bounds")
        if row["exit_kind"] not in ("natural", "boundary_mark"):
            raise ValueError("unknown exit kind")
        if row["exit_kind"] == "boundary_mark" and z != last:
            raise ValueError("boundary marks must use the last fold close")
        if row["exit_kind"] == "natural" and z <= a:
            raise ValueError("neutral exit must follow at least one held bar")
        if "signal_i" in row and row["signal_i"] + 1 != a:
            raise ValueError("entry must fill the open after signal close")
        expected_exit = prices[z, 0] if row["exit_kind"] == "natural" else prices[z, 3]
        if not np.isclose(row["entry_price"], prices[a, 0], rtol=1e-10, atol=0) or not np.isclose(
            row["exit_price"], expected_exit, rtol=1e-10, atol=0
        ):
            raise ValueError("event fill prices differ from bars")
    if len({r["event_id"] for r in rows}) != len(rows):
        raise ValueError("event IDs must be unique within this portfolio")
    rows.sort(key=lambda r: (r["entry_i"], r.get("signal_i", r["entry_i"] - 1)))
    by_entry: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        by_entry.setdefault(int(row["entry_i"]), []).append(row)

    equity = cash = 1.0
    active: dict[str, Any] | None = None
    notional = 0.0
    accepted: list[Any] = []
    trace: list[dict[str, Any]] = []
    overlap_blocked = insolvency_blocked = 0
    insolvent = False
    first_insolvent_time = first_insolvent_phase = None

    def record(bar_i: int, phase: str, timestamp: pd.Timestamp, event_id: Any = None) -> None:
        nonlocal insolvent, first_insolvent_time, first_insolvent_phase
        trace.append(dict(bar_i=bar_i, time=timestamp, kind=phase, phase=phase, equity=float(equity), event_id=event_id))
        if equity <= 0 and not insolvent:
            insolvent = True
            first_insolvent_time = timestamp.isoformat()
            first_insolvent_phase = phase

    record(first, "initial", bars.index[first])
    for bar_i in range(first, last + 1):
        stamp = bars.index[bar_i]
        if active is not None:
            equity = cash + notional * active["side"] * (prices[bar_i, 0] / active["entry_price"] - 1)
        else:
            equity = cash
        record(bar_i, "open", stamp, active["event_id"] if active else None)
        if active is not None and active["exit_kind"] == "natural" and active["exit_i"] == bar_i:
            equity -= notional * EXIT_COST_RATE
            record(bar_i, "exit_fee", stamp, active["event_id"])
            cash = equity
            active = None
        for candidate in by_entry.get(bar_i, []):
            if active is not None:
                overlap_blocked += 1
            elif insolvent:
                insolvency_blocked += 1
            else:
                active = candidate
                notional = cash
                equity = cash - notional * ENTRY_COST_RATE
                cash = equity
                accepted.append(candidate["event_id"])
                record(bar_i, "entry_fee", stamp, candidate["event_id"])
        if active is not None:
            equity = cash + notional * active["side"] * (prices[bar_i, 3] / active["entry_price"] - 1)
        else:
            equity = cash
        if active is not None and active["exit_kind"] == "boundary_mark" and active["exit_i"] == bar_i:
            record(bar_i, "boundary_pre_fee", stamp + step, active["event_id"])
            equity -= notional * EXIT_COST_RATE
            record(bar_i, "boundary_exit_fee", stamp + step, active["event_id"])
            cash = equity
            active = None
        record(bar_i, "close", stamp + step, active["event_id"] if active else None)
    if active is not None:
        raise AssertionError("position survived validated fold exit")
    series = pd.DataFrame(trace)
    curve = series["equity"].to_numpy()
    peaks = np.maximum.accumulate(curve)
    metrics = dict(
        initial_equity=1.0, ending_equity=float(cash), net_return_pct=float((cash - 1) * 100),
        max_drawdown_pct=float(np.max((peaks - curve) / peaks) * 100),
        trades=len(accepted), blocked=overlap_blocked + insolvency_blocked,
        overlap_blocked=overlap_blocked, insolvency_blocked=insolvency_blocked,
        insolvent=insolvent, first_insolvent_time=first_insolvent_time,
        first_insolvent_phase=first_insolvent_phase, cost_bp=COST_BP,
        sampled_drawdown_only=True, liquidation_model=False,
    )
    return metrics, series, accepted
