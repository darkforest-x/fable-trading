"""Pure causal context descriptors for every original V9 long candidate.

``build_context_features`` accepts one native UTC, bar-open-indexed OHLC plus
the frozen six-MA cache columns ``s20/e20/s60/e60/s120/e120``.  A row is
available at its native bar close: ``index + minutes``.  The 24-hour returns
are ``close[t] / close[t-24h] - 1`` and require every native candle between
those endpoints.  Four-hour OHLC is UTC epoch-aligned and an aggregate is
available only when every native component is valid and its close time is no
later than that row's available-at time.  SMA20 and its three-completed-4H-bar
slope use only those complete aggregates.

The morphology windows are: current six-MA span divided by current close;
that span divided by the *prior* 96-native-bar median; the consecutive run of
prior compact spans (span <= its prior96 median); median span of the latest
four native bars divided by median span of the preceding sixteen; and a
prior-32-bar count of failed upbreaks.  An upbreak at ``j`` is a close that
crosses from at-or-below to above the frozen high of bars ``j-20..j-1``; it is
failed only if close ``j+1`` is below that same frozen level.  Both bars are
at or before the feature row.

Every descriptor has a ``*_known`` coverage column.  Gaps, partial groups,
invalid prices, unavailable MA history, and a missing/stale BTC observation
produce ``NaN`` and ``False`` rather than an imputed value.  ``btc_frame`` is
the only permitted benchmark source; this module never fetches or substitutes
another venue.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


MA_COLUMNS = ("s20", "e20", "s60", "e60", "s120", "e120")
OHLC_COLUMNS = ("open", "high", "low", "close")

VALUE_COLUMNS = (
    "btc_trailing24h_return",
    "btc_completed4h_close_sma20_fraction",
    "btc_completed4h_above_sma20",
    "symbol_completed4h_close_sma20_fraction",
    "symbol_completed4h_sma20_slope3_fraction",
    "symbol_relative_strength24h",
    "six_ma_span_price",
    "six_ma_span_to_prior96_median",
    "prior_compact_span_duration",
    "span_expansion_last4_vs_previous16",
    "prior32_failed_upbreak_count",
)
KNOWN_COLUMNS = tuple(f"{name}_known" for name in VALUE_COLUMNS)
FEATURE_COLUMNS = (*VALUE_COLUMNS, *KNOWN_COLUMNS, "symbol_native_known")


def _validate_minutes(minutes: int, *, name: str) -> int:
    if isinstance(minutes, bool) or not isinstance(minutes, (int, np.integer)) or minutes <= 0:
        raise ValueError(f"{name} must be a positive integer")
    if 1_440 % int(minutes) or 240 % int(minutes):
        raise ValueError(f"{name} must divide both 24 hours and four hours")
    return int(minutes)


def _validate_index(frame: pd.DataFrame, *, minutes: int, name: str) -> None:
    index = frame.index
    if (not isinstance(frame, pd.DataFrame) or not frame.columns.is_unique
            or not isinstance(index, pd.DatetimeIndex) or index.tz is None
            or index.hasnans or not index.is_unique or not index.is_monotonic_increasing):
        raise ValueError(f"{name} requires unique, ordered UTC-like timestamps")
    if (index.asi8 % pd.Timedelta(minutes=minutes).value).any():
        raise ValueError(f"{name} timestamps are not aligned to {minutes}-minute opens")


def _ohlc_valid(frame: pd.DataFrame) -> np.ndarray:
    values = frame.loc[:, OHLC_COLUMNS].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    open_, high, low, close = values.T
    return (np.isfinite(values).all(axis=1) & (low > 0) & (high >= np.maximum(open_, close))
            & (low <= np.minimum(open_, close)) & (close > 0))


def _runs(valid: np.ndarray, index: pd.DatetimeIndex, minutes: int) -> np.ndarray:
    """Return consecutive valid cadence run lengths ending at each row."""
    out = np.zeros(len(valid), dtype=int)
    step = pd.Timedelta(minutes=minutes).value
    for i, is_valid in enumerate(valid):
        if is_valid:
            out[i] = 1 if i == 0 or index.asi8[i] - index.asi8[i - 1] != step else out[i - 1] + 1
    return out


def _empty(index: pd.DatetimeIndex) -> pd.DataFrame:
    out = pd.DataFrame(index=index)
    for name in VALUE_COLUMNS:
        out[name] = np.nan
        out[f"{name}_known"] = False
    out["symbol_native_known"] = False
    return out.loc[:, FEATURE_COLUMNS]


def _safe_ratio(numerator: np.ndarray, denominator: np.ndarray,
                eligible: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Divide only finite nonzero denominators and return its coverage mask."""
    numerator, denominator = np.asarray(numerator, float), np.asarray(denominator, float)
    known = (np.asarray(eligible, bool) & np.isfinite(numerator)
             & np.isfinite(denominator) & (denominator != 0))
    out = np.full(len(numerator), np.nan)
    np.divide(numerator, denominator, out=out, where=known)
    return out, known


def _finalize(out: pd.DataFrame) -> pd.DataFrame:
    """Enforce the public contract: every unknown feature value is NaN."""
    for name in VALUE_COLUMNS:
        known = out[f"{name}_known"].eq(True).to_numpy(bool)
        out.loc[~known, name] = np.nan
    return out.loc[:, FEATURE_COLUMNS]


def _complete_four_hour(frame: pd.DataFrame, *, minutes: int) -> pd.DataFrame:
    """Aggregate strictly complete UTC 4H OHLC and causal SMA20 fields."""
    valid = _ohlc_valid(frame)
    work = frame.loc[:, OHLC_COLUMNS].apply(pd.to_numeric, errors="coerce").copy()
    work["_valid"] = valid.astype(int)
    grouped = work.resample("4h", origin="epoch", label="left", closed="left")
    out = grouped.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                      close=("close", "last"), rows=("close", "size"), valid_rows=("_valid", "sum"))
    expected = 240 // minutes
    out["complete"] = out.rows.eq(expected) & out.valid_rows.eq(expected)
    out["complete_close"] = out.close.where(out.complete)
    out["sma20"] = out.complete_close.rolling(20, min_periods=20).mean()
    out["close_sma20_fraction"] = out.complete_close / out.sma20 - 1.0
    out["sma20_slope3_fraction"] = out.sma20 / out.sma20.shift(3) - 1.0
    out["available_at"] = out.index + pd.Timedelta(hours=4)
    return out


def _four_hour_at_signal(frame: pd.DataFrame, *, minutes: int,
                         signal_closes: pd.DatetimeIndex) -> pd.DataFrame:
    """Join only the exact 4H bucket expected at each signal-bar close.

    The expected completed bucket closes at ``signal_close.floor('4h')``.  An
    incomplete bucket therefore cannot fall back to an older completed bucket.
    """
    four = _complete_four_hour(frame, minutes=minutes)
    expected_close = signal_closes.floor("4h")
    expected_open = expected_close - pd.Timedelta(hours=4)
    fields = four.reindex(expected_open)[["complete", "close_sma20_fraction", "sma20_slope3_fraction"]]
    fields.index = signal_closes - pd.Timedelta(minutes=minutes)
    # Exact expected_open reindex establishes no stale carry.  The comparison
    # also guards the leading interval where floor() has no preceding bucket.
    available = expected_close >= four.index[0] + pd.Timedelta(hours=4) if len(four) else np.zeros(len(fields), bool)
    complete = fields["complete"].eq(True).to_numpy(bool) & np.asarray(available, bool)
    close_fraction = fields.close_sma20_fraction.to_numpy(float)
    slope_fraction = fields.sma20_slope3_fraction.to_numpy(float)
    return pd.DataFrame({"complete": complete,
                         "close_sma20_fraction": close_fraction,
                         "sma20_slope3_fraction": slope_fraction}, index=fields.index)


def _native_returns(frame: pd.DataFrame, *, minutes: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return current-close 24H returns, coverage, and current-bar coverage."""
    valid = _ohlc_valid(frame)
    run = _runs(valid, frame.index, minutes)
    count = 1_440 // minutes
    close = frame.close.to_numpy(float)
    known = run >= count + 1
    out = np.full(len(frame), np.nan)
    positions = np.flatnonzero(known)
    out[positions] = close[positions] / close[positions - count] - 1.0
    known &= np.isfinite(out)
    return out, known, valid


def _btc_at_signal(btc_frame: pd.DataFrame, *, btc_minutes: int,
                   signal_closes: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    """Return exact-clock BTC 24H data and complete-4H context at signal closes."""
    returns, return_known, current_known = _native_returns(btc_frame, minutes=btc_minutes)
    close_times = btc_frame.index + pd.Timedelta(minutes=btc_minutes)
    # Reindex is exact: any absent current BTC candle is unknown, never stale.
    locations = close_times.get_indexer(signal_closes)
    exact = locations >= 0
    result = np.full(len(signal_closes), np.nan)
    known = np.zeros(len(signal_closes), dtype=bool)
    hit = np.flatnonzero(exact)
    result[hit] = returns[locations[hit]]
    known[hit] = return_known[locations[hit]] & current_known[locations[hit]]
    result[~known] = np.nan
    four = _four_hour_at_signal(btc_frame, minutes=btc_minutes, signal_closes=signal_closes)
    four["exact_current"] = exact
    return result, known, four


def _morphology(frame: pd.DataFrame, *, minutes: int) -> pd.DataFrame:
    """Compute causal six-MA span and failed-upbreak process descriptors."""
    native_valid = _ohlc_valid(frame)
    native_run = _runs(native_valid, frame.index, minutes)
    ma = frame.loc[:, MA_COLUMNS].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    close = frame.close.to_numpy(float)
    ma_valid = np.isfinite(ma).all(axis=1) & np.isfinite(close) & (close > 0)
    span_valid = native_valid & ma_valid
    span_run = _runs(span_valid, frame.index, minutes)
    span = np.full(len(frame), np.nan)
    span[ma_valid] = ma[ma_valid].max(axis=1) - ma[ma_valid].min(axis=1)
    span_price, span_price_known = _safe_ratio(span, close, span_valid)
    n = len(frame)
    prior96_median = pd.Series(span_price).shift(1).rolling(96, min_periods=96).median().to_numpy(float)
    contraction, contraction_known = _safe_ratio(span_price, prior96_median, span_run >= 97)
    compact = np.zeros(n, dtype=bool)
    compact[contraction_known] = span_price[contraction_known] <= prior96_median[contraction_known]
    compact_run = np.zeros(n, dtype=int)
    for i in range(n):
        compact_run[i] = compact_run[i - 1] + 1 if compact[i] and i else 1 if compact[i] else 0
    prior_duration = np.full(n, np.nan)
    prior_duration_known = np.zeros(n, dtype=bool)
    if n > 1:
        prior_duration[1:] = compact_run[:-1]
        # The preceding compact predicate needs 97 spans; the current span is
        # also required so a just-observed gap cannot retain an old duration.
        prior_duration_known[1:] = (span_run[1:] >= 98) & contraction_known[:-1]
    latest4 = pd.Series(span_price).rolling(4, min_periods=4).median().to_numpy(float)
    previous16 = pd.Series(span_price).shift(4).rolling(16, min_periods=16).median().to_numpy(float)
    expansion, expansion_known = _safe_ratio(latest4, previous16, span_run >= 20)

    high = frame.high.to_numpy(float)
    previous_close = np.r_[np.nan, close[:-1]]
    frozen_high = pd.Series(high).shift(1).rolling(20, min_periods=20).max().to_numpy(float)
    upbreak_known = native_run >= 21
    upbreak = (upbreak_known & (previous_close <= frozen_high) & (close > frozen_high)
               & np.isfinite(frozen_high))
    failed_at_confirm = np.zeros(n, dtype=bool)
    if n > 1:
        failed_at_confirm[1:] = upbreak[:-1] & (close[1:] < frozen_high[:-1])
    failed_count = pd.Series(failed_at_confirm.astype(int)).rolling(32, min_periods=32).sum().to_numpy(float)
    failed_known = native_run >= 53  # prior32 breaks each require their prior20 highs plus current confirmation.
    failed_count[~failed_known] = np.nan
    return pd.DataFrame({
        "six_ma_span_price": span_price,
        "six_ma_span_price_known": span_price_known,
        "six_ma_span_to_prior96_median": contraction,
        "six_ma_span_to_prior96_median_known": contraction_known,
        "prior_compact_span_duration": prior_duration,
        "prior_compact_span_duration_known": prior_duration_known,
        "span_expansion_last4_vs_previous16": expansion,
        "span_expansion_last4_vs_previous16_known": expansion_known,
        "prior32_failed_upbreak_count": failed_count,
        "prior32_failed_upbreak_count_known": failed_known,
    }, index=frame.index)


def build_context_features(frame: pd.DataFrame, minutes: int, btc_frame: pd.DataFrame | None = None,
                           btc_minutes: int | None = None) -> pd.DataFrame:
    """Build all causal 5R context fields for each native signal bar.

    Required ``frame`` columns are ``open/high/low/close`` and
    ``s20/e20/s60/e60/s120/e120`` at UTC native bar-open timestamps.  ``minutes``
    is the native bar length.  Optional ``btc_frame`` must be an explicitly
    supplied OHLC frame from the intended BTC venue; it is never loaded or
    replaced here.  ``btc_minutes`` defaults to ``minutes`` and must describe
    BTC's native timestamps.  Each output row represents information available
    at that row's close.  See the module docstring for exact windows.
    """
    minutes = _validate_minutes(minutes, name="minutes")
    required = set(OHLC_COLUMNS + MA_COLUMNS)
    if not required.issubset(frame):
        raise ValueError("frame missing required OHLC/six-MA columns: " + ", ".join(sorted(required - set(frame))))
    _validate_index(frame, minutes=minutes, name="frame")
    out = _empty(frame.index)
    if frame.empty:
        return out
    symbol_return, symbol_return_known, native_valid = _native_returns(frame, minutes=minutes)
    signal_closes = frame.index + pd.Timedelta(minutes=minutes)
    symbol_four = _four_hour_at_signal(frame, minutes=minutes, signal_closes=signal_closes)
    symbol_four_known = symbol_four.complete.to_numpy(bool) & np.isfinite(symbol_four.close_sma20_fraction.to_numpy(float))
    slope_known = symbol_four_known & np.isfinite(symbol_four.sma20_slope3_fraction.to_numpy(float))
    out["symbol_completed4h_close_sma20_fraction"] = symbol_four.close_sma20_fraction.to_numpy(float)
    out["symbol_completed4h_close_sma20_fraction_known"] = symbol_four_known
    out["symbol_completed4h_sma20_slope3_fraction"] = symbol_four.sma20_slope3_fraction.to_numpy(float)
    out["symbol_completed4h_sma20_slope3_fraction_known"] = slope_known
    out["symbol_native_known"] = native_valid

    morphology = _morphology(frame, minutes=minutes)
    for name in morphology:
        out[name] = morphology[name]

    if btc_frame is None:
        return _finalize(out)
    btc_minutes = minutes if btc_minutes is None else _validate_minutes(btc_minutes, name="btc_minutes")
    if not set(OHLC_COLUMNS).issubset(btc_frame):
        raise ValueError("btc_frame missing required OHLC columns")
    _validate_index(btc_frame, minutes=btc_minutes, name="btc_frame")
    btc_return, btc_return_known, btc_four = _btc_at_signal(
        btc_frame, btc_minutes=btc_minutes, signal_closes=signal_closes)
    btc_fraction = btc_four.close_sma20_fraction.to_numpy(float)
    btc_four_known = (btc_four.complete.to_numpy(bool) & btc_four.exact_current.to_numpy(bool)
                      & np.isfinite(btc_fraction))
    btc_fraction[~btc_four_known] = np.nan
    out["btc_trailing24h_return"] = btc_return
    out["btc_trailing24h_return_known"] = btc_return_known
    out["btc_completed4h_close_sma20_fraction"] = btc_fraction
    out["btc_completed4h_close_sma20_fraction_known"] = btc_four_known
    above = np.full(len(out), np.nan)
    above[btc_four_known] = (btc_fraction[btc_four_known] > 0).astype(float)
    out["btc_completed4h_above_sma20"] = above
    out["btc_completed4h_above_sma20_known"] = btc_four_known
    relative_known = symbol_return_known & btc_return_known
    relative = np.full(len(out), np.nan)
    relative[relative_known] = symbol_return[relative_known] - btc_return[relative_known]
    out["symbol_relative_strength24h"] = relative
    out["symbol_relative_strength24h_known"] = relative_known
    return _finalize(out)
