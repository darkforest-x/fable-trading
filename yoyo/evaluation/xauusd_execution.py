"""Single-position, one-minute execution for the XAUUSD research comparison.

The caller supplies causal decision-bar signals. A decision executes at the first
real minute OPEN at or after its time_close; signal-bar CLOSE is never a fill.
This is a BID-price plus fixed 20bp round-trip stress model, not historical
bid/ask execution. No financing, swap, spread reconstruction or leverage is added.

Equity starts at 1. Entry notional N = equity / (1 + cost_bp / 20000), with
N * cost_bp / 20000 charged at each leg. Notional is collateral, not a cash
purchase: marked equity = post-entry-fee cash + signed units * price change.
Minute CLOSE marks determine drawdown and bankruptcy, never intraminute lows.
Bankruptcy floors equity at zero; the ledger exposes the uncapped balance and
floor adjustment. Work is vectorized over disjoint minute slices between events.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


_MINUTE = pd.Timedelta(minutes=1)
_LEDGER_COLUMNS = [
    "trade_id", "decision_time", "decision_index", "decision_bar_open",
    "entry_time", "entry_index", "entry_price", "side", "notional", "units",
    "equity_before", "cash_after_entry_fee", "entry_fee", "exit_decision_time",
    "exit_decision_index", "exit_time", "exit_index", "exit_price", "exit_fee",
    "gross_pnl", "net_pnl", "gross_bp", "net_bp", "raw_net_bp",
    "equity_after", "uncapped_equity_after", "bankruptcy_floor_adjustment",
    "holding_seconds", "exit_reason",
]


def _utc(value: Any) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if pd.isna(result):
        raise ValueError("Window boundaries must be finite")
    return result.tz_localize("UTC") if result.tzinfo is None else result.tz_convert("UTC")


def _index(frame: pd.DataFrame, name: str) -> pd.DatetimeIndex:
    index = frame.index
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise ValueError(f"{name} requires a timezone-aware DatetimeIndex")
    if index.hasnans or not index.is_monotonic_increasing or index.has_duplicates:
        raise ValueError(f"{name} timestamps must be finite, unique and increasing")
    return index.tz_convert("UTC")


def _signals(values: Any, bars: pd.DataFrame, name: str) -> np.ndarray:
    if isinstance(values, pd.Series) and not values.index.equals(bars.index):
        raise ValueError(f"{name} Series index must match bars exactly")
    result = np.asarray(values)
    if result.ndim != 1 or len(result) != len(bars):
        raise ValueError(f"{name} must have one value per decision bar")
    if not np.isin(result, [-1, 0, 1] if name == "entry" else [False, True]).all():
        raise ValueError(f"Invalid {name} signal values")
    return result.astype(np.int8 if name == "entry" else bool, copy=False)


def simulate(
    minutes: pd.DataFrame,
    bars: pd.DataFrame,
    entry: Any,
    exit_long: Any,
    exit_short: Any,
    start: Any,
    end: Any,
    cost_bp: float = 20,
) -> dict[str, Any]:
    """Execute causal close decisions on observed minute opens in [start, end).

    Inputs use bar-open DatetimeIndexes; ``bars.time_close`` is authoritative.
    Entry is -1/0/+1. Exit flags close only their matching side. At a shared
    decision time, exit precedes entry; an existing position ignores new entry
    signals unless closed first. Requests crossing a market gap retain decision
    order when several map to the same next open. There is no gap interpolation.

    Only minutes opening at/after start and closing at/before end are observable.
    Any surviving position closes at the final observed minute close, with fee.
    Ledger minute indices are positional indices in the ORIGINAL minutes frame;
    decision indices are positional indices in bars. Forced exits have no exit
    decision index. Daily equity is an end-of-day Series indexed by UTC date;
    missing calendar days carry the latest mark. ``exposure_time`` is seconds,
    ``winrate`` is a 0..1 fraction, and drawdown is a positive percentage.
    ``net_bp`` uses actual equity change / entry notional; ``raw_net_bp`` always
    equals gross_bp minus cost_bp, including a bankruptcy-floor adjustment case.
    """
    start, end = _utc(start), _utc(end)
    if start >= end:
        raise ValueError("start must precede end")
    if not np.isfinite(cost_bp) or cost_bp < 0:
        raise ValueError("cost_bp must be finite and nonnegative")
    mi, bi = _index(minutes, "minutes"), _index(bars, "bars")
    if len(mi) and (mi.asi8 % _MINUTE.value).any():
        raise ValueError("Minute opens must lie on whole-minute boundaries")
    if not {"open", "high", "low", "close"}.issubset(minutes.columns):
        raise ValueError("minutes requires open, high, low, close")
    if "time_close" not in bars:
        raise ValueError("bars requires time_close")
    decisions = pd.DatetimeIndex(pd.to_datetime(bars["time_close"], utc=True))
    if decisions.hasnans or not decisions.is_monotonic_increasing:
        raise ValueError("Decision closes must be finite and increasing")
    if len(decisions) and not (decisions > bi).all():
        raise ValueError("Each decision must follow its bar open")
    entries = _signals(entry, bars, "entry")
    exits_long = _signals(exit_long, bars, "exit_long")
    exits_short = _signals(exit_short, bars, "exit_short")

    first = int(mi.searchsorted(start, side="left"))
    last = int(mi.searchsorted(end - _MINUTE, side="right"))
    last = max(first, last)
    times = mi[first:last]
    prices = minutes.iloc[first:last][["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("Observed OHLC prices must be finite and positive")
    if len(prices) and (
        (prices[:, 1] < prices[:, [0, 3]].max(axis=1)).any()
        or (prices[:, 2] > prices[:, [0, 3]].min(axis=1)).any()
    ):
        raise ValueError("Observed OHLC geometry is invalid")
    count = len(times)
    equity = np.full(count, 1.0)
    fee_fraction = float(cost_bp) / 20000.0
    cash = 1.0
    cursor = 0
    position: dict[str, Any] | None = None
    ledger_rows: list[dict[str, Any]] = []
    bankrupt = False

    def close_position(
        price: float, timestamp: pd.Timestamp, offset: int, reason: str,
        decision_time: Any = pd.NaT, decision_index: Any = pd.NA,
    ) -> None:
        nonlocal position, cash, bankrupt
        assert position is not None
        gross = position["units"] * (price - position["entry_price"])
        exit_fee = position["notional"] * fee_fraction
        uncapped = position["cash_after_entry_fee"] + gross - exit_fee
        cash = max(0.0, float(uncapped))
        bankrupt = cash <= 0.0
        net = cash - position["equity_before"]
        row = {
            **position, "exit_decision_time": decision_time,
            "exit_decision_index": decision_index, "exit_time": timestamp,
            "exit_index": first + offset, "exit_price": float(price),
            "exit_fee": exit_fee, "gross_pnl": gross, "net_pnl": net,
            "gross_bp": gross / position["notional"] * 10000.0,
            "net_bp": net / position["notional"] * 10000.0,
            "raw_net_bp": gross / position["notional"] * 10000.0 - cost_bp,
            "equity_after": cash, "uncapped_equity_after": uncapped,
            "bankruptcy_floor_adjustment": cash - uncapped,
            "holding_seconds": (timestamp - position["entry_time"]).total_seconds(),
            "exit_reason": "bankruptcy" if bankrupt else reason,
        }
        ledger_rows.append(row)
        position = None

    def mark_until(stop: int) -> None:
        nonlocal cursor
        if stop <= cursor:
            return
        if position is None:
            equity[cursor:stop] = cash
        else:
            marked = position["cash_after_entry_fee"] + position["units"] * (
                prices[cursor:stop, 3] - position["entry_price"]
            )
            ruined = np.flatnonzero(marked <= 0.0)
            if len(ruined):
                failed = cursor + int(ruined[0])
                equity[cursor:failed] = marked[:int(ruined[0])]
                close_position(prices[failed, 3], times[failed] + _MINUTE, failed, "bankruptcy")
                equity[failed:] = 0.0
                cursor = count
                return
            equity[cursor:stop] = marked
        cursor = stop

    event_mask = (
        (decisions >= start) & (decisions < end)
        & ((entries != 0) | exits_long | exits_short)
    )
    event_indices = np.flatnonzero(event_mask)
    fill_offsets = times.searchsorted(decisions[event_indices], side="left")
    for decision_index, offset in zip(event_indices, fill_offsets):
        offset = int(offset)
        if offset >= count:
            continue
        mark_until(offset)
        if bankrupt:
            break
        price = float(prices[offset, 0])
        if position is not None and (
            (position["side"] == 1 and exits_long[decision_index])
            or (position["side"] == -1 and exits_short[decision_index])
        ):
            close_position(price, times[offset], offset, "signal", decisions[decision_index], int(decision_index))
            if bankrupt:
                equity[offset:] = 0.0
                cursor = count
                break
        if position is None and entries[decision_index] != 0:
            notional = cash / (1.0 + fee_fraction)
            entry_fee = notional * fee_fraction
            side = int(entries[decision_index])
            position = {
                "trade_id": len(ledger_rows), "decision_time": decisions[decision_index],
                "decision_index": int(decision_index), "decision_bar_open": bi[decision_index],
                "entry_time": times[offset], "entry_index": first + offset,
                "entry_price": price, "side": side, "notional": notional,
                "units": side * notional / price, "equity_before": cash,
                "cash_after_entry_fee": cash - entry_fee, "entry_fee": entry_fee,
            }

    if not bankrupt:
        mark_until(count)
    if not bankrupt and position is not None:
        close_position(float(prices[-1, 3]), times[-1] + _MINUTE, count - 1, "boundary")
        equity[-1] = cash

    ledger = pd.DataFrame(ledger_rows, columns=_LEDGER_COLUMNS)
    for name in ["decision_time", "decision_bar_open", "entry_time", "exit_decision_time", "exit_time"]:
        ledger[name] = pd.to_datetime(ledger[name], utc=True)
    for name in ["decision_index", "entry_index", "exit_decision_index", "exit_index", "side", "trade_id"]:
        ledger[name] = pd.array(ledger[name], dtype="Int64")
    calendar = pd.date_range(start.normalize(), (end - pd.Timedelta(nanoseconds=1)).normalize(), freq="D")
    marked = pd.Series(equity, index=times, name="equity")
    daily = marked.resample("D").last().reindex(calendar).ffill().fillna(1.0)
    daily.index.name = "date"
    running_peak = np.maximum.accumulate(np.r_[1.0, equity])
    mdd = float(np.max(1.0 - np.r_[1.0, equity] / running_peak))
    pnls = ledger["net_pnl"].to_numpy(dtype=float)
    gains = float(pnls[pnls > 0].sum())
    losses = float(-pnls[pnls < 0].sum())
    winrate = float(np.mean(pnls > 0)) if len(pnls) else float("nan")
    exposure = float(ledger["holding_seconds"].sum()) if len(ledger) else 0.0
    stats = {
        "return_pct": (cash - 1.0) * 100.0,
        "max_drawdown_pct": mdd * 100.0,
        "trades": len(ledger), "winrate": winrate, "winrate_pct": winrate * 100.0,
        "PF": gains / losses if losses > 0 else float("inf") if gains > 0 else float("nan"),
        "gross_trade_mean_bp": float(ledger["gross_bp"].mean()) if len(ledger) else float("nan"),
        "net_trade_mean_bp": float(ledger["net_bp"].mean()) if len(ledger) else float("nan"),
        "exposure_time": exposure, "exposure_fraction": exposure / (end - start).total_seconds(),
        "bankrupt": bankrupt, "final_equity": cash, "observed_minutes": count,
        "cost_bp": float(cost_bp), "price_model": "BID plus fixed round-trip cost; no swap",
    }
    return {"stats": stats, "ledger": ledger, "daily_equity": daily}
