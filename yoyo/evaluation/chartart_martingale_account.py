"""Causal cash-account replay for an already-produced ChartArt trade stream.

The module is deliberately independent of signal generation.  It consumes an
OHLC frame only to mark an accepted trade between its supplied entry and exit
bars.  It is a research boundary, not an exchange liquidation calculation:
zero equity is an optimistic zero-maintenance-margin stop, not an OKX
liquidation price.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


_TRADE_COLUMNS = {
    "entry_i", "exit_i", "side", "entry_price", "exit_price", "censored",
}
_OHLC_COLUMNS = ("open", "high", "low", "close")


def _number(value: Any, name: str, *, positive: bool = False,
            nonnegative: bool = False) -> float:
    """Return one finite numeric input, rejecting booleans and NaN values."""
    if isinstance(value, bool):
        raise ValueError("%s must be a finite number" % name)
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("%s must be a finite number" % name) from exc
    if not math.isfinite(out):
        raise ValueError("%s must be a finite number" % name)
    if positive and out <= 0:
        raise ValueError("%s must be positive" % name)
    if nonnegative and out < 0:
        raise ValueError("%s must be nonnegative" % name)
    return out


def _integer(value: Any, name: str) -> int:
    """Return an exact integer position; bar offsets cannot be rounded."""
    if isinstance(value, bool):
        raise ValueError("%s must be an integer" % name)
    try:
        out = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("%s must be an integer" % name) from exc
    if out != value:
        raise ValueError("%s must be an integer" % name)
    return out


def _settings(multiplier: Any, initial_cash: Any, base_notional: Any,
              max_leverage: Any, cost: Any) -> Tuple[float, float, float, float, float]:
    """Validate the account contract before any trade outcome is inspected."""
    return (
        _number(multiplier, "multiplier", positive=True),
        _number(initial_cash, "initial_cash", nonnegative=True),
        _number(base_notional, "base_notional", positive=True),
        _number(max_leverage, "max_leverage", positive=True),
        _number(cost, "cost", nonnegative=True),
    )


def _validated_inputs(frame: pd.DataFrame, trades: pd.DataFrame) -> List[Dict[str, Any]]:
    """Fail closed on malformed bars, overlapping positions, or censored tails."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("frame must be a non-empty DataFrame")
    missing_ohlc = set(_OHLC_COLUMNS) - set(frame.columns)
    if missing_ohlc:
        raise ValueError("frame is missing OHLC columns: %s" % ", ".join(sorted(missing_ohlc)))
    if not frame.index.is_monotonic_increasing or not frame.index.is_unique:
        raise ValueError("frame index must be unique and sorted")
    for column in _OHLC_COLUMNS:
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.isna().any() or not values.map(math.isfinite).all() or (values <= 0).any():
            raise ValueError("frame %s must contain finite positive prices" % column)

    if not isinstance(trades, pd.DataFrame):
        raise ValueError("trades must be a DataFrame")
    missing_trade = _TRADE_COLUMNS - set(trades.columns)
    if missing_trade:
        raise ValueError("trades is missing required columns: %s" % ", ".join(sorted(missing_trade)))

    rows: List[Dict[str, Any]] = []
    previous_entry = -1
    previous_exit = -1
    for position, (_, raw) in enumerate(trades.iterrows()):
        entry_i = _integer(raw["entry_i"], "trade %d entry_i" % position)
        exit_i = _integer(raw["exit_i"], "trade %d exit_i" % position)
        if not 0 <= entry_i < len(frame) or not 0 <= exit_i < len(frame):
            raise ValueError("trade %d bar position is outside frame" % position)
        if exit_i < entry_i:
            raise ValueError("trade %d exits before entry" % position)
        if entry_i < previous_entry:
            raise ValueError("trades must be sorted by entry_i")
        if entry_i < previous_exit:
            raise ValueError("trades overlap; only an exit/entry at the same open is allowed")
        if entry_i == previous_entry:
            raise ValueError("only one trade may enter at a given bar")
        side = _integer(raw["side"], "trade %d side" % position)
        if side not in (-1, 1):
            raise ValueError("trade %d side must be 1 or -1" % position)
        censored = raw["censored"]
        if not isinstance(censored, bool):
            raise ValueError("trade %d censored must be a boolean" % position)
        if censored and (position != len(trades) - 1 or exit_i != len(frame) - 1):
            raise ValueError("a censored trade must be the final trade at the final bar")
        entry_price = _number(raw["entry_price"], "trade %d entry_price" % position, positive=True)
        exit_price = None if censored else _number(
            raw["exit_price"], "trade %d exit_price" % position, positive=True
        )
        rows.append({
            # Keep strategy-owned identifiers and annotations intact in the
            # sized output while the account layer consumes only its contract.
            **raw.to_dict(), "source_index": trades.index[position],
            "entry_i": entry_i, "exit_i": exit_i,
            "side": side, "entry_price": entry_price, "exit_price": exit_price,
            "censored": censored,
        })
        previous_entry, previous_exit = entry_i, exit_i
    return rows


def _bar_path(row: pd.Series) -> List[float]:
    """Return TradingView's deterministic nearest-extreme intrabar path."""
    open_, high, low, close = (float(row[name]) for name in _OHLC_COLUMNS)
    if abs(high - open_) <= abs(low - open_):
        return [open_, high, low, close]
    return [open_, low, high, close]


def simulate_account(frame: pd.DataFrame, trades: pd.DataFrame, *, multiplier: float = 1.,
                     initial_cash: float = 1000., base_notional: float = 100.,
                     max_leverage: float = 1., cost: float = .002
                     ) -> Tuple[Dict[str, Any], pd.DataFrame, pd.DataFrame]:
    """Replay a finite ChartArt account without consulting strategy outcomes at entry.

    A closed negative *net* PnL advances one multiplier level, a positive net
    PnL resets it, and zero leaves it unchanged.  Entry requires the requested
    initial margin and entry half of the fixed round-trip cost in free cash.
    The other half is reserved in mark-to-market equity.  At a shared exit and
    entry bar the old position settles before the new one is admitted.

    ``summary`` includes ``ending_equity``, ``net_profit``, maximum intrabar
    drawdown (amount/fraction), requested/executed multipliers, maximum
    notional, the loss-run count, and closed recovery-cycle diagnostics.
    ``sized_trades`` keeps every supplied candidate and its terminal status;
    ``curve`` has one marked equity record per frame bar.
    """
    factor, starting_cash, base, leverage, cost_rate = _settings(
        multiplier, initial_cash, base_notional, max_leverage, cost
    )
    candidates = _validated_inputs(frame, trades)

    free_cash = starting_cash
    loss_streak = 0
    max_loss_streak = 0
    cycle_active = False
    cycle_value = 0.0
    closed_cycles: List[float] = []
    peak_equity = starting_cash
    max_dd_amount = 0.0
    max_dd_fraction = 0.0
    max_notional = 0.0
    max_requested_multiplier = 0.0
    max_executed_multiplier = 0.0
    halted_reason: Optional[str] = None
    active: Optional[Dict[str, Any]] = None
    sized: List[Dict[str, Any]] = []
    curve_rows: List[Dict[str, Any]] = []

    def current_equity(price: Optional[float] = None) -> float:
        if active is None:
            return free_cash
        mark = active["entry_price"] if price is None else price
        return (free_cash + active["margin"]
                + active["side"] * active["qty"] * (mark - active["entry_price"])
                - active["exit_fee"])

    def observe(equity: float) -> None:
        """Update peak-to-trough drawdown using every known mark, not just closes."""
        nonlocal peak_equity, max_dd_amount, max_dd_fraction
        peak_equity = max(peak_equity, equity)
        amount = peak_equity - equity
        max_dd_amount = max(max_dd_amount, amount)
        if peak_equity > 0:
            max_dd_fraction = max(max_dd_fraction, amount / peak_equity)

    def bankrupt() -> None:
        """Make zero equity absorbing; this is not a claimed exchange liquidation."""
        nonlocal free_cash, active, halted_reason
        free_cash = 0.0
        active = None
        halted_reason = "zero_equity"
        observe(0.0)

    def mark_to(price: float, previous_price: float, bar_i: int) -> bool:
        """Mark a held position and stop at an interpolated zero-equity crossing."""
        if active is None:
            return False
        previous_equity = current_equity(previous_price)
        equity = current_equity(price)
        if previous_equity > 0 and equity <= 0:
            # Mark PnL is affine in price, so this is the exact crossing under
            # the requested bar-path interpolation.
            if equity == previous_equity:
                zero_price = price
            else:
                zero_price = previous_price + (price - previous_price) * (
                    -previous_equity / (equity - previous_equity)
                )
            record = sized[active["sized_index"]]
            gross = active["side"] * active["qty"] * (zero_price - active["entry_price"])
            record.update({
                "status": "stopped_zero_equity", "closed": False,
                "zero_equity_price": zero_price, "zero_equity_bar_i": bar_i,
                "exit_price_used": zero_price, "gross_pnl": gross,
                "net_pnl": gross - active["entry_fee"] - active["exit_fee"],
                "cash_after_exit": 0.0,
            })
            bankrupt()
            return True
        observe(equity)
        return False

    def close_active(exit_price: float, bar_i: int) -> bool:
        """Settle an open candidate and update only realized sizing state."""
        nonlocal free_cash, active, loss_streak, max_loss_streak
        nonlocal cycle_active, cycle_value
        assert active is not None
        record = sized[active["sized_index"]]
        previous = active.get("last_mark_price", active["entry_price"])
        if mark_to(exit_price, previous, bar_i):
            return True
        gross = active["side"] * active["qty"] * (exit_price - active["entry_price"])
        net = gross - active["entry_fee"] - active["exit_fee"]
        free_cash += active["margin"] + gross - active["exit_fee"]
        record.update({
            "status": "closed", "closed": True, "exit_price_used": exit_price,
            "gross_pnl": gross, "net_pnl": net, "cash_after_exit": free_cash,
        })
        if net < 0:
            loss_streak += 1
            max_loss_streak = max(max_loss_streak, loss_streak)
            cycle_active = True
            cycle_value += net
        elif net > 0:
            if cycle_active:
                cycle_value += net
                closed_cycles.append(cycle_value)
                cycle_active, cycle_value = False, 0.0
            loss_streak = 0
        # A breakeven leaves both the martingale state and an open cycle intact.
        record["loss_streak_after"] = loss_streak
        active = None
        observe(free_cash)
        return False

    def append_unprocessed(from_index: int, reason: str) -> None:
        """Preserve every later candidate without pretending it was evaluated."""
        for candidate in candidates[from_index:]:
            sized.append({
                **candidate, "accepted": False, "closed": False, "status": reason,
                "requested_multiplier": math.nan, "executed_multiplier": math.nan,
                "requested_notional": math.nan, "notional": math.nan, "qty": math.nan,
                "initial_margin": math.nan, "entry_fee": math.nan, "exit_fee": math.nan,
                "gross_pnl": math.nan, "net_pnl": math.nan, "cash_before_entry": math.nan,
                "cash_after_entry": math.nan, "cash_after_exit": math.nan,
                "exit_price_used": math.nan, "loss_streak_before": math.nan,
                "loss_streak_after": math.nan,
            })

    next_candidate = 0
    # A source frame can include years of indicator warm-up before the first
    # candidate.  Those identical flat marks contain no account information,
    # so retain original bar offsets while starting the exported curve at entry.
    first_bar = candidates[0]["entry_i"] if candidates else 0
    for bar_i, (_, bar) in enumerate(frame.iloc[first_bar:].iterrows(), start=first_bar):
        bar_equity_start = current_equity(active["last_mark_price"] if active is not None else None)
        bar_low = bar_high = bar_equity_start

        if halted_reason is not None:
            curve_rows.append({
                "bar_i": bar_i, "equity": free_cash, "cash": free_cash, "margin": 0.0,
                "intrabar_equity_low": free_cash, "intrabar_equity_high": free_cash,
                "status": "stopped_%s" % halted_reason,
            })
            continue

        # The trade stream's natural exits occur at this bar's open.  Settle
        # before examining any OHLC extreme, so an already exited position is
        # never exposed to a later wick.  This also orders a same-open reversal
        # correctly: realized net PnL chooses the next requested size.
        if active is not None and not active["censored"] and active["exit_i"] == bar_i:
            if close_active(active["exit_price"], bar_i):
                append_unprocessed(next_candidate, "stopped_zero_equity")
                next_candidate = len(candidates)
            else:
                bar_low, bar_high = min(bar_low, free_cash), max(bar_high, free_cash)

        if halted_reason is None and active is None and next_candidate < len(candidates) and candidates[next_candidate]["entry_i"] == bar_i:
            candidate = candidates[next_candidate]
            requested_multiplier = factor ** loss_streak
            requested_notional = base * requested_multiplier
            if not math.isfinite(requested_notional):
                raise ValueError("requested notional must remain finite")
            max_requested_multiplier = max(max_requested_multiplier, requested_multiplier)
            margin = requested_notional / leverage
            entry_fee = cost_rate * requested_notional / 2.0
            exit_fee = entry_fee
            row = {
                **candidate, "accepted": False, "closed": False,
                "requested_multiplier": requested_multiplier, "executed_multiplier": math.nan,
                "requested_notional": requested_notional, "notional": math.nan, "qty": math.nan,
                "initial_margin": margin, "entry_fee": entry_fee, "exit_fee": exit_fee,
                "gross_pnl": math.nan, "net_pnl": math.nan, "cash_before_entry": free_cash,
                "cash_after_entry": math.nan, "cash_after_exit": math.nan,
                "exit_price_used": math.nan, "loss_streak_before": loss_streak,
                "loss_streak_after": math.nan,
            }
            next_candidate += 1
            if free_cash - margin - entry_fee < 0:
                row["status"] = "stopped_insufficient_margin"
                sized.append(row)
                halted_reason = "insufficient_margin"
                append_unprocessed(next_candidate, "stopped_insufficient_margin")
                next_candidate = len(candidates)
            else:
                qty = requested_notional / candidate["entry_price"]
                free_cash -= margin + entry_fee
                row.update({
                    "accepted": True, "status": "censored" if candidate["censored"] else "open",
                    "executed_multiplier": requested_multiplier, "notional": requested_notional,
                    "qty": qty, "cash_after_entry": free_cash,
                })
                sized.append(row)
                active = {**candidate, "margin": margin, "entry_fee": entry_fee, "exit_fee": exit_fee,
                          "qty": qty, "sized_index": len(sized) - 1,
                          "last_mark_price": candidate["entry_price"]}
                max_executed_multiplier = max(max_executed_multiplier, requested_multiplier)
                max_notional = max(max_notional, requested_notional)
                entry_equity = current_equity(candidate["entry_price"])
                if entry_equity <= 0:
                    sized[-1].update({
                        "status": "stopped_zero_equity", "closed": False,
                        "zero_equity_price": candidate["entry_price"], "zero_equity_bar_i": bar_i,
                        "exit_price_used": candidate["entry_price"], "gross_pnl": 0.0,
                        "net_pnl": -entry_fee - exit_fee, "cash_after_exit": 0.0,
                    })
                    bankrupt()
                    append_unprocessed(next_candidate, "stopped_zero_equity")
                    next_candidate = len(candidates)
                else:
                    observe(entry_equity)

        # A zero-bar holding is still a natural open exit.  It is uncommon in
        # source streams but has a well-defined causal order and must not be
        # carried through the bar merely because it was admitted above.
        if active is not None and not active["censored"] and active["exit_i"] == bar_i and halted_reason is None:
            if close_active(active["exit_price"], bar_i):
                append_unprocessed(next_candidate, "stopped_zero_equity")
                next_candidate = len(candidates)
            else:
                bar_low, bar_high = min(bar_low, free_cash), max(bar_high, free_cash)

        if active is not None and halted_reason is None:
            path = _bar_path(bar)
            previous_price = active["last_mark_price"]
            for price in path:
                if mark_to(price, previous_price, bar_i):
                    record = sized[active["sized_index"]] if active is not None else None
                    # bankrupt clears active, so use the current open record via
                    # the latest accepted row if needed.
                    if record is not None:
                        record["status"] = "stopped_zero_equity"
                    elif sized:
                        sized[-1]["status"] = "stopped_zero_equity"
                    append_unprocessed(next_candidate, "stopped_zero_equity")
                    next_candidate = len(candidates)
                    break
                previous_price = price
                active["last_mark_price"] = price
                marked = current_equity(price)
                bar_low, bar_high = min(bar_low, marked), max(bar_high, marked)

        if active is not None and active["censored"] and bar_i == len(frame) - 1 and halted_reason is None:
            mark = float(bar["close"])
            previous_price = active["last_mark_price"]
            if not mark_to(mark, previous_price, bar_i):
                record = sized[active["sized_index"]]
                gross = active["side"] * active["qty"] * (mark - active["entry_price"])
                record.update({"status": "censored", "closed": False, "exit_price_used": mark,
                               "gross_pnl": gross, "net_pnl": gross - active["entry_fee"] - active["exit_fee"]})

        equity = current_equity(active.get("last_mark_price") if active is not None else None)
        if halted_reason == "zero_equity":
            equity, bar_low, bar_high = 0.0, min(bar_low, 0.0), max(bar_high, 0.0)
        curve_rows.append({
            "bar_i": bar_i, "equity": equity, "cash": free_cash,
            "margin": 0.0 if active is None else active["margin"],
            "intrabar_equity_low": bar_low, "intrabar_equity_high": bar_high,
            "status": "stopped_%s" % halted_reason if halted_reason else ("open" if active else "flat"),
        })

    if next_candidate < len(candidates):
        # This is reachable only for malformed streams that never arrived on a
        # valid bar; validation above should already rule those out.
        append_unprocessed(next_candidate, "not_reached")
    ending_equity = current_equity(active.get("last_mark_price") if active is not None else None)
    if halted_reason == "zero_equity":
        ending_equity = 0.0
    sized_trades = pd.DataFrame(sized)
    curve = pd.DataFrame(curve_rows, index=frame.index[first_bar:].copy())
    curve.index.name = frame.index.name
    closed = sized_trades.loc[sized_trades.get("closed", pd.Series(dtype=bool)).eq(True)] if len(sized_trades) else sized_trades
    closed_wins = int((closed["net_pnl"] > 0).sum()) if len(closed) else 0
    negative_cycles = [value for value in closed_cycles if value < 0]
    summary: Dict[str, Any] = {
        "initial_cash": starting_cash, "ending_equity": ending_equity,
        "final_equity": ending_equity, "net_profit": ending_equity - starting_cash,
        "max_drawdown_amount": max_dd_amount, "max_drawdown_fraction": max_dd_fraction,
        "max_drawdown_pct": max_dd_fraction * 100.0,
        "max_executed_multiplier": max_executed_multiplier,
        "max_requested_multiplier": max_requested_multiplier, "max_notional": max_notional,
        "max_consecutive_net_losses": max_loss_streak, "cycle_net": closed_cycles,
        "negative_closed_cycles": len(negative_cycles), "closed_trades": len(closed),
        "censored_trades": int((sized_trades.get("status", pd.Series(dtype=str)) == "censored").sum()),
        "closed_wins": closed_wins,
        "closed_win_rate": None if not len(closed) else closed_wins / len(closed),
        "stopped_insufficient_margin": halted_reason == "insufficient_margin",
        "stopped_zero_equity": halted_reason == "zero_equity", "stopped_reason": halted_reason,
    }
    return summary, sized_trades, curve
