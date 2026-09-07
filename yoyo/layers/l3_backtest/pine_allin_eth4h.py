"""Historical-close replay of the owner's original ALLIN V7.2, not a Pine VM.

Signal inputs through bar t only: OHLC, time, SMA(hl2,10/40/60), EMA(close,100),
SMA-seeded Wilder ATR(14), percentile(diff,200,99), lag10 and HMA10. Reuse only
the existing signal math, not its corrected execution/calendar. Binance calendar
variables are UTC; sizing weekdays are UTC+8. Reference: TradingView Pine v5
concepts/strategies and concepts/time, checked 2026-09-07.

This deliberately preserves source quirks: stop submission after the entry bar,
pre-update cooldown boolean, shared sl_price and same-side stop resets. Orders
reverse at next open as one consolidated fill; simultaneous entry/exit/close
queue parity needs native TradingView exports. The OHLC path is only used after
entry for fills/drawdown, never for entry decisions or retroactive BE fills.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .pine_allin_v7 import SignalParameters, add_indicators


@dataclass(frozen=True)
class Policy:
    name: str = "original_zero_cost"
    fee: float = 0.0
    immediate_stop: bool = False
    unit_leverage: bool = False
    cross_only: bool = False


POLICIES = (
    Policy(),
    Policy("original_cost20", fee=0.001),
    Policy("entry_stop_cost20", fee=0.001, immediate_stop=True),
    Policy("one_x_cost20", fee=0.001, unit_leverage=True),
    Policy("sma_only_cost20", fee=0.001, cross_only=True),
)


def aggregate_4h(source: pd.DataFrame) -> pd.DataFrame:
    """Use only complete UTC-aligned 16x15m OHLCV groups; reject gaps."""
    t = pd.to_datetime(source.open_time, utc=True)
    if t.duplicated().any() or not t.is_monotonic_increasing:
        raise ValueError("source timestamps must be sorted and unique")
    if not t.diff().iloc[1:].eq(pd.Timedelta(minutes=15)).all():
        raise ValueError("source contains a 15m gap")
    numeric = source[["open", "high", "low", "close", "volume"]].to_numpy(float)
    if not np.isfinite(numeric).all() or (numeric[:, :4] <= 0).any():
        raise ValueError("invalid OHLCV")
    if (source.high < source[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("invalid high")
    if (source.low > source[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("invalid low")
    groups = source.assign(open_time=t).set_index("open_time").resample("4h", origin="epoch")
    out = groups.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    if not groups.size().eq(16).all():
        raise ValueError("incomplete 4h group")
    return out.reset_index()


def features(frame: pd.DataFrame) -> pd.DataFrame:
    """Frozen indicators; causal volatility bins use ATR% of prior 252 bars."""
    out = add_indicators(frame, SignalParameters())
    utc = pd.to_datetime(out.open_time, utc=True)
    out["calendar_allowed"] = ~utc.dt.hour.between(21, 22) & utc.dt.dayofweek.ne(6)
    out["entry_allowed"] = out.calendar_allowed & out.volatility_allowed
    prior = out.atr_percent.shift(1).rolling(252, min_periods=252)
    edges = np.column_stack([prior.quantile(q).to_numpy() for q in (0.2, 0.4, 0.6, 0.8)])
    bins = (out.atr_percent.to_numpy()[:, None] > edges).sum(axis=1)
    out["vol_bin"] = np.where(np.isfinite(edges).all(axis=1), bins, -1)
    return out


class Replay:
    """Single position, confirmed-bar event clock and auditable account ledger."""

    def __init__(self, frame: pd.DataFrame, policy: Policy):
        self.frame = frame
        self.policy = policy
        self.t = pd.to_datetime(frame.open_time, utc=True).tolist()
        self.o, self.h, self.l, self.c, self.atr = (
            frame[k].to_numpy(float) for k in ("open", "high", "low", "close", "atr")
        )
        prefix = "cross" if policy.cross_only else "v7"
        self.raw = frame[prefix + "_long"].to_numpy(bool).astype(int) - frame[prefix + "_short"].to_numpy(bool).astype(int)
        self.allowed = frame.entry_allowed.to_numpy(bool)
        self.score = frame.osc.abs().to_numpy(float)
        self.hk_dow = frame.hk_dayofweek.to_numpy(int)
        self.hk_hour = frame.hk_hour.to_numpy(int)

    def target_leverage(self, i: int, original: float) -> float:
        """Extension hook; the original replay preserves its sizing schedule."""
        return original

    def cooling_state(self, skip: int, original: bool) -> bool:
        """Extension hook; the original source reads the pre-update boolean."""
        return original

    def reset_stop(self, current: float, proposed: float, position, direction: int) -> float:
        """Extension hook; the original source overwrites its shared stop."""
        return proposed

    def entry_stop_state(self, current: float, initial: float) -> float:
        """Extension hook; original shared state survives a reversal fill."""
        return current

    def pending_quantity(self, quantity: float, position, direction: int) -> float:
        """Extension hook; original opposite entry reverses the position."""
        return quantity

    def run(self, start: int, end: int, *, injected: tuple[int, int] | None = None) -> dict[str, Any]:
        """Run [start,end); injected controls stop after their first exit.

        Future raw signals manage an injected position exactly as a case's
        position. No control opens additional trades. Equity marks are OHLC
        path approximations, not exchange margin/liquidation simulation.
        """
        p = self.policy
        cash, peak, max_dd, max_close_dd = 500.0, 500.0, 0.0, 0.0
        close_peak = 500.0
        pos = None
        pending = None
        sl = float("nan")
        skip = 0
        trades, equity, events = [], [], []
        exposed = 0
        insolvency = False
        total_fees = 0.0

        def marked(price: float) -> float:
            return cash if pos is None else cash + pos["direction"] * pos["qty"] * (price - pos["entry_price"])

        def mark(price: float) -> None:
            nonlocal peak, max_dd, insolvency
            value = marked(price)
            peak = max(peak, value)
            max_dd = max(max_dd, (peak - value) / peak)
            insolvency |= value <= 0

        def close_trade(i: int, price: float, reason: str) -> None:
            nonlocal cash, pos, total_fees
            gross_pnl = pos["direction"] * pos["qty"] * (price - pos["entry_price"])
            fee = pos["qty"] * price * p.fee
            total_fees += fee
            cash += gross_pnl - fee
            notional = pos["qty"] * pos["entry_price"]
            net = gross_pnl - pos["entry_fee"] - fee
            trades.append({**pos, "exit_i": i, "exit_time": self.t[i], "exit_price": price,
                           "exit_reason": reason, "gross_pnl": gross_pnl, "net_pnl": net,
                           "gross_return": gross_pnl / notional, "net_return": net / notional,
                           "fees": pos["entry_fee"] + fee, "holding_bars": i - pos["entry_i"]})
            pos = None

        for i in range(start, end):
            n_before = len(trades)
            if pending is not None:
                direction, qty, signal_i, initial_sl = pending
                mark(self.o[i])
                if pos is not None and pos["direction"] != direction:
                    close_trade(i, self.o[i], "reverse")
                if pos is None and cash > 0 and qty > 0:
                    sl = self.entry_stop_state(sl, initial_sl)
                    fee = qty * self.o[i] * p.fee
                    entry_equity = cash
                    cash -= fee
                    total_fees += fee
                    pos = {"direction": direction, "qty": qty, "signal_i": signal_i,
                           "signal_time": self.t[signal_i], "entry_i": i, "entry_time": self.t[i],
                           "entry_price": self.o[i], "entry_equity": entry_equity,
                           "entry_fee": fee, "leverage": qty * self.o[i] / entry_equity,
                           "score": self.score[signal_i], "stop": initial_sl if p.immediate_stop else None,
                           "initial_stop": initial_sl}
                pending = None
            if pos is not None:
                exposed += 1
                mark(self.o[i])
                stop = pos["stop"]
                side = pos["direction"]
                if stop is not None and side * (self.o[i] - stop) <= 0:
                    close_trade(i, self.o[i], "stop_gap")
                    mark(self.o[i])
                else:
                    nodes = [self.h[i], self.l[i]] if self.h[i] - self.o[i] < self.o[i] - self.l[i] else [self.l[i], self.h[i]]
                    for price in nodes + [self.c[i]]:
                        if pos is None:
                            break
                        if stop is not None and side * (price - stop) <= 0:
                            mark(stop)
                            close_trade(i, stop, "stop")
                            mark(stop)
                            break
                        mark(price)

            # Preserve source ordering: boolean is evaluated before new P/L.
            cooling = skip > 0
            if len(trades) > n_before:
                last = trades[-1]["net_return"] * 100
                if last > 20.0:
                    skip = 7
                elif last > 2.0:
                    skip = 1
            cooling = self.cooling_state(skip, cooling)
            direction = int(self.raw[i])
            if injected is not None and pos is None and not trades:
                direction = injected[1] if i == injected[0] else 0
            allowed = bool(self.allowed[i] and not cooling)
            if direction and cooling:
                skip -= 1
                events.append({"i": i, "event": "cooldown_skip", "direction": direction})
            signal = direction if direction and allowed else 0
            if signal:
                amount = min(4 * (1.5 if self.hk_hour[i] == 3 else 1) * (2 if self.hk_dow[i] == 3 else 1), 13)
                amount = 1.0 if p.unit_leverage else amount
                amount = self.target_leverage(i, amount)
                qty = marked(self.c[i]) * amount / self.c[i]
                distance = min(self.atr[i] * 4, self.c[i] * 0.03)
                proposed_sl = self.c[i] - signal * distance
                sl = self.reset_stop(sl, proposed_sl, pos, signal)
                if pos is not None and pos["direction"] == signal:
                    events.append({"i": i, "event": "same_side_stop_reset", "direction": signal})
                elif i + 1 < end and qty > 0:
                    if injected is not None and pos is not None:
                        # Only exit the matched event at next open.
                        pending = (signal, 0.0, i, sl)
                    else:
                        pending = (signal, self.pending_quantity(qty, pos, signal), i, proposed_sl)
                    events.append({"i": i, "event": "entry_signal", "direction": signal})
            if pos is not None:
                ep = pos["entry_price"]
                if pos["direction"] > 0 and self.h[i] > ep * 1.015:
                    sl = max(sl, ep * 1.001)
                elif pos["direction"] < 0 and self.l[i] < ep * 0.985:
                    sl = min(sl, ep * 0.999)
                if signal and signal != pos["direction"]:
                    events.append({"i": i, "event": "shared_stop_on_reverse", "direction": signal})
                pos["stop"] = sl
            value = marked(self.c[i])
            close_peak = max(close_peak, value)
            max_close_dd = max(max_close_dd, (close_peak - value) / close_peak)
            equity.append({"time": self.t[i] + pd.Timedelta(hours=4), "equity": value,
                           "cash": cash, "position": 0 if pos is None else pos["direction"]})
            if injected is not None and trades:
                break
            if cash <= 0 and pos is None:
                break

        return {"trades": pd.DataFrame(trades), "equity": pd.DataFrame(equity),
                "events": pd.DataFrame(events), "open_position": pos, "cash": cash,
                "final_equity": equity[-1]["equity"] if equity else 500.0,
                "max_drawdown_path": max_dd, "max_drawdown_close": max_close_dd,
                "nonpositive_equity_seen": insolvency, "fees": total_fees,
                "exposure_fraction": exposed / max(1, len(equity))}
