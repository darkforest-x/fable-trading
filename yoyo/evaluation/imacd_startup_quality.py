"""Causal startup-formation features for research, with no outcome access.

Source: ``yoyo.monitor.signals._compute`` and the monitor's visible-focus
state machine (warmup index 340, 12 near-zero bars, band 0.10 * ATR[1]).
This module reproduces that event contract; it neither changes live signals
nor filters notifications. OHLCV inputs are confirmed candles indexed by
their UTC open time. Their supplied history origin seeds every recurrence.

At decision bar i, formation_early_width is the median raw six-MA width of
the first six bars of its focus segment, only once all six precede i.
formation_late_width uses i-6 .. i-1. contraction_ratio is late / early;
zero / zero is 1 and positive / zero is unknown. proximity_atr is the median
of close-to-six-MA-interval outside distances on i-12 .. i-1 divided by the
single ATR[i-1]. No current-candle range enters those formation measures.
fast_slow_delta_atr uses six MAs at i and i-3, divided by ATR[i-1]; its fast
center is (SMA20 + EMA20) / 2 and its slow center averages the four 60/120
MAs. separation_delta multiplies that change by the release direction.
Only this separation feature intentionally uses the confirmed decision bar.

No future prices, returns, labels, fitted thresholds, HTTP, storage writes,
monitor service calls, or execution-layer imports occur here. The three
keep_* columns are independent research comparisons, not a bundled rule.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.monitor import signals


MA_COLUMNS = ("sma20", "ema20", "sma60", "ema60", "sma120", "ema120")
FOCUS_MIN_BARS = 12
FOCUS_ATR_BAND = 0.10


def _validate_bars(bars: pd.DataFrame) -> dict[str, np.ndarray]:
    """Reject ambiguous clocks and nonfinite or geometrically invalid OHLCV."""
    if not isinstance(bars, pd.DataFrame):
        raise ValueError("bars must be a pandas DataFrame")
    idx = bars.index
    if not isinstance(idx, pd.DatetimeIndex) or idx.tz is None:
        raise ValueError("bars must have a UTC DatetimeIndex")
    if str(idx.tz) not in ("UTC", "Etc/UTC", "GMT", "Etc/GMT", "UTC+00:00"):
        raise ValueError("bars must have a UTC DatetimeIndex")
    if idx.hasnans or not idx.is_unique or not idx.is_monotonic_increasing:
        raise ValueError("bar times must be finite, increasing and unique")
    if len(idx) > 2:
        steps = idx[1:] - idx[:-1]
        if not np.all(steps == steps[0]):
            raise ValueError("bar times must be gap-free with equal intervals")
    columns = ("open", "high", "low", "close", "volume")
    if not bars.columns.is_unique or not set(columns).issubset(bars.columns):
        raise ValueError("bars must contain unique open/high/low/close/volume columns")
    try:
        values = bars.loc[:, columns].to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("OHLCV must be finite numeric values") from exc
    if not np.isfinite(values).all():
        raise ValueError("OHLCV must be finite numeric values")
    o, h, l, c, v = values.T
    if np.any((l <= 0) | (v < 0) | (h < np.maximum(o, c)) | (l > np.minimum(o, c))):
        raise ValueError("invalid OHLCV range")
    time_ms = idx.tz_localize(None).to_numpy(dtype="datetime64[ms]").astype(np.int64)
    return dict(t=time_ms, o=o, h=h, l=l, c=c, v=v)


def build_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Return all input rows with causal indicators, focus events and features.

    See the module docstring for exact source columns, windows and availability.
    ``qualified`` and ``focus_band`` are states after processing this candle,
    matching the monitor chart; ``release_band`` is its pre-release frozen band.
    ``near_zero_bars`` and ``focus_start_i`` preserve the prior segment on a
    release candle, and otherwise describe the current segment. Missing start
    indices use nullable Int64. ``separation_delta`` is NaN without a release;
    ``fast_slow_delta_atr`` provides its unsigned counterpart on all rows for
    separately specified controls. Boolean keep columns fail closed on NaN.
    """
    b = _validate_bars(bars)
    n = len(bars)
    # _compute assumes at least one bar when constructing previous-close/TR.
    if not n:
        template = build_features(pd.DataFrame(
            {"open": [100.0], "high": [101.0], "low": [99.0],
             "close": [100.0], "volume": [1.0]},
            index=pd.date_range("1970-01-01", periods=1, freq="h", tz="UTC"),
        ))
        template = template.iloc[:0].copy()
        template.index = bars.index.copy()
        return template

    f = signals._compute(b)
    run = 0
    qualified = False
    frozen_band = np.nan
    start = -1
    release_side = np.zeros(n, dtype=np.int8)
    near_zero_bars = np.zeros(n, dtype=np.int64)
    focus_start = np.full(n, -1, dtype=np.int64)
    qualified_rows = np.zeros(n, dtype=bool)
    focus_band = np.full(n, np.nan)
    release_band = np.full(n, np.nan)

    for i in range(n):
        prior_run, prior_start = run, start
        candidate_band = FOCUS_ATR_BAND * f["atr"][i - 1] if i else np.nan
        if f["ready"][i] and np.isfinite(candidate_band):
            magnitude = max(abs(f["md"][i]), abs(f["sb"][i]))
            if qualified:
                if magnitude <= frozen_band:
                    run += 1
                else:
                    md = f["md"][i]
                    side = 1 if md > frozen_band else -1 if md < -frozen_band else 0
                    if side:
                        release_side[i] = side
                        release_band[i] = frozen_band
                    qualified, run, start, frozen_band = False, 0, -1, np.nan
            elif magnitude <= candidate_band:
                run += 1
                if run == 1:
                    start = i
                if run >= FOCUS_MIN_BARS:
                    qualified, frozen_band = True, float(candidate_band)
            else:
                run, start = 0, -1
        near_zero_bars[i] = prior_run if release_side[i] else run
        focus_start[i] = prior_start if release_side[i] else start
        qualified_rows[i], focus_band[i] = qualified, frozen_band

    out = pd.DataFrame({name: f[name] for name in (
        "md", "sb", "sh", "atr", *MA_COLUMNS, "rope_high", "rope_low",
        "dense", "dense_recent", "prior_width_atr", "prior_crosses", "ready",
    )}, index=bars.index.copy())
    out["release_side"] = release_side
    out["near_zero_bars"] = near_zero_bars
    out["focus_start_i"] = pd.array([x if x >= 0 else pd.NA for x in focus_start], dtype="Int64")
    out["qualified"] = qualified_rows
    out["focus_band"] = focus_band
    out["release_band"] = release_band

    width = out["rope_high"] - out["rope_low"]
    early = np.full(n, np.nan)
    # A start point is available only from current/past state, never backfilled.
    first_six_width = width.rolling(6).median().to_numpy()
    for i, start_i in enumerate(focus_start):
        if start_i >= 0 and start_i + 6 <= i:
            early[i] = first_six_width[start_i + 5]
    late = width.shift(1).rolling(6).median().to_numpy()
    ratio = np.divide(late, early, out=np.full(n, np.nan), where=early > 0)
    ratio[(early == 0) & (late == 0)] = 1.0
    atr_previous = out["atr"].shift(1).where(lambda x: x > 0)
    outside = pd.Series(np.maximum.reduce([
        out["rope_low"].to_numpy() - b["c"],
        b["c"] - out["rope_high"].to_numpy(),
        np.zeros(n),
    ]), index=bars.index)
    proximity = outside.shift(1).rolling(12).median() / atr_previous
    fast = (out["sma20"] + out["ema20"]) / 2
    slow = out[["sma60", "ema60", "sma120", "ema120"]].mean(axis=1, skipna=False)
    delta = (fast - slow).diff(3) / atr_previous
    separation = delta * pd.Series(release_side, index=bars.index).replace(0, np.nan)
    out["formation_early_width"] = early
    out["formation_late_width"] = late
    out["contraction_ratio"] = ratio
    out["proximity_atr"] = proximity
    out["fast_slow_delta_atr"] = delta
    out["separation_delta"] = separation
    out["keep_contraction"] = np.isfinite(early) & np.isfinite(late) & (late <= early)
    out["keep_proximity"] = proximity.notna() & (proximity <= 1)
    out["keep_separation"] = separation.notna() & (separation > 0)
    return out
