"""Batched 5-minute event exits using VectorBT's native portfolio lifecycle.

Each candidate is an independent VectorBT column. ``Portfolio.from_order_func``
reads that candidate's OHLC bar and submits the actual entry and exit orders;
there is no precomputed fill scan followed by a portfolio re-posting step.
One call handles all candidates for one parameter/scenario combination.

Execution contract
------------------
* Entry is a market buy/sell at the supplied ``entry_idx`` bar open.
* The holding horizon counts the entry bar as bar 1. Protective barriers are
  checked before timeout; the Nth bar exits at its close if neither barrier hit.
* The bar open is checked first. A stop crossed at the open fills at that open
  (adverse market slippage applies); a resting target crossed at the open fills
  conservatively at the target limit, without slippage.
* After the open, high/low touches are evaluated. If stop and target are both
  touched in one OHLC bar, the stop wins. Intrabar stop fills at its stop level
  plus adverse slippage. Intrabar target fills at its limit price.
* Market entry, stop and timeout fills pay the market fee and adverse slippage
  on each fill. Target fills pay the limit fee and no slippage. Fees and
  slippage are separate inputs.
* Since OHLC cannot reveal intrabar order, excursion marks on a target exit bar
  include the complete adverse wick but cap the favorable extreme at the target.
  On a stop exit bar, the stop-first assumption includes only the open/entry and
  stop fill, excluding a potentially post-exit favorable wick. Timeout bars
  include the full observed range through the close exit.

``entry_ms`` and ``exit_ms`` are the source 5-minute bar's ``ts_ms`` (bar-open
time); ``exit_phase`` carries the available resolution: open, intrabar, or close.
"""
from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import NoOrder


HORIZONS = frozenset((6, 12, 36))
TAKER_BPS = 5.0
LIMIT_BPS = 2.0
BAR_COUNT = 240

_REASON_NAMES = {
    1: "stop_gap_open",
    2: "stop_intrabar",
    3: "take_profit_gap_open",
    4: "take_profit_intrabar",
    5: "timeout_close",
}
_PHASE_NAMES = {1: "open", 2: "intrabar", 3: "close"}


@njit(cache=True)
def _order_func(
    c,
    bars,
    starts,
    ends,
    sides,
    source_targets,
    tp_mode_code,
    tp_value,
    sl_pct,
    slip_rate,
    fee_market,
    fee_limit,
    entry_prices,
    target_prices,
    stop_prices,
    exit_prices,
    reason_codes,
    phase_codes,
    active_highs,
    active_lows,
    mfe_prices,
    mae_prices,
):
    """Emit one candidate's open or barrier order from within VectorBT."""
    col = c.from_col
    start = starts[col]
    if c.i < start:
        return -1, NoOrder

    # Entry at the candidate's designated bar open. The callback is flexible,
    # so VectorBT asks again on this bar after an entry and permits a same-bar
    # barrier close.
    if c.i == start and c.call_idx == 0 and c.last_position[col] == 0.0:
        op = bars[c.i, col, 0]
        side = sides[col]
        entry = op * (1.0 + side * slip_rate)
        entry_prices[col] = entry
        if tp_mode_code == 0:
            target = entry * (1.0 + side * tp_value)
        else:
            target = entry + (source_targets[col] - entry) * tp_value
        target_prices[col] = target
        stop_prices[col] = entry * (1.0 - side * sl_pct)
        mfe_prices[col] = entry
        mae_prices[col] = entry
        return col, nb.order_nb(
            size=side,
            price=entry,
            fees=fee_market,
            allow_partial=False,
            raise_reject=True,
        )

    # The first callback on the entry row submits entry; the next handles the
    # rest of that row. Later active rows have call_idx == 0.
    if c.call_idx != int(c.i == start) or c.last_position[col] == 0.0:
        return -1, NoOrder

    op, hi, lo, close = bars[c.i, col]
    side = sides[col]
    entry = entry_prices[col]
    target = target_prices[col]
    stop = stop_prices[col]
    entry_bar = c.i == start
    first_held_price = entry if entry_bar else op

    # Open has known precedence over the remainder of the OHLC range.
    stop_at_open = (op - stop) * side <= 0.0
    target_at_open = side * (op - target) >= 0.0
    exit_reason = 0
    exit_phase = 0
    exit_fill = 0.0
    eff_hi = first_held_price
    eff_lo = first_held_price

    if stop_at_open:
        exit_reason = 1
        exit_phase = 1
        exit_fill = op * (1.0 - side * slip_rate)
        eff_hi = max(eff_hi, op, exit_fill)
        eff_lo = min(eff_lo, op, exit_fill)
    elif target_at_open:
        exit_reason = 3
        exit_phase = 1
        exit_fill = target
        eff_hi = max(eff_hi, op, exit_fill)
        eff_lo = min(eff_lo, op, exit_fill)
    else:
        adverse = lo if side > 0.0 else hi
        favorable = hi if side > 0.0 else lo
        hit_stop = (adverse - stop) * side <= 0.0
        hit_target = side * (favorable - target) >= 0.0

        if hit_stop:  # Includes dual-hit bars: stop is deliberately first.
            exit_reason = 2
            exit_phase = 2
            exit_fill = stop * (1.0 - side * slip_rate)
            eff_hi = max(first_held_price, exit_fill)
            eff_lo = min(first_held_price, exit_fill)
        elif hit_target:
            exit_reason = 4
            exit_phase = 2
            exit_fill = target
            # Unknown OHLC order: assume the entire adverse wick came before
            # the resting target, while target-side travel ends at its fill.
            if side > 0.0:
                eff_hi = max(first_held_price, target)
                eff_lo = min(first_held_price, op, lo)
            else:
                eff_hi = max(first_held_price, op, hi)
                eff_lo = min(first_held_price, target)
        elif c.i == ends[col]:
            exit_reason = 5
            exit_phase = 3
            exit_fill = close * (1.0 - side * slip_rate)
            eff_hi = max(first_held_price, op, hi, exit_fill)
            eff_lo = min(first_held_price, op, lo, exit_fill)
        else:
            # No exit: the full observed range remains active.
            eff_hi = max(first_held_price, op, hi)
            eff_lo = min(first_held_price, op, lo)

    active_highs[c.i, col] = eff_hi
    active_lows[c.i, col] = eff_lo
    if side > 0.0:
        mfe_prices[col] = max(mfe_prices[col], eff_hi)
        mae_prices[col] = min(mae_prices[col], eff_lo)
    else:
        mfe_prices[col] = min(mfe_prices[col], eff_lo)
        mae_prices[col] = max(mae_prices[col], eff_hi)

    if exit_reason == 0:
        return -1, NoOrder

    reason_codes[col] = exit_reason
    phase_codes[col] = exit_phase
    exit_prices[col] = exit_fill
    fee = fee_limit if exit_reason in (3, 4) else fee_market
    return col, nb.order_nb(
        size=-c.last_position[col],
        price=exit_fill,
        fees=fee,
        allow_partial=False,
        raise_reject=True,
    )


def _empty_results() -> tuple[pd.DataFrame, pd.DataFrame]:
    trade_cols = [
        "candidate_id", "event_id", "symbol", "side", "side_label", "entry_idx",
        "entry_ms", "entry_price", "entry_fee_rate", "entry_fee_bps", "entry_fee",
        "target_price", "near_side_target_price", "stop_price", "exit_idx", "exit_ms",
        "exit_price", "exit_fee_rate", "exit_fee_bps", "exit_fee", "exit_phase",
        "exit_reason", "gross_pnl", "fee_amount", "net_pnl", "gross_pct", "fee_pct",
        "net_pct", "gross_r", "fee_r", "net_r", "mfe_price", "mae_price", "mfe_pct",
        "mae_pct", "ambiguous_stop_target_bar", "engine", "engine_version",
    ]
    path_cols = [
        "candidate_id", "event_id", "symbol", "bar_idx", "bar_ms", "open", "high",
        "low", "close", "mark_open", "mark_close", "position_side", "active_high",
        "active_low", "active_mfe_price", "active_mae_price", "active_mfe_pct",
        "active_mae_pct", "entry_bar", "exit_bar", "exit_phase", "exit_reason",
    ]
    return pd.DataFrame(columns=trade_cols), pd.DataFrame(columns=path_cols)


def _side_number(value) -> int:
    if isinstance(value, str):
        side = value.strip().lower()
        if side == "lower":
            return 1
        if side == "upper":
            return -1
    elif value in (-1, 1, -1.0, 1.0):
        return int(value)
    raise ValueError(f"side must be 'upper', 'lower', -1, or +1; got {value!r}")


def simulate_batch(
    events: dict[str, pd.DataFrame],
    candidates: pd.DataFrame,
    *,
    tp_mode: Literal["fixed_pct", "target_distance"],
    tp_value: float,
    sl_pct: float,
    horizon_bars: int,
    slippage_bps: float = 0.0,
    fee_market_bps: float = TAKER_BPS,
    fee_limit_bps: float = LIMIT_BPS,
    bar_count: int = BAR_COUNT,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Simulate all candidates once through a VectorBT native portfolio.

    Args:
        events: Mapping of frozen string event IDs to ``bar_count``-row OHLC frames with
            ``ts_ms, open, high, low, close``. The pandas index is ignored.
        candidates: One row per trade candidate with unique ``candidate_id``,
            ``event_id``, ``symbol``, integer ``entry_idx``, ``side`` (``upper``
            short / ``lower`` long, or -1/+1), and ``target_price`` (the
            candidate's near-side weekly target).
        tp_mode: ``fixed_pct`` anchors ``tp_value`` to entry (0.01 means 1%).
            ``target_distance`` uses that fraction of entry-to-near-side-target
            distance (0..1).
        tp_value: TP proportion in decimal form for ``fixed_pct`` and 0..1 for
            ``target_distance``.
        sl_pct: Fixed stop proportion (0.01 means 1%).
        horizon_bars: One of 6, 12, or 36 for the default 240-row event window.
            Longer explicit windows may use 288 or 2016 bars; the entry bar
            counts as bar 1.
        slippage_bps: Adverse market slippage per market/stop/timeout fill.
            Limit targets never receive slippage. Pass a separate call for each
            1..5 bp stress scenario; 0 is the baseline.
        fee_market_bps: Per-fill taker fee, default 5 bp.
        fee_limit_bps: Per-fill maker fee, default 2 bp.
        bar_count: Number of real 5-minute OHLC rows supplied per event. The
            default preserves the original 240-row contract.

    Returns:
        ``(trades, mark_path)``. ``trades`` has one actual VectorBT entry and
        exit per candidate. ``mark_path`` has every held 5-minute bar including
        entry and exit bars, with full OHLC, open/close marks, and effective
        high/low and cumulative MFE/MAE clipped to the active holding interval.
    """
    if tp_mode not in ("fixed_pct", "target_distance"):
        raise ValueError("tp_mode must be 'fixed_pct' or 'target_distance'")
    try:
        n_bars = int(bar_count)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("bar_count must be a positive integer") from exc
    if n_bars <= 0 or n_bars != bar_count:
        raise ValueError("bar_count must be a positive integer")
    supported_horizons = set(HORIZONS)
    if n_bars > BAR_COUNT:
        supported_horizons.update((288, 2016))
    if horizon_bars not in supported_horizons:
        raise ValueError(f"horizon_bars must be one of {sorted(supported_horizons)}")
    if horizon_bars > n_bars:
        raise ValueError("holding horizon cannot exceed the supplied event window")
    for name, value in (("tp_value", tp_value), ("sl_pct", sl_pct),
                        ("slippage_bps", slippage_bps),
                        ("fee_market_bps", fee_market_bps), ("fee_limit_bps", fee_limit_bps)):
        if not np.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and non-negative")
    if sl_pct <= 0:
        raise ValueError("sl_pct must be greater than zero")
    if tp_mode == "fixed_pct" and tp_value <= 0:
        raise ValueError("fixed_pct tp_value must be greater than zero")
    if tp_mode == "target_distance" and tp_value > 1:
        raise ValueError("target_distance tp_value must be in [0, 1]")
    if not isinstance(events, dict):
        raise TypeError("events must be dict[str, DataFrame]")

    required_candidate_cols = {"candidate_id", "event_id", "symbol", "entry_idx", "side", "target_price"}
    missing = required_candidate_cols.difference(candidates.columns)
    if missing:
        raise ValueError(f"candidates missing columns: {sorted(missing)}")
    if candidates.empty:
        return _empty_results()
    if candidates["candidate_id"].isna().any() or candidates["event_id"].isna().any():
        raise ValueError("candidate_id and event_id cannot be null")
    f = candidates.reset_index(drop=True).copy()
    f["candidate_id"] = f["candidate_id"].astype(str)
    f["event_id"] = f["event_id"].astype(str)
    f["symbol"] = f["symbol"].astype(str)
    if f["candidate_id"].eq("").any() or f["event_id"].eq("").any() or f["symbol"].eq("").any():
        raise ValueError("candidate_id, event_id, and symbol must be non-empty")
    if f["candidate_id"].duplicated().any():
        raise ValueError("candidate_id must be unique within a batch")
    for event_id in events:
        if not isinstance(event_id, str):
            raise TypeError("event IDs in events must be strings")

    try:
        starts = pd.to_numeric(f["entry_idx"], errors="raise").to_numpy(dtype=np.int64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("entry_idx must contain integer bar positions") from exc
    if (starts < 0).any() or (starts >= n_bars).any():
        raise ValueError(f"entry_idx must be in [0, {n_bars - 1}]")
    if not np.equal(pd.to_numeric(f["entry_idx"], errors="raise"), starts).all():
        raise ValueError("entry_idx must contain exact integer values")
    ends = starts + int(horizon_bars) - 1
    if (ends >= n_bars).any():
        raise ValueError("candidate does not have the complete requested holding horizon")

    sides = np.asarray([_side_number(x) for x in f["side"]], dtype=np.float64)
    source_targets = pd.to_numeric(f["target_price"], errors="raise").to_numpy(float)
    if not np.isfinite(source_targets).all() or (source_targets <= 0).any():
        raise ValueError("target_price must be finite and positive")
    slip_rate = float(slippage_bps) / 10000.0
    fee_market = float(fee_market_bps) / 10000.0
    fee_limit = float(fee_limit_bps) / 10000.0

    n_cols = len(f)
    bars = np.empty((n_bars, n_cols, 4), dtype=np.float64)
    ts_matrix = np.empty((n_bars, n_cols), dtype=np.int64)
    for col, row in f.iterrows():
        event_id = row.event_id
        if event_id not in events:
            raise KeyError(f"candidate {row.candidate_id!r} references unknown event_id {event_id!r}")
        frame = events[event_id]
        needed = ("ts_ms", "open", "high", "low", "close")
        missing_frame = [name for name in needed if name not in frame.columns]
        if missing_frame:
            raise ValueError(f"event {event_id!r} missing columns {missing_frame}")
        if len(frame) != n_bars:
            raise ValueError(f"event {event_id!r} must have exactly {n_bars} rows; got {len(frame)}")
        try:
            ts = pd.to_numeric(frame["ts_ms"], errors="raise").to_numpy(dtype=np.int64)
            ohlc = frame[["open", "high", "low", "close"]].to_numpy(dtype=np.float64)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"event {event_id!r} has non-numeric timestamp/OHLC values") from exc
        if not np.equal(pd.to_numeric(frame["ts_ms"], errors="raise"), ts).all():
            raise ValueError(f"event {event_id!r} ts_ms must contain exact integer epoch milliseconds")
        if not np.isfinite(ohlc).all() or (ohlc <= 0).any():
            raise ValueError(f"event {event_id!r} OHLC must be finite and positive")
        if (np.diff(ts) <= 0).any():
            raise ValueError(f"event {event_id!r} ts_ms must be strictly increasing")
        op, hi, lo, close = ohlc.T
        if ((hi < np.maximum.reduce((op, lo, close)))
                | (lo > np.minimum.reduce((op, hi, close)))).any():
            raise ValueError(f"event {event_id!r} contains invalid OHLC ordering")
        bars[:, col, :] = ohlc
        ts_matrix[:, col] = ts

    # Validate the resolved barriers against actual slipped entry prices before
    # entering Numba. A zero-distance target is supported as an immediate limit.
    entry_open = bars[starts, np.arange(n_cols), 0]
    expected_entry = entry_open * (1.0 + sides * slip_rate)
    if tp_mode == "fixed_pct":
        expected_target = expected_entry * (1.0 + sides * float(tp_value))
    else:
        expected_target = expected_entry + (source_targets - expected_entry) * float(tp_value)
    if (expected_entry <= 0).any() or (expected_target <= 0).any():
        raise ValueError("resolved entry and target prices must be positive")
    if tp_value > 0 and (sides * (expected_target - expected_entry) <= 0.0).any():
        raise ValueError("near-side target must be on the favorable side of the actual entry")

    entry_prices = np.full(n_cols, np.nan, dtype=np.float64)
    target_prices = np.full(n_cols, np.nan, dtype=np.float64)
    stop_prices = np.full(n_cols, np.nan, dtype=np.float64)
    exit_prices = np.full(n_cols, np.nan, dtype=np.float64)
    reason_codes = np.zeros(n_cols, dtype=np.int8)
    phase_codes = np.zeros(n_cols, dtype=np.int8)
    active_highs = np.full((n_bars, n_cols), np.nan, dtype=np.float64)
    active_lows = np.full((n_bars, n_cols), np.nan, dtype=np.float64)
    mfe_prices = np.full(n_cols, np.nan, dtype=np.float64)
    mae_prices = np.full(n_cols, np.nan, dtype=np.float64)
    marks = pd.DataFrame(bars[:, :, 3], columns=np.arange(n_cols))
    initial_cash = np.maximum(np.max(bars[:, :, 1], axis=0), np.max(bars[:, :, 0], axis=0)) * 4.0

    # VectorBT drives the one event/column lifecycle. Independent columns avoid
    # cross-candidate cash rejection; the separate account module replays these
    # same actual fills through shared capital.
    portfolio = vbt.Portfolio.from_order_func(
        marks,
        _order_func,
        bars,
        starts.astype(np.int64),
        ends.astype(np.int64),
        sides,
        source_targets,
        0 if tp_mode == "fixed_pct" else 1,
        float(tp_value),
        float(sl_pct),
        slip_rate,
        fee_market,
        fee_limit,
        entry_prices,
        target_prices,
        stop_prices,
        exit_prices,
        reason_codes,
        phase_codes,
        active_highs,
        active_lows,
        mfe_prices,
        mae_prices,
        flexible=True,
        init_cash=initial_cash,
        cash_sharing=False,
        group_by=False,
        max_orders=2 * n_cols,
        freq="5min",
    )

    orders = pd.DataFrame(portfolio.orders.records).sort_values(["col", "id"]).reset_index(drop=True)
    trades = pd.DataFrame(portfolio.trades.records).sort_values("col").reset_index(drop=True)
    if len(orders) != 2 * n_cols or len(trades) != n_cols:
        raise AssertionError("VectorBT must produce one entry and one exit per candidate")
    if not trades.status.eq(1).all() or not orders.groupby("col").size().eq(2).all():
        raise AssertionError("Every candidate must be a closed VectorBT trade with two fills")
    if (reason_codes == 0).any() or not np.isfinite(entry_prices).all() or not np.isfinite(exit_prices).all():
        raise AssertionError("VectorBT did not reach an exit for every candidate")

    trade_rows = []
    path_rows = []
    for col, candidate in f.iterrows():
        event_id = candidate.event_id
        side = int(sides[col])
        entry_fill = orders.iloc[2 * col]
        exit_fill = orders.iloc[2 * col + 1]
        vector_trade = trades.iloc[col]
        entry_idx = int(entry_fill.idx)
        exit_idx = int(exit_fill.idx)
        entry_price = float(entry_fill.price)
        exit_price = float(exit_fill.price)
        entry_fee = float(entry_fill.fees)
        exit_fee = float(exit_fill.fees)
        total_fee = entry_fee + exit_fee
        net_pnl = float(vector_trade.pnl)
        gross_pnl = net_pnl + total_fee
        if entry_idx != int(starts[col]):
            raise AssertionError(f"VectorBT entry row mismatch for candidate {candidate.candidate_id!r}")
        if not np.isclose(gross_pnl, side * (exit_price - entry_price), rtol=1e-9, atol=1e-9):
            raise AssertionError(f"VectorBT cashflow mismatch for candidate {candidate.candidate_id!r}")
        if not np.isclose(total_fee, gross_pnl - net_pnl, rtol=1e-9, atol=1e-9):
            raise AssertionError(f"VectorBT fee reconciliation failed for candidate {candidate.candidate_id!r}")

        reason = _REASON_NAMES[int(reason_codes[col])]
        phase = _PHASE_NAMES[int(phase_codes[col])]
        expected_exit_fee_rate = fee_limit if reason.startswith("take_profit") else fee_market
        if not np.isclose(entry_fee, entry_price * fee_market, rtol=1e-9, atol=1e-9):
            raise AssertionError(f"VectorBT entry fee mismatch for candidate {candidate.candidate_id!r}")
        if not np.isclose(exit_fee, exit_price * expected_exit_fee_rate, rtol=1e-9, atol=1e-9):
            raise AssertionError(f"VectorBT exit fee mismatch for candidate {candidate.candidate_id!r}")
        entry_notional = entry_price
        risk_cash = entry_price * float(sl_pct)
        gross_pct = gross_pnl / entry_notional * 100.0
        fee_pct = total_fee / entry_notional * 100.0
        net_pct = net_pnl / entry_notional * 100.0
        gross_r = gross_pnl / risk_cash
        fee_r = total_fee / risk_cash
        net_r = net_pnl / risk_cash

        bars_for_col = bars[:, col, :]
        ts_for_col = ts_matrix[:, col]
        source = active_highs[:, col]
        live_indices = np.flatnonzero(np.isfinite(source))
        if len(live_indices) == 0 or live_indices[0] != entry_idx or live_indices[-1] != exit_idx:
            raise AssertionError(f"Active VectorBT path does not align with fills for {candidate.candidate_id!r}")
        ambiguous = False
        if reason in ("stop_intrabar", "take_profit_intrabar"):
            op, hi, lo, _ = bars_for_col[exit_idx]
            ambiguous = bool(lo <= min(stop_prices[col], target_prices[col])
                             and hi >= max(stop_prices[col], target_prices[col]))

        best_price = float(mfe_prices[col])
        worst_price = float(mae_prices[col])
        if side > 0:
            mfe_pct = max(0.0, (best_price - entry_price) / entry_price * 100.0)
            mae_pct = max(0.0, (entry_price - worst_price) / entry_price * 100.0)
        else:
            mfe_pct = max(0.0, (entry_price - best_price) / entry_price * 100.0)
            mae_pct = max(0.0, (worst_price - entry_price) / entry_price * 100.0)

        trade_rows.append({
            "candidate_id": candidate.candidate_id,
            "event_id": event_id,
            "symbol": candidate.symbol,
            "side": side,
            "side_label": "lower" if side > 0 else "upper",
            "entry_idx": entry_idx,
            "entry_ms": int(ts_for_col[entry_idx]),
            "entry_price": entry_price,
            "entry_fee_rate": fee_market,
            "entry_fee_bps": float(fee_market_bps),
            "entry_fee": entry_fee,
            "target_price": float(target_prices[col]),
            "near_side_target_price": float(source_targets[col]),
            "stop_price": float(stop_prices[col]),
            "exit_idx": exit_idx,
            "exit_ms": int(ts_for_col[exit_idx]),
            "exit_price": exit_price,
            "exit_fee_rate": expected_exit_fee_rate,
            "exit_fee_bps": float(fee_limit_bps if reason.startswith("take_profit") else fee_market_bps),
            "exit_fee": exit_fee,
            "exit_phase": phase,
            "exit_reason": reason,
            "gross_pnl": gross_pnl,
            "fee_amount": total_fee,
            "net_pnl": net_pnl,
            "gross_pct": gross_pct,
            "fee_pct": fee_pct,
            "net_pct": net_pct,
            "gross_r": gross_r,
            "fee_r": fee_r,
            "net_r": net_r,
            "mfe_price": best_price,
            "mae_price": worst_price,
            "mfe_pct": mfe_pct,
            "mae_pct": mae_pct,
            "ambiguous_stop_target_bar": ambiguous,
            "engine": "VectorBT Portfolio.from_order_func flexible OHLC callback",
            "engine_version": getattr(vbt, "__version__", "unknown"),
        })

        previous_best = entry_price
        previous_worst = entry_price
        for i in live_indices:
            i = int(i)
            eff_hi = float(active_highs[i, col])
            eff_lo = float(active_lows[i, col])
            if side > 0:
                previous_best = max(previous_best, eff_hi)
                previous_worst = min(previous_worst, eff_lo)
                bar_mfe = max(0.0, (previous_best - entry_price) / entry_price * 100.0)
                bar_mae = max(0.0, (entry_price - previous_worst) / entry_price * 100.0)
            else:
                previous_best = min(previous_best, eff_lo)
                previous_worst = max(previous_worst, eff_hi)
                bar_mfe = max(0.0, (entry_price - previous_best) / entry_price * 100.0)
                bar_mae = max(0.0, (previous_worst - entry_price) / entry_price * 100.0)
            is_exit = i == exit_idx
            path_rows.append({
                "candidate_id": candidate.candidate_id,
                "event_id": event_id,
                "symbol": candidate.symbol,
                "bar_idx": i,
                "bar_ms": int(ts_for_col[i]),
                "open": float(bars_for_col[i, 0]),
                "high": float(bars_for_col[i, 1]),
                "low": float(bars_for_col[i, 2]),
                "close": float(bars_for_col[i, 3]),
                "mark_open": float(bars_for_col[i, 0]),
                "mark_close": float(bars_for_col[i, 3]),
                "position_side": side,
                "active_high": eff_hi,
                "active_low": eff_lo,
                "active_mfe_price": previous_best,
                "active_mae_price": previous_worst,
                "active_mfe_pct": bar_mfe,
                "active_mae_pct": bar_mae,
                "entry_bar": i == entry_idx,
                "exit_bar": is_exit,
                "exit_phase": phase if is_exit else "",
                "exit_reason": reason if is_exit else "",
            })

    trade_frame = pd.DataFrame(trade_rows)
    path_frame = pd.DataFrame(path_rows)
    if len(trade_frame) != n_cols:
        raise AssertionError("Trade result cardinality does not match candidates")
    if len(path_frame) == 0 or not path_frame.groupby("candidate_id").exit_bar.sum().eq(1).all():
        raise AssertionError("Every candidate path must contain exactly one exit bar")
    return trade_frame, path_frame


__all__ = ["simulate_batch", "HORIZONS", "TAKER_BPS", "LIMIT_BPS"]
