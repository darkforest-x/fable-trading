"""A causal, finite-cash simulator for an already frozen SPIKE trade stream.

This module deliberately knows nothing about candles, signal selection, or a
live exchange.  It sizes each non-overlapping candidate using only the
realized cash balance and fields available at entry: ``initial_risk_frac`` and
the current martingale level.  ``net_return`` and ``exit_reason`` are consumed
only after an accepted position exits.  The 20 bp round-trip cost is reserved
at entry for admission safety, but is *not* charged again: input
``net_return`` is already net of that cost.

Balances and drawdown are realized-cash accounting only.  This is not an
exchange liquidation model: it has no mark prices, intra-position drawdown,
funding, spread/slippage beyond the supplied return, or tiered maintenance
margin.  Consequently it intentionally provides no liquidation probability.
"""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any


ROUND_TRIP_COST_RATE = 0.002
_REQUIRED_TRADE_FIELDS = {
    "trade_id",
    "entry_time",
    "exit_time",
    "net_return",
    "initial_risk_frac",
    "exit_reason",
}


def _finite_number(value: Any, name: str, *, positive: bool = False,
                   nonnegative: bool = False) -> float:
    """Return a finite float or fail closed on an invalid accounting input."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    if positive and number <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and number < 0:
        raise ValueError(f"{name} must be nonnegative")
    return number


def _parse_time(value: Any, name: str) -> datetime:
    """Parse one ISO timestamp without silently inventing an ordering."""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO timestamp string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO timestamp string") from exc


def _settings(initial_balance: Any, base_risk_usdt: Any, multiplier: Any,
              max_level: Any, reset_mode: Any, leverage_cap: Any) -> tuple[float, float, float, int, str, float]:
    balance = _finite_number(initial_balance, "initial_balance", nonnegative=True)
    risk = _finite_number(base_risk_usdt, "base_risk_usdt", positive=True)
    factor = _finite_number(multiplier, "multiplier", positive=True)
    cap = _finite_number(leverage_cap, "leverage_cap", positive=True)
    if isinstance(max_level, bool) or not isinstance(max_level, int) or max_level < 0:
        raise ValueError("max_level must be a nonnegative integer")
    if reset_mode not in {"win", "recovery"}:
        raise ValueError("reset_mode must be 'win' or 'recovery'")
    # Fail early instead of letting an extreme but nominally finite setting turn
    # into an infinite requested position several candidates later.
    try:
        maximum_risk = risk * factor ** max_level
    except OverflowError as exc:
        raise ValueError("martingale risk schedule must remain finite") from exc
    if not math.isfinite(maximum_risk):
        raise ValueError("martingale risk schedule must remain finite")
    return balance, risk, factor, max_level, reset_mode, cap


def _validated_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and sort the immutable, single-position stream before replay."""
    if not isinstance(trades, list):
        raise ValueError("trades must be a list of dictionaries")
    rows: list[dict[str, Any]] = []
    aware: bool | None = None
    for index, trade in enumerate(trades):
        if not isinstance(trade, dict):
            raise ValueError(f"trade {index} must be a dictionary")
        missing = _REQUIRED_TRADE_FIELDS - trade.keys()
        if missing:
            raise ValueError(f"trade {index} is missing required fields: {', '.join(sorted(missing))}")
        if not isinstance(trade["trade_id"], str):
            raise ValueError(f"trade {index} trade_id must be a string")
        if not isinstance(trade["exit_reason"], str):
            raise ValueError(f"trade {index} exit_reason must be a string")
        entry = _parse_time(trade["entry_time"], f"trade {index} entry_time")
        exit_ = _parse_time(trade["exit_time"], f"trade {index} exit_time")
        is_aware = entry.tzinfo is not None and entry.utcoffset() is not None
        exit_aware = exit_.tzinfo is not None and exit_.utcoffset() is not None
        if is_aware != exit_aware:
            raise ValueError(f"trade {index} entry_time and exit_time must both be timezone-aware or both be naive")
        if aware is None:
            aware = is_aware
        elif aware != is_aware:
            raise ValueError("all trade timestamps must use the same timezone convention")
        if exit_ < entry:
            raise ValueError(f"trade {index} exit_time precedes entry_time")
        rows.append({
            "trade_id": trade["trade_id"],
            "entry_time": entry,
            "exit_time": exit_,
            # These fields are deliberately not used until after entry has been admitted.
            "net_return": _finite_number(trade["net_return"], f"trade {index} net_return"),
            "initial_risk_frac": _finite_number(
                trade["initial_risk_frac"], f"trade {index} initial_risk_frac", positive=True
            ),
            "exit_reason": trade["exit_reason"],
            "input_index": index,
        })
    rows.sort(key=lambda row: (row["entry_time"], row["input_index"]))
    previous_exit: datetime | None = None
    for row in rows:
        if previous_exit is not None and row["entry_time"] < previous_exit:
            raise ValueError("trades overlap; the frozen stream must have one position at a time")
        previous_exit = row["exit_time"]
    return rows


def _drawdown(balance: float, peak: float) -> float:
    return 0.0 if peak <= 0 else (peak - balance) / peak


def simulate(trades: list[dict[str, Any]], initial_balance: float = 1000,
             base_risk_usdt: float = 10, multiplier: float = 2,
             max_level: int = 3, reset_mode: str = "win", leverage_cap: float = 10,
             trigger: str = "losing_stop", record_ledger: bool = True) -> dict[str, Any]:
    """Replay a frozen non-overlapping trade stream through a finite cash account.

    A candidate requests ``base_risk_usdt * multiplier**level`` at entry.
    Its notional is that requested cash risk divided by ``initial_risk_frac``;
    it is accepted only when both the capped initial margin plus a conservative
    20 bp fee reserve and the requested risk plus that reserve fit in realized
    cash.  Rejected candidates never inspect their outcome and leave both cash
    and level unchanged.  The sole supported trigger is ``losing_stop``: a
    negative ``net_return`` whose exit reason contains ``stop``.  It therefore
    covers initial, trailing and gap stops without pretending that every loss
    was an initial stop.

    ``reset_mode='win'`` resets to level zero after any positive return.
    ``reset_mode='recovery'`` resets only once accumulated realized PnL for the
    current cycle has recovered to zero or above.  At the configured maximum
    level, another triggering loss realizes its PnL and resets the cycle and
    level; it never erases the loss.  ``multiplier=1, max_level=0`` is a fixed
    cash-risk baseline.
    """
    if trigger != "losing_stop":
        raise ValueError("only trigger='losing_stop' is supported")
    if not isinstance(record_ledger, bool):
        raise ValueError("record_ledger must be a boolean")
    balance, base_risk, factor, max_level, reset_mode, leverage_cap = _settings(
        initial_balance, base_risk_usdt, multiplier, max_level, reset_mode, leverage_cap
    )
    rows = _validated_trades(trades)

    initial = balance
    peak = balance
    minimum = balance
    maximum = balance
    max_drawdown = 0.0
    level = 0
    cycle_pnl = 0.0
    cycle_trades = 0
    accepted = 0
    rejected = 0
    wins = 0
    losses = 0
    capped_cycle_resets = 0
    max_requested_level = 0
    max_executed_level = 0
    max_executed_notional = 0.0
    max_effective_leverage = 0.0
    ledger: list[dict[str, Any]] = []

    for trade in rows:
        requested_level = level
        max_requested_level = max(max_requested_level, requested_level)
        planned_risk = base_risk * factor ** requested_level
        notional = planned_risk / trade["initial_risk_frac"]
        if not math.isfinite(notional):
            raise ValueError("requested notional must remain finite")
        fee_reserve = ROUND_TRIP_COST_RATE * notional
        equity_before = balance
        margin_required = notional / leverage_cap
        admission_cost = max(margin_required + fee_reserve, planned_risk + fee_reserve)

        if balance <= 0:
            rejected += 1
            if record_ledger:
                ledger.append({
                    "trade_id": trade["trade_id"], "entry_time": trade["entry_time"].isoformat(),
                    "level": requested_level, "planned_risk": planned_risk, "notional": notional,
                    "equity_before": equity_before, "equity_after": balance,
                    "accepted": False, "reason": "ruined", "pnl": None,
                })
            continue
        if admission_cost > balance:
            rejected += 1
            if record_ledger:
                ledger.append({
                    "trade_id": trade["trade_id"], "entry_time": trade["entry_time"].isoformat(),
                    "level": requested_level, "planned_risk": planned_risk, "notional": notional,
                    "equity_before": equity_before, "equity_after": balance,
                    "accepted": False, "reason": "insufficient_balance", "pnl": None,
                })
            continue

        # No outcome field has participated in the admission branch above.
        pnl = notional * trade["net_return"]
        if not math.isfinite(pnl):
            raise ValueError("realized pnl must remain finite")
        balance = max(0.0, balance + pnl)
        accepted += 1
        wins += int(trade["net_return"] > 0)
        losses += int(trade["net_return"] < 0)
        max_executed_level = max(max_executed_level, requested_level)
        max_executed_notional = max(max_executed_notional, notional)
        max_effective_leverage = max(max_effective_leverage, notional / equity_before)
        minimum = min(minimum, balance)
        maximum = max(maximum, balance)
        peak = max(peak, balance)
        max_drawdown = max(max_drawdown, _drawdown(balance, peak))

        cycle_pnl += pnl
        cycle_trades += 1
        cycle_pnl_before_reset = cycle_pnl
        triggering_loss = trade["net_return"] < 0 and "stop" in trade["exit_reason"].lower()
        capped_reset = False
        if reset_mode == "win" and trade["net_return"] > 0:
            level = 0
            cycle_pnl = 0.0
            cycle_trades = 0
        elif reset_mode == "recovery" and cycle_trades and cycle_pnl >= 0:
            level = 0
            cycle_pnl = 0.0
            cycle_trades = 0
        elif triggering_loss:
            if requested_level >= max_level:
                level = 0
                cycle_pnl = 0.0
                cycle_trades = 0
                capped_cycle_resets += 1
                capped_reset = True
            else:
                level = requested_level + 1

        if record_ledger:
            ledger.append({
                "trade_id": trade["trade_id"], "entry_time": trade["entry_time"].isoformat(),
                "exit_time": trade["exit_time"].isoformat(), "level": requested_level,
                "planned_risk": planned_risk, "notional": notional,
                "equity_before": equity_before, "equity_after": balance,
                "accepted": True, "reason": "accepted", "pnl": pnl,
                "cycle_pnl_before_reset": cycle_pnl_before_reset,
                "cycle_pnl_after": cycle_pnl, "capped_cycle_reset": capped_reset,
            })

    return {
        "summary": {
            "initial_balance": initial,
            "final_balance": balance,
            "min_balance": minimum,
            "max_balance": maximum,
            "profit": balance - initial,
            "return": None if initial == 0 else balance / initial - 1,
            "max_realized_drawdown": max_drawdown,
            "n_candidates": len(rows),
            "n_accepted": accepted,
            "n_rejected": rejected,
            "ruined": balance <= 0,
            "max_requested_level": max_requested_level,
            "max_executed_level": max_executed_level,
            "max_executed_notional": max_executed_notional,
            "max_effective_leverage": max_effective_leverage,
            "wins": wins,
            "losses": losses,
            "capped_cycle_resets": capped_cycle_resets,
            "ending_level": level,
        },
        "ledger": ledger if record_ledger else [],
    }
