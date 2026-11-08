"""Native execution adapter for independent Monday 1.618 retest orders.

The public input uses UTC bar-open timestamps. The pinned project adapter
``nautilus_independent.run_native`` consumes close-timestamped bars; shifting
timestamps by one execution step keeps each close callback causal and lets a
resting order first match on the following bar.

The underlying engine is pinned to NautilusTrader 1.231.0. The project adapter
owns its version-specific order/fill wiring. Official execution references:
https://nautilustrader.io/docs/latest/concepts/backtesting/bar-execution/#bar-timestamp-convention
https://nautilustrader.io/docs/latest/concepts/backtesting/execution-flow/
https://nautilustrader.io/docs/latest/concepts/backtesting/apis-and-runs/#streaming-batches-manually
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

_OHLC = ("open", "high", "low", "close")
_TRADE_COLUMNS = [
    "trade_id", "entry_i", "entry_price", "exit_i", "exit_price", "reason",
    "reason_label", "gross_bp", "fee_bp", "net_bp", "signal_entry_i",
    "quantity", "entry_commission", "exit_commission", "reclaim_status",
    "censor_reason",
]
_REASON_LABELS = {
    0: "window_end_censored",
    1: "initial_stop",
    2: "trailing_stop",
    3: "opposite_hour_exit",
    4: "target",
    5: "reclaim_breakeven_stop",
    7: "confirmed_pivot_exit",
    8: "structural_stop",
    9: "stop_update_exit",
    11: "reclaim_failed_market_exit",
    12: "reclaimed_inside_breakeven_market_exit",
    13: "pyramid_ratchet_stop",
    14: "fill_close_market_exit",
}


def _step_minutes(step) -> int:
    if isinstance(step, (int, np.integer)) and not isinstance(step, (bool, np.bool_)):
        minutes = int(step)
    else:
        try:
            delta = pd.Timedelta(step)
        except (TypeError, ValueError) as exc:
            raise ValueError("step must be a positive 1m or 5m interval") from exc
        if delta <= pd.Timedelta(0) or delta.value % pd.Timedelta(minutes=1).value:
            raise ValueError("step must be a positive whole number of minutes")
        minutes = int(delta / pd.Timedelta(minutes=1))
    if minutes not in (1, 5):
        raise ValueError("The pinned native adapter supports 1m or 5m execution bars")
    return minutes


def _whole_index(value, name: str) -> int:
    if isinstance(value, (bool, np.bool_)):
        raise TypeError(f"{name} must be an integer bar index, not bool")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer bar index") from exc
    if not math.isfinite(number) or number != math.floor(number):
        raise ValueError(f"{name} must be an integer bar index")
    return int(number)


def _aligned(values, tick: float) -> bool:
    values = np.asarray(values, dtype=float)
    return bool(np.isfinite(values).all() and
                (np.abs(values / tick - np.rint(values / tick)) <= 1e-5).all())


def _empty_result(audit: dict):
    return pd.DataFrame(columns=_TRADE_COLUMNS), audit


def run_orders(symbol, frame, orders, step, tick):
    """Replay independent limit retests with native OHLC fills and fees.

    Args:
        symbol: Base asset (for example ``"BTC"`` or ``"BTCUSDT"``).
        frame: OHLC DataFrame indexed by UTC bar-open timestamps. Missing bars
            are retained as visible gaps. Prices must already be rounded to
            ``tick``.
        orders: DataFrame with unique ``trade_id`` plus ``side``, ``entry``,
            ``entry_i``, ``expire_i``, ``end_i`` and ``target_price``. ``entry_i``
            is the confirmation-bar index; native resting limits first match
            after that bar closes. Each row is its own hedged position.
        step: Execution interval, as 1 or 5 minutes (integer or Timedelta-like).
        tick: Market price increment.

    Returns:
        ``(trades, audit)``. ``trades`` has one row per completed native
        position. Censored ``reason == 0`` rows have NaN return/fee bp fields.
        Unfilled and pre-gap-censored orders are listed in ``audit``.

    Entry limits and resting targets pay maker 0.02%; market and stop-market
    exits pay taker 0.05%, as selected by the pinned adapter's native fill
    event/order type. Causal reclaim follows the existing adapter's fixed
    ``reclaim_minutes=60`` boundary; a successful reclaim protects at the exact
    entry price. No input is filtered
    using its future execution result.
    """
    step_minutes = _step_minutes(step)
    try:
        tick = float(tick)
    except (TypeError, ValueError) as exc:
        raise ValueError("tick must be a finite positive number") from exc
    if not math.isfinite(tick) or tick <= 0:
        raise ValueError("tick must be a finite positive number")

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    missing_ohlc = set(_OHLC) - set(frame.columns)
    if missing_ohlc:
        raise ValueError(f"frame is missing OHLC columns: {sorted(missing_ohlc)}")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("frame index must be timezone-aware UTC bar-open timestamps")
    if frame.empty:
        raise ValueError("frame must contain at least one OHLC bar")

    utc_naive = frame.index.tz_convert("UTC").tz_localize(None).asi8
    local_naive = frame.index.tz_localize(None).asi8
    if not np.array_equal(utc_naive, local_naive):
        raise ValueError("frame index must be UTC, not a local-time index")
    opens = pd.DatetimeIndex(frame.index.tz_convert("UTC")).as_unit("ns")
    if not opens.is_monotonic_increasing or opens.has_duplicates:
        raise ValueError("bar-open timestamps must be unique and chronological")
    interval_ns = pd.Timedelta(minutes=step_minutes).value
    deltas = np.diff(opens.asi8)
    if len(deltas) and ((deltas <= 0).any() or (deltas % interval_ns != 0).any()):
        raise ValueError("timestamp spacing must be an integer multiple of step")

    bars = frame.loc[:, list(_OHLC)].copy()
    bars.index = opens
    bars = bars.astype(float)
    prices = bars.to_numpy()
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("OHLC prices must be finite and positive")
    if (bars.high < bars[["open", "close"]].max(axis=1)).any() or \
            (bars.low > bars[["open", "close"]].min(axis=1)).any() or \
            (bars.high < bars.low).any():
        raise ValueError("OHLC values do not form valid bars")
    if not _aligned(prices, tick):
        raise ValueError("OHLC must already be rounded to tick")

    if not isinstance(orders, pd.DataFrame):
        raise TypeError("orders must be a pandas DataFrame")
    required = {"trade_id", "side", "entry", "entry_i", "expire_i", "end_i", "target_price"}
    missing_orders = required - set(orders.columns)
    if missing_orders:
        raise ValueError(f"orders is missing required columns: {sorted(missing_orders)}")
    if orders.empty:
        return _empty_result({
            "engine": "nautilus_trader", "orders": 0, "positions": 0, "fills": 0,
            "opportunities": [], "order_status_counts": {}, "gap_count": len(deltas[deltas > interval_ns]),
            "entry_fee_type": "maker", "resting_target_fee_type": "maker",
            "market_exit_fee_type": "taker", "maker_fee": 0.0002, "taker_fee": 0.0005,
        })
    if orders.trade_id.isna().any() or not orders.trade_id.astype(str).is_unique:
        raise ValueError("trade_id must be present and unique")

    tick_tol = max(tick * 1e-7, np.finfo(float).eps * 2)
    normalized = []
    for source in orders.to_dict("records"):
        row = dict(source)
        row["trade_id"] = str(row["trade_id"])
        if not row["trade_id"]:
            raise ValueError("trade_id cannot be empty")
        side = _whole_index(row["side"], "side")
        if side not in (-1, 1):
            raise ValueError("side must be -1 or 1")
        row["side"] = side
        for column in ("entry_i", "expire_i", "end_i"):
            row[column] = _whole_index(row[column], column)
        if not 0 <= row["entry_i"] < len(bars):
            raise ValueError(f"entry_i is outside frame for {row['trade_id']}")
        if not row["entry_i"] <= row["end_i"] < len(bars):
            raise ValueError(f"end_i must be at or after entry_i and inside frame for {row['trade_id']}")
        if row["expire_i"] < 0:
            raise ValueError(f"expire_i cannot be negative for {row['trade_id']}")
        try:
            row["entry"] = float(row["entry"])
            row["target_price"] = float(row["target_price"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"entry and target_price must be numeric for {row['trade_id']}") from exc
        if not np.isfinite([row["entry"], row["target_price"]]).all() or min(row["entry"], row["target_price"]) <= 0:
            raise ValueError(f"entry and target_price must be positive finite prices for {row['trade_id']}")
        if not _aligned([row["entry"], row["target_price"]], tick):
            raise ValueError(f"entry and target_price must lie on tick for {row['trade_id']}")
        if side * (row["target_price"] - row["entry"]) < tick - tick_tol:
            raise ValueError(f"target_price must be favorable to entry for {row['trade_id']}")
        if side * (bars.close.iloc[row["entry_i"]] - row["entry"]) < tick - tick_tol:
            raise ValueError(f"entry limit must rest beyond the confirmation close for {row['trade_id']}")
        normalized.append(row)

    # Native adapter indexes callbacks by bar close. Shifting only timestamps
    # preserves the original order indices and puts OHLC visibility at close.
    native_frame = bars.copy()
    native_frame.index = (native_frame.index + pd.Timedelta(minutes=step_minutes)).as_unit("ns")
    gap_starts = (np.flatnonzero(deltas > interval_ns) + 1).astype(int)
    native_orders = []
    pre_skipped = []
    for row in normalized:
        end_i = int(row["end_i"])
        expire_i = min(int(row["expire_i"]), end_i)
        next_gap_pos = int(np.searchsorted(gap_starts, row["entry_i"] + 1, side="left"))
        next_gap = int(gap_starts[next_gap_pos]) if next_gap_pos < len(gap_starts) else None
        if next_gap is not None and next_gap <= end_i:
            # Positions stop at the first gap even when the pending-entry
            # deadline came earlier; pending orders are clipped only if that
            # same gap arrives before their own expiry.
            end_i = min(end_i, next_gap - 1)
            if next_gap <= expire_i:
                expire_i = min(expire_i, next_gap - 1)
        if end_i <= row["entry_i"] or expire_i <= row["entry_i"]:
            reason = "data_gap_before_first_eligible_bar" if next_gap == row["entry_i"] + 1 else "no_post_confirmation_bar"
            pre_skipped.append({
                "trade_id": row["trade_id"], "status": reason, "submitted_i": row["entry_i"],
                "fill_i": -1, "expire_i": expire_i, "end_i": end_i,
                "gap_start_i": next_gap,
            })
            continue
        native_orders.append({
            "trade_id": row["trade_id"], "side": row["side"], "entry": row["entry"],
            "entry_i": row["entry_i"], "expire_i": expire_i, "end_i": end_i,
            "stop": np.nan, "risk": row["entry"], "reclaim_minutes": 60,
            "target_price": row["target_price"],
        })

    gap_count = len(gap_starts)
    batch_bars = max(2, 1440 // step_minutes)  # One day per native stream chunk.
    base_audit = {
        "engine": "nautilus_trader", "version": "1.231.0", "symbol": str(symbol),
        "step_minutes": step_minutes, "timestamp_input": "UTC bar open; shifted to close for native run",
        "intrabar_path": "OHLC", "target_mode": "one independent resting limit per trade",
        "reclaim_minutes": 60, "reclaim_protection": "exact entry price after successful reclaim",
        "maker_fee": 0.0002, "taker_fee": 0.0005,
        "entry_fee_type": "maker limit", "resting_target_fee_type": "maker limit",
        "stop_market_exit_fee_type": "taker", "market_exit_fee_type": "taker",
        "positions": 0, "fills": 0, "orders": len(normalized),
        "native_orders": len(native_orders), "gap_count": gap_count,
        "batch_bars": batch_bars, "skip_idle_bars": True,
        "pre_skipped_orders": pre_skipped,
    }
    if not native_orders:
        all_opportunities = pre_skipped
        base_audit.update(
            opportunities=all_opportunities,
            order_status_counts={r["status"]: sum(x["status"] == r["status"] for x in all_opportunities)
                                 for r in all_opportunities},
            cancelled=0, expired=0, native_audit=None,
        )
        return _empty_result(base_audit)

    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native

    policy = ExitPolicy(
        trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0.0,
        target_from_entry=True, intrabar_path="OHLC", reclaim_next_bar=True,
        breakeven_includes_cost=False, fill_maker_fee=0.0002, fill_taker_fee=0.0005,
        monotone_segments=True, reclaim_backstop=False,
    )
    base_symbol = str(symbol).upper().removesuffix("-USDT").removesuffix("USDT")
    rows, fills, native_audit = run_native(
        native_frame, np.full(len(native_frame), np.nan), np.zeros(len(native_frame)),
        pd.DataFrame(native_orders), policy, tick=tick, market_tick=tick,
        symbol=base_symbol, execution_minutes=step_minutes, entry_order_mode="limit",
        batch_bars=batch_bars, skip_idle_bars=True,
    )

    trades = []
    if not rows.empty:
        if fills.empty:
            raise RuntimeError("Native positions closed without recorded fills")
        fill_ids = fills.trade_id.astype(str)
        for executed in rows.to_dict("records"):
            trade_id = str(executed["trade_id"])
            trade_fills = fills.loc[fill_ids.eq(trade_id)].copy()
            entry_fills = trade_fills.loc[trade_fills.phase.eq("entry")]
            exit_fills = trade_fills.loc[~trade_fills.phase.eq("entry")]
            if len(entry_fills) != 1 or len(exit_fills) != 1:
                raise RuntimeError(
                    f"Expected one actual entry and one actual exit fill for {trade_id}; "
                    f"got {len(entry_fills)} and {len(exit_fills)}"
                )
            entry_fill, exit_fill = entry_fills.iloc[0], exit_fills.iloc[0]
            entry_price = float(entry_fill.price)
            exit_price = float(exit_fill.price)
            quantity = float(entry_fill.quantity)
            reason = int(executed["reason"])
            entry_commission = float(entry_fill.commission)
            exit_commission = float(exit_fill.commission)
            if abs(entry_price - float(executed["entry"])) > max(tick * 1e-7, 2 * math.ulp(entry_price)):
                raise RuntimeError(f"Native row entry differs from actual entry fill for {trade_id}")
            if abs(exit_price - float(executed["exit_price"])) > max(tick * 1e-7, 2 * math.ulp(exit_price)):
                raise RuntimeError(f"Native row exit differs from actual exit fill for {trade_id}")
            gross_bp = fee_bp = net_bp = float("nan")
            if reason != 0:
                side = int(executed["side"])
                entry_notional = entry_price * quantity
                gross_bp = side * (exit_price - entry_price) / entry_price * 10_000.0
                fee_bp = (entry_commission + exit_commission) / entry_notional * 10_000.0
                net_bp = gross_bp - fee_bp
            trades.append({
                "trade_id": trade_id,
                "entry_i": int(entry_fill.bar_i),
                "entry_price": entry_price,
                "exit_i": int(exit_fill.bar_i),
                "exit_price": exit_price,
                "reason": reason,
                "reason_label": _REASON_LABELS.get(reason, f"native_reason_{reason}"),
                "gross_bp": gross_bp, "fee_bp": fee_bp, "net_bp": net_bp,
                "signal_entry_i": int(executed.get("signal_entry_i", -1)),
                "quantity": quantity,
                "entry_commission": entry_commission,
                "exit_commission": exit_commission,
                "reclaim_status": executed.get("reclaim_status"),
                "censor_reason": executed.get("censor_reason"),
            })

    trade_frame = pd.DataFrame(trades, columns=_TRADE_COLUMNS)
    opportunities = list(native_audit.get("opportunities", [])) + pre_skipped
    statuses = [str(row.get("status", "unknown")) for row in opportunities]
    base_audit.update(
        engine=native_audit.get("engine"), version=native_audit.get("version"),
        positions=native_audit.get("positions", 0), fills=native_audit.get("fills", 0),
        cancelled=native_audit.get("cancelled", 0), expired=native_audit.get("expired", 0),
        opportunities=opportunities,
        order_status_counts={status: statuses.count(status) for status in sorted(set(statuses))},
        native_audit=native_audit,
        accounting="actual fill prices and commissions; independent positions; reason 0 is censored",
        fee_bp_denominator="actual entry fill notional",
    )
    if len(trade_frame) != int(base_audit["positions"]):
        raise RuntimeError("Output trade rows do not match closed native position count")
    if not fills.empty and len(fills) != 2 * len(trade_frame):
        raise RuntimeError("Expected exactly two native fills per independent position")
    return trade_frame, base_audit
