"""Causal entry-clock variants; native exit management always remains 15m.

Read completed 5m OHLC only. Stoch(5,3,3) uses the selected completed entry
timeframe. Each confirmed H1 arrow refreshes the watch; entries must close
strictly later and match its direction. For 5m candidate/control selection,
forward-fill only the most recently completed 15m ATR14 (no backfill).
The native engine still receives the original sparse 15m ATR update series.
Source: frozen pepe_range_exits.prepare/entries and owner 2026-10-20 direction.
"""
import numpy as np
import pandas as pd

from yoyo.evaluation.pepe_range_exits import prepare, signals


def watched_entries(hour, arrows):
    """Admit every matching close after the latest H1 signal, never the same close."""
    if len(hour) != len(arrows):
        raise ValueError("Entry and watch arrays must align")
    admitted = np.zeros(len(hour), dtype=int)
    since = np.full(len(hour), -1, dtype=int)
    watch, observed = 0, -1
    for i, (h, arrow) in enumerate(zip(hour, arrows)):
        if h:
            watch, observed = int(h), i
        if watch and arrow == watch and i > observed:
            admitted[i], since[i] = watch, observed
    return admitted, since


def entry_context(five, minutes=15):
    """Return a candidate-only context; do not pass its dense ATR into exit replay."""
    ctx = prepare(five)
    if minutes == 15:
        return ctx
    if minutes != 5:
        raise ValueError("Only frozen 15m and owner-authorized 5m entry clocks supported")
    ctx = dict(ctx)
    ctx["signal"], ctx["observed"] = watched_entries(
        ctx["hour"], signals(five).to_numpy(int))
    ctx["atr"] = pd.Series(ctx["atr"], index=five.index).ffill().to_numpy(float)
    return ctx
