"""Finite-memory causal breakout/first-retest observations, without a model.

Columns: open/high/low/close/volume/quote_volume/confirmed, UTC candle opens.
Each raw breakout uses the preceding ``lookback`` candles for range and median
volume, excluding its own candle: close > prior high and relative volume >=
``volume_multiple``. An episode anchor must additionally have NO raw breakout
in its preceding 12 candles. This nonrecursive de-clustering rule is crucial:
accepted-episode state must not be seeded at an arbitrary API response start.

Only an anchor within the latest 12 subsequent candles can be active. Its
preceding 12 raw flags each need their own ``lookback`` history, so a complete
snapshot requires ``lookback + 2 * 12 + 1`` closed candles (45 by default).
Any longer continuous suffix ending at the same candle returns identical
results. No persistent state or ever-increasing download limit is required.

Unvalidated v1 heuristics: a first later low at/below 1% above the frozen high,
with close at/above that high and smaller volume than the anchor, is a retest.
A low below the frozen range low invalidates the anchor for its remaining
lifetime, even if price subsequently recovers. A close more than max(5%,
min(15%, 2 * prior range width ratio)) above the high is extended. Frozen range,
breakout time and first retest never shift to a later raw breakout in a cluster.
No ATR, future candles, labels, order instructions or trained model are used.
"""
from __future__ import annotations

from typing import Any

from yoyo.data.rotation_features import INTERVALS, closed_prefix, finite_number, utc_as_of


MAX_EPISODE_BARS = 12
RETEST_TOLERANCE = 0.01


def detect_setup(bars, *, as_of: Any, interval: str = "4h", lookback: int = 20,
                 volume_multiple: float = 2.0) -> dict:
    """Describe only the latest available candle using a bounded dependency cone.

    Raw breakouts de-cluster against RAW flags, never recursively accepted
    episodes. Current anchors lie in [tip-12, tip]; their preceding raw flags
    lie in [tip-24, tip-1]. Every raw flag uses only its own prior range/volume.
    A pullback appears on its actual first retest candle, never on the anchor.
    Invalidated anchors remain invalidated until their 12-candle lifetime ends.
    """
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 2:
        raise ValueError("lookback must be an integer of at least 2")
    multiple = finite_number(volume_multiple)
    if multiple is None or multiple <= 0:
        raise ValueError("volume_multiple must be finite and positive")
    frame = closed_prefix(bars, as_of=as_of, interval=interval)
    required = lookback + 2 * MAX_EPISODE_BARS + 1
    result = {
        "state": "insufficient_data", "as_of": utc_as_of(as_of).isoformat(),
        "interval": interval, "decision_at": None, "range_high": None,
        "range_low": None, "relative_volume": None, "entry_reference": None,
        "stop_reference": None, "breakout_at": None, "first_pullback_at": None,
        "invalidated_at": None, "required_completed_candles": required,
        "reasons": [], "heuristic": True,
    }
    if not frame.empty:
        result["decision_at"] = (frame.index[-1] + INTERVALS[interval]).isoformat()
        result["entry_reference"] = finite_number(frame.close.iloc[-1])
    if len(frame) < required:
        result["reasons"] = [f"need_{required}_completed_candles_for_origin_invariance:have_{len(frame)}"]
        return result

    tip = len(frame) - 1
    first_raw = tip - 2 * MAX_EPISODE_BARS
    raw = {}
    for index in range(first_raw, tip + 1):
        past = frame.iloc[index - lookback:index]
        candle = frame.iloc[index]
        median = float(past.volume.median())
        relative = finite_number(candle.volume / median) if median > 0 else None
        high, low = float(past.high.max()), float(past.low.min())
        raw[index] = {
            "qualified": bool(candle.close > high and relative is not None and relative >= multiple),
            "range_high": high, "range_low": low, "relative_volume": relative,
        }
    anchors = [
        index for index in range(tip - MAX_EPISODE_BARS, tip + 1)
        if raw[index]["qualified"] and not any(
            raw[previous]["qualified"] for previous in range(index - MAX_EPISODE_BARS, index)
        )
    ]
    # Two anchors require at least 13 candles of separation, so this 13-candle
    # inclusive range can contain at most one. The latest form is explicit.
    anchor = anchors[-1] if anchors else None
    evidence = raw[tip] if anchor is None else raw[anchor]
    range_high, range_low = evidence["range_high"], evidence["range_low"]
    result.update({
        "state": "watch", "range_high": finite_number(range_high),
        "range_low": finite_number(range_low), "stop_reference": finite_number(range_low),
        "relative_volume": raw[tip]["relative_volume"],
    })
    if anchor is None:
        reasons = ["no_declustered_breakout_anchor_in_last_12_subsequent_candles"]
        if raw[tip]["qualified"]:
            reasons.append("raw_breakout_cluster_needs_12_quiet_candles_before_a_new_anchor")
        if raw[tip]["relative_volume"] is None:
            reasons.append("zero_prior_median_volume_breakout_unverifiable")
        result["reasons"] = reasons + ["unvalidated_structure_observation_only"]
        return result

    result["breakout_at"] = (frame.index[anchor] + INTERVALS[interval]).isoformat()
    anchor_volume = float(frame.volume.iloc[anchor])
    extension = max(0.05, min(0.15, 2 * (range_high / range_low - 1)))
    first_pullback_at = None
    invalidated_at = None
    for index in range(anchor, tip + 1):
        candle = frame.iloc[index]
        timestamp = (frame.index[index] + INTERVALS[interval]).isoformat()
        state = "breakout" if index == anchor else "watch"
        reasons = (["close_above_prior_range_after_12_raw_breakout_free_candles"] if index == anchor
                   else ["frozen_breakout_anchor_active_waiting_for_new_structure"])
        # Intrabar ordering cannot establish whether a structural breach came
        # before or after a breakout/retest. Refuse the entire conflicting bar.
        if invalidated_at is None and candle.low < range_low:
            invalidated_at = timestamp
        if invalidated_at is not None:
            state = "invalidated"
            reasons = ["frozen_structural_low_breached_anchor_invalid_for_remaining_lifetime"]
        elif (index > anchor and first_pullback_at is None
              and candle.low <= range_high * (1 + RETEST_TOLERANCE)
              and range_high <= candle.close <= range_high * (1 + extension)
              and candle.volume < anchor_volume):
            first_pullback_at = timestamp
            state = "pullback"
            reasons = ["first_later_retest_holds_frozen_range_high_on_smaller_volume"]
        if state != "invalidated" and candle.close / range_high - 1 > extension:
            state = "extended"
            reasons.append("close_beyond_frozen_range_extension_heuristic")
        result.update({"state": state, "first_pullback_at": first_pullback_at,
                       "invalidated_at": invalidated_at,
                       "reasons": reasons + ["unvalidated_structure_observation_only"]})
    return result
