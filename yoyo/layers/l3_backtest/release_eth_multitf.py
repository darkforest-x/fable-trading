"""Causal ETH release replay for 15 minute, hourly, and four-hour bars.

This deliberately keeps the historical-close mechanics of ``pine_allin_eth4h``
available as the default: signals are confirmed at a parent-bar close, entries
and reversals fill at the next open, and a delayed initial stop is not active
on its fill bar.  Individual policy switches expose corrections for research;
they do not change the default baseline.  Parent OHLC is the default execution
path.  A separately requested 15-minute execution frame can refine stop-path
diagnostics for 1h/4h parents while preserving parent-close signal decisions.
Same-bar reversal remains one consolidated next-open fill, so this engine does
not claim an intrabar ordering between the old exit and new entry.

References: https://www.tradingview.com/pine-script-docs/concepts/strategies/
and https://www.tradingview.com/pine-script-docs/concepts/bar-states/ .
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Policy:
    """A one-variable research policy; defaults preserve historical replay."""

    name: str = "original_cost20"
    fee: float = 0.001
    immediate_stop: bool = False
    unit_leverage: bool = False
    ratchet_only: bool = False
    isolate_stops: bool = False
    fresh_cooldown: bool = False
    skip_enabled: bool = True
    cross_only: bool = False
    entry_gate: str = "none"

    def __post_init__(self) -> None:
        if self.fee < 0:
            raise ValueError("fee must be non-negative")
        if self.entry_gate not in {"none", "slope"}:
            raise ValueError("entry_gate must be 'none' or 'slope'")


class Replay:
    """Single-position causal replay with account, path, and stop evidence.

    Required frame columns are OHLC, ``open_time``, ``atr``, ``entry_allowed``,
    ``osc``, ``hk_dayofweek``, ``hk_hour``, both ``v7_*`` and ``cross_*``
    signal booleans, and ``slow_ma``.  ``slow_ma`` is only read by the optional
    flat-entry slope gate.  The frame must contain only already-computed,
    parent-close features; this class intentionally has no data-loading path.
    """

    _REQUIRED = {
        "open_time", "open", "high", "low", "close", "atr", "entry_allowed",
        "osc", "hk_dayofweek", "hk_hour", "v7_long", "v7_short",
        "cross_long", "cross_short", "slow_ma",
    }

    def __init__(
        self, frame: pd.DataFrame, policy: Policy, bar_minutes: int = 15,
        execution_frame: pd.DataFrame | None = None,
    ) -> None:
        if bar_minutes not in {15, 60, 240}:
            raise ValueError("bar_minutes must be one of 15, 60, 240")
        missing = self._REQUIRED.difference(frame.columns)
        if missing:
            raise ValueError(f"frame missing required columns: {sorted(missing)}")
        self.frame = frame.reset_index(drop=True).copy()
        self.policy, self.bar_minutes = policy, bar_minutes
        self.t = pd.to_datetime(self.frame.open_time, utc=True)
        if self.t.isna().any() or self.t.duplicated().any() or not self.t.is_monotonic_increasing:
            raise ValueError("open_time must be sorted, unique, valid UTC timestamps")
        self.o, self.h, self.l, self.c, self.atr = (
            self.frame[k].to_numpy(float) for k in ("open", "high", "low", "close", "atr")
        )
        if not np.isfinite(np.column_stack((self.o, self.h, self.l, self.c, self.atr))).all():
            raise ValueError("OHLC and atr must be finite")
        if (self.o <= 0).any() or (self.h < np.maximum(self.o, self.c)).any() or (self.l > np.minimum(self.o, self.c)).any():
            raise ValueError("invalid parent OHLC")
        prefix = "cross" if policy.cross_only else "v7"
        self.raw = (self.frame[f"{prefix}_long"].to_numpy(bool).astype(int)
                    - self.frame[f"{prefix}_short"].to_numpy(bool).astype(int))
        self.allowed = self.frame.entry_allowed.to_numpy(bool)
        self.score = self.frame.osc.abs().to_numpy(float)
        self.hk_dow = self.frame.hk_dayofweek.to_numpy(int)
        self.hk_hour = self.frame.hk_hour.to_numpy(int)
        self.slow_ma = self.frame.slow_ma.to_numpy(float)
        self.execution = self._prepare_execution(execution_frame)

    def _prepare_execution(self, execution_frame: pd.DataFrame | None) -> dict[pd.Timestamp, pd.DataFrame] | None:
        """Validate complete same-source 15m groups without changing baseline."""
        if execution_frame is None:
            return None
        if self.bar_minutes == 15:
            raise ValueError("execution_frame is only meaningful for 60m or 240m parents")
        need = {"open_time", "open", "high", "low", "close"}
        if missing := need.difference(execution_frame.columns):
            raise ValueError(f"execution_frame missing columns: {sorted(missing)}")
        child = execution_frame.loc[:, sorted(need)].copy()
        child["open_time"] = pd.to_datetime(child.open_time, utc=True)
        child = child.sort_values("open_time").reset_index(drop=True)
        ct = child.open_time
        if ct.isna().any() or ct.duplicated().any() or not ct.diff().iloc[1:].eq(pd.Timedelta(minutes=15)).all():
            raise ValueError("execution_frame must be complete, sorted 15m OHLC")
        values = child[["open", "high", "low", "close"]].to_numpy(float)
        if not np.isfinite(values).all() or (values <= 0).any():
            raise ValueError("execution_frame has invalid OHLC")
        child_open, child_high, child_low, child_close = values.T
        if ((child_high < np.maximum(child_open, child_close)).any()
                or (child_low > np.minimum(child_open, child_close)).any()):
            raise ValueError("execution_frame has invalid OHLC geometry")
        groups: dict[pd.Timestamp, pd.DataFrame] = {}
        width = self.bar_minutes // 15
        child_times = pd.DatetimeIndex(child.open_time)
        for i, parent_time in enumerate(self.t):
            end = parent_time + pd.Timedelta(minutes=self.bar_minutes)
            left = child_times.searchsorted(parent_time, side="left")
            right = child_times.searchsorted(end, side="left")
            rows = child.iloc[left:right]
            expected = pd.date_range(parent_time, periods=width, freq="15min", tz="UTC")
            if len(rows) != width or not pd.DatetimeIndex(rows.open_time).equals(expected):
                raise ValueError(f"execution_frame lacks complete 15m coverage for parent bar {i}")
            # Same-source identity prevents accidentally diagnosing another venue.
            aggregate = (float(rows.open.iloc[0]), float(rows.high.max()), float(rows.low.min()), float(rows.close.iloc[-1]))
            if not np.allclose(aggregate, (self.o[i], self.h[i], self.l[i], self.c[i]), rtol=0, atol=1e-10):
                raise ValueError(f"execution_frame OHLC does not match parent bar {i}")
            groups[parent_time] = rows
        return groups

    def _entry_allowed(self, i: int, direction: int, pos: dict[str, Any] | None) -> bool:
        if not direction:
            return False
        if self.policy.entry_gate == "slope" and pos is None:
            if i == 0 or not np.isfinite(self.slow_ma[i - 1:i + 1]).all():
                return False
            return direction * (self.slow_ma[i] - self.slow_ma[i - 1]) > 0
        return True

    def _reset_stop(self, current: float, proposed: float, pos: dict[str, Any] | None, direction: int) -> float:
        if pos is None:
            return proposed
        if pos["direction"] != direction:
            return current if self.policy.isolate_stops else proposed
        if self.policy.ratchet_only:
            return max(current, proposed) if direction > 0 else min(current, proposed)
        return proposed

    def run(
        self, start: int, end: int, injected: tuple[int, int] | None = None,
        *, record_equity: bool = True,
    ) -> dict[str, Any]:
        """Run ``[start, end)``; injected controls close once and never reopen.

        An injected tuple is ``(signal_bar, direction)`` and suppresses all
        ordinary entries.  It may still be closed by an ordinary reverse at the
        next open, matching the historical-control convention.  Open end
        positions are censored; ``mark_to_market_equity`` is reported separately
        and is not a liquidation assertion.
        """
        if not (0 <= start <= end <= len(self.frame)):
            raise ValueError("start/end must define a frame slice")
        p = self.policy
        cash = peak = close_peak = 500.0
        max_dd = max_close_dd = 0.0
        total_fees = 0.0
        max_leverage = 0.0
        pos: dict[str, Any] | None = None
        pending: tuple[int, float, int, float] | None = None
        sl = float("nan")
        skip = 0
        exposed = 0
        insolvency = False
        trades: list[dict[str, Any]] = []
        equity: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        bars_processed = 0

        def marked(price: float) -> float:
            return cash if pos is None else cash + pos["direction"] * pos["qty"] * (price - pos["entry_price"])

        def observe(price: float, time: pd.Timestamp, i: int) -> None:
            """Record only nodes causally reached before a possible exit."""
            nonlocal peak, max_dd, insolvency, max_leverage
            value = marked(price)
            peak = max(peak, value)
            max_dd = max(max_dd, (peak - value) / peak) if peak else max_dd
            insolvency |= value <= 0
            if pos is not None:
                ret = pos["direction"] * (price - pos["entry_price"]) / pos["entry_price"]
                pos["mfe_return"] = max(pos["mfe_return"], ret)
                pos["mae_return"] = min(pos["mae_return"], ret)
                risk = pos["initial_risk_fraction"]
                if risk > 0:
                    pos["mfe_r"] = max(pos["mfe_r"], ret / risk)
                    pos["mae_r"] = min(pos["mae_r"], ret / risk)
                if value > 0:
                    lev = pos["qty"] * price / value
                    pos["max_leverage"] = max(pos["max_leverage"], lev)
                    max_leverage = max(max_leverage, lev)

        def event(i: int, kind: str, **values: Any) -> None:
            events.append({"i": i, "time": values.pop("time", self.t.iloc[i]), "event": kind, **values})

        def close_trade(i: int, price: float, reason: str, time: pd.Timestamp) -> None:
            nonlocal cash, pos, total_fees
            assert pos is not None
            gross_pnl = pos["direction"] * pos["qty"] * (price - pos["entry_price"])
            fee = pos["qty"] * price * p.fee
            total_fees += fee
            cash += gross_pnl - fee
            notional = pos["qty"] * pos["entry_price"]
            net = gross_pnl - pos["entry_fee"] - fee
            trades.append({**pos, "exit_i": i, "exit_time": time, "exit_price": price,
                           "exit_reason": reason, "gross_pnl": gross_pnl, "net_pnl": net,
                           "gross_return": gross_pnl / notional, "net_return": net / notional,
                           "fees": pos["entry_fee"] + fee, "holding_bars": i - pos["entry_i"]})
            event(i, "stop_fill" if reason.startswith("stop") else "exit", time=time, reason=reason, price=price)
            pos = None
            # The exit fee changes flat cash.  Observe it only after clearing
            # the position so an already-closed trade cannot gain further MFE.
            observe(price, time, i)

        def process_path(i: int) -> None:
            """Fill stop chronology from parent OHLC or complete 15m children."""
            nonlocal pos
            if pos is None:
                return
            if self.execution is None:
                bars = [(self.t.iloc[i], self.o[i], self.h[i], self.l[i], self.c[i])]
            else:
                rows = self.execution[self.t.iloc[i]]
                bars = [(r.open_time, r.open, r.high, r.low, r.close) for r in rows.itertuples(index=False)]
            for time, op, hi, lo, cl in bars:
                if pos is None:
                    break
                observe(float(op), time, i)
                stop, side = pos["stop"], pos["direction"]
                if stop is not None and side * (float(op) - stop) <= 0:
                    close_trade(i, float(op), "stop_gap", time)
                    break
                nodes = [float(hi), float(lo)] if float(hi) - float(op) < float(op) - float(lo) else [float(lo), float(hi)]
                for price in nodes + [float(cl)]:
                    if pos is None:
                        break
                    if stop is not None and side * (price - stop) <= 0:
                        observe(stop, time, i)
                        close_trade(i, stop, "stop", time)
                        break
                    observe(price, time, i)

        for i in range(start, end):
            bars_processed += 1
            n_before = len(trades)
            if pending is not None:
                direction, qty, signal_i, initial_sl = pending
                observe(self.o[i], self.t.iloc[i], i)
                if pos is not None and pos["direction"] != direction:
                    close_trade(i, self.o[i], "reverse", self.t.iloc[i])
                if pos is None and cash > 0 and qty > 0:
                    active_stop = initial_sl if p.immediate_stop else None
                    if p.isolate_stops:
                        sl = initial_sl
                    elif not np.isfinite(sl):
                        sl = initial_sl
                    fee = qty * self.o[i] * p.fee
                    entry_equity = cash
                    cash -= fee
                    total_fees += fee
                    risk = abs(self.o[i] - initial_sl) / self.o[i]
                    pos = {"direction": direction, "qty": qty, "signal_i": signal_i,
                           "signal_time": self.t.iloc[signal_i], "entry_i": i, "entry_time": self.t.iloc[i],
                           "entry_price": self.o[i], "entry_equity": entry_equity, "entry_fee": fee,
                           "leverage": qty * self.o[i] / entry_equity, "score": self.score[signal_i],
                           "stop": active_stop, "initial_stop": initial_sl,
                           "initial_risk_fraction": risk, "mfe_return": 0.0, "mae_return": 0.0,
                           "mfe_r": 0.0 if risk > 0 else float("nan"),
                           "mae_r": 0.0 if risk > 0 else float("nan"),
                           "max_leverage": qty * self.o[i] / entry_equity,
                           "firstbar_unprotected_touch": False}
                    max_leverage = max(max_leverage, pos["max_leverage"])
                    event(i, "entry_fill", direction=direction, price=self.o[i], stop_active=active_stop is not None)
                pending = None
            if pos is not None:
                exposed += 1
                if pos["stop"] is None:
                    side, initial = pos["direction"], pos["initial_stop"]
                    adverse = self.l[i] <= initial if side > 0 else self.h[i] >= initial
                    if adverse:
                        pos["firstbar_unprotected_touch"] = True
                process_path(i)

            # Default preserves the legacy pre-profit boolean.  The fresh flag
            # intentionally observes the just-closed trade before admission.
            cooling = skip > 0
            if len(trades) > n_before and p.skip_enabled:
                last = trades[-1]["net_return"] * 100
                if last > 20.0:
                    skip = 7
                elif last > 2.0:
                    skip = 1
            if not p.skip_enabled:
                skip = 0
                cooling = False
            elif p.fresh_cooldown:
                cooling = skip > 0

            direction = int(self.raw[i])
            if injected is not None and pos is None and not trades:
                direction = injected[1] if i == injected[0] else 0
            allowed = bool(self.allowed[i] and not cooling and self._entry_allowed(i, direction, pos))
            if direction and cooling:
                skip -= 1
                event(i, "cooldown_skip", direction=direction)
            signal = direction if direction and allowed else 0
            if signal:
                amount = min(4 * (1.5 if self.hk_hour[i] == 3 else 1) * (2 if self.hk_dow[i] == 3 else 1), 13)
                amount = 1.0 if p.unit_leverage else amount
                qty = marked(self.c[i]) * amount / self.c[i]
                distance = min(self.atr[i] * 4, self.c[i] * .03)
                proposed = self.c[i] - signal * distance
                before = sl
                sl = self._reset_stop(sl, proposed, pos, signal)
                if pos is not None and pos["direction"] == signal:
                    event(i, "same_side_stop_reset", direction=signal, previous_stop=before, proposed_stop=proposed, stop=sl)
                elif i + 1 < end and qty > 0:
                    # A synthetic control exits on reverse but is never reopened.
                    if injected is not None and pos is not None:
                        pending = (signal, 0.0, i, proposed)
                    else:
                        pending = (signal, qty, i, proposed)
                    event(i, "entry_signal", direction=signal, proposed_stop=proposed)
            if pos is not None:
                old = pos["stop"]
                ep, side = pos["entry_price"], pos["direction"]
                if side > 0 and self.h[i] > ep * 1.015:
                    sl = max(sl, ep * 1.001)
                elif side < 0 and self.l[i] < ep * .985:
                    sl = min(sl, ep * .999)
                pos["stop"] = sl
                if old != sl:
                    event(i, "stop_update", previous_stop=old, stop=sl)
                if signal and signal != side:
                    event(i, "shared_stop_on_reverse", direction=signal)
            value = marked(self.c[i])
            close_peak = max(close_peak, value)
            max_close_dd = max(max_close_dd, (close_peak - value) / close_peak) if close_peak else max_close_dd
            if record_equity:
                equity.append({"time": self.t.iloc[i] + pd.Timedelta(minutes=self.bar_minutes), "equity": value,
                               "cash": cash, "position": 0 if pos is None else pos["direction"]})
            if injected is not None and trades:
                break
            if cash <= 0 and pos is None:
                break
        mtm = marked(self.c[min(end - 1, len(self.c) - 1)]) if end > start else 500.0
        return {"trades": pd.DataFrame(trades), "equity": pd.DataFrame(equity), "events": pd.DataFrame(events),
                "open_position": pos, "cash": cash, "final_equity": (equity[-1]["equity"] if equity else mtm),
                "mark_to_market_equity": mtm, "max_drawdown_path": max_dd, "max_drawdown_close": max_close_dd,
                "path_peak": peak, "close_peak": close_peak,
                "nonpositive_equity_seen": insolvency, "fees": total_fees, "max_leverage": max_leverage,
                "exposure_fraction": exposed / max(1, (len(equity) if record_equity else bars_processed)),
                "execution_precision": "15m" if self.execution is not None else "parent_ohlc"}
