"""Owner-selected V9 admission bundle, with causal reference-risk diagnostics.

Source: V8 and the fixed six-filter study (2026-09-14); owner authorized the
combined USDC-base, RV>50 and UTC-Sunday exclusions on 2026-09-15. No search or
new performance evaluation is implied. Volume ratio is the existing ``rv``:
signal volume / median of the preceding 20 valid volumes in the same segment.
The calendar uses signal close, the scheduled next open on contiguous candles.
Reference risk uses current close/ATR and the last five highs/lows including
the signal. Actual next-open price is never read to explain the signal.

Only the entry mask changes. ``replay_v9`` delegates to the original serial
engine, which still receives every raw V6 reversal and its original stop rules.
"""
from __future__ import annotations

from dataclasses import replace
import math

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import risk_reference
from yoyo.evaluation.spike_v8_replay import replay_mask, v8_admissions


VERSION = "spike-v9-entry-bundle-20260915-v1"
MAX_VOLUME_RATIO = 50.0
ROUND_TRIP_COST = 0.002


def entry_decision(base_asset: str | None, volume_ratio: float,
                   scheduled_open: pd.Timestamp) -> dict[str, object]:
    """Apply the three fixed gates using explicit base metadata and UTC time.

    Unknown inputs are visible and do not pass. Never derive base from a ticker
    substring: ETH/USDC is ETH, whereas USDC/USDT is the excluded USDC asset.
    """
    asset = base_asset.strip().upper() if isinstance(base_asset, str) else ""
    try:
        rv = float(volume_ratio)
    except (TypeError, ValueError):
        rv = math.nan
    stamp = pd.Timestamp(scheduled_open)
    clock_known = not pd.isna(stamp) and stamp.tzinfo is not None
    utc = stamp.tz_convert("UTC") if clock_known else pd.NaT
    reasons: list[str] = []
    if not asset:
        reasons.append("base_asset_unknown")
    elif asset == "USDC":
        reasons.append("base_usdc")
    if not math.isfinite(rv) or rv < 0:
        reasons.append("volume_ratio_unknown")
    elif rv > MAX_VOLUME_RATIO:
        reasons.append("volume_ratio_gt50")
    if not clock_known:
        reasons.append("scheduled_open_unknown")
    elif utc.dayofweek == 6:
        reasons.append("scheduled_open_utc_sunday")
    return {"base_asset": asset or None, "volume_ratio": rv,
            "scheduled_open_utc": utc, "v9_bundle_allowed": not reasons,
            "v9_bundle_reasons": "|".join(reasons) if reasons else "passed"}


def v9_admissions(context) -> pd.DataFrame:
    """Return V9 decisions for supplied closed bars; perform no file/data reads.

    ``context`` follows the existing StreamContext contract. Identity ``asset``
    must be venue-derived base metadata. Optional ``asset_type`` is descriptive
    only: its absence is reported as unknown, not inferred to mean crypto.
    Risk diagnostics use high/low over [t-4,t], current close/ATR and cache tick.
    They describe the confirmation price, not a filled trade or account risk.
    """
    bars = context.cache["bars"]
    if (not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None
            or not bars.index.is_monotonic_increasing or not bars.index.is_unique
            or context.minutes <= 0):
        raise ValueError("V9 requires one ordered timezone-aware fixed-duration clock")
    result = v8_admissions(context)
    delta = pd.Timedelta(minutes=context.minutes)
    close_times = bars.index + delta
    volume = bars.get("rv", pd.Series(np.nan, index=bars.index))
    base_asset = context.identity.get("asset")
    decisions = pd.DataFrame(
        [entry_decision(base_asset, rv, stamp) for rv, stamp in zip(volume, close_times)],
        index=bars.index,
        columns=["base_asset", "volume_ratio", "scheduled_open_utc", "v9_bundle_allowed",
                 "v9_bundle_reasons"],
    )
    result = result.join(decisions)
    result["v9"] = result.v8 & result.v9_bundle_allowed
    result["v9_reason"] = np.where(~result.v8, result.reason, result.v9_bundle_reasons)
    result["strategy_version"] = VERSION
    result["asset_type"] = context.identity.get("asset_type") or "unknown"
    result["reference_price"] = np.where(result.side.ne(0), bars.close, np.nan)
    result["reference_initial_stop"] = np.nan
    result["reference_initial_risk"] = np.nan
    result["reference_risk_fraction"] = np.nan
    result["reference_cost_r"] = np.nan
    result["risk_basis"] = "confirmation_close_reference_not_fill"
    result["risk_status"] = "no_raw_signal"
    low = bars.low.rolling(5, min_periods=5).min()
    high = bars.high.rolling(5, min_periods=5).max()
    gaps = context.cache.get("data_gap", pd.Series(False, index=bars.index))
    for i in np.flatnonzero(result.side.to_numpy()):
        stamp = bars.index[i]
        result.at[stamp, "risk_status"] = "reference_unavailable"
        if i < 4 or bool(gaps.iloc[i-3:i+1].any()):
            continue
        if not all(bars.index[j] - bars.index[j-1] == delta for j in range(i-3, i+1)):
            continue
        side = int(result.side.iloc[i])
        entry = float(bars.close.iloc[i])
        ref = risk_reference(side, entry, float(low.iloc[i] if side == 1 else high.iloc[i]),
                             float(bars.atr.iloc[i]), tick=float(context.cache["tick"]))
        if not ref.valid:
            continue
        fraction = ref.risk / entry
        result.loc[stamp, ["reference_initial_stop", "reference_initial_risk",
                           "reference_risk_fraction", "reference_cost_r"]] = (
                               ref.stop, ref.risk, fraction, ROUND_TRIP_COST / fraction)
        result.at[stamp, "risk_status"] = "known"
    return result


def replay_v9(context):
    """Run only V9 on caller-provided approved input, preserving raw reversals.

    No default dates, history loader, control arms or parameter search. Callers
    remain responsible for dataset authorization before supplying ``context``.
    Missing next bars cancel pending entries through the original gap handler;
    gaps are recognized at their arrival, never used to rewrite past decisions.
    Returns version-tagged trades, fills, engine events, and admission evidence.
    """
    evidence = v9_admissions(context)
    cache = dict(context.cache)
    index = cache["bars"].index
    clock_gap = index.to_series().diff().ne(pd.Timedelta(minutes=context.minutes))
    if len(clock_gap):
        clock_gap.iloc[0] = False
    cache["data_gap"] = cache["data_gap"].reindex(index).fillna(True).astype(bool) | clock_gap
    trades, fills, events = replay_mask(replace(context, cache=cache), evidence.v9)
    for frame in (trades, fills, events):
        frame["strategy_version"] = VERSION
        frame["arm"] = "v9"
    return trades, fills, events, evidence
