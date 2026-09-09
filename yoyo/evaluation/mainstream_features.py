"""Frozen mainstream transfer hypotheses, using close-available data only.

Sources: current altcoin_features IMACD contract and the Owner's Momentum1.0
SMA50 formula. Every feature uses OHLCV at or before its own bar. Formation
memory spans the original near-zero episode plus its release; relative volume
uses the prior20 median. Low/high joins use nanosecond-normalized close clocks.
Pending entries preserve original formation edges and never backdate a later
high-timeframe permission. No labels, fitting, market IO or live service code.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.data.altcoin_features import MA_COLUMNS, build_altcoin_features

ARMS = ("baseline", "volume2", "formation_price", "momentum_now", "momentum_memory",
        "lower_active", "higher_now", "higher_wait", "strict_same_time", "sequence",
        "sequence_wait", "baseline_3r")
TIMEFRAMES = {60: (15, 240), 240: (60, 1440)}


def enrich(bars: pd.DataFrame) -> pd.DataFrame:
    """Use current/prior OHLCV; no outcome or preselected winning threshold."""
    f = build_altcoin_features(bars)
    for name in ("open", "high", "low", "close", "volume"):
        f[name] = bars[name]
    diff = bars.close - bars.close.rolling(50).mean()
    f["momentum10"] = 100 * diff / diff.abs().rolling(50, min_periods=1).max().replace(0, np.nan)
    f["above_all6"] = bars.close.gt(f.loc[:, MA_COLUMNS].max(axis=1))
    release = f.release_side.to_numpy(int)
    strong = f.momentum10.ge(90).to_numpy(bool)
    cumulative = np.r_[0, np.cumsum(strong)]
    memory = np.zeros(len(f), bool)
    for i in np.flatnonzero(release == 1):
        count = int(f.near_zero_bars.iloc[i])
        first = max(0, i-count)
        memory[i] = cumulative[i+1] - cumulative[first] > 0
    f["momentum_memory"] = memory
    active, began, bottom = False, -1, np.nan
    states, starts, inside, counts = [], [], [], 0
    md, close = f.md.to_numpy(), f.close.to_numpy()
    zone_low, zone_high = f.release_zone_low.to_numpy(), f.release_zone_high.to_numpy()
    top = np.nan
    for i in range(len(f)):
        if release[i] != 0:
            active = release[i] == 1 and md[i] > 0 and close[i] >= zone_low[i]
            began, bottom, top, counts = (i, zone_low[i], zone_high[i], 0) if active else (-1, np.nan, np.nan, 0)
        elif active and (md[i] <= 0 or close[i] < bottom):
            active, began = False, -1
        if active and bottom <= close[i] <= top:
            counts += 1
        states.append(active)
        starts.append(began)
        inside.append(counts if active else 0)
    f["active_long"] = states
    f["active_start_i"] = starts
    f["active_inside_count"] = inside
    return f


def closed_positions(source: pd.DatetimeIndex, source_minutes: int,
                     target: pd.DatetimeIndex, target_minutes: int) -> np.ndarray:
    """Latest source CLOSE <= target CLOSE; -1 means genuinely unavailable."""
    if source.tz is None or target.tz is None or not source.is_monotonic_increasing or not source.is_unique or not target.is_monotonic_increasing or not target.is_unique:
        raise ValueError("Unique chronological timezone-aware clocks required")
    source_closes = source.as_unit("ns").asi8 + int(pd.Timedelta(minutes=source_minutes).value)
    target_closes = target.as_unit("ns").asi8 + int(pd.Timedelta(minutes=target_minutes).value)
    pos = np.searchsorted(source_closes, target_closes, side="right") - 1
    available = pos >= 0
    if np.any(source_closes[pos[available]] > target_closes[available]):
        raise AssertionError("Unclosed higher/lower bar entered a decision")
    return pos


def add_context(base: pd.DataFrame, lower: pd.DataFrame, higher: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Align other periods at their completed clocks; missing state never passes."""
    lo_m, hi_m = TIMEFRAMES[minutes]
    if lower.empty or higher.empty:
        raise ValueError("Context sources must be nonempty")
    f = base.copy()
    lo = closed_positions(lower.index, lo_m, base.index, minutes)
    hi = closed_positions(higher.index, hi_m, base.index, minutes)
    low_ready = (lo >= 0) & lower.ready.to_numpy(bool)[np.maximum(lo, 0)]
    high_ready = (hi >= 0) & higher.ready.to_numpy(bool)[np.maximum(hi, 0)]
    f["context_ready"] = low_ready & high_ready
    f["lower_active"] = low_ready & lower.active_long.to_numpy(bool)[np.maximum(lo, 0)]
    beginnings = lower.active_start_i.to_numpy(int)[np.maximum(lo, 0)]
    lower_start_close = np.full(len(f), np.iinfo(np.int64).min, dtype=np.int64)
    known = (beginnings >= 0) & f.lower_active.to_numpy(bool)
    lower_start_close[known] = lower.index.as_unit("ns").asi8[beginnings[known]] + pd.Timedelta(minutes=lo_m).value
    clocks = f.index.as_unit("ns").asi8 + pd.Timedelta(minutes=minutes).value
    ages = np.full(len(f), np.nan)
    ages[known] = (clocks[known]-lower_start_close[known]) / 60e9
    f["lower_age_minutes"] = ages
    f["lower_inside_count"] = np.where(low_ready, lower.active_inside_count.to_numpy()[np.maximum(lo, 0)], 0)
    hm, hs = higher.md.to_numpy()[np.maximum(hi, 0)], higher.sb.to_numpy()[np.maximum(hi, 0)]
    hm, hs = np.where(hi >= 0, hm, np.nan), np.where(hi >= 0, hs, np.nan)
    f["higher_permission"] = high_ready & (hm > hs) & (hm > 0)
    f["higher_md"], f["higher_sb"] = hm, hs
    source_closes = higher.index + pd.Timedelta(minutes=hi_m)
    f["higher_source_close"] = pd.to_datetime([source_closes[p] if p >= 0 else pd.NaT for p in hi], utc=True)
    return f


def candidate_events(f: pd.DataFrame, first_i: int, last_i: int, minutes: int) -> list[dict]:
    """Long hypotheses, with later permission appended at its actual base bar.

    This is a chronological state loop: no future price can select an earlier
    entry. Waiting can end at the fold boundary with no entry, never a fabricated
    fill. Original validity edges are used only while waiting, not as an exit.
    """
    rows, pending = [], []
    release, md, close = f.release_side.to_numpy(), f.md.to_numpy(), f.close.to_numpy()
    ready = f.ready.to_numpy(bool) & f.context_ready.to_numpy(bool)
    permission = f.higher_permission.to_numpy(bool)
    low = f.release_zone_low.to_numpy()
    volume = f.relative_volume.to_numpy()
    formation = f.dense_recent.to_numpy(bool) & f.above_all6.to_numpy(bool)
    memory, current = f.momentum_memory.to_numpy(bool), f.momentum10.ge(90).to_numpy(bool)
    lower = f.lower_active.to_numpy(bool)
    age = f.lower_age_minutes.to_numpy()

    def emit(arm, anchor, decision):
        rows.append({"arm": arm, "anchor_i": int(anchor), "decision_i": int(decision)})

    for i in range(max(0, first_i), last_i+1):
        if pending:
            valid = [(a, side, edge) for a, side, edge in pending if release[i] == 0 and md[i] > 0 and close[i] >= edge]
            pending = []
            for anchor, arm, edge in valid:
                if ready[i] and permission[i]:
                    emit(arm, anchor, i)
                else:
                    pending.append((anchor, arm, edge))
        if i < 550 or not ready[i] or release[i] != 1 or md[i] <= 0:
            continue
        seq = formation[i] and volume[i] >= 2 and memory[i] and lower[i]
        masks = {"baseline": True, "volume2": volume[i] >= 2, "formation_price": formation[i],
            "momentum_now": current[i], "momentum_memory": memory[i], "lower_active": lower[i],
            "higher_now": permission[i], "strict_same_time": formation[i] and volume[i] >= 2 and current[i] and lower[i] and 0 <= age[i] <= minutes,
            "sequence": seq, "baseline_3r": True}
        for arm, passed in masks.items():
            if passed:
                emit(arm, i, i)
        for arm, passed in (("higher_wait", True), ("sequence_wait", seq)):
            if passed and close[i] >= low[i]:
                if permission[i]:
                    emit(arm, i, i)
                else:
                    pending.append((i, arm, low[i]))
    return rows
