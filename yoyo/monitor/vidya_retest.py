"""VIDYA trend-line retest: indicator replica, signal rule and paper position stepping.

Source: Volumatic Variable Index Dynamic Average [BigBeluga], open-source Pine v6
(TradingView script llhVjhA5, read 2026-10-12). The indicator is replicated line by
line: CMO-scaled VIDYA smoothed by SMA(15), bands at +/- ATR(200) x distance, trend
state from closes crossing the bands, the plotted trend line (lower band in an
uptrend, upper band in a downtrend, NaN on the flip bar) and the per-trend buy/sell
volume delta reset exactly as the script resets it.

Strategy (owner request 2026-10-12, from a TradingView screenshot): trade only the
retest of the plotted trend line itself, not the script's pivot short lines.
Long: uptrend, the bar's low reaches the trend line (optionally within ``tol`` ATR)
while it closes on or above it, the previous bar was clear of the line, a green
touch bar confirms immediately and a red one waits for the next green close that
stays above the line; delta >= threshold at the confirming close. Short mirrors.
Stop: beyond the touch/confirm extremes by ``k_atr`` x ATR(14). Exit: fixed ``rr``
multiple or the first close after the trend flips (``exit_mode='trend'``).

One ``Strategy`` object steps one symbol bar by bar and is used unchanged by the
offline backtest and by the live paper worker, so both see identical decisions.
Every decision at bar i uses only bars <= i; the fill happens at bar i+1's open
(``fill='next_open'``, the monitor's paper convention) or at bar i's close
(``fill='close'``, used only to reproduce the 2026-10-12 exploratory numbers).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

ROUND_TRIP_COST = 0.002  # monitor paper/forward convention (20bp), same as ExecutionSpec


@dataclass(frozen=True)
class Params:
    """Frozen strategy parameters. Changing any value is a new strategy id."""

    length: int = 10
    momentum: int = 20
    distance: float = 2.0
    delta_threshold: float = 50.0
    k_atr: float = 1.0
    rr: float = 2.0
    exit_mode: str = "fixed"      # "fixed" (rr target) or "trend" (close after trend flip)
    tol_atr: float = 0.0          # touch tolerance in ATR(14) units
    use_delta: bool = True


STRATEGIES: Dict[str, dict] = {
    "tl-2r": {
        "label": "趋势线回踩 · 2R",
        "params": Params(length=10, delta_threshold=50.0, k_atr=1.0, rr=2.0, exit_mode="fixed", tol_atr=0.0),
    },
    "tl-trend": {
        "label": "趋势线回踩 · 趋势离场",
        "params": Params(length=10, delta_threshold=50.0, k_atr=1.0, rr=2.0, exit_mode="trend", tol_atr=0.2),
    },
}


def _rma(x: np.ndarray, n: int) -> np.ndarray:
    """Pine ta.rma seeded with the SMA of the first n values."""
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    out[n - 1] = float(np.mean(x[:n]))
    a = 1.0 / n
    for i in range(n, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def indicator(o, h, l, c, v, *, length: int = 10, momentum: int = 20, distance: float = 2.0) -> dict:
    """Replicate the script's series. Inputs are equal-length float arrays, oldest first.

    Uses only the current and earlier bars at every index (recursions and rolling
    windows run forward). Returns line (NaN where the script plots na), up (bool
    trend state), delta (percent, 0 where the script shows 0%) and atr14.
    """
    o, h, l, c, v = (np.asarray(a, dtype=float) for a in (o, h, l, c, v))
    n = len(c)
    mom = np.diff(c, prepend=np.nan)
    pos = np.asarray(pd.Series(np.where(mom >= 0, mom, 0.0)).rolling(momentum).sum(), dtype=float)
    neg = np.asarray(pd.Series(np.where(mom >= 0, 0.0, -mom)).rolling(momentum).sum(), dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        cmo = np.abs(100 * (pos - neg) / (pos + neg))
    alpha = 2.0 / (length + 1)
    vid = np.zeros(n)
    prev = 0.0
    for i in range(n):
        if math.isnan(cmo[i]):
            vid[i] = np.nan
            prev = 0.0
        else:
            k = alpha * cmo[i] / 100
            prev = k * c[i] + (1 - k) * prev
            vid[i] = prev
    vidya = np.asarray(pd.Series(vid).rolling(15).mean(), dtype=float)

    pc = np.roll(c, 1)
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    if n:
        tr[0] = h[0] - l[0]
    atr200 = _rma(tr, 200)
    upper = vidya + atr200 * distance
    lower = vidya - atr200 * distance

    up = np.zeros(n, bool)
    state = False
    for i in range(1, n):
        if c[i] > upper[i] and c[i - 1] <= upper[i - 1]:
            state = True
        if c[i] < lower[i] and c[i - 1] >= lower[i - 1]:
            state = False
        up[i] = state
    line = np.where(up, lower, upper)
    flip = np.zeros(n, bool)
    flip[1:] = up[1:] != up[:-1]
    line[flip] = np.nan

    cross_up = np.zeros(n, bool)
    cross_dn = np.zeros(n, bool)
    cross_up[1:] = ~up[:-1] & up[1:]
    cross_dn[1:] = up[:-1] & ~up[1:]
    buy = np.full(n, np.nan)
    sell = np.full(n, np.nan)
    b = s = np.nan
    for i in range(n):
        if i > 0 and (cross_up[i] != cross_up[i - 1] or cross_dn[i] != cross_dn[i - 1]):
            b = s = 0.0
        else:
            b = b + (v[i] if c[i] > o[i] else 0.0)
            s = s + (v[i] if c[i] < o[i] else 0.0)
        buy[i], sell[i] = b, s
    with np.errstate(invalid="ignore", divide="ignore"):
        delta = (buy - sell) / ((buy + sell) / 2) * 100
    delta = np.nan_to_num(delta, nan=0.0)
    return dict(line=line, up=up, delta=delta, atr14=_rma(tr, 14), vidya=vidya)


@dataclass
class State:
    """Per-symbol, per-strategy state; JSON-serialisable for the live book."""

    pending: Dict[str, Optional[int]] = field(default_factory=lambda: {"long": None, "short": None})
    order: Optional[dict] = None       # signal awaiting the next bar's open
    position: Optional[dict] = None    # open paper position

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Optional[dict]) -> "State":
        if not raw:
            return cls()
        pending = raw.get("pending") or {}
        return cls(pending={"long": pending.get("long"), "short": pending.get("short")},
                   order=raw.get("order"), position=raw.get("position"))


class Strategy:
    """Step one symbol bar by bar. ``on_bar`` returns events for that bar.

    Event kinds: ``signal`` (confirmed retest that passed every gate), ``skip``
    (confirmed retest rejected by delta, an open position, staleness in live
    catch-up, or a stop on the wrong side of the fill), ``entry`` and ``exit``. Bar indices are positions in the
    arrays passed to ``on_bar``; callers that roll their arrays must translate
    them (the live worker stores times, not indices).
    """

    def __init__(self, params: Params, cost: float = ROUND_TRIP_COST, fill: str = "next_open", slip: float = 0.0):
        if fill not in ("next_open", "close"):
            raise ValueError("fill must be next_open or close")
        self.p = params
        self.cost = cost
        self.fill = fill
        self.slip = slip

    def on_bar(self, i: int, bars: dict, ind: dict, st: State, allow_new: bool = True) -> List[dict]:
        o, h, l, c = bars["o"], bars["h"], bars["l"], bars["c"]
        line, up, delta, atr = ind["line"], ind["up"], ind["delta"], ind["atr14"]
        p = self.p
        events: List[dict] = []

        # 1) fill an order placed at the previous close, at this bar's open
        if st.order is not None and self.fill == "next_open":
            order, st.order = st.order, None
            if st.position is None:
                side = order["side"]
                entry = o[i]
                risk = entry - order["stop"] if side == "long" else order["stop"] - entry
                if risk > 0:
                    target = (entry + p.rr * risk if side == "long" else entry - p.rr * risk) if p.exit_mode == "fixed" else None
                    st.position = dict(order, entry=entry, risk=risk, target=target, entry_index=i)
                    events.append(dict(kind="entry", index=i, side=side, price=entry, stop=order["stop"], target=target))
                else:
                    events.append(dict(kind="skip", index=i, side=side, reason="stop_beyond_fill",
                                       signal_index=order["signal_index"]))

        # 2) manage the open position on this bar
        pos = st.position
        if pos is not None and i > pos.get("signal_index", -1) and (self.fill == "next_open" or i > pos["entry_index"]):
            side = pos["side"]
            hit_sl = l[i] <= pos["stop"] if side == "long" else h[i] >= pos["stop"]
            hit_tp = pos["target"] is not None and (h[i] >= pos["target"] if side == "long" else l[i] <= pos["target"])
            trend_exit = (p.exit_mode == "trend" and not hit_sl
                          and ((side == "long" and not up[i]) or (side == "short" and up[i])))
            if hit_sl or hit_tp or trend_exit:
                if trend_exit:
                    px, reason = c[i] * ((1 - self.slip) if side == "long" else (1 + self.slip)), "trend"
                elif hit_sl:
                    px, reason = pos["stop"] * ((1 - self.slip) if side == "long" else (1 + self.slip)), "stop"
                else:
                    px, reason = pos["target"], "target"
                gross = (px - pos["entry"]) if side == "long" else (pos["entry"] - px)
                cost = self.cost * (pos["entry"] + px) / 2  # round-trip fraction applied to average notional
                events.append(dict(kind="exit", index=i, side=side, price=px, reason=reason,
                                   entry=pos["entry"], stop=pos["stop"], target=pos["target"], risk=pos["risk"],
                                   level=pos["level"], delta=pos["delta"], signal_index=pos["signal_index"],
                                   touch_index=pos["touch_index"], entry_index=pos["entry_index"],
                                   r_gross=gross / pos["risk"], r=(gross - cost) / pos["risk"]))
                st.position = None

        # 3) signal detection at this bar's close
        L = line[i]
        if not np.isfinite(L) or i < 1:
            st.pending = {"long": None, "short": None}
            return events
        for side in ("long", "short"):
            if (side == "long") != bool(up[i]):
                st.pending[side] = None
                continue
            band = p.tol_atr * atr[i] if np.isfinite(atr[i]) else 0.0
            prev_band = p.tol_atr * atr[i - 1] if np.isfinite(atr[i - 1]) else 0.0
            if side == "long":
                touch = l[i] <= L + band and c[i] >= L
                prev_clear = (not np.isfinite(line[i - 1])) or l[i - 1] > line[i - 1] + prev_band
                confirms = c[i] > o[i]
                holds = c[i] >= L
            else:
                touch = h[i] >= L - band and c[i] <= L
                prev_clear = (not np.isfinite(line[i - 1])) or h[i - 1] < line[i - 1] - prev_band
                confirms = c[i] < o[i]
                holds = c[i] <= L
            touch_index = None
            if st.pending[side] is not None:
                if confirms and holds:
                    touch_index = st.pending[side]
                st.pending[side] = None
            elif touch and prev_clear:
                if confirms:
                    touch_index = i
                else:
                    st.pending[side] = i
            if touch_index is None:
                continue
            d = float(delta[i])
            base = dict(index=i, side=side, touch_index=touch_index, level=float(L), delta=d)
            if p.use_delta and not (d >= p.delta_threshold if side == "long" else d <= -p.delta_threshold):
                events.append(dict(base, kind="skip", reason="delta"))
                continue
            if st.position is not None or st.order is not None:
                events.append(dict(base, kind="skip", reason="position_open"))
                continue
            if not allow_new:  # live catch-up after downtime: never open on a signal nobody saw in time
                events.append(dict(base, kind="skip", reason="stale"))
                continue
            a = atr[i] if np.isfinite(atr[i]) else 0.0
            ti = touch_index
            stop = (min(l[i], l[ti]) - p.k_atr * a) if side == "long" else (max(h[i], h[ti]) + p.k_atr * a)
            events.append(dict(base, kind="signal", stop=float(stop), close=float(c[i])))
            order = dict(side=side, stop=float(stop), level=float(L), delta=d, signal_index=i, touch_index=ti)
            if self.fill == "next_open":
                st.order = order
            else:
                entry = c[i] * ((1 + self.slip) if side == "long" else (1 - self.slip))
                risk = entry - stop if side == "long" else stop - entry
                if risk > 0:
                    target = (entry + p.rr * risk if side == "long" else entry - p.rr * risk) if p.exit_mode == "fixed" else None
                    st.position = dict(order, entry=entry, risk=risk, target=target, entry_index=i)
                    events.append(dict(kind="entry", index=i, side=side, price=entry, stop=stop, target=target))
        return events


def backtest(df: pd.DataFrame, params: Params, *, warmup: int = 1000, cost: float = ROUND_TRIP_COST,
             fill: str = "next_open", slip: float = 0.0) -> pd.DataFrame:
    """Offline run of the exact live stepping. ``df`` columns: time, open, high, low, close, volume."""
    bars = {k: df[col].to_numpy(float) for k, col in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"))}
    ind = indicator(bars["o"], bars["h"], bars["l"], bars["c"], df["volume"].to_numpy(float),
                    length=params.length, momentum=params.momentum, distance=params.distance)
    strat = Strategy(params, cost=cost, fill=fill, slip=slip)
    st = State()
    times = df["time"].to_numpy()
    rows = []
    for i in range(max(warmup, 1), len(df)):
        for ev in strat.on_bar(i, bars, ind, st):
            if ev["kind"] == "exit":
                rows.append(dict(side=ev["side"], signal_time=times[ev["signal_index"]], entry_time=times[ev["entry_index"]],
                                 exit_time=times[ev["index"]], entry=ev["entry"], exit=ev["price"], stop=ev["stop"],
                                 target=ev["target"], level=ev["level"], delta=ev["delta"], reason=ev["reason"],
                                 risk_pct=ev["risk"] / ev["entry"] * 100, r_gross=ev["r_gross"], r=ev["r"],
                                 bars=ev["index"] - ev["entry_index"]))
    return pd.DataFrame(rows)


def summarize(trades: pd.DataFrame, risk_frac: float = 0.01) -> dict:
    """Trade count, win rate, mean/total R, compounding return at 1% risk, max drawdown."""
    if trades is None or len(trades) == 0:
        return dict(trades=0)
    r = trades["r"].to_numpy(float)
    eq = np.cumprod(1 + risk_frac * r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    return dict(trades=int(len(r)), win_rate=round(float((r > 0).mean() * 100), 1), avg_r=round(float(r.mean()), 3),
                total_r=round(float(r.sum()), 1), pf=round(float(gains / losses), 2) if losses > 0 else None,
                return_pct=round(float((eq[-1] - 1) * 100), 1), max_dd_pct=round(float((eq / peak - 1).min() * 100), 1))
