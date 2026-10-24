"""Load the frozen weekly-Monday limit/reclaim source matrix without resampling.

The asset universe and 5m crypto sources are reused from
``monday_ma6_retest.source_jobs/source_frame``. Six Binance UM crypto assets
use the existing real 1m OOS files, while spot XAUUSD uses the corrected
HistData New York wall-clock reader and excludes every conflicted 5m bin.
Missing timestamps remain missing. HistData has no usable volume, so its
volume column is NaN rather than an invented value.

``weekly_context`` reports one audit record per New York week. Crypto anchors
require every completed Monday execution bar, and monitoring stops at the
first missing execution open after Tuesday 00:00 NY. Spot gold preserves the
existing minimum of 240 observed Monday 5m bins and exposes monitor gaps for
the execution adapter to classify; it does not synthesize bars or silently
bridge a gap.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
TZ = "America/New_York"
OHLC = ["open", "high", "low", "close"]
OHLCV = [*OHLC, "volume"]
MA6_CONFIG = ROOT / "experiments/active/exp-monday-ma6-retest-20261024-v1/config.json"
OOS_1M_CONFIG = ROOT / "experiments/active/exp-monday-1618-oos-1m-20261023-v1/config.json"
GOLD_IDENTITY = ROOT / "experiments/active/exp-monday-5m-wick-gold-clock-20261023-v2/identity.json"

ONE_MINUTE_ASSETS = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
    "SOLUSDT": "SOL",
    "BNBUSDT": "BNB",
    "XRPUSDT": "XRP",
    "DOGEUSDT": "DOGE",
}
CRYPTO_1M_CONFIRM_MINUTES = [1, 3, 5, 15, 60]
CRYPTO_5M_CONFIRM_MINUTES = [5, 15, 60]


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _config_value(value: Any, fallback: Path) -> dict[str, Any]:
    if value is None:
        return _read_json(fallback)
    if isinstance(value, dict):
        return value
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return _read_json(path)


def jobs(cfg: dict[str, Any] | str | Path | None = None) -> list[dict[str, Any]]:
    """Return one immutable source job per asset in the frozen 633-asset cohort.

    ``cfg`` is the existing Monday MA6 source configuration, or its path. The
    six 1m paths come from the existing OOS config; all other paths come from
    the frozen source identity matrix, including migrated-path symlinks.
    """
    from yoyo.evaluation import monday_ma6_retest as ma6

    study_cfg = _config_value(cfg, MA6_CONFIG)
    if "crypto_root" in study_cfg and "crypto_tail" in study_cfg:
        # Preserve the historical direct-MA6-config call signature.
        source_cfg = study_cfg
        minute_cfg = _read_json(OOS_1M_CONFIG)
    else:
        # The new study config points at both frozen source configurations.
        source_cfg = _config_value(study_cfg.get("baseline_config"), MA6_CONFIG)
        minute_cfg = _config_value(study_cfg.get("minute_config"), OOS_1M_CONFIG)

    identities = ma6.source_jobs(source_cfg)
    one_minute_paths = {
        symbol: str((ROOT / minute_cfg["series"][short_name]).absolute())
        for symbol, short_name in ONE_MINUTE_ASSETS.items()
    }

    result: list[dict[str, Any]] = []
    for symbol, original_paths in identities:
        if symbol in ONE_MINUTE_ASSETS:
            group = "crypto_1m"
            execution_minutes = 1
            confirm_minutes = CRYPTO_1M_CONFIRM_MINUTES.copy()
            paths = [one_minute_paths[symbol]]
        elif symbol == "XAUUSD":
            group = "spot_gold"
            execution_minutes = 1
            confirm_minutes = CRYPTO_1M_CONFIRM_MINUTES.copy()
            paths = list(original_paths)
        else:
            group = "crypto_5m"
            execution_minutes = 5
            confirm_minutes = CRYPTO_5M_CONFIRM_MINUTES.copy()
            paths = list(original_paths)

        result.append(dict(
            job_id=f"{symbol}-{execution_minutes}m",
            symbol=symbol,
            group=group,
            execution_minutes=execution_minutes,
            confirm_minutes=confirm_minutes,
            paths=paths,
        ))

    counts = pd.Series([job["group"] for job in result]).value_counts().to_dict()
    expected = {"crypto_1m": 6, "crypto_5m": 626, "spot_gold": 1}
    if len(result) != 633 or counts != expected:
        raise ValueError(f"Unexpected Monday-limit source matrix: jobs={len(result)}, groups={counts}")
    ids = [job["job_id"] for job in result]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate asset job identity")
    return result


def _gap_records(index: pd.DatetimeIndex, step: pd.Timedelta,
                 start_i: int = 0, end_i: int | None = None) -> list[dict[str, Any]]:
    """Describe observed timestamp breaks without filling or classifying them."""
    if end_i is None:
        end_i = len(index)
    if end_i - start_i < 2:
        return []
    times = index[start_i:end_i]
    deltas = np.diff(times.asi8)
    breaks = np.flatnonzero(deltas != step.value)
    result = []
    for offset in breaks:
        before_i = start_i + int(offset)
        after_i = before_i + 1
        delta = int(index[after_i].value - index[before_i].value)
        divisible = delta % step.value == 0
        result.append(dict(
            before_i=before_i,
            after_i=after_i,
            before=str(index[before_i]),
            after=str(index[after_i]),
            elapsed_minutes=delta / pd.Timedelta(minutes=1).value,
            missing_intervals=max(0, delta // step.value - 1) if divisible else None,
            reason="noncontiguous_source_timestamps",
        ))
    return result


def _validate_ohlcv(frame: pd.DataFrame, label: str) -> None:
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError(f"{label}: index must be unique and sorted")
    values = frame[OHLCV].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError(f"{label}: OHLCV contains non-finite values")
    if (frame[OHLC] <= 0).any().any() or (frame.volume < 0).any():
        raise ValueError(f"{label}: invalid price or negative volume")
    if (frame.high < frame[OHLC].max(axis=1)).any():
        raise ValueError(f"{label}: high is below an OHLC value")
    if (frame.low > frame[OHLC].min(axis=1)).any():
        raise ValueError(f"{label}: low is above an OHLC value")


def _load_crypto_1m(paths: list[str]) -> tuple[pd.DataFrame, dict[str, Any]]:
    parts = []
    for path in paths:
        frame = pd.read_csv(path, usecols=["ts", *OHLCV], float_precision="round_trip")
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("ts"), unit="ms", utc=True)).as_unit("ns")
        parts.append(frame)
    if not parts:
        raise ValueError("crypto_1m job has no source files")
    frame = pd.concat(parts).sort_index()
    duplicate_rows = frame[frame.index.duplicated(keep=False)]
    if len(duplicate_rows):
        conflicting = duplicate_rows.groupby(level=0).nunique().gt(1).any(axis=1)
        if conflicting.any():
            raise ValueError("Conflicting 1m crypto OHLCV overlap")
    frame = frame[~frame.index.duplicated()].copy()
    _validate_ohlcv(frame, "crypto_1m")
    gaps = _gap_records(frame.index, pd.Timedelta(minutes=1))
    return frame, dict(
        rows=len(frame), source_files=len(paths), duplicate_rows=len(duplicate_rows),
        overlap_policy="Exact OHLCV agreement, then deduplicate; no source preference",
        gap_count=len(gaps), gap_reasons={"noncontiguous_source_timestamps": len(gaps)}, gaps=gaps,
        blocked_monday_weeks=[],
    )


def _clean_gold_minutes(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Apply the HistData conflict policy at 1m resolution, excluding whole 5m bins."""
    frame = raw[OHLC].copy().sort_index()
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("HistData minute index must be timezone-aware")
    frame.index = frame.index.tz_convert("UTC").as_unit("ns")
    if not np.isfinite(frame[OHLC].to_numpy(dtype=float)).all():
        raise ValueError("HistData OHLC contains non-finite values")

    duplicate_rows = frame[frame.index.duplicated(keep=False)]
    conflicts = duplicate_rows.groupby(level=0).nunique().gt(1).any(axis=1) if len(duplicate_rows) else pd.Series(dtype=bool)
    conflicting_times = conflicts.index[conflicts] if len(conflicts) else pd.DatetimeIndex([], tz="UTC")
    excluded_bins = conflicting_times.floor("5min").unique().sort_values()

    clean = frame[~frame.index.floor("5min").isin(excluded_bins)]
    clean = clean[~clean.index.duplicated()].copy()
    clean["volume"] = np.nan  # HistData source volume is not an executable traded-volume field.
    clean["quality_segment"] = np.searchsorted(excluded_bins.asi8, clean.index.asi8, side="right")

    excluded_times = [str(time) for time in excluded_bins]
    quality = dict(
        rows=len(clean), duplicate_rows=len(duplicate_rows),
        conflicting_minutes=len(conflicting_times),
        excluded_five_minute_bins=len(excluded_bins),
        conflicting_minute_times=[str(time) for time in conflicting_times],
        excluded_bar_times=excluded_times,
        policy="Exclude every minute in each conflicting 5m bin; exact duplicates are deduplicated",
        volume_policy="Unavailable; NaN, never fabricated",
    )
    from yoyo.evaluation.monday_5m_wick_gold import invalid_monday_weeks
    blocked = sorted(invalid_monday_weeks(quality))
    quality["blocked_monday_weeks"] = blocked
    step = pd.Timedelta(minutes=1)
    gaps = _gap_records(clean.index, step)
    quality.update(
        gap_count=len(gaps),
        gap_reasons={"noncontiguous_source_timestamps": len(gaps)},
        gaps=gaps,
    )
    return clean, quality


def load_job(job: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load one asset into UTC bar-open OHLCV rows and return an audit record."""
    group = job["group"]
    execution_minutes = int(job["execution_minutes"])
    paths = list(job["paths"])
    if execution_minutes not in (1, 5):
        raise ValueError(f"Unsupported execution interval {execution_minutes}m")

    if group == "crypto_1m":
        if execution_minutes != 1:
            raise ValueError("crypto_1m must use native 1m source bars")
        frame, quality = _load_crypto_1m(paths)
    elif group == "crypto_5m":
        if execution_minutes != 5:
            raise ValueError("crypto_5m must use native 5m source bars; upsampling is prohibited")
        from yoyo.evaluation import monday_ma6_retest as ma6
        frame, quality = ma6.source_frame(job["symbol"], paths)
        frame = frame[OHLCV].copy()
        _validate_ohlcv(frame, "crypto_5m")
        gaps = _gap_records(frame.index, pd.Timedelta(minutes=5))
        quality = dict(quality)
        quality.update(
            rows=len(frame), gap_count=len(gaps),
            gap_reasons={"noncontiguous_source_timestamps": len(gaps)}, gaps=gaps,
            blocked_monday_weeks=list(quality.get("blocked_weeks", [])),
        )
    elif group == "spot_gold":
        if execution_minutes != 1 or job["symbol"] != "XAUUSD":
            raise ValueError("spot_gold is the real HistData XAUUSD 1m source")
        from yoyo.evaluation.monday_5m_wick_gold_clock import read_minutes
        raw, _ = read_minutes(paths)
        frame, quality = _clean_gold_minutes(raw)
        _validate_ohlcv(frame.assign(volume=0.0), "spot_gold")
        frame["volume"] = np.nan
        quality["monday_grid_audit"] = _monday_grid_audit(frame.index)
    else:
        raise ValueError(f"Unknown source group: {group}")

    frame.index = frame.index.tz_convert("UTC").as_unit("ns")
    frame.attrs.update(group=group, execution_minutes=execution_minutes)
    quality.update(
        job_id=job["job_id"], symbol=job["symbol"], group=group,
        execution_minutes=execution_minutes,
        confirm_minutes=list(job["confirm_minutes"]),
        first=str(frame.index[0]) if len(frame) else None,
        last=str(frame.index[-1]) if len(frame) else None,
    )
    frame.attrs["quality"] = quality
    return frame, quality


def _week_mondays(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if index.empty:
        return pd.DatetimeIndex([])
    local = index.tz_convert(TZ)
    first = local[0].tz_localize(None).normalize() - pd.Timedelta(days=local[0].dayofweek)
    last = local[-1].tz_localize(None).normalize() - pd.Timedelta(days=local[-1].dayofweek)
    return pd.date_range(first, last, freq="7D")


def _monday_grid_audit(index: pd.DatetimeIndex) -> dict[str, Any]:
    """Report exact NY-Monday 1m-grid completeness without changing gold eligibility."""
    index = pd.DatetimeIndex(index).tz_convert("UTC").as_unit("ns")
    rows = []
    for naive_monday in _week_mondays(index):
        monday_utc = naive_monday.tz_localize(TZ).tz_convert("UTC")
        tuesday_utc = (naive_monday.tz_localize(TZ) + pd.DateOffset(days=1)).tz_convert("UTC")
        left = int(index.searchsorted(monday_utc))
        right = int(index.searchsorted(tuesday_utc))
        expected = pd.date_range(monday_utc, tuesday_utc, freq="1min", inclusive="left")
        observed = index[left:right]
        complete = bool(observed.equals(expected))
        rows.append(dict(
            week=naive_monday.strftime("%Y-%m-%d"),
            status="complete" if complete else "incomplete_grid",
            complete=complete,
            expected_minutes=len(expected),
            observed_minutes=len(observed),
            missing_minutes=int(len(expected.difference(observed))),
        ))
    incomplete = [row["week"] for row in rows if not row["complete"]]
    return dict(
        eligibility_policy="Existing gold anchor threshold remains >=240 observed Monday 5m bins",
        completeness_policy="Exact NY-Monday 1m grid is diagnostic only; it does not replace the anchor threshold",
        weeks=rows,
        incomplete_week_count=len(incomplete),
        incomplete_weeks=incomplete,
    )


def _segments(index: pd.DatetimeIndex, start_i: int, end_i: int,
              step: pd.Timedelta) -> list[dict[str, Any]]:
    if start_i is None or end_i is None or end_i <= start_i:
        return []
    times = index[start_i:end_i]
    breaks = (np.flatnonzero(np.diff(times.asi8) != step.value) + 1).tolist()
    bounds = [0, *breaks, len(times)]
    return [dict(
        start_i=start_i + bounds[k],
        end_i_exclusive=start_i + bounds[k + 1],
        first=str(times[bounds[k]]),
        last=str(times[bounds[k + 1] - 1]),
    ) for k in range(len(bounds) - 1) if bounds[k] < bounds[k + 1]]


def _monitor_gaps(index: pd.DatetimeIndex, start_i: int, end_i: int,
                  step: pd.Timedelta, gold: bool) -> list[dict[str, Any]]:
    gaps = _gap_records(index, step, start_i, end_i)
    if gold:
        for gap in gaps:
            gap["reason"] = "unclassified_gold_gap"
    else:
        for gap in gaps:
            gap["reason"] = "missing_execution_bar"
    return gaps


def weekly_context(frame: pd.DataFrame, execution_minutes: int,
                   blocked_weeks: Any = ()) -> list[dict[str, Any]]:
    """Return auditable NY-Monday anchors and monitor ranges as half-open indices.

    Index positions always refer to the supplied frame. For crypto, a week is
    anchor-valid only if every expected execution OPEN exists on Monday, and
    monitoring starts only at the exact Tuesday 00:00 NY open. Its range ends
    immediately before the first missing bar. Gold keeps the established
    >=240 observed Monday 5m-bin threshold, flags incomplete minute grids, and
    treats every unclassified monitor gap as a cutoff because this layer has no
    auditable session calendar.
    """
    if execution_minutes not in (1, 5):
        raise ValueError("execution_minutes must be 1 or 5")
    index = pd.DatetimeIndex(frame.index)
    if index.tz is None:
        raise ValueError("weekly_context requires a timezone-aware UTC-open index")
    index = index.tz_convert("UTC").as_unit("ns")
    if not index.is_monotonic_increasing or index.has_duplicates:
        raise ValueError("weekly_context requires a unique, sorted index")
    gold = frame.attrs.get("group") == "spot_gold"
    blocked = set()
    for value in blocked_weeks or ():
        blocked.add(pd.Timestamp(value).strftime("%Y-%m-%d"))

    step = pd.Timedelta(minutes=execution_minutes)
    rows = []
    for naive_monday in _week_mondays(index):
        monday_local = naive_monday.tz_localize(TZ)
        tuesday_local = monday_local + pd.DateOffset(days=1)
        next_monday_local = monday_local + pd.DateOffset(days=7)
        monday_utc = monday_local.tz_convert("UTC")
        tuesday_utc = tuesday_local.tz_convert("UTC")
        next_monday_utc = next_monday_local.tz_convert("UTC")
        monday_start_i = int(index.searchsorted(monday_utc))
        monday_end_i = int(index.searchsorted(tuesday_utc))
        monday_frame = frame.iloc[monday_start_i:monday_end_i]
        week = naive_monday.strftime("%Y-%m-%d")
        expected = pd.date_range(monday_utc, tuesday_utc, freq=step, inclusive="left")
        observed = index[monday_start_i:monday_end_i]
        missing_monday = expected.difference(observed)
        observed_5m_bins = int(observed.floor("5min").nunique()) if len(observed) else 0

        if gold:
            monday_exclusion = "blocked_monday_conflict" if week in blocked else (
                "valid" if observed_5m_bins >= 240 else "fewer_than_240_monday_5m_bins")
            monday_valid = monday_exclusion == "valid"
        else:
            monday_exclusion = "blocked_monday_conflict" if week in blocked else (
                "valid" if observed.equals(expected) else "incomplete_monday_execution_grid")
            monday_valid = monday_exclusion == "valid"

        if monday_valid:
            monday_high = float(monday_frame.high.max())
            monday_low = float(monday_frame.low.min())
        else:
            monday_high = None
            monday_low = None

        monitor_left = int(index.searchsorted(tuesday_utc))
        monitor_right = int(index.searchsorted(next_monday_utc))
        monitor_start_i: int | None = None
        monitor_end_i: int | None = None
        monitor_start_time = None
        monitor_start_delay_minutes = None
        monitor_cutoff_time = None
        monitor_cutoff_cause = None
        monitor_start_reason = None
        monitor_unobservable_reason = None

        if gold:
            if monitor_left < monitor_right:
                monitor_start_i = monitor_left
                monitor_start_time = str(index[monitor_start_i])
                delay = index[monitor_start_i] - tuesday_utc
                monitor_start_delay_minutes = delay.total_seconds() / 60
                if delay == pd.Timedelta(0):
                    monitor_start_reason = "exact_tuesday_open"
                else:
                    monitor_start_reason = "missing_exact_tuesday_open"
                    monitor_unobservable_reason = "missing_exact_tuesday_open"

                segment = index[monitor_left:monitor_right]
                expected_last = next_monday_utc - step
                deltas = np.diff(segment.asi8)
                break_positions = np.flatnonzero(deltas != step.value)
                if len(break_positions):
                    first_break = int(break_positions[0])
                    monitor_end_i = monitor_left + first_break + 1
                    monitor_cutoff_time = str(index[monitor_end_i - 1] + step)
                    monitor_cutoff_cause = "unclassified_gold_gap"
                elif len(segment) and segment[-1] == expected_last:
                    monitor_end_i = monitor_right
                    monitor_cutoff_time = str(next_monday_utc)
                    monitor_cutoff_cause = "next_monday"
                elif monitor_right < len(index):
                    monitor_end_i = monitor_right
                    monitor_cutoff_time = str(segment[-1] + step) if len(segment) else str(tuesday_utc)
                    monitor_cutoff_cause = "unclassified_gold_gap"
                elif len(index) and index[-1] < next_monday_utc:
                    monitor_end_i = monitor_right
                    monitor_cutoff_time = str(index[-1] + step)
                    monitor_cutoff_cause = "source_end"
                else:
                    monitor_end_i = monitor_right
                    monitor_cutoff_time = str(next_monday_utc)
                    monitor_cutoff_cause = "next_monday"
            else:
                monitor_start_reason = "missing_exact_tuesday_open"
                monitor_unobservable_reason = "missing_exact_tuesday_open"
                monitor_cutoff_time = str(tuesday_utc)
                monitor_cutoff_cause = "missing_monitor_start"
        else:
            if monitor_left < monitor_right and index[monitor_left] == tuesday_utc:
                monitor_start_i = monitor_left
                monitor_start_time = str(index[monitor_start_i])
                monitor_start_delay_minutes = 0.0
                monitor_start_reason = "exact_tuesday_open"
                expected_last = next_monday_utc - step
                segment = index[monitor_left:monitor_right]
                deltas = np.diff(segment.asi8)
                break_positions = np.flatnonzero(deltas != step.value)
                if len(break_positions):
                    first_break = int(break_positions[0])
                    monitor_end_i = monitor_left + first_break + 1
                    monitor_cutoff_time = str(index[monitor_end_i - 1] + step)
                    monitor_cutoff_cause = "missing_execution_bar"
                elif len(segment) and segment[-1] == expected_last:
                    monitor_end_i = monitor_right
                    monitor_cutoff_time = str(next_monday_utc)
                    monitor_cutoff_cause = "next_monday"
                elif monitor_right < len(index):
                    monitor_end_i = monitor_right
                    monitor_cutoff_time = str(segment[-1] + step) if len(segment) else str(tuesday_utc)
                    monitor_cutoff_cause = "missing_execution_bar"
                else:
                    monitor_end_i = monitor_right
                    monitor_cutoff_time = str(segment[-1] + step) if len(segment) else str(tuesday_utc)
                    monitor_cutoff_cause = "source_end"
            else:
                monitor_start_reason = "missing_exact_tuesday_open"
                monitor_cutoff_time = str(tuesday_utc)
                monitor_cutoff_cause = "missing_monitor_start"

        if monitor_start_i is not None and monitor_end_i is not None:
            # Keep all observed breaks in the NY monitoring week for audit, even
            # though crypto's usable prefix ends at its first break.
            gap_scan_end = monitor_right
            monitor_gaps = _monitor_gaps(index, monitor_start_i, gap_scan_end, step, gold)
            if gold and monitor_start_reason == "missing_exact_tuesday_open":
                monitor_gaps.insert(0, dict(
                    before_i=None, after_i=monitor_start_i, before=None,
                    after=str(index[monitor_start_i]), expected_missing=str(tuesday_utc),
                    elapsed_minutes=monitor_start_delay_minutes,
                    missing_intervals=None, reason="missing_exact_tuesday_open",
                ))
            if not gold and monitor_cutoff_cause == "source_end":
                last_i = monitor_end_i - 1
                if last_i >= monitor_start_i and index[last_i] + step < next_monday_utc:
                    monitor_gaps.append(dict(
                        before_i=last_i, after_i=None, before=str(index[last_i]), after=None,
                        expected_missing=str(index[last_i] + step), elapsed_minutes=None,
                        missing_intervals=None, reason="source_end_before_next_monday",
                    ))
            # Preserve every observed source segment for audit, including data
            # after the first unclassified gold cutoff.
            monitor_segments = _segments(index, monitor_start_i,
                                         monitor_right if gold else monitor_end_i, step)
        else:
            monitor_gaps = []
            if monitor_start_reason == "missing_exact_tuesday_open":
                monitor_gaps.append(dict(
                    before_i=None, after_i=monitor_left, before=None,
                    after=str(index[monitor_left]) if monitor_left < monitor_right else None,
                    expected_missing=str(tuesday_utc),
                    elapsed_minutes=((index[monitor_left] - tuesday_utc).total_seconds() / 60
                                     if monitor_left < monitor_right else None),
                    missing_intervals=None, reason="missing_exact_tuesday_open",
                ))
            monitor_segments = []

        monitor_valid = (monitor_start_i is not None and monitor_end_i is not None
                         and monitor_end_i > monitor_start_i
                         and monitor_start_reason == "exact_tuesday_open")
        monday_grid_complete = bool(observed.equals(expected))
        rows.append(dict(
            week=week,
            valid=bool(monday_valid and monitor_valid),
            monday_valid=bool(monday_valid),
            monitor_valid=bool(monitor_valid),
            monday_exclusion_reason=monday_exclusion,
            monday_grid_complete=monday_grid_complete,
            monday_grid_status="complete" if monday_grid_complete else "incomplete_grid",
            monitor_start_reason=monitor_start_reason,
            monitor_unobservable_reason=monitor_unobservable_reason,
            monday_high=monday_high,
            monday_low=monday_low,
            monday_start_utc=str(monday_utc),
            monday_end_utc=str(tuesday_utc),
            next_monday_utc=str(next_monday_utc),
            monday_start_i=monday_start_i,
            monday_end_i_exclusive=monday_end_i,
            expected_monday_bars=len(expected),
            observed_monday_bars=len(observed),
            missing_monday_bars=len(missing_monday),
            observed_monday_5m_bins=observed_5m_bins,
            blocked_monday=week in blocked,
            monitor_start_i=monitor_start_i,
            monitor_end_i_exclusive=monitor_end_i,
            monitor_start_time=monitor_start_time,
            monitor_start_delay_minutes=monitor_start_delay_minutes,
            monitor_cutoff_time=monitor_cutoff_time,
            monitor_cutoff_cause=monitor_cutoff_cause,
            monitor_gap_count=len(monitor_gaps),
            monitor_gaps=monitor_gaps,
            monitor_segments=monitor_segments,
        ))
    return rows
