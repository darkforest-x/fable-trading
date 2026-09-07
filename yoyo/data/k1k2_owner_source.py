"""Pure, closed-hour owner-source K1 -> K2 morphology, without trading gates.

Source of truth is ``f_findBestK1`` and the indicator-state block in
``experiments/active/exp-btcusdtp-1h-owner-causal-v2-preholdout-20260904-v1/``
``pine/fable_k1_k2_owner_causal_v2.pine`` (lines 77--203). This extracts the
approved FIXED_SPEC shape, not Pine's later next-open/cost/cooldown/exit code.

Input columns are open_time/open/high/low/close/volume, optionally segment_id.
Each row is an already completed hour supplied by the caller. No wall clock,
file, network, future open, outcome, or eligibility lookup is performed here.
Feature windows, entirely within each contiguous segment, are:
* hl2: current high/low; sma40_hl2: the last 40 HL2 values, including current.
* true_range: current high/low and preceding close (first bar: high-low).
* atr14: first 14 TR values' mean, then Wilder (13 * prior + TR) / 14.
* sma20/60/120: complete trailing close windows; ema20/60/120: first-close
  seed, alpha=2/(period+1), recursively including the current close.
* rope_high/low/mid: all six close MAs; ALL six must be finite, without skipna.
* ma_candle_side: current HL2 >= SMA40 gives +1, below gives -1; 0 is warmup.

Thus the first possible K1 needs 120 segment bars; gap 2 permits the first K2
at count 122. The caller supplies the full available historical prefix; EMA
seeds on a truncated prefix are not asserted to match a longer Pine history.
Candidate geometry uses K2 and a K1 2--8 bars earlier plus every strictly
intermediate close/HL2/SMA. K1's quality uses its six-MA rope, body, range/ATR,
and rope-cross depths. Strict quality improvement keeps the shorter gap on
ties. Event clocks are K1 open+1h and K2 open+1h, not an assumed fill time.

The last supplied closed bar is eligible. Candidates are NOT executions: no
next-open risk, fee/risk, cooldown, K1 reuse suppression, single-position
policy, future-horizon eligibility, stop execution, or profit label is added.

Pandas 2.3.3 window conventions:
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.DataFrame.rolling.html
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.DataFrame.ewm.html
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse import _rma


BAR_COLUMNS = ["open_time", "open", "high", "low", "close", "volume"]
MA_COLUMNS = ["sma20", "ema20", "sma60", "ema60", "sma120", "ema120"]
FEATURE_COLUMNS = [
    "hl2", "true_range", "atr14", "sma40_hl2", *MA_COLUMNS,
    "rope_high", "rope_low", "rope_mid", "ma_candle_side",
]
CANDIDATE_COLUMNS = [
    "event_id", "venue", "symbol", "timeframe", "segment_id", "direction",
    "k1_time", "k1_decision_time", "k2_time", "decision_time", "gap_bars",
    "initial_stop", "signal_atr", "ma", "k1_quality", "range_atr", "body_ratio",
    "k1_open", "k1_high", "k1_low", "k1_close", "k1_atr", "k1_ma", "k1_hl2",
    "k1_close_location", "k1_entry_depth", "k1_exit_depth", "k1_ma_side",
    "k1_rope_high", "k1_rope_low", "k1_rope_mid", "k1_rope_coverage",
    "k1_rope_entry_depth", "k1_rope_exit_depth", "k1_rope_cross_depth",
    "k2_open", "k2_high", "k2_low", "k2_close", "k2_body_ratio",
    "k2_wick_share", "k2_reject_location", "k2_touch_depth", "k2_close_side",
    "k2_body_trend_side", "middle_wrong_closes", "middle_aligned_ma_bars",
]
HOUR = pd.Timedelta(hours=1)


def _bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Fail closed on malformed hours; never reorder, fill, or mutate input."""
    if not isinstance(frame, pd.DataFrame) or frame.columns.duplicated().any():
        raise ValueError("bars must be a DataFrame with unique column names")
    missing = set(BAR_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError("missing bar columns: %s" % sorted(missing))
    result = frame.copy(deep=True).reset_index(drop=True)
    times = result["open_time"]
    if len(times):
        if pd.api.types.is_numeric_dtype(times.dtype):
            raise ValueError("open_time must be timezone-aware, not numeric")
        for value in times:
            stamp = pd.Timestamp(value)
            if pd.isna(stamp) or stamp.tzinfo is None:
                raise ValueError("open_time must be non-null and timezone-aware")
    result["open_time"] = pd.to_datetime(times, utc=True, errors="raise").dt.as_unit("ns")
    times = result["open_time"]
    if times.isna().any() or times.duplicated().any() or not times.is_monotonic_increasing:
        raise ValueError("open_time must be unique and strictly increasing")
    if not times.eq(times.dt.floor("h")).all():
        raise ValueError("open_time must be aligned to the exact UTC hourly grid")
    for name in BAR_COLUMNS[1:]:
        if result[name].map(lambda x: isinstance(x, (bool, np.bool_))).any():
            raise ValueError("OHLCV must not contain booleans")
        result[name] = pd.to_numeric(result[name], errors="raise").astype(float)
    if not np.isfinite(result[BAR_COLUMNS[1:]].to_numpy(dtype=float)).all():
        raise ValueError("OHLCV must be finite")
    invalid = (
        result[["open", "high", "low", "close"]].le(0).any(axis=1)
        | result["volume"].lt(0)
        | result["low"].gt(result[["open", "close"]].min(axis=1))
        | result["high"].lt(result[["open", "close"]].max(axis=1))
    )
    if invalid.any():
        raise ValueError("invalid positive-price OHLC geometry or negative volume")
    gap = times.diff().ne(HOUR)
    if "segment_id" not in result:
        result["segment_id"] = gap.cumsum().sub(1).astype("int64")
    else:
        segments = result["segment_id"]
        if segments.isna().any() or segments.map(lambda x: isinstance(x, (bool, np.bool_))).any():
            raise ValueError("segment_id must be non-null and non-boolean")
        try:
            boundary = segments.ne(segments.shift())
            if segments.loc[boundary].duplicated().any():
                raise ValueError("segment_id must not reappear after another segment")
            if (gap & ~boundary).any():
                raise ValueError("a segment cannot span a missing hour")
        except TypeError as exc:
            raise ValueError("segment_id must contain scalar hashable identities") from exc
    return result


def add_features(hourly: pd.DataFrame) -> pd.DataFrame:
    """Return source-exact feature formulas; independent segments reset all state.

    Supplied segment boundaries may reset a contiguous history, but a supplied
    segment must not span a time gap or reappear later. If absent, segment_id is
    derived from gaps. Existing feature columns are rejected, not overwritten.
    The input's non-feature columns and row order are retained in a copied frame.
    """
    if set(FEATURE_COLUMNS).intersection(hourly.columns):
        raise ValueError("refusing to overwrite existing owner-source feature columns")
    result = _bars(hourly)
    for column in FEATURE_COLUMNS:
        result[column] = np.nan
    for _, part in result.groupby("segment_id", sort=False):
        idx = part.index
        high, low, close = part["high"], part["low"], part["close"]
        hl2 = high / 2.0 + low / 2.0
        true_range = pd.concat([
            high - low, (high - close.shift()).abs(), (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        result.loc[idx, "hl2"] = hl2
        result.loc[idx, "true_range"] = true_range
        result.loc[idx, "atr14"] = _rma(true_range, 14)
        result.loc[idx, "sma40_hl2"] = hl2.rolling(40, min_periods=40).mean()
        for period in (20, 60, 120):
            result.loc[idx, "sma%d" % period] = close.rolling(period, min_periods=period).mean()
            result.loc[idx, "ema%d" % period] = close.ewm(span=period, adjust=False).mean()
    mas = result[MA_COLUMNS]
    all_finite = np.isfinite(mas.to_numpy(dtype=float)).all(axis=1)
    result["rope_high"] = mas.max(axis=1, skipna=False).where(all_finite)
    result["rope_low"] = mas.min(axis=1, skipna=False).where(all_finite)
    result["rope_mid"] = (mas.sum(axis=1, skipna=False) / 6.0).where(all_finite)
    valid_ma = result["sma40_hl2"].notna()
    result["ma_candle_side"] = np.where(
        valid_ma, np.where(result["hl2"].ge(result["sma40_hl2"]), 1, -1), 0,
    ).astype("int64")
    if np.isinf(result[FEATURE_COLUMNS].to_numpy(dtype=float)).any():
        raise ValueError("source feature calculation overflowed")
    return result


def _event_id(venue: str, symbol: str, direction: int, k1: pd.Timestamp, k2: pd.Timestamp) -> str:
    identity = [venue, symbol, "1h", direction, k1.isoformat(), k2.isoformat()]
    payload = json.dumps(identity, ensure_ascii=True, separators=(",", ":"))
    return "k1k2_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def detect_candidates(
    featured: pd.DataFrame, *, venue: str = "OKX", symbol: str = "BTC-USDT-SWAP",
) -> pd.DataFrame:
    """One best K1 per closed K2/direction, including the final supplied row.

    Required features are FEATURE_COLUMNS produced by add_features. Inputs may
    contain warmup NaNs, but an unknown intermediate MA, unavailable six-MA
    rope, or zero ATR cannot qualify. No positional index enters event_id.
    Output is CANDIDATE_COLUMNS, ordered by K2 time and then long before short.
    k1_quality/range_atr/body_ratio describe K1; signal_atr/ma/initial_stop
    describe K2. All extra geometry is contemporaneous or prior, never a label.
    """
    if not isinstance(venue, str) or not venue.strip() or venue != venue.strip():
        raise ValueError("venue must be a non-empty normalized string")
    if not isinstance(symbol, str) or not symbol.strip() or symbol != symbol.strip():
        raise ValueError("symbol must be a non-empty normalized string")
    frame = _bars(featured)
    missing = set(FEATURE_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError("missing owner-source features: %s" % sorted(missing))
    for name in FEATURE_COLUMNS:
        if frame[name].map(lambda x: isinstance(x, (bool, np.bool_))).any():
            raise ValueError("owner-source features must not contain booleans")
        frame[name] = pd.to_numeric(frame[name], errors="raise").astype(float)
    if np.isinf(frame[FEATURE_COLUMNS].to_numpy(dtype=float)).any():
        raise ValueError("owner-source features must be finite or warmup NaN")
    if frame["atr14"].lt(0).any():
        raise ValueError("ATR cannot be negative")
    rows: List[Dict[str, Any]] = []
    for segment, part in frame.groupby("segment_id", sort=False):
        a = {column: part[column].to_numpy() for column in BAR_COLUMNS[1:] + FEATURE_COLUMNS}
        times = list(part["open_time"])
        for k2 in range(2, len(part)):
            o2, h2, l2, c2 = (float(a[x][k2]) for x in ("open", "high", "low", "close"))
            ma2, atr2 = float(a["sma40_hl2"][k2]), float(a["atr14"][k2])
            r2 = h2 - l2
            if r2 <= 0 or not np.isfinite([ma2, atr2]).all() or atr2 <= 0:
                continue
            body2 = abs(c2 - o2) / r2
            for direction in (1, -1):
                wick = (min(o2, c2) - l2 if direction == 1 else h2 - max(o2, c2)) / r2
                reject = (c2 - l2 if direction == 1 else h2 - c2) / r2
                touch = (ma2 - l2 if direction == 1 else h2 - ma2) / atr2
                close_side = direction * (c2 - ma2) / atr2
                body_side = min(o2, c2) >= ma2 if direction == 1 else max(o2, c2) <= ma2
                if not (wick >= .25 and body2 <= .50 and reject >= .25
                        and 0 <= touch <= 1.50 and close_side >= 0 and body_side):
                    continue
                best = None
                for gap in range(2, min(8, k2) + 1):
                    k1 = k2 - gap
                    o, h, l, c = (float(a[x][k1]) for x in ("open", "high", "low", "close"))
                    ma, atr = float(a["sma40_hl2"][k1]), float(a["atr14"][k1])
                    rope_h, rope_l, rope_m = (float(a[x][k1]) for x in ("rope_high", "rope_low", "rope_mid"))
                    if not np.isfinite([ma, atr, rope_h, rope_l, rope_m]
                                       + [a[x][k1] for x in MA_COLUMNS]).all() or atr <= 0 or h <= l:
                        continue
                    body = abs(c - o) / (h - l)
                    range_atr = (h - l) / atr
                    location = (c - l if direction == 1 else h - c) / (h - l)
                    entry_depth, exit_depth = direction * (ma - o) / atr, direction * (c - ma) / atr
                    side = 1 if (h / 2.0 + l / 2.0) >= ma else -1
                    if not (direction * (c - o) > 0 and body >= .65 and range_atr >= .95
                            and location >= .70 and entry_depth >= -.05 and exit_depth >= -.05
                            and side == direction):
                        continue
                    middle = slice(k1 + 1, k2)
                    middle_ma = a["sma40_hl2"][middle]
                    middle_close = a["close"][middle]
                    middle_hl2 = a["high"][middle] / 2.0 + a["low"][middle] / 2.0
                    if not np.isfinite(middle_ma).all():
                        continue
                    wrong = int((direction * (middle_close - middle_ma) < 0).sum())
                    aligned = int((np.where(middle_hl2 >= middle_ma, 1, -1) == direction).sum())
                    if wrong or aligned != gap - 1:
                        continue
                    body_low, body_high = min(o, c), max(o, c)
                    width = rope_h - rope_l
                    overlap = max(0.0, min(body_high, rope_h) - max(body_low, rope_l))
                    coverage = min(1.0, overlap / width) if width > 0 else float(body_low <= rope_m <= body_high)
                    rope_entry = (rope_l - o if direction == 1 else o - rope_h) / atr
                    rope_exit = (c - rope_h if direction == 1 else rope_l - c) / atr
                    depth = min(rope_entry, rope_exit)
                    quality = (min(1., coverage) + min(1., max(0., body))
                               + min(1., max(0., range_atr / 2.))
                               + min(1., max(0., (depth + .15) / .50))) / 4.
                    if best is not None and quality <= best["k1_quality"]:
                        continue
                    best = {
                        "event_id": _event_id(venue, symbol, direction, times[k1], times[k2]),
                        "venue": venue, "symbol": symbol, "timeframe": "1h", "segment_id": segment,
                        "direction": direction, "k1_time": times[k1], "k1_decision_time": times[k1] + HOUR,
                        "k2_time": times[k2], "decision_time": times[k2] + HOUR, "gap_bars": gap,
                        "initial_stop": l2 if direction == 1 else h2, "signal_atr": atr2, "ma": ma2,
                        "k1_quality": quality, "range_atr": range_atr, "body_ratio": body,
                        "k1_open": o, "k1_high": h, "k1_low": l, "k1_close": c,
                        "k1_atr": atr, "k1_ma": ma, "k1_hl2": h / 2.0 + l / 2.0,
                        "k1_close_location": location, "k1_entry_depth": entry_depth,
                        "k1_exit_depth": exit_depth, "k1_ma_side": side,
                        "k1_rope_high": rope_h, "k1_rope_low": rope_l, "k1_rope_mid": rope_m,
                        "k1_rope_coverage": coverage, "k1_rope_entry_depth": rope_entry,
                        "k1_rope_exit_depth": rope_exit, "k1_rope_cross_depth": depth,
                        "k2_open": o2, "k2_high": h2, "k2_low": l2, "k2_close": c2,
                        "k2_body_ratio": body2, "k2_wick_share": wick, "k2_reject_location": reject,
                        "k2_touch_depth": touch, "k2_close_side": close_side,
                        "k2_body_trend_side": bool(body_side), "middle_wrong_closes": wrong,
                        "middle_aligned_ma_bars": aligned,
                    }
                if best is not None:
                    rows.append(best)
    result = pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)
    if not result.empty and (result["event_id"].duplicated().any()
                             or result.duplicated(["k2_time", "direction"]).any()):
        raise ValueError("duplicate owner-source candidate identity")
    return result
