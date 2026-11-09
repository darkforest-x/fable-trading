"""Causal 12H entry features for the frozen Binance funded-parent comparison.

The caller supplies a funded-parent frame indexed by ``event_id`` with only
``symbol``, ``side`` and ``entry_ms``.  This builder returns LONG rows only.
The source signal timestamp is the 12H close and the next coincident H1 open,
so the signal candle opens at ``entry_ms - 12h`` and closes at ``entry_ms``.

For current contracts, canonical signal fields come from the frozen
``exp-signal-center-score-20261106-v1`` signal tables. Missing historical
signal rows are rebuilt with the original ``signal_center_score.build_inputs``
from the registered Binance 5m source, or (for retired contracts) from the
already archived official Binance 15m files. No future price, trade result,
funded PnL, or exit field enters a feature. 15m reconstructions retain their
source status and leave 5m-only quote-volume fields missing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SIGNAL_EXP = ROOT / "experiments/active/exp-signal-center-score-20261106-v1"
SIGNAL_RESULTS = SIGNAL_EXP / "results"
SIGNAL_TABLES = SIGNAL_RESULTS / "symbols"
SIGNAL_CONFIG = SIGNAL_EXP / "config.json"
ARCHIVE_SYMBOLS = ROOT / "data/research/cloud_close_archive_cohort/symbols"
ARCHIVE_DAILY = ROOT / "data/research/cloud_close_archive_cohort/daily"
NY = "America/New_York"
BAR_MS = 12 * 60 * 60 * 1000
HOUR_MS = 60 * 60 * 1000
NS_PER_MS = 1_000_000
OHLC = ["open", "high", "low", "close"]
OHLCV = [*OHLC, "volume"]

# Preserve the names used by the frozen signal-center feature matrix.
CANONICAL_FEATURES = [
    "pen_atr", "pen_pct", "reclaim_atr", "close_pos", "wick_ratio",
    "body_atr", "range_atr", "atr_pct", "ema_spread_atr",
    "ema20_slope_atr", "close_ema20_atr", "monday_width_pct",
    "stop_dist_pct", "rr_close", "log_vol_ratio", "hours_since_monday",
    "dow_ny", "prior_touches", "ret_24h", "ret_7d", "log_qv_7d",
    "age_days", "btc_ret_24h", "btc_side",
]

RAW_FEATURES = [
    "signal_open", "signal_high", "signal_low", "signal_close",
    "signal_return_pct", "signal_close_location_in_bar",
    "signal_lower_wick_fraction", "signal_range",
    "atr14_previous", "range_previous_atr14",
    "ema20", "ema50", "close_vs_ema20_pct", "close_vs_ema50_pct",
    "ema_spread_pct", "ema20_slope_5bar_pct",
    "monday_high", "monday_low", "monday_width",
    "weekly_lower_1618", "weekly_upper_1618",
    "weekly_lower_penetration_pct", "weekly_close_reclaim_pct",
    "weekly_close_location_in_1618_band", "weekly_lower_reclaimed",
    "signal_base_volume", "base_volume_expansion_log20",
    "prior_7d_return_log",
]

OUTPUT_COLUMNS = [
    "symbol", "side", "entry_ms", "signal_time_ms", "signal_open_ms",
    "signal_week", *RAW_FEATURES, *CANONICAL_FEATURES,
    "signal_feature_source_status", "native_ohlc_source_status",
    "signal_bar_source_intervals_observed", "signal_bar_source_intervals_expected",
    "signal_bar_h1_complete_hours", "signal_bar_complete",
    "signal_h1_open_present", "signal_h1_open_price",
    "contiguous_12h_bars_at_signal", "ema50_ready_at_signal",
    "prior_atr14_available", "monday_source_rows_observed",
    "monday_source_rows_expected", "monday_anchor_complete",
    "monday_end_ms", "monday_complete_before_signal",
    "source_feature_cutoff_ms", "quote_volume_12h_usdt",
    "quote_volume_expansion_log20", "quote_volume_source_status",
    "canonical_qv_feature_status", "reconstructed_signal_id_match",
]


def _normalize_parents(parents: pd.DataFrame) -> pd.DataFrame:
    """Validate event identity/timing and select all LONG parents explicitly."""
    if not isinstance(parents, pd.DataFrame):
        raise TypeError("parents must be a pandas DataFrame")
    frame = parents.copy()
    if "event_id" in frame.columns:
        frame = frame.set_index("event_id", verify_integrity=True)
    if not frame.index.is_unique:
        raise ValueError("Parent event_id index must be unique")
    frame.index = pd.Index(frame.index.astype(str), name="event_id")
    required = {"symbol", "side", "entry_ms"}
    if required - set(frame.columns):
        raise ValueError(f"Parent input is missing {sorted(required - set(frame.columns))}")
    if frame["symbol"].isna().any() or frame["side"].isna().any() or frame["entry_ms"].isna().any():
        raise ValueError("Parent symbol, side, and entry_ms must be populated")
    frame["symbol"] = frame["symbol"].astype(str)
    side = pd.to_numeric(frame["side"], errors="raise").astype(int)
    entry_ms = pd.to_numeric(frame["entry_ms"], errors="raise").astype("int64")
    longs = frame.loc[side.eq(1)].copy()
    longs["side"] = 1
    longs["entry_ms"] = entry_ms.loc[longs.index]

    for event_id, row in longs.iterrows():
        match = re.fullmatch(r"12H\|(.+?)USDT\|(\d+)\|1", event_id)
        if not match:
            raise ValueError(f"Unexpected frozen LONG event_id format: {event_id}")
        ticker, close_ns_text = match.groups()
        close_ns = int(close_ns_text)
        if close_ns % NS_PER_MS:
            raise ValueError(f"Signal timestamp is not millisecond aligned: {event_id}")
        id_ms = close_ns // NS_PER_MS
        if id_ms != int(row.entry_ms):
            raise ValueError(f"Frozen event_id close clock differs from entry_ms: {event_id}")
        if ticker != row.symbol:
            raise ValueError(f"Frozen event_id ticker differs from parent symbol: {event_id}")
        if id_ms % BAR_MS:
            raise ValueError(f"Frozen signal/entry time is not on the UTC 12H clock: {event_id}")
    return longs


def _load_frozen_signal_table(symbol: str) -> pd.DataFrame:
    path = SIGNAL_TABLES / f"{symbol}USDT" / "signals.csv.gz"
    if not path.is_file():
        return pd.DataFrame()
    wanted = {"signal_id", "close_ns", "side", "tf", "week", *CANONICAL_FEATURES}
    frame = pd.read_csv(
        path,
        dtype={"signal_id": str, "tf": str, "week": str},
        usecols=lambda name: name in wanted,
        float_precision="round_trip",
    )
    if not {"signal_id", "close_ns", "side", "tf"}.issubset(frame.columns):
        raise ValueError(f"Frozen signal table schema is incomplete: {path}")
    frame = frame.loc[frame.tf.eq("12H")].copy()
    if frame.signal_id.duplicated().any():
        raise ValueError(f"Frozen 12H signal table has duplicate IDs: {symbol}")
    return frame.set_index("signal_id", verify_integrity=True)


def _read_15m_zip(path: Path) -> pd.DataFrame:
    """Read one already-local official Binance UM 15m archive without filling gaps."""
    raw = pd.read_csv(path, header=None, usecols=range(6),
                      names=["time", *OHLC, "volume"], dtype=str)
    if raw.empty:
        raise ValueError(f"Empty registered Binance source archive: {path}")
    time = pd.to_numeric(raw.time, errors="coerce")
    header = time.isna()
    if header.any():
        if int(header.sum()) != 1 or not bool(header.iloc[0]):
            raise ValueError(f"Malformed time column in Binance archive: {path}")
        raw = raw.loc[~header].copy()
    raw["time"] = pd.to_numeric(raw.time, errors="raise").astype("int64")
    for column in [*OHLC, "volume"]:
        raw[column] = pd.to_numeric(raw[column], errors="raise").astype(float)
    raw = raw.set_index("time").sort_index()
    if raw.empty or not raw.index.is_unique or (raw.index % 900_000).any():
        raise ValueError(f"Invalid Binance 15m source clock: {path}")
    values = raw[OHLCV].to_numpy(dtype=float)
    if (not np.isfinite(values).all() or (raw[OHLC] <= 0).any().any()
            or raw.volume.lt(0).any()
            or raw.high.lt(raw[OHLC].max(axis=1)).any()
            or raw.low.gt(raw[OHLC].min(axis=1)).any()):
        raise ValueError(f"Invalid Binance 15m OHLCV: {path}")
    raw.index = pd.to_datetime(raw.index, unit="ms", utc=True).as_unit("ns")
    return raw


def _load_archived_15m(symbol: str) -> pd.DataFrame:
    """Load the frozen local monthly plus repaired-daily source for a retired coin."""
    ticker = f"{symbol}USDT"
    manifest_path = ARCHIVE_SYMBOLS / ticker / "source.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"No frozen 15m source receipt for {ticker}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    paths: list[Path] = []
    for month in manifest.get("months", []):
        path = ROOT / month["local"]
        if not path.is_file():
            raise FileNotFoundError(f"Registered monthly source is unavailable: {path}")
        paths.append(path)
    # The archive cohort builder patched listed daily gaps when available. Reuse
    # those exact local files; never fetch or replace them in this feature pass.
    for repair in manifest.get("repairs", []):
        if int(repair.get("added", 0)) <= 0:
            continue
        name = f"{ticker}-15m-{repair['day']}.zip"
        path = ARCHIVE_DAILY / ticker / name
        if not path.is_file():
            raise FileNotFoundError(f"Registered repaired source is unavailable: {path}")
        paths.append(path)
    if not paths:
        raise FileNotFoundError(f"Frozen 15m source receipt has no local archives: {ticker}")
    frames = [_read_15m_zip(path) for path in paths]
    source = pd.concat(frames).sort_index()
    if source.index.has_duplicates:
        values = source.groupby(level=0)[OHLCV].agg(["min", "max"])
        if not np.allclose(values.xs("min", axis=1, level=1).to_numpy(),
                           values.xs("max", axis=1, level=1).to_numpy(),
                           rtol=1e-10, atol=1e-12):
            raise ValueError(f"Conflicting monthly/daily source overlap for {ticker}")
        source = source.loc[~source.index.duplicated(keep="first")]
    if not source.index.is_monotonic_increasing:
        raise ValueError(f"Unordered archived Binance 15m source for {ticker}")
    return source


def _job_map() -> dict[str, dict[str, Any]]:
    config = json.loads(SIGNAL_CONFIG.read_text(encoding="utf-8"))
    from yoyo.evaluation.monday_limit_reclaim_inputs import jobs

    result = {}
    for job in jobs(config["source_config"]):
        if job["group"] == "spot_gold":
            continue
        symbol = str(job["symbol"])
        if not symbol.endswith("USDT"):
            raise ValueError(f"Unexpected non-USDT frozen Binance source job: {symbol}")
        result[symbol[:-4]] = job
    return result


def _signal_builder():
    from yoyo.evaluation import signal_center_score

    ctx_path = SIGNAL_RESULTS / "btc_context.npz"
    if not ctx_path.is_file():
        raise FileNotFoundError(f"Frozen BTC causal context is unavailable: {ctx_path}")
    return signal_center_score, signal_center_score._ctx(ctx_path)


def _time_series_coverage(frame: pd.DataFrame, source_minutes: int) -> tuple[pd.Series, pd.Series]:
    interval = pd.Series(1, index=frame.index, dtype="int16")
    interval_count = interval.resample("12h", origin="epoch", label="left", closed="left").sum()
    hour_count = interval.resample("1h", origin="epoch", label="left", closed="left").sum()
    interval_count.index = interval_count.index.as_unit("ns")
    hour_count.index = hour_count.index.as_unit("ns")
    return interval_count, hour_count


def _monday_coverage(frame: pd.DataFrame, source_minutes: int) -> pd.Series:
    local = frame.index.tz_convert(NY)
    monday = local.normalize() - pd.to_timedelta(local.weekday, unit="D")
    is_monday = local.weekday == 0
    count = pd.Series(1, index=monday[is_monday], dtype="int16").groupby(level=0).sum()
    return count


def _week_key(signal_open_ms: int) -> pd.Timestamp:
    local = pd.Timestamp(signal_open_ms, unit="ms", tz="UTC").tz_convert(NY)
    return local.normalize() - pd.Timedelta(days=local.weekday())


def _raw_features(
    *, symbol: str, side: int, entry_ms: int, week: str | None, frame: pd.DataFrame,
    bars: pd.DataFrame, monday: pd.DataFrame, interval_count: pd.Series,
    hour_count: pd.Series, monday_count: pd.Series, source_minutes: int,
    source_status: str,
) -> dict[str, Any]:
    """Extract only values whose underlying bars close no later than signal time."""
    signal_open_ms = entry_ms - BAR_MS
    signal_open_ns = signal_open_ms * NS_PER_MS
    signal_time_ns = entry_ms * NS_PER_MS
    expected_intervals = (12 * 60) // source_minutes
    expected_per_hour = 60 // source_minutes
    open_ts = pd.Timestamp(signal_open_ns, unit="ns", tz="UTC")
    signal_ts = pd.Timestamp(signal_time_ns, unit="ns", tz="UTC")
    bar_pos = int(bars.close_ns.searchsorted(signal_time_ns)) if len(bars) else 0
    has_bar = bool(bar_pos < len(bars) and int(bars.close_ns.iloc[bar_pos]) == signal_time_ns
                   and int(bars.index.asi8[bar_pos]) == signal_open_ns)
    observed_intervals = int(interval_count.get(open_ts, 0))
    hours = pd.date_range(open_ts, signal_ts, freq="1h", inclusive="left")
    observed_hour_counts = hour_count.reindex(hours).fillna(0).to_numpy(dtype=int)
    complete_hours = int((observed_hour_counts == expected_per_hour).sum())
    interval_complete = observed_intervals == expected_intervals
    signal_h1_open_pos = int(frame.index.searchsorted(signal_ts))
    has_entry_open = bool(signal_h1_open_pos < len(frame) and frame.index[signal_h1_open_pos] == signal_ts)
    entry_open = float(frame.open.iloc[signal_h1_open_pos]) if has_entry_open else np.nan
    week_key = _week_key(signal_open_ms)
    monday_rows = int(monday_count.get(week_key, 0))
    expected_monday_rows = 1440 // source_minutes
    band_exists = week_key in monday.index
    monday_complete = band_exists and monday_rows == expected_monday_rows
    monday_high = monday_low = lower = upper = monday_width = np.nan
    monday_end_ms: int | None = None
    monday_before_signal = False
    values = {column: np.nan for column in RAW_FEATURES}
    contiguous = ema_ready = atr_ready = False
    signal_week = week or str(week_key.date())
    if has_bar:
        bar = bars.iloc[bar_pos]
        op, hi, lo, close = (float(bar[name]) for name in OHLC)
        candle_range = hi - lo
        atr_now = float(bar.atr) if pd.notna(bar.atr) else np.nan
        bar_count = int(bar["count"])
        contiguous = bar_count > 1 and int(bars.seg.iloc[bar_pos]) == int(bars.seg.iloc[bar_pos - 1])
        ema_ready = bar_count >= 50 and np.isfinite([bar.e20, bar.e50]).all()
        atr_prior = np.nan
        if bar_pos > 0 and int(bars.seg.iloc[bar_pos]) == int(bars.seg.iloc[bar_pos - 1]):
            previous = bars.atr.iloc[bar_pos - 1]
            if pd.notna(previous) and float(previous) > 0:
                atr_prior = float(previous)
                atr_ready = True
        slope = np.nan
        if bar_pos >= 5 and int(bars.seg.iloc[bar_pos]) == int(bars.seg.iloc[bar_pos - 5]):
            slope = float(bar.e20 - bars.e20.iloc[bar_pos - 5])
        values.update({
            "signal_open": op, "signal_high": hi, "signal_low": lo, "signal_close": close,
            "signal_return_pct": close / op - 1.0 if op else np.nan,
            "signal_close_location_in_bar": (close - lo) / candle_range if candle_range > 0 else np.nan,
            "signal_lower_wick_fraction": (min(op, close) - lo) / candle_range if candle_range > 0 else np.nan,
            "signal_range": candle_range,
            "atr14_previous": atr_prior,
            "range_previous_atr14": candle_range / atr_prior if atr_ready else np.nan,
            "ema20": float(bar.e20), "ema50": float(bar.e50),
            "close_vs_ema20_pct": close / float(bar.e20) - 1.0 if bar.e20 > 0 else np.nan,
            "close_vs_ema50_pct": close / float(bar.e50) - 1.0 if bar.e50 > 0 else np.nan,
            "ema_spread_pct": (float(bar.e20) - float(bar.e50)) / close if close else np.nan,
            "ema20_slope_5bar_pct": slope / close if close and np.isfinite(slope) else np.nan,
            "signal_base_volume": float(bar.volume) if pd.notna(bar.volume) else np.nan,
            "base_volume_expansion_log20": np.nan,
        })
        previous_volumes = bars.volume.iloc[max(0, bar_pos - 20):bar_pos]
        if bar_count > 20 and len(previous_volumes) == 20 and previous_volumes.notna().all():
            median_volume = float(previous_volumes.median())
            if median_volume > 0 and pd.notna(bar.volume) and float(bar.volume) > 0:
                values["base_volume_expansion_log20"] = float(np.log(float(bar.volume) / median_volume))
        if band_exists:
            band = monday.loc[week_key]
            monday_high, monday_low = float(band.high), float(band.low)
            monday_width = monday_high - monday_low
            lower = float(band.lower)
            upper = float(band.upper)
            monday_end_ms = int(pd.Timestamp(band.end).value // NS_PER_MS)
            monday_before_signal = monday_end_ms <= signal_open_ms
            if monday_before_signal and monday_width > 0 and close > 0:
                values.update({
                    "monday_high": monday_high, "monday_low": monday_low,
                    "monday_width": monday_width,
                    "weekly_lower_1618": lower, "weekly_upper_1618": upper,
                    "weekly_lower_penetration_pct": (lower - lo) / close,
                    "weekly_close_reclaim_pct": (close - lower) / close,
                    "weekly_close_location_in_1618_band": (close - lower) / (upper - lower),
                    "weekly_lower_reclaimed": bool(lo < lower and close > lower),
                })
    return {
        "symbol": symbol, "side": side, "entry_ms": entry_ms,
        "signal_time_ms": entry_ms, "signal_open_ms": signal_open_ms,
        "signal_week": signal_week, **values,
        "native_ohlc_source_status": source_status if has_bar else f"{source_status}_signal_bar_missing",
        "signal_bar_source_intervals_observed": observed_intervals,
        "signal_bar_source_intervals_expected": expected_intervals,
        "signal_bar_h1_complete_hours": complete_hours,
        "signal_bar_complete": bool(has_bar and interval_complete and complete_hours == 12),
        "signal_h1_open_present": has_entry_open, "signal_h1_open_price": entry_open,
        "contiguous_12h_bars_at_signal": int(bars["count"].iloc[bar_pos]) if has_bar else 0,
        "ema50_ready_at_signal": bool(ema_ready),
        "prior_atr14_available": bool(atr_ready),
        "monday_source_rows_observed": monday_rows,
        "monday_source_rows_expected": expected_monday_rows,
        "monday_anchor_complete": bool(monday_complete),
        "monday_end_ms": monday_end_ms,
        "monday_complete_before_signal": bool(monday_complete and monday_before_signal),
        "source_feature_cutoff_ms": entry_ms,
        "quote_volume_12h_usdt": np.nan,
        "quote_volume_expansion_log20": np.nan,
        "quote_volume_source_status": "exact_quote_asset_volume_not_in_frozen_ohlcv_source",
        "canonical_qv_feature_status": (
            "frozen_signal_center_close_times_base_volume_7d_proxy" if source_minutes == 5 else
            "unavailable_5m_close_volume_sum_for_15m_reconstruction"
        ),
    }


def _cached_features(record: pd.Series | None) -> dict[str, Any]:
    if record is None:
        return {name: np.nan for name in CANONICAL_FEATURES}
    return {name: record[name] if name in record.index else np.nan for name in CANONICAL_FEATURES}


def _validate_clock_feature(event_id: str, record: pd.Series, entry_ms: int) -> None:
    if int(record.close_ns) // NS_PER_MS != entry_ms or int(record.side) != 1:
        raise ValueError(f"Frozen signal feature timestamp/direction differs from parent: {event_id}")


def _compare_frozen_to_rebuilt(event_id: str, frozen: pd.Series, rebuilt: pd.Series) -> None:
    """Fail if cached 5m canonical features no longer reproduce from their source."""
    for name in ["close", "stop", *CANONICAL_FEATURES]:
        if name not in frozen.index or name not in rebuilt.index:
            continue
        left, right = frozen[name], rebuilt[name]
        if pd.isna(left) and pd.isna(right):
            continue
        if pd.isna(left) or pd.isna(right) or not np.isclose(float(left), float(right), rtol=2e-8, atol=1e-10):
            raise ValueError(f"Frozen and original-builder feature differ for {event_id}: {name}")


def _missing_raw_row(symbol: str, side: int, entry_ms: int, feature_status: str) -> dict[str, Any]:
    row = {column: np.nan for column in RAW_FEATURES}
    row.update({
        "symbol": symbol, "side": side, "entry_ms": entry_ms,
        "signal_time_ms": entry_ms, "signal_open_ms": entry_ms - BAR_MS,
        "signal_week": str(_week_key(entry_ms - BAR_MS).date()), **row,
        "native_ohlc_source_status": "source_unavailable",
        "signal_bar_source_intervals_observed": 0,
        "signal_bar_source_intervals_expected": None,
        "signal_bar_h1_complete_hours": 0, "signal_bar_complete": False,
        "signal_h1_open_present": False, "signal_h1_open_price": np.nan,
        "contiguous_12h_bars_at_signal": 0, "ema50_ready_at_signal": False,
        "prior_atr14_available": False, "monday_source_rows_observed": 0,
        "monday_source_rows_expected": None, "monday_anchor_complete": False,
        "monday_end_ms": None, "monday_complete_before_signal": False,
        "source_feature_cutoff_ms": entry_ms, "quote_volume_12h_usdt": np.nan,
        "quote_volume_expansion_log20": np.nan,
        "quote_volume_source_status": "source_unavailable",
        "canonical_qv_feature_status": "source_unavailable",
        "signal_feature_source_status": feature_status,
        "reconstructed_signal_id_match": False,
    })
    return row


def build_features(parents: pd.DataFrame) -> pd.DataFrame:
    """Build one causal feature row per supplied funded LONG, keyed by event_id.

    The input may contain shorts; they are intentionally excluded. Every LONG
    event is retained, including source gaps, with explicit per-row coverage and
    source-status fields. The parent ledger's PnL and exit columns are ignored.
    """
    long_parents = _normalize_parents(parents)
    if long_parents.empty:
        empty = pd.DataFrame(columns=OUTPUT_COLUMNS, index=pd.Index([], name="event_id"))
        empty.attrs["source_summary"] = {"long_parent_count": 0, "returned_count": 0}
        return empty

    score, ctx = _signal_builder()
    job_by_symbol = _job_map()
    feature_rows: list[dict[str, Any]] = []

    for symbol, group in long_parents.groupby("symbol", sort=False):
        ticker = f"{symbol}USDT"
        frozen = _load_frozen_signal_table(symbol)
        try:
            if symbol in job_by_symbol:
                source_minutes = 5
                source_status = "reconstructed_from_registered_binance_5m"
                try:
                    source_frame = score.five_minute(job_by_symbol[symbol])
                except FileNotFoundError:
                    source_minutes = 15
                    source_status = "reconstructed_from_frozen_binance_15m_archive"
                    source_frame = _load_archived_15m(symbol)
            else:
                source_minutes = 15
                source_status = "reconstructed_from_frozen_binance_15m_archive"
                source_frame = _load_archived_15m(symbol)
        except FileNotFoundError:
            for event_id, parent in group.iterrows():
                frozen_record = frozen.loc[event_id] if event_id in frozen.index else None
                if frozen_record is not None:
                    _validate_clock_feature(event_id, frozen_record, int(parent.entry_ms))
                feature_status = "frozen_signal_center_table" if frozen_record is not None else (
                    "signal_table_and_native_source_unavailable"
                )
                raw = _missing_raw_row(symbol, 1, int(parent.entry_ms), feature_status)
                raw.update(_cached_features(frozen_record))
                raw["signal_feature_source_status"] = feature_status
                raw["reconstructed_signal_id_match"] = False
                raw["prior_7d_return_log"] = raw.get("ret_7d", np.nan)
                feature_rows.append({"event_id": event_id, **raw})
            continue

        if source_frame.empty or not source_frame.index.is_unique or not source_frame.index.is_monotonic_increasing:
            raise ValueError(f"Invalid native input frame for {ticker}")
        if not set(OHLCV).issubset(source_frame.columns):
            raise ValueError(f"Native source lacks OHLCV for {ticker}")
        source_frame = source_frame[OHLCV].copy()
        if source_minutes == 15:
            # The archived 15m source does not retain the five-minute
            # close-times-base-volume sum needed by the original 7d qv field.
            # Keep that field missing instead of fabricating a 5m equivalent.
            source_frame["close_volume_sum_5m"] = np.nan

        rebuilt = pd.DataFrame()
        rounded, _, _, _, rebuilt, _, _, _ = score.build_inputs(
            source_frame, ticker, ctx, key_timeframes=["12H"], source_minutes=source_minutes,
            feature_start_ns=int(source_frame.index.asi8[0]),
        )
        if len(rebuilt):
            if rebuilt.signal_id.duplicated().any():
                raise ValueError(f"Original builder produced duplicate 12H signal IDs for {ticker}")
            rebuilt = rebuilt.set_index("signal_id", verify_integrity=True)
        bars = score.tf_bars(rounded, 720, source_minutes)
        monday = score.monday_ranges(rounded, source_minutes)
        interval_count, hour_count = _time_series_coverage(rounded, source_minutes)
        monday_count = _monday_coverage(rounded, source_minutes)

        for event_id, parent in group.iterrows():
            entry_ms = int(parent.entry_ms)
            frozen_record = frozen.loc[event_id] if event_id in frozen.index else None
            rebuilt_record = rebuilt.loc[event_id] if event_id in rebuilt.index else None
            if frozen_record is not None:
                _validate_clock_feature(event_id, frozen_record, entry_ms)
            if rebuilt_record is not None:
                _validate_clock_feature(event_id, rebuilt_record, entry_ms)
            if frozen_record is not None and source_minutes == 5 and rebuilt_record is not None:
                _compare_frozen_to_rebuilt(event_id, frozen_record, rebuilt_record)

            if frozen_record is not None:
                signal_features = _cached_features(frozen_record)
                feature_status = "frozen_signal_center_table"
            elif rebuilt_record is not None:
                signal_features = _cached_features(rebuilt_record)
                feature_status = (
                    "reconstructed_missing_row_with_original_5m_builder" if source_minutes == 5 else
                    "reconstructed_missing_row_with_original_15m_archive_builder"
                )
            else:
                signal_features = _cached_features(None)
                feature_status = "signal_id_unmatched_in_frozen_and_rebuilt_sources"

            raw = _raw_features(
                symbol=symbol, side=1, entry_ms=entry_ms,
                week=(str(frozen_record.week) if frozen_record is not None and "week" in frozen_record.index
                      else str(rebuilt_record.week) if rebuilt_record is not None and "week" in rebuilt_record.index
                      else None),
                frame=rounded, bars=bars, monday=monday,
                interval_count=interval_count, hour_count=hour_count,
                monday_count=monday_count, source_minutes=source_minutes,
                source_status=source_status,
            )
            # The canonical 12H base-volume ratio uses the original frozen field
            # where present. In the reconstructed path, it is derived by the
            # same original builder from complete prior 12H bars.
            if pd.notna(signal_features.get("log_vol_ratio", np.nan)):
                raw["base_volume_expansion_log20"] = signal_features["log_vol_ratio"]
            # Exact quote-asset turnover was not retained in these frozen OHLCV
            # sources. Do not relabel close*base-volume proxies as exact quote volume.
            raw["quote_volume_12h_usdt"] = np.nan
            raw["quote_volume_expansion_log20"] = np.nan
            raw["quote_volume_source_status"] = "exact_quote_asset_volume_not_in_frozen_ohlcv_source"
            raw["canonical_qv_feature_status"] = (
                "frozen_signal_center_close_times_base_volume_7d_proxy" if frozen_record is not None else
                "unavailable_5m_close_volume_sum_for_15m_reconstruction" if source_minutes == 15 else
                "original_builder_close_times_base_volume_7d_proxy"
            )
            raw.update(signal_features)
            raw["prior_7d_return_log"] = raw.get("ret_7d", np.nan)
            raw["signal_feature_source_status"] = feature_status
            raw["reconstructed_signal_id_match"] = rebuilt_record is not None
            feature_rows.append({"event_id": event_id, **raw})

    result = pd.DataFrame(feature_rows).set_index("event_id", verify_integrity=True)
    missing = long_parents.index.difference(result.index)
    extra = result.index.difference(long_parents.index)
    if len(missing) or len(extra) or len(result) != len(long_parents):
        raise ValueError(f"Feature roster mismatch: missing={len(missing)} extra={len(extra)}")
    result = result.reindex(long_parents.index)
    for column in OUTPUT_COLUMNS:
        if column not in result:
            result[column] = np.nan
    result = result[OUTPUT_COLUMNS]
    if not result.index.is_unique or result.index.hasnans:
        raise ValueError("Feature output event_id index must be unique and complete")
    result.attrs["source_summary"] = {
        "long_parent_count": int(len(long_parents)),
        "returned_count": int(len(result)),
        "frozen_signal_feature_rows": int(result.signal_feature_source_status.eq("frozen_signal_center_table").sum()),
        "reconstructed_feature_rows": int(result.signal_feature_source_status.str.startswith("reconstructed_").sum()),
        "unmatched_signal_feature_rows": int(result.signal_feature_source_status.eq(
            "signal_id_unmatched_in_frozen_and_rebuilt_sources").sum()),
        "complete_signal_ohlc_rows": int(result.signal_bar_complete.fillna(False).sum()),
        "incomplete_signal_ohlc_rows": int((~result.signal_bar_complete.fillna(False)).sum()),
        "exact_quote_volume_rows": int(result.quote_volume_12h_usdt.notna().sum()),
        "source_status_counts": result.native_ohlc_source_status.value_counts(dropna=False).to_dict(),
    }
    return result
