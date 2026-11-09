"""Causal LONG-entry features for the frozen OKX original V1.7 signal pool.

Price features use the exact H1/12H paths supplied by the source builder.  The
week anchor is reconstructed from 24 closed New York Monday H1 candles; touch
history uses only complete 12H candles strictly before the signal candle.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.monitor.cloud_signal_plans import atr_points, stop_pct_for
from yoyo.monitor.monday_fib import HOUR, week_window


STEP_12H = 12 * HOUR
OHLC = ("open", "high", "low", "close")


def _empty_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=list(OHLC), index=pd.Index([], name="time", dtype="int64"))


def _read_bars(path, end_ms: int, step_ms: int) -> pd.DataFrame:
    """Read only confirmed source candles; never fill holes in a supplied path."""
    if path is None or not isinstance(path, (str, Path)) or not Path(path).is_file():
        return _empty_bars()
    raw = pd.read_csv(path, usecols=lambda c: c in {"time", *OHLC}, float_precision="round_trip")
    if not {"time", *OHLC}.issubset(raw.columns):
        raise ValueError(f"Invalid OHLC source schema: {path}")
    raw = raw.set_index("time")
    raw.index = raw.index.astype("int64")
    if raw.index.has_duplicates or not raw.index.is_monotonic_increasing:
        raise ValueError(f"Non-unique or unordered OHLC source: {path}")
    raw = raw.loc[(raw.index % step_ms == 0) & (raw.index + step_ms <= end_ms), list(OHLC)]
    values = raw.to_numpy(dtype="float64")
    if len(values) and (
        not np.isfinite(values).all()
        or (values <= 0).any()
        or (raw.low > raw[["open", "high", "close"]].min(axis=1)).any()
        or (raw.high < raw[["open", "low", "close"]].max(axis=1)).any()
    ):
        raise ValueError(f"Invalid OHLC candle values: {path}")
    raw.index.name = "time"
    return raw


def _aggregate_12h_from_h1(hourly: pd.DataFrame) -> pd.DataFrame:
    """Reproduce the source's complete-hour aggregate where no 12H path exists."""
    if hourly.empty:
        return _empty_bars()
    records = []
    for start in hourly.index.to_numpy(dtype="int64"):
        if start % STEP_12H:
            continue
        expected = np.arange(start, start + STEP_12H, HOUR, dtype="int64")
        block = hourly.reindex(expected)
        if len(block) != 12 or block[list(OHLC)].isna().any().any():
            continue
        records.append((start, block.open.iat[0], block.high.max(), block.low.min(), block.close.iat[-1]))
    if not records:
        return _empty_bars()
    return pd.DataFrame(records, columns=["time", *OHLC]).set_index("time")


def _ny_session(key_open_ms: int) -> dict:
    """Return exact NY Monday boundaries, converting each local midnight separately."""
    window = week_window(key_open_ms)
    return {
        "week": window["week"],
        "monday_start_ms": int(window["start"]),
        "monday_end_ms": int(window["end"]),
        "week_expires_ms": int(window["expires"]),
    }


def _source_paths(src, symbol: str):
    value = src.get(symbol)
    if not isinstance(value, (tuple, list)) or len(value) < 3:
        return None, None, None
    return value[0], value[1], int(value[2]) if value[2] is not None else None


def _touch_count(bars12: pd.DataFrame, lower: float, monday_end_ms: int,
                 key_open_ms: int, expires_ms: int) -> tuple[float, bool, int, int]:
    """Count prior strict low penetrations; missing expected candles make it unknown."""
    first_open = ((monday_end_ms + STEP_12H - 1) // STEP_12H) * STEP_12H
    if key_open_ms < first_open or key_open_ms + STEP_12H > expires_ms:
        return np.nan, True, 0, 0
    expected = np.arange(first_open, key_open_ms, STEP_12H, dtype="int64")
    expected = expected[expected + STEP_12H <= expires_ms]
    if not len(expected):
        return 0.0, False, 0, 0
    prior = bars12.reindex(expected)
    missing = int(prior.low.isna().sum())
    if missing:
        return np.nan, True, len(expected), missing
    return float(prior.low.lt(lower).sum()), False, len(expected), 0


def _event_records(parents: pd.DataFrame) -> pd.DataFrame:
    required = {"event_id", "symbol", "side", "entry_ms"}
    if not required.issubset(parents.columns):
        raise ValueError(f"LONG parents lack fields: {sorted(required - set(parents.columns))}")
    supplied = parents.loc[pd.to_numeric(parents.side, errors="coerce").eq(1)].copy()
    if "tp_r" in supplied:
        # V1.7 TP children share one parent event; compute the signal once.
        zero = pd.to_numeric(supplied.tp_r, errors="coerce").eq(0)
        if zero.any():
            supplied = supplied.loc[zero]
    if supplied.event_id.isna().any() or supplied.event_id.duplicated().any():
        # Duplicate TP paths are allowed only when their event clock and identity agree.
        duplicated = parents.loc[pd.to_numeric(parents.side, errors="coerce").eq(1)]
        keep = [c for c in ("symbol", "side", "entry_ms", "key_open_ms") if c in duplicated]
        conflicts = duplicated.groupby("event_id", dropna=False)[keep].nunique(dropna=False).gt(1).any(axis=1)
        if conflicts.any():
            raise ValueError("Duplicate LONG event IDs disagree on identity or signal clock")
        supplied = supplied.drop_duplicates("event_id", keep="first")
    if supplied.empty:
        return supplied
    supplied["entry_ms"] = pd.to_numeric(supplied.entry_ms, errors="raise").astype("int64")
    if "key_open_ms" in supplied:
        supplied["key_open_ms"] = pd.to_numeric(supplied.key_open_ms, errors="coerce")
        implied = supplied.entry_ms - STEP_12H
        supplied["key_open_ms"] = supplied.key_open_ms.fillna(implied).astype("int64")
        if not supplied.key_open_ms.add(STEP_12H).eq(supplied.entry_ms).all():
            raise ValueError("Parent key-open and confirmation clocks disagree")
    else:
        supplied["key_open_ms"] = supplied.entry_ms - STEP_12H
    return supplied


def _price_features_for_symbol(events: pd.DataFrame, src) -> list[dict]:
    symbol = str(events.symbol.iloc[0])
    h1_path, h12_path, end_ms = _source_paths(src, symbol)
    if end_ms is None:
        hourly = bars12 = _empty_bars()
        source_kind = "missing_source_map"
    else:
        hourly = _read_bars(h1_path, end_ms, HOUR)
        if h12_path is None:
            bars12 = _aggregate_12h_from_h1(hourly)
            source_kind = "complete_h1_aggregate"
        else:
            bars12 = _read_bars(h12_path, end_ms, STEP_12H)
            source_kind = "supplied_12h"

    if bars12.empty:
        atr_by_time = pd.Series(dtype="float64")
        run_by_time = pd.Series(dtype="int64")
    else:
        atr = atr_points(bars12.reset_index().to_dict("records"))
        atr_by_time = pd.Series([p["atr14"] for p in atr], index=bars12.index, dtype="float64")
        times = bars12.index.to_numpy(dtype="int64")
        segment = np.cumsum(np.r_[True, np.diff(times) != STEP_12H])
        run_by_time = pd.Series(np.arange(1, len(times) + 1), index=bars12.index).groupby(segment).cumcount() + 1
    by_time = {int(t): row for t, row in bars12.iterrows()}
    out = []

    for row in events.itertuples(index=False):
        key_open = int(row.key_open_ms)
        decision_ms = int(row.entry_ms)
        session = _ny_session(key_open)
        monday_start, monday_end, expires = (session["monday_start_ms"], session["monday_end_ms"],
                                               session["week_expires_ms"])
        in_session = key_open >= monday_end and key_open + STEP_12H <= expires
        monday_times = np.arange(monday_start, monday_end, HOUR, dtype="int64")
        monday = hourly.reindex(monday_times) if not hourly.empty else _empty_bars().reindex(monday_times)
        monday_complete = bool(
            len(monday) == len(monday_times) == 24
            and monday[list(OHLC)].notna().all().all()
        )
        if monday_complete:
            monday_high, monday_low = float(monday.high.max()), float(monday.low.min())
            monday_width = monday_high - monday_low
            if not np.isfinite(monday_width) or monday_width <= 0:
                monday_complete = False
        if monday_complete:
            lower = monday_low - .618 * monday_width
            upper = monday_high + .618 * monday_width
        else:
            monday_high = monday_low = monday_width = lower = upper = np.nan

        key = by_time.get(key_open)
        current_missing = key is None or not in_session
        atr14 = float(atr_by_time.get(key_open, np.nan)) if key_open in atr_by_time.index else np.nan
        warmup_bars = int(run_by_time.get(key_open, 0)) if key_open in run_by_time.index else 0
        range_atr_missing = current_missing or not np.isfinite(atr14) or atr14 <= 0
        if range_atr_missing:
            range_atr = np.nan
        else:
            range_atr = float((float(key.high) - float(key.low)) / atr14)

        if monday_complete and in_session:
            prior_touches, touches_missing, prior_expected, prior_missing = _touch_count(
                bars12, lower, monday_end, key_open, expires
            )
        else:
            prior_touches, touches_missing, prior_expected, prior_missing = np.nan, True, 0, 0

        source_reference = np.nan
        if hasattr(row, "stop_price"):
            stop = pd.to_numeric(pd.Series([row.stop_price]), errors="coerce").iat[0]
            if np.isfinite(stop) and stop > 0:
                pct = stop_pct_for(symbol)
                source_reference = float(stop / (1.0 - pct))
        anchor_delta = (source_reference - lower) if np.isfinite(source_reference) and np.isfinite(lower) else np.nan
        anchor_matches_stop = bool(
            np.isfinite(anchor_delta) and np.isclose(source_reference, lower, rtol=1e-9, atol=1e-12)
        )
        key_low = float(key.low) if key is not None else np.nan
        key_high = float(key.high) if key is not None else np.nan
        key_close = float(key.close) if key is not None else np.nan
        key_upper_close_ok = bool(np.isfinite(key_close) and np.isfinite(upper) and key_close < upper)
        key_lower_close_ok = bool(np.isfinite(key_close) and np.isfinite(lower) and key_close > lower)
        out.append({
            "event_id": row.event_id,
            "symbol": symbol,
            "key_open_ms": key_open,
            "key_close_ms": key_open + STEP_12H,
            "decision_ms": decision_ms,
            "causal_available_ms": decision_ms,
            "causal_timing_valid": bool(decision_ms == key_open + STEP_12H),
            "price_causal_valid": bool(
                monday_complete and monday_end <= key_open
                and key_open + STEP_12H <= decision_ms <= expires
                and key is not None
            ),
            "ny_week": session["week"],
            "monday_start_ms": monday_start,
            "monday_end_ms": monday_end,
            "week_expires_ms": expires,
            "key_inside_completed_week": bool(in_session),
            "monday_h1_expected": int(len(monday_times)),
            "monday_h1_observed": int(monday[list(OHLC)].notna().all(axis=1).sum()),
            "monday_anchor_missing": not monday_complete,
            "monday_high": monday_high,
            "monday_low": monday_low,
            "monday_width": monday_width,
            "lower_1618": lower,
            "upper_1618": upper,
            "source_reference_entry_from_stop": source_reference,
            "lower_1618_minus_source_reference": anchor_delta,
            "anchor_geometry_matches_original_stop": anchor_matches_stop,
            "key_12h_missing": current_missing,
            "key_low": key_low,
            "key_high": key_high,
            "key_close": key_close,
            "key_wick_strictly_below_lower_1618": bool(np.isfinite(key_low) and np.isfinite(lower) and key_low < lower),
            "key_close_inside_lower_1618": key_lower_close_ok,
            "key_close_inside_monday_extensions": key_lower_close_ok and key_upper_close_ok,
            "prior_touches": prior_touches,
            "prior_touches_missing": bool(touches_missing),
            "prior_12h_bars_expected": int(prior_expected),
            "prior_12h_bars_missing": int(prior_missing),
            "prior_12h_run_bars": max(warmup_bars - 1, 0) if warmup_bars else 0,
            "ema50_warmup_bars_including_key": warmup_bars,
            "ema50_warmup_complete": bool(warmup_bars >= 50),
            "ema50_warmup_missing": bool(warmup_bars < 50),
            "atr14_current": atr14,
            "range_atr": range_atr,
            "range_atr_missing": bool(range_atr_missing),
            "price_source_kind": source_kind,
            "source_end_ms": int(end_ms) if end_ms is not None else np.nan,
        })
    return out


def _volume_features(events: pd.DataFrame, src) -> pd.DataFrame:
    """Join the separately maintained, source-audited OKX base-volume adapter."""
    try:
        from yoyo.evaluation import cloud_close_okx_volume_sources as volume_source
    except ImportError as exc:
        raise RuntimeError("OKX volume adapter is unavailable; cannot build complete feature frame") from exc
    result = volume_source.volume_features(events[["event_id", "symbol", "key_open_ms"]], source_map=src)
    if not isinstance(result, pd.DataFrame) or "relative_volume" not in result:
        raise TypeError("OKX volume adapter must return an event_id-indexed DataFrame with relative_volume")
    if result.index.name != "event_id" or not result.index.is_unique:
        raise ValueError("OKX volume adapter output must have unique event_id index")
    if set(result.index) != set(events.event_id):
        raise ValueError("OKX volume adapter did not preserve every supplied LONG event")
    result = result.reindex(events.event_id).copy()
    expected = events.set_index("event_id")
    for column in ("symbol", "key_open_ms"):
        if column in result:
            left = result[column]
            right = expected[column].reindex(result.index)
            if column == "key_open_ms":
                same = pd.to_numeric(left, errors="coerce").astype("Int64").eq(
                    pd.to_numeric(right, errors="coerce").astype("Int64")
                )
            else:
                same = left.astype(str).eq(right.astype(str))
            if not same.all():
                raise ValueError(f"OKX volume adapter {column} keys disagree with source parents")
            result = result.drop(columns=[column])
    if "relative_volume_missing" not in result:
        result = result.copy()
        result["relative_volume_missing"] = result.relative_volume.isna()
    missing_waves = result.symbol.eq("WAVES") & result.relative_volume.isna()
    if missing_waves.any():
        from yoyo.evaluation.cloud_close_okx_waves_entry_volume import waves_volume

        keys = result.loc[missing_waves, "key_open_ms"]
        recovered = waves_volume(keys.to_list())
        complete = keys[keys.isin(recovered.dropna(subset=["relative_volume"]).index)]
        for column in ["current_base_volume", "prior20_median_base_volume", "relative_volume"]:
            result.loc[complete.index, column] = complete.map(recovered[column])
        result.loc[complete.index, ["prior_complete_bars", "missing_bar_count", "relative_volume_missing",
                                    "volume_missing_reason", "volume_source"]] = [
            20, 0, False, "", "OKX_official_monthly_trade_records",
        ]
    return result


def build_features(parents: pd.DataFrame, src) -> pd.DataFrame:
    """Return causal OKX features indexed by event_id for every supplied LONG.

    Duplicate target legs collapse to the same event-level feature row. Unknown
    source values remain null and carry an explicit ``*_missing`` flag.
    """
    events = _event_records(parents)
    if events.empty:
        return pd.DataFrame(index=pd.Index([], name="event_id"))
    records = []
    for _, group in events.groupby("symbol", sort=False):
        records.extend(_price_features_for_symbol(group, src))
    price = pd.DataFrame(records).set_index("event_id")
    if price.index.has_duplicates or len(price) != len(events):
        raise ValueError("Price feature construction did not preserve unique LONG event IDs")
    volume = _volume_features(events, src)
    if set(volume.index) != set(events.event_id):
        raise ValueError("Volume adapter did not preserve every supplied LONG event")
    out = price.join(volume, how="left", validate="one_to_one")
    out["relative_volume_missing"] = (
        out.relative_volume_missing.fillna(True).astype(bool) | out.relative_volume.isna()
    )
    return out

