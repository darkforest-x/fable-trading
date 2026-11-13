"""Shared 1x USDT account with per-5m MTM and exchange funding cashflows.

This module consumes fills already produced by ``execution.py``. Each ``pool``
starts from an independent 1000 USDT wallet. It does not inspect future exit
prices when admitting a position. Same-time funding settles first, followed by
exits and then new entries in symbol / trade-id order.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
from typing import Any, Callable

import numpy as np
import pandas as pd


STEP_MS = 5 * 60_000
HOLD_MS = 168 * 60 * 60_000
INITIAL_EQUITY = 1000.0
RISK_FRACTION = 0.01
STOP_FRACTION = 0.05
MAX_NOTIONAL_FRACTION = RISK_FRACTION / STOP_FRACTION
MAX_POSITIONS = 5
FLOOR_FRACTION = 0.70
MAKER_FEE = 0.0002
TAKER_FEE = 0.0005
VALID_REASONS = {"stop", "target", "timeout", "cutoff_mark"}


@dataclass
class Position:
    trade_id: str
    symbol: str
    side: int
    entry_ms: int
    entry_px: float
    stop_px: float
    qty: float
    notional: float
    entry_fee: float
    mark_px: float
    mark_ms: int
    row_id: int
    funding_net: float = 0.0


def _side_sign(value: Any) -> int:
    text = str(value).strip().lower()
    if text in {"long", "buy", "1", "+1"}:
        return 1
    if text in {"short", "sell", "-1"}:
        return -1
    raise ValueError(f"Unrecognized side: {value!r}")


def _validate(trades: pd.DataFrame, bars_by_trade_id: dict[str, np.ndarray]) -> pd.DataFrame:
    required = {
        "trade_id", "pool", "symbol", "side", "entry_ms", "exit_ms", "entry_px",
        "exit_px", "stop_px", "target_px", "exit_reason", "exit_phase", "is_censored",
        "entry_fee_rate", "exit_fee_rate",
    }
    missing = sorted(required - set(trades.columns))
    if missing:
        raise ValueError(f"Native fills missing fields: {missing}")
    out = trades.copy().reset_index(drop=True)
    out["trade_id"] = out.trade_id.astype(str)
    if out.trade_id.duplicated().any():
        raise ValueError("trade_id must be unique within the account input")
    if set(out.trade_id) != set(bars_by_trade_id):
        raise ValueError("Native fill tape ids do not match the OHLC mapping")
    for i, row in out.iterrows():
        side = _side_sign(row.side)
        entry_ms, exit_ms = int(row.entry_ms), int(row.exit_ms)
        entry, exit_px, stop, target = map(float, (row.entry_px, row.exit_px, row.stop_px, row.target_px))
        if (entry_ms % STEP_MS or exit_ms <= entry_ms or not np.isfinite([entry, exit_px, stop, target]).all()
                or min(entry, exit_px, stop, target) <= 0):
            raise ValueError(f"{row.trade_id}: invalid clock or price")
        if side * (entry - stop) <= 0 or side * (target - entry) <= 0:
            raise ValueError(f"{row.trade_id}: invalid stop / target geometry")
        if str(row.exit_reason) not in VALID_REASONS:
            raise ValueError(f"{row.trade_id}: invalid exit reason {row.exit_reason!r}")
        if bool(row.is_censored) != (str(row.exit_reason) == "cutoff_mark"):
            raise ValueError(f"{row.trade_id}: cutoff flag and exit reason disagree")
        bars = np.asarray(bars_by_trade_id[str(row.trade_id)], dtype=np.float64)
        if bars.ndim != 2 or bars.shape[1] != 5 or not len(bars):
            raise ValueError(f"{row.trade_id}: expected nonempty [open_ms,O,H,L,C]")
        if int(bars[0, 0]) != entry_ms or not np.isclose(float(bars[0, 1]), entry, rtol=1e-12, atol=1e-12):
            raise ValueError(f"{row.trade_id}: entry fill does not match its first tape bar")
        out.at[i, "side_sign"] = side
        out.at[i, "entry_ms"] = entry_ms
        out.at[i, "exit_ms"] = exit_ms
    return out


def _mark_value(pos: Position) -> float:
    return pos.qty * pos.side * (pos.mark_px - pos.entry_px)


def _nav(active: dict[str, Position], cash: float) -> float:
    return float(cash + sum(_mark_value(pos) for pos in active.values()))


def _reserved(active: dict[str, Position]) -> float:
    return float(sum(pos.notional for pos in active.values()))


def _all_stopped_nav(active: dict[str, Position], cash: float) -> float:
    value = float(cash)
    for pos in active.values():
        stop_exit = pos.qty * pos.stop_px * TAKER_FEE
        value += pos.qty * pos.side * (pos.stop_px - pos.entry_px) - stop_exit
    return value


def _drawdown(equity: float, peak: float) -> float:
    return 0.0 if peak <= 0 else max(0.0, (peak - equity) / peak)


def _round_settlement_ms(value: float) -> tuple[int, int]:
    if not np.isfinite(value) or value != int(value):
        raise ValueError(f"fundingTime must be finite integer milliseconds: {value!r}")
    original = int(value)
    rounded = int(math.floor(original / STEP_MS + 0.5)) * STEP_MS
    return rounded, original - rounded


def _path_prices(pos: Position, bar: np.ndarray, open_ms: int,
                 exit_ms: int | None, exit_px: float | None, exit_phase: int) -> np.ndarray:
    """Synthetic adverse-first path observed before this trade's terminal fill."""
    order = [1, 3, 2, 4] if pos.side > 0 else [1, 2, 3, 4]
    open_px = float(bar[1])
    if exit_ms == open_ms + STEP_MS and exit_px is not None and exit_phase > 0:
        prefix = [float(bar[k]) for k in order[:min(4, int(exit_phase))]]
        return np.asarray([open_px, *prefix, float(exit_px)], dtype=np.float64)
    return np.asarray([open_px, *[float(bar[k]) for k in order]], dtype=np.float64)


def _simulate_pool(frame: pd.DataFrame, bars_by_trade_id: dict[str, np.ndarray],
                   funding_lookup: Callable[[str, int, int], np.ndarray]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if frame.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {
            "pool": "", "initial_equity_u": INITIAL_EQUITY, "final_equity_u": INITIAL_EQUITY,
            "total_return_pct": 0.0, "candidate_rows": 0, "accepted_entries": 0,
            "rejected_entries": 0, "closed_trades": 0, "cutoff_positions": 0,
            "max_drawdown_5m_close_pct": 0.0, "max_intrabar_dd_envelope_pct": 0.0,
        }
    pool = str(frame.iloc[0].pool)
    if not frame.pool.astype(str).eq(pool).all():
        raise ValueError("_simulate_pool accepts only one independently funded pool")
    rows: list[dict[str, Any]] = []
    for row_id, src in frame.reset_index(drop=True).iterrows():
        d = src.to_dict()
        d.update(_row_id=row_id, account_pool=pool, account_status="pending",
                 account_reject_reason="", account_notional_u=np.nan, account_qty=np.nan,
                 account_entry_fee_u=np.nan, account_exit_fee_u=np.nan,
                 account_gross_pnl_u=np.nan, account_realized_net_u=np.nan,
                 account_funding_u=0.0, account_final_mark_px=np.nan,
                 account_marked_net_u=np.nan, account_entry_nav_u=np.nan)
        rows.append(d)

    entries: dict[int, list[dict[str, Any]]] = defaultdict(list)
    exits: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_id = {str(row["trade_id"]): row for row in rows}
    for row in rows:
        entries[int(row["entry_ms"])].append(row)
        if not bool(row["is_censored"]):
            exits[int(row["exit_ms"])].append(row)

    # Funding data are obtained for the maximum causal holding window. Actual
    # events are applied only while the account position is open.
    funding_events: dict[int, list[dict[str, Any]]] = defaultdict(list)
    funding_audit: list[dict[str, Any]] = []
    for symbol in sorted(frame.symbol.astype(str).unique()):
        rows_s = frame[frame.symbol.astype(str).eq(symbol)]
        start_ms = int(rows_s.entry_ms.min())
        end_ms = int(rows_s.entry_ms.max()) + HOLD_MS
        data = np.asarray(funding_lookup(symbol, start_ms, end_ms), dtype=np.float64)
        if data.size == 0:
            data = np.empty((0, 3), dtype=np.float64)
        if data.ndim != 2 or data.shape[1] != 3 or not np.isfinite(data).all():
            raise ValueError(f"{symbol}: funding lookup must return finite Nx3 [fundingTime,rate,markPrice]")
        if len(data) and ((data[:, 2] <= 0).any() or not np.equal(data[:, 0], np.floor(data[:, 0])).all()):
            raise ValueError(f"{symbol}: invalid funding mark/time")
        if len(data) and (np.diff(data[:, 0]) <= 0).any():
            raise ValueError(f"{symbol}: funding times are not strictly increasing")
        for original_ms, rate, mark_price in data:
            settle_ms, delta_ms = _round_settlement_ms(float(original_ms))
            event = dict(symbol=symbol, funding_time_ms=int(original_ms), settle_ms_5m=settle_ms,
                         rounding_delta_ms=delta_ms, rate=float(rate), mark_price=float(mark_price))
            funding_events[settle_ms].append(event)
            funding_audit.append(event.copy())

    all_open_times = [np.asarray(bars_by_trade_id[str(tid)])[:, 0].astype(np.int64)
                      for tid in frame.trade_id.astype(str)]
    timeline_parts: list[np.ndarray] = [np.concatenate(all_open_times)]
    timeline_parts.append(np.concatenate([x + STEP_MS for x in all_open_times]))
    timeline_parts.append(frame.entry_ms.to_numpy(dtype=np.int64))
    timeline_parts.append(frame.exit_ms.to_numpy(dtype=np.int64))
    if funding_events:
        timeline_parts.append(np.fromiter(funding_events.keys(), dtype=np.int64))
    times = np.unique(np.concatenate(timeline_parts))
    global_cutoff_ms = int(max(int(a[-1]) + STEP_MS for a in all_open_times))

    cash = INITIAL_EQUITY
    active: dict[str, Position] = {}
    active_symbol: dict[str, str] = {}
    settled_gross = 0.0
    settled_net = 0.0
    total_entry_fee = 0.0
    total_exit_fee = 0.0
    total_funding = 0.0
    accepted = rejected = reduced = 0
    max_open = 0
    hwm = INITIAL_EQUITY
    close_peak = INITIAL_EQUITY
    intrabar_peak = INITIAL_EQUITY
    max_dd_close = 0.0
    max_dd_intrabar = 0.0
    rejection_counts: Counter[str] = Counter()
    curve: list[dict[str, Any]] = []
    funding_cashflows: list[dict[str, Any]] = []

    def reject(row: dict[str, Any], reason: str) -> None:
        nonlocal rejected
        row["account_status"] = "rejected"
        row["account_reject_reason"] = reason
        rejection_counts[reason] += 1
        rejected += 1

    def update_dd(equity: float, phase: str, at_ms: int, favorable: float | None = None) -> None:
        nonlocal hwm, close_peak, intrabar_peak, max_dd_close, max_dd_intrabar
        if favorable is not None:
            hwm = max(hwm, float(favorable))
            intrabar_peak = max(intrabar_peak, float(favorable))
            max_dd_intrabar = max(max_dd_intrabar, _drawdown(float(equity), intrabar_peak))
        else:
            close_peak = max(close_peak, float(equity))
            hwm = max(hwm, float(equity))
            intrabar_peak = max(intrabar_peak, float(equity))
            max_dd_close = max(max_dd_close, _drawdown(float(equity), close_peak))
            max_dd_intrabar = max(max_dd_intrabar, _drawdown(float(equity), intrabar_peak))
        curve.append(dict(pool=pool, timestamp_ms=int(at_ms), phase=phase, cash_u=cash,
                          equity_u=_nav(active, cash), unrealized_pnl_u=sum(_mark_value(p) for p in active.values()),
                          open_positions=len(active), reserved_notional_u=_reserved(active),
                          high_water_mark_u=hwm, drawdown_5m_close_pct=max_dd_close * 100.0,
                          drawdown_intrabar_envelope_pct=max_dd_intrabar * 100.0,
                          funding_cashflow_u=0.0))

    for at_ms in times:
        t = int(at_ms)
        funding_total_at_time = 0.0
        # Contract funding rate is applied to the prior position and exchange
        # mark, before any equal-time exits or newly submitted entries.
        for event in funding_events.get(t, []):
            tid = active_symbol.get(str(event["symbol"]))
            if tid is None:
                funding_cashflows.append({**event, "trade_id": "", "qty": 0.0,
                                          "funding_cashflow_u": 0.0, "side": "", "applied": False})
                continue
            pos = active[tid]
            amount = -pos.side * pos.qty * float(event["mark_price"]) * float(event["rate"])
            cash += amount
            pos.funding_net += amount
            by_id[tid]["account_funding_u"] += amount
            total_funding += amount
            funding_total_at_time += amount
            applied = event.copy()
            applied.update(trade_id=tid, qty=pos.qty, funding_cashflow_u=amount,
                           side="long" if pos.side > 0 else "short", applied=True)
            funding_cashflows.append(applied)

        # Funding is settled before all exits at this timestamp, so a position
        # closing at the settlement instant remains eligible for that payment.
        for row in sorted(exits.get(t, []), key=lambda r: (str(r["symbol"]), str(r["trade_id"]))):
            tid = str(row["trade_id"])
            pos = active.get(tid)
            if pos is None:
                continue  # rejected by the shared account
            gross = pos.qty * pos.side * (float(row["exit_px"]) - pos.entry_px)
            exit_rate = float(row["exit_fee_rate"])
            exit_fee = pos.qty * float(row["exit_px"]) * exit_rate
            cash += gross - exit_fee
            total_exit_fee += exit_fee
            settled_gross += gross
            row["account_exit_fee_u"] = exit_fee
            row["account_gross_pnl_u"] = gross
            row["account_realized_net_u"] = gross - pos.entry_fee - exit_fee + pos.funding_net
            row["account_final_mark_px"] = float(row["exit_px"])
            row["account_marked_net_u"] = row["account_realized_net_u"]
            row["account_status"] = "closed"
            row["account_exit_available_ms"] = t
            settled_net += float(row["account_realized_net_u"])
            active.pop(tid)
            active_symbol.pop(pos.symbol, None)

        # A real open print is known after same-time funding and exits. A stale
        # mark is retained only for already-censored, unresolved positions.
        for pos in active.values():
            bars = np.asarray(bars_by_trade_id[pos.trade_id])
            i = int(np.searchsorted(bars[:, 0], t))
            if i < len(bars) and int(bars[i, 0]) == t:
                pos.mark_px = float(bars[i, 1])
                pos.mark_ms = t
        open_equity = _nav(active, cash)
        update_dd(open_equity, "5m_open_after_funding_and_exits", t)

        # Entries never inspect future path results. Equal-time opportunities
        # are admitted deterministically against the same current NAV/collateral.
        for row in sorted(entries.get(t, []), key=lambda r: (str(r["symbol"]), str(r["trade_id"]))):
            symbol = str(row["symbol"])
            if symbol in active_symbol:
                reject(row, "same_symbol_position_open")
                continue
            if len(active) >= MAX_POSITIONS:
                reject(row, "max_positions")
                continue
            nav_now = _nav(active, cash)
            hwm_now = max(hwm, nav_now)
            if not np.isfinite(nav_now) or nav_now <= 0:
                reject(row, "nonpositive_equity")
                continue
            entry = float(row["entry_px"])
            entry_rate = float(row["entry_fee_rate"])
            if not np.isfinite(entry_rate) or not 0 <= entry_rate < 0.1:
                raise ValueError(f"{row['trade_id']}: invalid native entry fee rate {entry_rate}")
            desired = nav_now * MAX_NOTIONAL_FRACTION
            free = max(0.0, nav_now - _reserved(active))
            notional = min(desired, free / (1.0 + entry_rate))
            if notional <= 0:
                reject(row, "insufficient_collateral")
                continue
            qty = notional / entry
            entry_fee = qty * entry * entry_rate
            floor = hwm_now * FLOOR_FRACTION
            stress_before = _all_stopped_nav(active, cash)
            side = int(row["side_sign"])
            stop = float(row["stop_px"])
            stop_fee = qty * stop * TAKER_FEE
            stress_after = stress_before - entry_fee + qty * side * (stop - entry) - stop_fee
            if nav_now <= floor + 1e-9 or stress_after < floor - 1e-9:
                reason = ("at_or_below_70pct_floor" if nav_now <= floor + 1e-9
                          else "fixed_stop_stress_breaches_floor")
                reject(row, reason)
                continue

            cash -= entry_fee
            total_entry_fee += entry_fee
            if notional < desired * (1.0 - 1e-10):
                reduced += 1
                row["account_size_reduction_reason"] = "available_collateral"
            else:
                row["account_size_reduction_reason"] = ""
            pos = Position(
                trade_id=str(row["trade_id"]), symbol=symbol, side=side,
                entry_ms=t, entry_px=entry, stop_px=stop, qty=qty, notional=notional,
                entry_fee=entry_fee, mark_px=entry, mark_ms=t, row_id=int(row["_row_id"]),
            )
            active[pos.trade_id] = pos
            active_symbol[symbol] = pos.trade_id
            accepted += 1
            max_open = max(max_open, len(active))
            row.update(account_notional_u=notional, account_qty=qty,
                       account_entry_fee_u=entry_fee, account_entry_nav_u=nav_now,
                       account_stress_floor_u=floor, account_margin_reserved_u=notional,
                       account_status="accepted_open")

        # Intrabar envelope is the sum of each active position's worst/best
        # observed path point. Cross-symbol extrema need not be synchronous.
        adverse_equity = favorable_equity = cash
        for pos in active.values():
            bars = np.asarray(bars_by_trade_id[pos.trade_id])
            i = int(np.searchsorted(bars[:, 0], t))
            if i < len(bars) and int(bars[i, 0]) == t:
                bar = bars[i]
                row = by_id[pos.trade_id]
                exit_ms = int(row["exit_ms"])
                px = _path_prices(pos, bar, t, exit_ms if exit_ms == t + STEP_MS else None,
                                  float(row["exit_px"]), int(row["exit_phase"]))
                pnl = pos.qty * pos.side * (px - pos.entry_px)
                adverse_equity += float(np.min(pnl))
                favorable_equity += float(np.max(pnl))
                # If a real fill happened inside this candle, do not carry the
                # candle's post-exit close into NAV or the high-water mark.
                intrabar_exit = (not bool(row["is_censored"])
                                 and exit_ms == t + STEP_MS and int(row["exit_phase"]) > 0)
                if not intrabar_exit:
                    pos.mark_px = float(bar[4])
                    pos.mark_ms = t + STEP_MS
            else:
                pnl = _mark_value(pos)
                adverse_equity += pnl
                favorable_equity += pnl
        close_equity = _nav(active, cash)
        # Use the favorable / adverse 5m envelope for the budget high-water and
        # maximum intrabar drawdown; the path is not tick-synchronized cross-asset.
        intrabar_peak = max(intrabar_peak, float(favorable_equity), float(close_equity))
        max_dd_intrabar = max(max_dd_intrabar, _drawdown(float(adverse_equity), intrabar_peak))
        # Intrabar fills occur before the next 5m-boundary funding event. Gap
        # fills (phase 0) remain for the next timestamp so prior-position
        # funding is applied first. A cutoff mark is never settled as a fill.
        for row in sorted(exits.get(t + STEP_MS, []), key=lambda r: (str(r["symbol"]), str(r["trade_id"]))):
            if int(row["exit_phase"]) <= 0:
                continue
            tid = str(row["trade_id"])
            pos = active.get(tid)
            if pos is None:
                continue
            gross = pos.qty * pos.side * (float(row["exit_px"]) - pos.entry_px)
            exit_fee = pos.qty * float(row["exit_px"]) * float(row["exit_fee_rate"])
            cash += gross - exit_fee
            total_exit_fee += exit_fee
            settled_gross += gross
            row["account_exit_fee_u"] = exit_fee
            row["account_gross_pnl_u"] = gross
            row["account_realized_net_u"] = gross - pos.entry_fee - exit_fee + pos.funding_net
            row["account_final_mark_px"] = float(row["exit_px"])
            row["account_marked_net_u"] = row["account_realized_net_u"]
            row["account_status"] = "closed"
            row["account_exit_available_ms"] = t + STEP_MS
            settled_net += float(row["account_realized_net_u"])
            active.pop(tid)
            active_symbol.pop(pos.symbol, None)

        close_equity = _nav(active, cash)
        close_peak = max(close_peak, float(close_equity))
        max_dd_close = max(max_dd_close, _drawdown(float(close_equity), close_peak))
        hwm = max(hwm, float(close_equity))
        intrabar_peak = max(intrabar_peak, float(close_equity))
        max_dd_intrabar = max(max_dd_intrabar, _drawdown(float(close_equity), intrabar_peak))
        curve.append(dict(pool=pool, timestamp_ms=t + STEP_MS, phase="5m_close_before_next_boundary_events",
                          cash_u=cash, equity_u=close_equity,
                          unrealized_pnl_u=sum(_mark_value(p) for p in active.values()),
                          open_positions=len(active), reserved_notional_u=_reserved(active),
                          high_water_mark_u=hwm, drawdown_5m_close_pct=max_dd_close * 100.0,
                          drawdown_intrabar_envelope_pct=max_dd_intrabar * 100.0,
                          funding_cashflow_u=funding_total_at_time))

    # Censored positions are not converted into hypothetical fills. They remain
    # collateral-reserved and carry the latest real mark through the account's
    # common terminal timestamp.
    for pos in active.values():
        row = by_id[pos.trade_id]
        row["account_status"] = "open_marked_at_cutoff"
        row["account_final_mark_px"] = pos.mark_px
        row["account_unrealized_gross_u"] = _mark_value(pos)
        row["account_marked_net_u"] = (_mark_value(pos) - pos.entry_fee + pos.funding_net)
    final_equity = _nav(active, cash)
    accepted_rows = [r for r in rows if r["account_status"] in {"closed", "open_marked_at_cutoff"}]
    closed = [r for r in rows if r["account_status"] == "closed"]
    marked = [r for r in rows if r["account_status"] == "open_marked_at_cutoff"]
    summary = {
        "pool": pool,
        "initial_equity_u": INITIAL_EQUITY,
        "final_equity_u": final_equity,
        "final_cash_u": cash,
        "final_unrealized_pnl_u": sum(_mark_value(p) for p in active.values()),
        "total_return_pct": (final_equity / INITIAL_EQUITY - 1.0) * 100.0,
        "candidate_rows": int(len(frame)),
        "accepted_entries": int(accepted),
        "rejected_entries": int(rejected),
        "closed_trades": int(len(closed)),
        "cutoff_positions": int(len(marked)),
        "reduced_entries": int(reduced),
        "max_open_positions": int(max_open),
        "entry_fees_u": float(total_entry_fee),
        "exit_fees_u": float(total_exit_fee),
        "funding_net_u": float(total_funding),
        "settled_gross_pnl_u": float(settled_gross),
        "settled_net_pnl_u": float(settled_net),
        "max_drawdown_5m_close_pct": float(max_dd_close * 100.0),
        "max_intrabar_dd_envelope_pct": float(max_dd_intrabar * 100.0),
        "high_water_mark_u": float(hwm),
        "budget_floor_fraction": FLOOR_FRACTION,
        "budget_floor_breached_at_5m_close": bool(max_dd_close > 0.30 + 1e-12),
        "funding_records_loaded": int(len(funding_audit)),
        "funding_records_applied": int(sum(bool(r.get("applied")) for r in funding_cashflows)),
        "global_data_cutoff_ms": global_cutoff_ms,
        "rejection_reasons": dict(sorted(rejection_counts.items())),
        "accounting_notes": {
            "sizing": "1% of current marked NAV at the 5% stop, hence 20% target notional; 1x reserved notional, max five symbols",
            "floor_gate": "reject a new position if NAV is at/below 70% of prior 5m-mark high-water or existing positions plus its fixed-stop stress would fall below that floor; no forced liquidation",
            "mtm": "5m open/close marks include unrealized price PnL; account funding enters at each recorded settlement mark",
            "intrabar_drawdown": "adverse/favorable per-position 5m OHLC envelopes can combine different assets' non-simultaneous extrema",
            "cutoff": "a source ending before 168h leaves the position marked and collateral-reserved through the account cutoff; no fictional exit fill or exit fee",
            "fees": "actual native rates are scaled to fractional account quantity: taker entry/stop/timeout, maker target",
            "funding_sign": "positive rate: long pays and short receives; uses funding endpoint mark_price, not candle substitute",
        },
    }
    out_frame = pd.DataFrame(rows)
    curve_frame = pd.DataFrame(curve)
    funding_frame = pd.DataFrame(funding_cashflows)
    return out_frame, curve_frame, funding_frame, summary


def run(trades: pd.DataFrame, bars_by_trade_id: dict[str, np.ndarray],
        funding_lookup: Callable[[str, int, int], np.ndarray]) -> dict[str, pd.DataFrame]:
    """Return one independent shared account for every distinct pool name."""
    valid = _validate(trades, bars_by_trade_id)
    order_rows: list[pd.DataFrame] = []
    curve_rows: list[pd.DataFrame] = []
    funding_rows: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    for _, subset in valid.groupby(valid.pool.astype(str), sort=True):
        subset = subset.sort_values(["entry_ms", "symbol", "trade_id"], kind="stable").reset_index(drop=True)
        orders, curve, funding, summary = _simulate_pool(subset, bars_by_trade_id, funding_lookup)
        order_rows.append(orders)
        curve_rows.append(curve)
        funding_rows.append(funding)
        summaries.append(summary)
    return {
        "trades": pd.concat(order_rows, ignore_index=True) if order_rows else pd.DataFrame(),
        "curve": pd.concat(curve_rows, ignore_index=True) if curve_rows else pd.DataFrame(),
        "funding": pd.concat(funding_rows, ignore_index=True) if funding_rows else pd.DataFrame(),
        "summary": pd.DataFrame(summaries),
    }
