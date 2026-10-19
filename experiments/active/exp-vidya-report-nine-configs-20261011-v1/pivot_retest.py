"""Pivot-line retest rules from the PineTrades video (configs A-G of the 2026-10-11 report).

Video rule (YouTube CKy7-5rosP0, 10:54-12:30): in a green cloud, wait for price to retest
the most recent pivot-low line drawn by the Volumatic VIDYA script, with the touching
candle's wick reaching the line and the close holding; a green touch confirms, a red one
waits for the next green candle; delta >= 30%; stop decently under the entry candle;
target 2R. Short mirrors with pivot highs in a red cloud.

Pivot lines replicate the script: a pivot low is a close lower than the 3 closes before it
and not higher than the 3 after (confirmed 3 bars later), drawn at that bar's low, kept
only if the low is above the trend line; pivot highs use highs and must be below the line.
The newest pivot replaces the older one, each line is traded once, a close beyond the line
invalidates it, and a trend flip clears both. Indicator series come from
yoyo.monitor.vidya_retest.indicator (the same code the live paper worker runs).

Execution (report basis): entry at the confirming close plus slippage, stop beyond the
touch/confirm extremes by k x ATR(14), stop checked before target inside one bar, costs
0.05% per side plus 0.01% slippage on market fills. This module was ported verbatim from
the exploratory script that produced the report; build.py checks every config against the
report's published numbers.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.monitor.vidya_retest import indicator as vidya_indicator


def series(df: pd.DataFrame, length: int, pivot: int = 3) -> dict:
    """Indicator plus pivot-line levels; every value at bar i uses bars <= i only."""
    o, h, l, c, v = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    ind = vidya_indicator(o, h, l, c, v, length=length)
    up, line = ind["up"], ind["line"]
    n = len(c)
    flip = np.zeros(n, bool)
    flip[1:] = up[1:] != up[:-1]
    plo = np.full(n, np.nan)
    phi = np.full(n, np.nan)
    p = pivot
    for i in range(2 * p, n):
        cc = c[i - p]
        if cc < c[i - 2 * p:i - p].min() and cc <= c[i - p + 1:i + 1].min():
            if l[i - p] > line[i]:          # NaN-safe: False while the line is NaN
                plo[i] = l[i - p]
        hh = h[i - p]
        if hh > h[i - 2 * p:i - p].max() and hh >= h[i - p + 1:i + 1].max():
            if h[i - p] < line[i]:
                phi[i] = h[i - p]
    return dict(o=o, h=h, l=l, c=c, up=up, line=line, delta=ind["delta"], atr14=ind["atr14"],
                plo=plo, phi=phi, flip=flip)


def backtest(df: pd.DataFrame, ind: dict, *, delta_th=30.0, use_delta=True, k_atr=0.3, rr=2.0,
             exit_mode="fixed", fee=0.0005, slip=0.0001, warmup=1000) -> pd.DataFrame:
    o, h, l, c = ind["o"], ind["h"], ind["l"], ind["c"]
    up, delta, plo, phi, atr, flip = ind["up"], ind["delta"], ind["plo"], ind["phi"], ind["atr14"], ind["flip"]
    times = df["time"].to_numpy()
    n = len(c)
    trades = []
    pos = None
    lv: dict = {"long": None, "short": None}

    def enter(side, i, touch, level):
        nonlocal pos
        if side == "long":
            e = c[i] * (1 + slip)
            sl = min(l[i], l[touch]) - k_atr * atr[i]
            r = e - sl
            tp = e + rr * r if exit_mode == "fixed" else np.inf
        else:
            e = c[i] * (1 - slip)
            sl = max(h[i], h[touch]) + k_atr * atr[i]
            r = sl - e
            tp = e - rr * r if exit_mode == "fixed" else -np.inf
        if r > 0:
            pos = dict(side=side, i=i, entry=e, sl=sl, tp=tp, risk=r, delta=delta[i], level=level, touch=touch)

    for i in range(warmup, n):
        if pos is not None and i > pos["i"]:
            s = pos["side"]
            hit_sl = l[i] <= pos["sl"] if s == "long" else h[i] >= pos["sl"]
            hit_tp = h[i] >= pos["tp"] if s == "long" else l[i] <= pos["tp"]
            trend_exit = exit_mode == "trend" and not hit_sl and ((s == "long" and not up[i]) or (s == "short" and up[i]))
            if hit_sl or hit_tp or trend_exit:
                reason = "trend" if trend_exit else ("stop" if hit_sl else "target")
                if trend_exit:
                    px = c[i] * (1 - slip) if s == "long" else c[i] * (1 + slip)
                elif hit_sl:
                    px = pos["sl"] * (1 - slip) if s == "long" else pos["sl"] * (1 + slip)
                else:
                    px = pos["tp"]
                gross = (px - pos["entry"]) if s == "long" else (pos["entry"] - px)
                cost = fee * (pos["entry"] + px)
                trades.append(dict(side=s, signal_time=times[pos["i"]], entry_time=times[pos["i"]], exit_time=times[i],
                                   touch_time=times[pos["touch"]], stop=pos["sl"],
                                   target=pos["tp"] if np.isfinite(pos["tp"]) else None, level=pos["level"],
                                   reason=reason, entry=pos["entry"], exit=px, delta=pos["delta"],
                                   r_gross=gross / pos["risk"], r=(gross - cost) / pos["risk"],
                                   bars=i - pos["i"], risk_pct=pos["risk"] / pos["entry"] * 100))
                pos = None

        if flip[i]:
            lv = {"long": None, "short": None}

        for side in ("long", "short"):
            st = lv[side]
            if st is None or st["used"] or st["conf"] >= i:
                continue
            L = st["level"]
            trend_ok = up[i] if side == "long" else not up[i]
            if side == "long":
                touch, broke, with_trend = l[i] <= L and c[i] >= L, c[i] < L, c[i] > o[i]
            else:
                touch, broke, with_trend = h[i] >= L and c[i] <= L, c[i] > L, c[i] < o[i]
            signal_touch = None
            if st["pending"] is not None:
                if with_trend and not broke:
                    signal_touch = st["pending"]
                st["pending"] = None
                if broke:
                    lv[side] = None
                    continue
            elif touch:
                if with_trend:
                    signal_touch = i
                else:
                    st["pending"] = i
            elif broke:
                lv[side] = None
                continue
            if signal_touch is not None and pos is None and trend_ok:
                d_ok = (delta[i] >= delta_th) if side == "long" else (delta[i] <= -delta_th)
                if d_ok or not use_delta:
                    enter(side, i, signal_touch, L)
                    if pos is not None:
                        st["used"] = True

        if not np.isnan(plo[i]):
            lv["long"] = dict(level=plo[i], conf=i, used=False, pending=None)
        if not np.isnan(phi[i]):
            lv["short"] = dict(level=phi[i], conf=i, used=False, pending=None)
    return pd.DataFrame(trades)
