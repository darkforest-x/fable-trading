"""Research E: original V3 parents vetoed only by known opposing 4H IMACD.

Original hourly OHLCV are aggregated into UTC00/04/08/... four-hour buckets.
Only four complete consecutive valid price bars form a bucket; partial heads
and tails are discarded. Every hourly time gap starts a new aggregation and
indicator epoch. SMMA34(high/low), ZLEMA34(hlc3) and SMA9(MD) reuse the original
Pine replay mathematics on these 4H prices, never an average of 1H indicators.
Volume may remain missing; it is not an input to the 4H momentum calculation.

For each 1H decision at open+1h, only a complete 4H close <= that decision in
the SAME hourly epoch is visible, including exact close equality. At least341
complete 4H bars plus finite MD/SB are required for known status. Known MD<SB
vetoes a new parent; MD>=SB (including two zeros or negative values) passes.
Unknown also passes, but remains explicitly unknown rather than confirmed.

All other V3 fields are unchanged current/past OHLCV, ready, prior12 high and
fast SMA/EMA20, prior density, three-bar advance/volume with prior20 baseline,
and 1H MD/SB/ZLEMA. The raw full-condition edge, accepted-only12-bar cooldown
and child age0..3 stay unchanged. Children do not reapply this 4H gate. No
future incomplete 4H bar, outcome label, risk occupancy or trade exit is used.
This is a fixed research hypothesis with no performance or production claim.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_early_warning import HOUR, early_fields
from yoyo.evaluation.spike_burst_recall_study import _clock
from yoyo.evaluation.spike_burst_replay import _ema, _sma, _smma

FOUR_HOURS = pd.Timedelta(hours=4)
HTF_WARMUP = 341
OHLCV = ("open", "high", "low", "close", "volume")


def _segments(index):
    return np.r_[0, np.cumsum(np.diff(index.asi8) != HOUR.value)]


def aggregate_4h(frame):
    """Closed aligned 4H OHLCV with hourly-epoch identity, without imputation.

    Missing volume preserves a missing aggregate rather than a fabricated zero.
    Malformed prices/volume fail explicitly, matching original feature inputs.
    A complete bucket must have exactly UTC bucket+0,+1,+2,+3 hour observations.
    """
    index = _clock(frame)
    if frame.empty or not frame.columns.is_unique:
        raise ValueError("Nonempty hourly bars with unique columns required")
    missing = set(OHLCV) - set(frame.columns)
    if missing:
        raise ValueError("Missing OHLCV columns: " + ", ".join(sorted(missing)))
    source = frame.loc[:, OHLCV].astype(float).copy()
    prices = source[["open", "high", "low", "close"]]
    if (not np.isfinite(prices.to_numpy()).all() or (prices <= 0).any().any()
            or (source.high < prices.max(axis=1)).any()
            or (source.low > prices.min(axis=1)).any()):
        raise ValueError("OHLC prices must be positive, finite and geometrically valid")
    if np.isinf(source.volume).any() or (source.volume.dropna() < 0).any():
        raise ValueError("Volume must be nonnegative or missing")
    source["htf_epoch"] = _segments(index)
    source["htf_open"] = index.floor("4h")
    source["_observed_open"] = index
    groups = source.groupby(["htf_epoch", "htf_open"], sort=True)
    bars = groups.agg(open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
        volume=("volume", lambda v: v.sum(min_count=4)),
        _count=("open", "size"), _first=("_observed_open", "min"),
        _last=("_observed_open", "max")).reset_index()
    complete = (bars._count.eq(4) & bars._first.eq(bars.htf_open)
        & bars._last.eq(bars.htf_open + 3 * HOUR))
    return bars.loc[complete, ["htf_epoch", "htf_open", *OHLCV]].set_index("htf_open")


def htf_fields(frame):
    """Same-epoch asof join of complete recomputed 4H momentum to 1H closes.

    ``htf_close`` is a UTC timestamp, and ``htf_age_hours`` is0..3 when any
    complete bar is mapped, even during warmup. Before the first complete bar
    in an epoch: close/age/MD/SB are missing, count0, unknownTrue, gatepassTrue.
    Readiness depends only on341 complete bars and finite MD/SB, never ATR,
    six-MA readiness, volume availability, or a future bucket's existence.
    """
    bars = aggregate_4h(frame)
    index = frame.index
    epochs = _segments(index)
    out = pd.DataFrame(index=index)
    out["htf_close"] = pd.Series(pd.NaT, index=index, dtype="datetime64[ns, UTC]")
    out["htf_age_hours"] = np.nan
    out["htf_count"] = np.zeros(len(frame), dtype=np.int64)
    out["htf_ready"] = False
    out["htf_md"] = np.nan
    out["htf_sb"] = np.nan
    out["htf_epoch"] = epochs
    for epoch, part in bars.groupby("htf_epoch", sort=False):
        # Recursive averages restart here; none of the gap-before values seed
        # this epoch. The original MD convention is zero before the SMMA seed.
        sm_high, sm_low = _smma(part.high, 34), _smma(part.low, 34)
        ema1 = _ema((part.high + part.low + part.close) / 3., 34)
        middle = 2. * ema1 - _ema(ema1, 34)
        md = pd.Series(np.where(middle > sm_high, middle - sm_high,
            np.where(middle < sm_low, middle - sm_low, 0.)), index=part.index)
        sb = _sma(md, 9)
        count = np.arange(1, len(part) + 1, dtype=np.int64)
        ready = (count >= HTF_WARMUP) & np.isfinite(md) & np.isfinite(sb)
        closes = part.index + FOUR_HOURS
        positions = np.flatnonzero(epochs == epoch)
        decisions = index[positions] + HOUR
        available = closes.searchsorted(decisions, side="right") - 1
        valid = available >= 0
        target = index[positions[valid]]
        selected = available[valid]
        out.loc[target, "htf_close"] = closes[selected].to_numpy()
        out.loc[target, "htf_age_hours"] = (decisions[valid] - closes[selected]) / HOUR
        out.loc[target, "htf_count"] = count[selected]
        out.loc[target, "htf_ready"] = ready.to_numpy()[selected]
        out.loc[target, "htf_md"] = md.to_numpy()[selected]
        out.loc[target, "htf_sb"] = sb.to_numpy()[selected]
    out["htf_unknown"] = ~out.htf_ready
    out["htf_opposed"] = out.htf_ready & out.htf_md.lt(out.htf_sb)
    out["htf_gatepass"] = ~out.htf_opposed
    return out


def detect(frame):
    """Original V3 edge clock and children, with one 4H parent-only veto.

    Rejected raw edges are consumed, not queued and not counted as accepted
    cooldown events. Unknown HTF accepts the original early but stays labelled
    unknown. A later child belongs only to an accepted parent and never reapplies
    the higher-timeframe gate or creates a replacement parent/reference.
    """
    fields = early_fields(frame).join(htf_fields(frame))
    last_accepted = parent = None
    parent_high = np.nan
    previous_condition = child_sent = False
    rows = []
    for i, row in enumerate(fields.itertuples()):
        if i and frame.index[i] - frame.index[i - 1] != HOUR:
            last_accepted = parent = None
            parent_high = np.nan
            previous_condition = child_sent = False
        condition = bool(row.early_condition)
        edge = bool(condition and not previous_condition)
        cooldown = last_accepted is None or i - last_accepted >= 12
        early = bool(edge and cooldown and row.htf_gatepass)
        if early:
            last_accepted = parent = i
            parent_high, child_sent = float(row.prog_prior_high), False
        if parent is not None and i - parent > 3:
            parent, parent_high, child_sent = None, np.nan, False
        age = i - parent if parent is not None else None
        child = bool(parent is not None and not child_sent and frame.ready.iloc[i]
            and frame.close.iloc[i] > parent_high and row.tag_recent_density
            and row.tag_advance and row.tag_volume and row.tag_md_ge_signal and row.tag_middle_rising)
        if child:
            child_sent = True
        rows.append(dict(early=early, confirmed=child,
            parent_i=float(parent) if parent is not None else np.nan,
            frozen_parent_high=parent_high, confirm_age=float(age) if child else np.nan,
            candidate_edge=edge, cooldown_blocked=bool(edge and not cooldown),
            htf_blocked=bool(edge and cooldown and row.htf_opposed)))
        previous_condition = condition
    return fields.join(pd.DataFrame(rows, index=frame.index))
