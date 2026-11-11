"""Fund 5m OHLC paths in one shared-cash VectorBT account.

The trade engine owns each position's OHLC exit path. This module only admits
those frozen fills to a shared wallet, sizes them from current NAV, and marks
the resulting account on timestamps where an admitted position is active.
The mark matrix is therefore bounded by active-path timestamps, not by every
symbol's full 5m history.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

import numpy as np
import pandas as pd
from numba import njit
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import NoOrder

ROOT = Path(__file__).resolve().parents[3]
INITIAL_CASH = 1_000.0
MAX_SLOTS = 10
PER_TRADE_FRACTION = 0.10
TAKER_FEE_RATE = 0.0005
MAKER_FEE_RATE = 0.0002
BAR_MS = 5 * 60_000
PERIOD_START_MS = pd.Timestamp("2022-02-01T00:00:00Z").value // 1_000_000
SPLIT_MS = pd.Timestamp("2025-01-01T00:00:00Z").value // 1_000_000
PERIOD_END_MS = pd.Timestamp("2026-11-01T00:00:00Z").value // 1_000_000
PERIODS = {
    "early": (PERIOD_START_MS, SPLIT_MS),
    "late": (SPLIT_MS, PERIOD_END_MS),
    "full": (PERIOD_START_MS, PERIOD_END_MS),
}

TRADE_COLUMNS = (
    "candidate_id", "event_id", "symbol", "side", "entry_ms", "entry_price",
    "exit_ms", "exit_price", "exit_phase",
)
MARK_COLUMNS = (
    "candidate_id", "symbol", "bar_ms", "open", "close", "active_high", "active_low",
)


@njit(cache=True)
def _cash_safe_account_order(
    c, nav, actions, prices, fees, market_slip, exposure, reservation,
):
    """Submit full VectorBT orders with the existing shared-wallet sizing.

    ``free_cash_now`` can exceed ``cash_now`` by a few float ulps after a
    position releases collateral. Bound the next reservation by both values;
    VectorBT still validates every fill with ``raise_reject=True``.
    """
    action = actions[c.i, c.col]
    if action == 0:
        return NoOrder
    fee = fees[c.i, c.col]
    slip = market_slip if fee == TAKER_FEE_RATE else 0.0
    price = prices[c.i, c.col]
    if action == 2:
        if c.position_now == 0.0:
            return NoOrder
        return nb.order_nb(
            size=-c.position_now,
            price=price,
            fees=fee,
            slippage=slip,
            lock_cash=True,
            allow_partial=False,
            raise_reject=True,
        )
    if c.position_now != 0.0:
        raise ValueError("Position collision")

    free_slots = 0
    for col in range(c.from_col, c.to_col):
        if c.last_position[col] == 0.0:
            free_slots += 1
    if free_slots < 1:
        raise ValueError("No free slot for an admitted entry")
    capacity = c.group_len
    target_budget = nav * exposure / capacity
    available_free_cash = c.free_cash_now / free_slots
    available_cash = max(0.0, c.cash_now) / free_slots
    reservation[0] = min(target_budget, available_free_cash, available_cash)

    budget = reservation[0]
    if budget < 1.0e-8:
        return NoOrder
    size = action * (budget * (1.0 - 1.0e-10) / (price * (1.0 + slip) * (1.0 + fee)))
    return nb.order_nb(
        size=size,
        price=price,
        fees=fee,
        slippage=slip,
        lock_cash=True,
        allow_partial=False,
        raise_reject=True,
    )


def _epoch_ms(values: pd.Series, name: str) -> np.ndarray:
    """Convert timestamps or integer milliseconds to a validated int64 array."""
    if pd.api.types.is_datetime64_any_dtype(values.dtype):
        out = pd.to_datetime(values, utc=True).astype("int64").to_numpy() // 1_000_000
    else:
        numeric = pd.to_numeric(values, errors="coerce")
        if numeric.isna().any() or not np.isfinite(numeric.to_numpy(dtype=float)).all():
            raise ValueError(f"{name} contains a missing or non-finite timestamp")
        out = numeric.to_numpy(dtype=np.int64)
    return out


def _period_bounds(period: str, start_ms: int | None, end_ms: int | None) -> tuple[int, int]:
    if period not in PERIODS:
        raise ValueError(f"period must be one of {tuple(PERIODS)}, got {period!r}")
    default_start, default_end = PERIODS[period]
    start = default_start if start_ms is None else int(start_ms)
    end = default_end if end_ms is None else int(end_ms)
    if start >= end:
        raise ValueError("period start must precede the exclusive period end")
    return start, end


def _fee_rates(trades: pd.DataFrame, side: str) -> np.ndarray:
    rate_col = f"{side}_fee_rate"
    bps_col = f"{side}_fee_bps"
    if rate_col in trades:
        result = pd.to_numeric(trades[rate_col], errors="coerce").to_numpy(dtype=float)
    elif bps_col in trades:
        result = pd.to_numeric(trades[bps_col], errors="coerce").to_numpy(dtype=float) / 10_000.0
    else:
        default = MAKER_FEE_RATE if side == "exit" else TAKER_FEE_RATE
        result = np.full(len(trades), default, dtype=float)
    if not np.isfinite(result).all() or (result < 0).any():
        raise ValueError(f"Invalid {side} fee rate")
    return result


def _validate_inputs(trades: pd.DataFrame, mark_path: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    missing = sorted(set(TRADE_COLUMNS) - set(trades.columns))
    if missing:
        raise ValueError(f"Trade path is missing columns: {missing}")
    missing = sorted(set(MARK_COLUMNS) - set(mark_path.columns))
    if missing:
        raise ValueError(f"Mark path is missing columns: {missing}")

    fills = trades.copy()
    marks = mark_path.copy()
    if fills["candidate_id"].isna().any() or not fills["candidate_id"].is_unique:
        raise ValueError("candidate_id must uniquely identify each position path")
    if marks["candidate_id"].isna().any():
        raise ValueError("mark path contains a missing candidate_id")
    if marks.duplicated(["candidate_id", "bar_ms"]).any():
        raise ValueError("mark path has duplicate candidate/bar rows")

    fills["entry_ms"] = _epoch_ms(fills["entry_ms"], "entry_ms")
    fills["exit_ms"] = _epoch_ms(fills["exit_ms"], "exit_ms")
    marks["bar_ms"] = _epoch_ms(marks["bar_ms"], "bar_ms")
    if ((fills.entry_ms % BAR_MS) != 0).any() or ((fills.exit_ms % BAR_MS) != 0).any():
        raise ValueError("Trade timestamps must be 5m bar-open epochs")
    if (marks.bar_ms % BAR_MS != 0).any():
        raise ValueError("Mark timestamps must be 5m bar-open epochs")
    for column in ("side", "entry_price", "exit_price"):
        fills[column] = pd.to_numeric(fills[column], errors="coerce")
    for column in ("open", "close", "active_high", "active_low"):
        marks[column] = pd.to_numeric(marks[column], errors="coerce")

    side = fills["side"].to_numpy(dtype=float)
    if not np.isin(side, (-1.0, 1.0)).all():
        raise ValueError("side must be +1 for long or -1 for short")
    if ((fills["entry_price"] <= 0) | (fills["exit_price"] <= 0)).any():
        raise ValueError("Entry and exit prices must be positive")
    if (fills["exit_ms"] < fills["entry_ms"]).any():
        raise ValueError("An exit precedes its entry")
    if not fills["exit_phase"].isin(("open", "intrabar", "close")).all():
        raise ValueError("exit_phase must be open, intrabar, or close")
    if ((fills["exit_ms"] == fills["entry_ms"]) & fills["exit_phase"].eq("open")).any():
        raise ValueError("An open exit cannot precede an entry at that same bar open")
    if not np.isfinite(fills[["entry_price", "exit_price"]].to_numpy(dtype=float)).all():
        raise ValueError("Entry/exit prices must be finite")
    if not np.isfinite(marks[["open", "close", "active_high", "active_low"]].to_numpy(dtype=float)).all():
        raise ValueError("Mark OHLC values must be finite")
    if ((marks[["open", "close", "active_high", "active_low"]] <= 0).any().any()
            or (marks["active_high"] < marks["active_low"]).any()):
        raise ValueError("Invalid active mark prices")

    fills["entry_fee_rate"] = _fee_rates(fills, "entry")
    fills["exit_fee_rate"] = _fee_rates(fills, "exit")
    if not fills["symbol"].map(lambda value: isinstance(value, str) and bool(value)).all():
        raise ValueError("symbol must be a non-empty string")

    # Exact entry and exit marks are needed to verify timing and close the
    # VectorBT orders. Marks after the engine's resolved exit are never used.
    ids = set(fills["candidate_id"])
    marks = marks[marks.candidate_id.isin(ids)].copy()
    if set(marks.candidate_id) != ids:
        missing_ids = ids - set(marks.candidate_id)
        raise ValueError(f"Trade paths have no marks: {list(missing_ids)[:5]}")
    fill_keys = fills.set_index("candidate_id")
    grouped = marks.groupby("candidate_id", sort=False)
    for candidate_id, fill in fill_keys.iterrows():
        path = grouped.get_group(candidate_id)
        if not path.symbol.eq(fill.symbol).all():
            raise ValueError(f"Mark symbol differs from trade symbol: {candidate_id}")
        clock = path.bar_ms.to_numpy(dtype=np.int64)
        if not np.all(np.diff(clock) > 0):
            raise ValueError(f"Unordered mark timestamps: {candidate_id}")
        if clock[0] != fill.entry_ms or clock[-1] != fill.exit_ms:
            raise ValueError(f"Mark path does not span entry through exit: {candidate_id}")
        # Engine entry_price may already include adverse market slippage, while
        # mark-path open is the raw observed OHLC price. Both must share a bar
        # timestamp, but their prices intentionally need not match.
    return fills, marks


def _admit(fills: pd.DataFrame, max_slots: int) -> tuple[pd.DataFrame, dict[str, int]]:
    """Assign fixed slots chronologically; simultaneous congestion sorts by symbol."""
    if max_slots < 1:
        raise ValueError("max_slots must be positive")
    ordered = fills.copy()
    ordered["source_row"] = np.arange(len(ordered), dtype=np.int64)
    ordered = ordered.sort_values(
        ["entry_ms", "symbol", "event_id", "candidate_id"], kind="mergesort"
    )
    exit_ms = np.full(max_slots, -1, dtype=np.int64)
    exit_phase = np.full(max_slots, "", dtype=object)
    symbols = np.full(max_slots, "", dtype=object)
    chosen: list[tuple[int, int]] = []
    skipped = {"skipped_same_symbol": 0, "skipped_capacity": 0}

    for row in ordered.itertuples(index=False):
        occupied = (exit_ms > row.entry_ms) | (
            (exit_ms == row.entry_ms) & (exit_phase != "open")
        )
        same_symbol = (exit_ms >= row.entry_ms) & (symbols == row.symbol)
        if same_symbol.any():
            skipped["skipped_same_symbol"] += 1
            continue
        free_slots = np.flatnonzero(~occupied)
        if not len(free_slots):
            skipped["skipped_capacity"] += 1
            continue
        slot = int(free_slots[0])
        source_row = int(row.source_row)
        chosen.append((source_row, slot))
        exit_ms[slot] = int(row.exit_ms)
        exit_phase[slot] = row.exit_phase
        symbols[slot] = row.symbol

    if not chosen:
        return fills.iloc[0:0].assign(slot=pd.Series(dtype="int64")), skipped
    source_rows, slot_ids = zip(*chosen)
    selected = fills.iloc[list(source_rows)].copy()
    selected["slot"] = np.asarray(slot_ids, dtype=np.int64)
    return selected, skipped


def _fill_forward_marks(values: np.ndarray) -> None:
    """Carry the last known mark through other positions' sparse timestamps."""
    indices = np.arange(values.shape[0], dtype=np.int64)
    for column in range(values.shape[1]):
        series = values[:, column]
        last = np.where(np.isfinite(series), indices, -1)
        np.maximum.accumulate(last, out=last)
        missing = last < 0
        series[missing] = 1.0
        present = ~missing
        series[present] = series[last[present]]


def _build_vectorbt_data(
    selected: pd.DataFrame,
    mark_path: pd.DataFrame,
    max_slots: int,
    *,
    initial_cash: float,
    per_trade_fraction: float,
    slippage_bps: float,
) -> dict:
    """Create three-stage, active-timestamp-only VectorBT input arrays."""
    marks_selected = mark_path[mark_path.candidate_id.isin(selected.candidate_id)].copy()
    times = np.sort(marks_selected.bar_ms.unique().astype(np.int64))
    if len(times) == 0:
        raise ValueError("No active mark timestamps for admitted positions")
    time_ix = np.searchsorted(times, marks_selected.bar_ms.to_numpy(dtype=np.int64))
    slots = selected.set_index("candidate_id").slot
    cols = marks_selected.candidate_id.map(slots).to_numpy(dtype=np.int64)
    if (cols < 0).any() or (cols >= max_slots).any():
        raise ValueError("Invalid assigned slot in mark path")

    n_rows = len(times) * 3
    mark_prices = np.full((n_rows, max_slots), np.nan, dtype=np.float64)
    actions = np.zeros((n_rows, max_slots), dtype=np.int8)
    order_prices = np.ones((n_rows, max_slots), dtype=np.float64)
    fees = np.zeros((n_rows, max_slots), dtype=np.float64)
    high_delta = np.zeros((len(times), max_slots), dtype=np.float64)
    low_delta = np.zeros((len(times), max_slots), dtype=np.float64)

    # Stage 0: open mark and old-position open exits.
    # Stage 1: open mark and entries after those open exits.
    # Stage 2: close mark and intrabar/close exits.
    trade_meta = selected.set_index("candidate_id")
    path_candidate = marks_selected.candidate_id.to_numpy()
    path_time = marks_selected.bar_ms.to_numpy(dtype=np.int64)
    path_entry = trade_meta.loc[path_candidate, "entry_ms"].to_numpy(dtype=np.int64)
    path_exit = trade_meta.loc[path_candidate, "exit_ms"].to_numpy(dtype=np.int64)
    path_exit_phase = trade_meta.loc[path_candidate, "exit_phase"].to_numpy()
    path_open = marks_selected.open.to_numpy(dtype=float)
    path_close = marks_selected.close.to_numpy(dtype=float)
    stage0_active = (path_entry < path_time) & (path_exit >= path_time)
    stage1_active = (path_entry <= path_time) & (
        (path_exit > path_time) | ((path_exit == path_time) & (path_exit_phase != "open"))
    )
    stage2_active = (path_entry <= path_time) & (path_exit > path_time)

    for stage, active, price in (
        (0, stage0_active, path_open),
        (1, stage1_active, path_open),
        (2, stage2_active, path_close),
    ):
        row_ix = time_ix[active] * 3 + stage
        col_ix = cols[active]
        if pd.MultiIndex.from_arrays((row_ix, col_ix)).has_duplicates:
            raise ValueError(f"Two positions occupy the same slot at VectorBT stage {stage}")
        mark_prices[row_ix, col_ix] = price[active]

    # The intrabar envelope is evaluated after open exits and entries, while
    # each selected position is still active; each event row carries only the
    # extrema observed before its resolved exit.
    if pd.MultiIndex.from_arrays((time_ix[stage1_active], cols[stage1_active])).has_duplicates:
        raise ValueError("Overlapping active paths share a slot during a 5m bar")
    high_delta[time_ix[stage1_active], cols[stage1_active]] = (
        marks_selected.active_high.to_numpy(dtype=float)[stage1_active]
        - path_open[stage1_active]
    )
    low_delta[time_ix[stage1_active], cols[stage1_active]] = (
        marks_selected.active_low.to_numpy(dtype=float)[stage1_active]
        - path_open[stage1_active]
    )

    fill_time_ix = np.searchsorted(times, selected.entry_ms.to_numpy(dtype=np.int64))
    exit_time_ix = np.searchsorted(times, selected.exit_ms.to_numpy(dtype=np.int64))
    if (fill_time_ix >= len(times)).any() or (exit_time_ix >= len(times)).any():
        raise ValueError("Entry or exit timestamp was omitted from active marks")
    if not np.array_equal(times[fill_time_ix], selected.entry_ms.to_numpy(dtype=np.int64)):
        raise ValueError("Entry timestamp was omitted from active marks")
    if not np.array_equal(times[exit_time_ix], selected.exit_ms.to_numpy(dtype=np.int64)):
        raise ValueError("Exit timestamp was omitted from active marks")
    slot_ix = selected.slot.to_numpy(dtype=np.int64)
    entry_rows = fill_time_ix * 3 + 1
    exit_stage = np.where(selected.exit_phase.eq("open"), 0, 2)
    exit_rows = exit_time_ix * 3 + exit_stage
    if pd.MultiIndex.from_arrays((entry_rows, slot_ix)).has_duplicates:
        raise ValueError("Duplicate entry action in one slot/stage")
    if pd.MultiIndex.from_arrays((exit_rows, slot_ix)).has_duplicates:
        raise ValueError("Duplicate exit action in one slot/stage")
    if (actions[entry_rows, slot_ix] != 0).any() or (actions[exit_rows, slot_ix] != 0).any():
        raise ValueError("Conflicting account actions in one slot/stage")
    actions[entry_rows, slot_ix] = selected.side.to_numpy(dtype=np.int8)
    order_prices[entry_rows, slot_ix] = selected.entry_price.to_numpy(dtype=float)
    fees[entry_rows, slot_ix] = selected.entry_fee_rate.to_numpy(dtype=float)
    actions[exit_rows, slot_ix] = 2
    order_prices[exit_rows, slot_ix] = selected.exit_price.to_numpy(dtype=float)
    fees[exit_rows, slot_ix] = selected.exit_fee_rate.to_numpy(dtype=float)

    _fill_forward_marks(mark_prices)
    return {
        "selected": selected,
        "times": times,
        "marks": mark_prices,
        "prices": order_prices,
        "fees": fees,
        "actions": actions,
        "high": high_delta,
        "low": low_delta,
        "initial_cash": float(initial_cash),
        "per_trade_fraction": float(per_trade_fraction),
        "exposure": float(per_trade_fraction) * max_slots,
        "slippage": float(slippage_bps) / 10_000.0,
    }


def _run_vectorbt(data: dict, max_slots: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the shared VectorBT wallet callback with sparse 5m marks."""
    import vectorbt as vbt

    from yoyo.evaluation import cloud_close_reward_account as wallet

    actions = data["actions"]
    portfolio = vbt.Portfolio.from_order_func(
        data["marks"],
        _cash_safe_account_order,
        actions,
        data["prices"],
        data["fees"],
        data["slippage"],
        data["exposure"],
        np.zeros(1, dtype=np.float64),
        pre_segment_func_nb=wallet.pre_segment,
        pre_segment_args=(),
        cash_sharing=True,
        group_by=True,
        init_cash=data["initial_cash"],
        max_orders=int(np.count_nonzero(actions)) + 1,
        fill_pos_record=False,
    )

    selected = data["selected"].copy()
    times = data["times"]
    entries_at = np.searchsorted(times, selected.entry_ms.to_numpy(dtype=np.int64)) * 3 + 1
    exits_at = np.searchsorted(times, selected.exit_ms.to_numpy(dtype=np.int64)) * 3 + np.where(
        selected.exit_phase.eq("open"), 0, 2
    )
    keys = portfolio.orders.records.set_index(["idx", "col"])
    entry_rows = keys.reindex(pd.MultiIndex.from_arrays((entries_at, selected.slot)))
    exit_rows = keys.reindex(pd.MultiIndex.from_arrays((exits_at, selected.slot)))
    have_entries = entry_rows["size"].notna().to_numpy()
    have_exits = exit_rows["size"].notna().to_numpy()
    if not have_entries.all() or not have_exits.all():
        raise ValueError("A selected VectorBT position did not receive both fills")
    if not np.allclose(entry_rows["size"], exit_rows["size"], rtol=1e-10, atol=1e-12):
        raise ValueError("VectorBT entry and exit sizes do not match")
    expected_entry_side = np.where(selected.side.to_numpy() > 0, 0, 1)
    expected_exit_side = 1 - expected_entry_side
    if not np.array_equal(entry_rows["side"].to_numpy(), expected_entry_side):
        raise ValueError("VectorBT entry direction differs from the frozen trade paths")
    if not np.array_equal(exit_rows["side"].to_numpy(), expected_exit_side):
        raise ValueError("VectorBT exit direction differs from the frozen trade paths")

    quantity = entry_rows["size"].to_numpy(dtype=float)
    entry_price = entry_rows["price"].to_numpy(dtype=float)
    exit_price = exit_rows["price"].to_numpy(dtype=float)
    entry_fees = entry_rows["fees"].to_numpy(dtype=float)
    exit_fees = exit_rows["fees"].to_numpy(dtype=float)
    selected["quantity"] = quantity
    selected["entry_fill"] = entry_price
    selected["exit_fill"] = exit_price
    selected["entry_fee_paid"] = entry_fees
    selected["exit_fee_paid"] = exit_fees
    selected["notional"] = quantity * entry_price
    selected["gross_pnl"] = selected.side.to_numpy(dtype=float) * (
        exit_price - entry_price
    ) * quantity
    selected["cash_pnl"] = selected.gross_pnl - entry_fees - exit_fees
    selected["net_return_pct"] = 100.0 * selected.cash_pnl / selected.notional

    orders = portfolio.orders.records
    cash_from_orders = data["initial_cash"] + float(
        (
            np.where(orders.side.eq(0), -1.0, 1.0) * orders["size"] * orders.price
            - orders.fees
        ).sum()
    )
    cash_from_fills = data["initial_cash"] + float(selected.cash_pnl.sum())
    values = np.asarray(portfolio.value(), dtype=float).reshape(-1, 3)
    if (
        not np.isclose(cash_from_orders, cash_from_fills, rtol=1e-10, atol=1e-6)
        or not np.isclose(values[-1, 2], cash_from_orders, rtol=1e-10, atol=0.005)
    ):
        raise ValueError(
            "VectorBT ending NAV does not reconcile to its executed orders and funded fills"
        )

    assets = np.asarray(portfolio.assets(), dtype=float).reshape(-1, 3, max_slots)
    quantity_after_entries = assets[:, 1, :]
    mark_after_entries = data["marks"].reshape(-1, 3, max_slots)[:, 1, :]
    gross_exposure = (np.abs(quantity_after_entries) * mark_after_entries).sum(axis=1)
    exposure_ratio = gross_exposure / np.maximum(values[:, 1], 1e-12)
    favorable = np.where(quantity_after_entries >= 0, data["high"], data["low"])
    adverse = np.where(quantity_after_entries >= 0, data["low"], data["high"])
    upper = np.maximum(
        values.max(axis=1),
        values[:, 1] + (quantity_after_entries * favorable).sum(axis=1),
    )
    lower = np.minimum(
        values.min(axis=1),
        values[:, 1] + (quantity_after_entries * adverse).sum(axis=1),
    )
    times_utc = pd.to_datetime(times + BAR_MS, unit="ms", utc=True)
    equity = pd.DataFrame(
        {
            "bar_open_ms": times,
            "open_exit_nav": values[:, 0],
            "open_entry_nav": values[:, 1],
            "nav": values[:, 2],
            "gross_notional": gross_exposure,
            "gross_exposure_pct": exposure_ratio * 100.0,
            "lower_nav": lower,
            "upper_nav": upper,
        },
        index=pd.DatetimeIndex(times_utc, name="bar_close_utc"),
    )
    equity["close_drawdown_pct"] = 100.0 * (
        equity.nav / np.maximum.accumulate(np.r_[data["initial_cash"], equity.nav.to_numpy()])[1:]
        - 1.0
    )
    return selected, equity


def _max_consecutive_losses(funded: pd.DataFrame) -> int:
    if funded.empty:
        return 0
    ordered = funded.sort_values(["exit_ms", "symbol", "candidate_id"], kind="mergesort")
    longest = current = 0
    for pnl in ordered.cash_pnl.to_numpy(dtype=float):
        if pnl < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return int(longest)


def _mfe_stats(funded: pd.DataFrame, mark_path: pd.DataFrame) -> tuple[float | None, float | None]:
    if funded.empty or "active_mfe_price" not in mark_path:
        return None, None
    marks = mark_path[mark_path.candidate_id.isin(funded.candidate_id)]
    if marks.empty:
        return None, None
    meta = funded.set_index("candidate_id")
    prices = pd.to_numeric(marks.active_mfe_price, errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(prices)
    if not valid.any():
        return None, None
    ids = marks.candidate_id.to_numpy()[valid]
    entry = meta.loc[ids, "entry_fill"].to_numpy(dtype=float)
    side = meta.loc[ids, "side"].to_numpy(dtype=float)
    qty = meta.loc[ids, "quantity"].to_numpy(dtype=float)
    moves = side * (prices[valid] - entry)
    mfe_pct = np.maximum(0.0, 100.0 * moves / entry)
    mfe_usdt = np.maximum(0.0, moves * qty)
    return float(mfe_pct.max()), float(mfe_usdt.max())


def _summarize(
    funded: pd.DataFrame,
    equity: pd.DataFrame,
    mark_path: pd.DataFrame,
    *,
    period: str,
    start_ms: int,
    end_ms: int,
    initial_cash: float,
    admission: dict[str, int],
    outside_period: int,
) -> dict:
    pnl = funded.cash_pnl.to_numpy(dtype=float) if len(funded) else np.empty(0)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    profit_factor = (
        float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else None
    )
    if equity.empty:
        final_equity = float(initial_cash)
        close_dd = 0.0
        intrabar_dd = 0.0
    else:
        final_equity = float(equity.nav.iloc[-1])
        close_curve = np.r_[initial_cash, equity.nav.to_numpy(dtype=float)]
        close_dd = float(100.0 * (1.0 - np.min(close_curve / np.maximum.accumulate(close_curve))))
        bound_low = np.r_[initial_cash, equity.lower_nav.to_numpy(dtype=float)]
        bound_high = np.r_[initial_cash, equity.upper_nav.to_numpy(dtype=float)]
        intrabar_dd = float(
            100.0 * (1.0 - np.min(bound_low / np.maximum.accumulate(bound_high)))
        )
    duration_years = (end_ms - start_ms) / (365.25 * 24 * 60 * 60 * 1000)
    net_return = 100.0 * (final_equity / initial_cash - 1.0)
    mfe_pct, mfe_usdt = _mfe_stats(funded, mark_path)
    return_to_close_dd = net_return / close_dd if close_dd > 0 else None
    return_to_intrabar_dd = net_return / intrabar_dd if intrabar_dd > 0 else None
    return {
        "period": period,
        "trades_funded": int(len(funded)),
        "win_rate_pct": float(100.0 * len(wins) / len(funded)) if len(funded) else None,
        "net_return_pct": float(net_return),
        "final_equity": final_equity,
        "max_dd_close_pct": close_dd,
        "max_dd_intrabar_pct": intrabar_dd,
        "max_gross_exposure_pct": (
            float(equity.gross_exposure_pct.max()) if "gross_exposure_pct" in equity and len(equity) else 0.0
        ),
        "profit_factor": profit_factor,
        "return_to_close_dd": return_to_close_dd,
        "return_to_intrabar_dd": return_to_intrabar_dd,
        "trade_frequency_per_year": float(len(funded) / duration_years),
        "max_consecutive_losses": _max_consecutive_losses(funded),
        "max_unrealized_profit_pct": mfe_pct,
        "max_unrealized_profit_usdt": mfe_usdt,
        "trades_outside_period": int(outside_period),
        "skipped_same_symbol": int(admission["skipped_same_symbol"]),
        "skipped_capacity": int(admission["skipped_capacity"]),
        "initial_cash": float(initial_cash),
        "period_start_ms": int(start_ms),
        "period_end_ms": int(end_ms),
    }


def run_account(
    trades_df: pd.DataFrame,
    mark_path_df: pd.DataFrame,
    *,
    period: str = "full",
    period_start_ms: int | None = None,
    period_end_ms: int | None = None,
    initial_cash: float = INITIAL_CASH,
    max_slots: int = MAX_SLOTS,
    per_trade_fraction: float = PER_TRADE_FRACTION,
    slippage_bps: float = 0.0,
) -> dict:
    """Run one strategy's already-resolved trades through a shared VectorBT wallet.

    ``candidate_id`` is a unique position key, not a strategy/configuration
    label. Pass all events for a single configuration together. The caller may
    pass only an early/late fill subset; each call starts from ``initial_cash``.
    Period endpoints are exclusive. A path crossing either endpoint is purged.

    Returns ``{"funded_trades": ..., "equity_5m": ..., "summary": ...}``.
    """
    if initial_cash <= 0 or not np.isfinite(initial_cash):
        raise ValueError("initial_cash must be positive and finite")
    if not 0 < per_trade_fraction <= 1 or not np.isfinite(per_trade_fraction):
        raise ValueError("per_trade_fraction must lie in (0, 1]")
    if slippage_bps < 0 or not np.isfinite(slippage_bps):
        raise ValueError("slippage_bps must be nonnegative and finite")
    start_ms, end_ms = _period_bounds(period, period_start_ms, period_end_ms)
    fills, marks = _validate_inputs(trades_df, mark_path_df)

    in_period = (
        (fills.entry_ms >= start_ms)
        & (fills.entry_ms < end_ms)
        & (fills.exit_ms >= start_ms)
        & (fills.exit_ms < end_ms)
    )
    outside_period = int((~in_period).sum())
    fills = fills.loc[in_period].copy()
    if fills.empty:
        admission = {"skipped_same_symbol": 0, "skipped_capacity": 0}
        empty_funded = fills.assign(slot=pd.Series(dtype="int64"))
        equity = pd.DataFrame(
            columns=["bar_open_ms", "open_exit_nav", "open_entry_nav", "nav", "gross_notional",
                     "gross_exposure_pct", "lower_nav", "upper_nav"]
        )
        summary = _summarize(
            empty_funded, equity, marks, period=period, start_ms=start_ms, end_ms=end_ms,
            initial_cash=initial_cash, admission=admission, outside_period=outside_period,
        )
        return {"funded_trades": empty_funded, "equity_5m": equity, "summary": summary}

    marks = marks[marks.candidate_id.isin(fills.candidate_id)].copy()
    selected, admission = _admit(fills, max_slots)
    if selected.empty:
        empty_funded = selected.copy()
        equity = pd.DataFrame(
            columns=["bar_open_ms", "open_exit_nav", "open_entry_nav", "nav", "gross_notional",
                     "gross_exposure_pct", "lower_nav", "upper_nav"]
        )
    else:
        data = _build_vectorbt_data(
            selected,
            marks,
            max_slots,
            initial_cash=initial_cash,
            per_trade_fraction=per_trade_fraction,
            slippage_bps=slippage_bps,
        )
        funded, equity = _run_vectorbt(data, max_slots)
        empty_funded = funded

    summary = _summarize(
        empty_funded,
        equity,
        marks,
        period=period,
        start_ms=start_ms,
        end_ms=end_ms,
        initial_cash=initial_cash,
        admission=admission,
        outside_period=outside_period,
    )
    return {"funded_trades": empty_funded, "equity_5m": equity, "summary": summary}


def run_batch(
    cases: Mapping[str, tuple[pd.DataFrame, pd.DataFrame]]
    | Iterable[tuple[str, pd.DataFrame, pd.DataFrame]],
    *,
    period: str = "early",
    period_start_ms: int | None = None,
    period_end_ms: int | None = None,
    initial_cash: float = INITIAL_CASH,
    max_slots: int = MAX_SLOTS,
    per_trade_fraction: float = PER_TRADE_FRACTION,
    slippage_bps: float = 0.0,
) -> pd.DataFrame:
    """Run strategy cases sequentially and return only compact summary rows.

    ``cases`` is a mapping from strategy label to ``(trades, mark_path)`` or
    an iterator yielding ``(label, trades, mark_path)``. Inputs can be yielded
    lazily to avoid retaining many strategies' 5m paths in memory.
    """
    if isinstance(cases, Mapping):
        entries = ((label, pair[0], pair[1]) for label, pair in cases.items())
    else:
        entries = iter(cases)
    summaries = []
    for strategy_id, trades, mark_path in entries:
        result = run_account(
            trades,
            mark_path,
            period=period,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
            initial_cash=initial_cash,
            max_slots=max_slots,
            per_trade_fraction=per_trade_fraction,
            slippage_bps=slippage_bps,
        )
        summaries.append({"strategy_id": strategy_id, **result["summary"]})
    return pd.DataFrame(summaries)
