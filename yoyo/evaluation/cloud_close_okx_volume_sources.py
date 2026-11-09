"""Recover causal OKX 12H base-volume features from frozen venue sources.

The primary source is the original nine-field OKX ``history-candles`` payload
for native ``12Hutc`` bars (``volCcy`` is base-asset volume for swaps).  For
retired instruments whose native pages are absent, the loader can aggregate
only pre-existing official OKX monthly one-minute archives, requiring all 720
confirmed minutes in every 12H bar.  Missing or conflicting source data stays
unknown; it is never filled from another venue or from price-only files.
"""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from zipfile import ZipFile

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
STEP_12H_MS = 12 * 60 * 60 * 1000
STEP_1M_MS = 60 * 1000
ALLMARKET_INPUTS = ROOT / "experiments/active/exp-cloud-allmarket-screen-20261108-v1/inputs"
TOP20_INPUTS = ROOT / "experiments/active/exp-cloud-top20-peak10r-20261108-v1/inputs"
SHARED_CANDLE_CACHE = Path.home() / "Library/Application Support/Fable/ImpulseMonitor/shared-candles/cache.sqlite3"
RETIRED_ARCHIVE_CACHE = ROOT / "data/research/cloud_close_okx_roster_audit/raw"


def _number(value: object) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _timestamp(value: object) -> Optional[int]:
    try:
        stamp = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if isinstance(value, float) and stamp != value:
        return None
    return stamp if stamp >= 0 else None


def _payload_rows(value: object) -> Sequence[Sequence[object]]:
    if isinstance(value, dict):
        value = value.get("data", [])
    return value if isinstance(value, list) else []


def _close(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12)


def _path_label(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _merge_native(
    observations: Dict[str, Dict[int, dict]],
    symbol: str,
    row: Sequence[object],
    source: str,
) -> None:
    """Add one raw 9-field OKX candle, retaining conflicts instead of guessing."""
    if len(row) < 9:
        return
    stamp = _timestamp(row[0])
    if stamp is None or stamp % STEP_12H_MS:
        return
    confirm = str(row[8])
    if confirm not in {"0", "1"}:
        confirm = "invalid"
    candidate = {
        "base_volume": _number(row[6]),
        "contract_volume": _number(row[5]),
        "quote_volume": _number(row[7]),
        "confirmed": confirm == "1",
        "confirm_value": confirm,
        "source": "OKX_native_12Hutc_history_candles_cache",
        "source_paths": [source],
        "conflict": False,
    }
    by_time = observations.setdefault(symbol, {})
    prior = by_time.get(stamp)
    if prior is None:
        by_time[stamp] = candidate
        return
    same = (
        prior["confirm_value"] == candidate["confirm_value"]
        and _close(prior["base_volume"], candidate["base_volume"])
        and _close(prior["contract_volume"], candidate["contract_volume"])
        and _close(prior["quote_volume"], candidate["quote_volume"])
    )
    prior["source_paths"].append(source)
    if not same:
        prior["conflict"] = True


def _read_json_gzip(path: Path) -> object:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, EOFError, json.JSONDecodeError):
        return []


def _native_payload_paths(symbol: str, extra_cache: Optional[Path]) -> List[Path]:
    paths: List[Path] = []
    folder = ALLMARKET_INPUTS / symbol
    paths.extend(sorted((folder / "raw" / "12Hutc").glob("*.json.gz")))

    folder = TOP20_INPUTS / symbol
    if folder.is_dir():
        cached = folder / "12Hutc-cache.json.gz"
        if cached.is_file():
            paths.append(cached)
        paths.extend(sorted(folder.glob("12Hutc-*.json.gz")))

    if extra_cache is not None:
        paths.extend(sorted((extra_cache / "raw" / "12Hutc" / symbol).glob("*.json.gz")))
    return paths


def _load_native(symbols: Iterable[str], extra_cache: Optional[Path]) -> Dict[str, Dict[int, dict]]:
    observations: Dict[str, Dict[int, dict]] = {}
    symbols = sorted(set(map(str, symbols)))
    for symbol in symbols:
        for path in _native_payload_paths(symbol, extra_cache):
            for row in _payload_rows(_read_json_gzip(path)):
                _merge_native(observations, symbol, row, _path_label(path))

    if SHARED_CANDLE_CACHE.is_file():
        db_uri = "file:" + str(SHARED_CANDLE_CACHE.resolve()) + "?mode=ro"
        try:
            with sqlite3.connect(db_uri, uri=True, timeout=20) as db:
                for symbol in symbols:
                    key = "https://www.okx.com|%s-USDT-SWAP|12Hutc" % symbol
                    for (payload,) in db.execute("SELECT raw FROM candles WHERE key=? ORDER BY t", (key,)):
                        try:
                            row = json.loads(payload)
                        except (TypeError, json.JSONDecodeError):
                            continue
                        _merge_native(observations, symbol, row, "shared-candles.sqlite3:" + key)
        except sqlite3.Error:
            # Missing/locked optional cache does not invalidate the immutable pages.
            pass
    return observations


def _required_key_rows(parents: pd.DataFrame) -> pd.DataFrame:
    required = {"event_id", "symbol"}
    if not required.issubset(parents.columns):
        raise ValueError("parents require event_id and symbol")
    frame = parents.copy()
    if "key_open_ms" in frame:
        key = pd.to_numeric(frame["key_open_ms"], errors="coerce")
    elif "entry_ms" in frame:
        key = pd.to_numeric(frame["entry_ms"], errors="coerce") - STEP_12H_MS
    else:
        raise ValueError("parents require key_open_ms or entry_ms")
    if key.isna().any() or not np.equal(key.to_numpy(dtype="float64"), np.floor(key.to_numpy(dtype="float64"))).all():
        raise ValueError("key_open_ms must contain integral UTC milliseconds")
    frame["key_open_ms"] = key.astype("int64")
    if (frame["key_open_ms"] < 0).any() or (frame["key_open_ms"] % STEP_12H_MS).any():
        raise ValueError("key_open_ms must align to an OKX 12Hutc open")
    if "entry_ms" in frame:
        entry = pd.to_numeric(frame["entry_ms"], errors="coerce")
        if entry.notna().any() and not entry.loc[entry.notna()].astype("int64").eq(
            frame.loc[entry.notna(), "key_open_ms"] + STEP_12H_MS
        ).all():
            raise ValueError("entry_ms must follow key_open_ms by one complete 12H bar")
    if frame["event_id"].isna().any() or frame["symbol"].isna().any():
        raise ValueError("event_id and symbol cannot be missing")
    frame["event_id"] = frame["event_id"].astype(str)
    frame["symbol"] = frame["symbol"].astype(str)
    identity = frame.groupby("event_id", sort=False)[["symbol", "key_open_ms"]].nunique(dropna=False)
    if identity.gt(1).to_numpy().any():
        raise ValueError("duplicate event_id values disagree on symbol or key_open_ms")
    return frame.drop_duplicates("event_id", keep="first").reset_index(drop=True)


def _window_times(key_open_ms: int) -> List[int]:
    return list(range(key_open_ms - 20 * STEP_12H_MS, key_open_ms + STEP_12H_MS, STEP_12H_MS))


def _native_window_status(times: Sequence[int], bars: Mapping[int, dict]) -> Tuple[bool, List[int], bool]:
    invalid: List[int] = []
    conflict = False
    for stamp in times:
        item = bars.get(stamp)
        if item is None or not item.get("confirmed") or item.get("base_volume") is None:
            invalid.append(stamp)
        if item is not None and item.get("conflict"):
            conflict = True
    return not invalid and not conflict, invalid, conflict


def _month_strings(start_ms: int, end_ms_exclusive: int) -> List[str]:
    """Return UTC and UTC+8 archive-month labels intersecting the interval.

    OKX's public candle archives partition trading dates at UTC+8. Around a
    month boundary, UTC timestamps from the prior month can therefore live in
    the next month's ZIP; considering both month clocks avoids a false gap.
    """
    def clock_months(offset_ms: int) -> Set[str]:
        first = datetime.fromtimestamp((start_ms + offset_ms) / 1000, tz=timezone.utc)
        last = datetime.fromtimestamp((end_ms_exclusive - 1 + offset_ms) / 1000, tz=timezone.utc)
        result: Set[str] = set()
        year, month = first.year, first.month
        while (year, month) <= (last.year, last.month):
            result.add("%04d-%02d" % (year, month))
            month += 1
            if month == 13:
                year += 1
                month = 1
        return result
    return sorted(clock_months(0) | clock_months(8 * 60 * 60 * 1000))


def _merge_intervals(times: Iterable[int]) -> List[Tuple[int, int]]:
    intervals = sorted((int(t), int(t) + STEP_12H_MS) for t in set(times))
    merged: List[Tuple[int, int]] = []
    for left, right in intervals:
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    return merged


def _within_intervals(times: np.ndarray, intervals: Sequence[Tuple[int, int]]) -> np.ndarray:
    if not len(times) or not intervals:
        return np.zeros(len(times), dtype=bool)
    starts = np.asarray([item[0] for item in intervals], dtype="int64")
    ends = np.asarray([item[1] for item in intervals], dtype="int64")
    positions = np.searchsorted(starts, times, side="right") - 1
    valid = positions >= 0
    result = np.zeros(len(times), dtype=bool)
    result[valid] = times[valid] < ends[positions[valid]]
    return result


def _zip_path(instrument: str, month: str) -> Path:
    filename = "%s-candlesticks-%s.zip" % (instrument, month)
    return RETIRED_ARCHIVE_CACHE / instrument / filename


def _read_archive_minutes(
    instrument: str,
    month: str,
    intervals: Sequence[Tuple[int, int]],
) -> Tuple[pd.DataFrame, dict]:
    """Read one already-cached official monthly ZIP; this function never downloads."""
    path = _zip_path(instrument, month)
    if not path.is_file():
        marker = path.with_suffix(".404")
        return pd.DataFrame(), {"status": "official_404" if marker.is_file() else "not_cached",
                                "path": _path_label(path)}
    try:
        with ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(members) != 1:
                return pd.DataFrame(), {"status": "invalid_archive_members", "path": _path_label(path)}
            with archive.open(members[0]) as handle:
                header = pd.read_csv(handle, nrows=0).columns.tolist()
            volume_col = next((name for name in ("vol_ccy", "volCcy") if name in header), None)
            if volume_col is None or not {"instrument_name", "open_time", "confirm"}.issubset(header):
                return pd.DataFrame(), {"status": "volume_fields_missing", "path": _path_label(path)}
            with archive.open(members[0]) as handle:
                frame = pd.read_csv(handle, usecols=["instrument_name", "open_time", "confirm", volume_col],
                                    float_precision="round_trip")
    except (OSError, ValueError, KeyError, EOFError):
        return pd.DataFrame(), {"status": "archive_read_error", "path": _path_label(path)}

    if len(frame) and set(frame.instrument_name.astype(str)) != {instrument}:
        return pd.DataFrame(), {"status": "instrument_mismatch", "path": _path_label(path)}
    try:
        frame["open_time"] = pd.to_numeric(frame["open_time"], errors="raise").astype("int64")
        frame["base_volume"] = pd.to_numeric(frame[volume_col], errors="raise").astype("float64")
        frame["confirm"] = frame["confirm"].astype(str)
    except (TypeError, ValueError, OverflowError):
        return pd.DataFrame(), {"status": "invalid_archive_values", "path": _path_label(path)}
    if (frame["open_time"] % STEP_1M_MS).any() or (frame["base_volume"] < 0).any() or not np.isfinite(frame.base_volume).all():
        return pd.DataFrame(), {"status": "invalid_archive_grid_or_volume", "path": _path_label(path)}
    times = frame.open_time.to_numpy(dtype="int64")
    frame = frame.loc[_within_intervals(times, intervals), ["open_time", "base_volume", "confirm"]].copy()
    frame["source_month"] = month
    frame["source_path"] = _path_label(path)
    return frame, {"status": "cached_official_monthly_zip", "path": _path_label(path),
                   "rows_in_needed_windows": int(len(frame)), "volume_column": volume_col}


def _archive_bars(
    missing_by_symbol: Mapping[str, Set[int]],
) -> Tuple[Dict[str, Dict[int, dict]], Dict[str, List[dict]]]:
    result: Dict[str, Dict[int, dict]] = {}
    source_receipts: Dict[str, List[dict]] = {}
    for symbol, missing_times in missing_by_symbol.items():
        if not missing_times:
            continue
        instrument = symbol + "-USDT-SWAP"
        intervals = _merge_intervals(missing_times)
        months = sorted({m for left, right in intervals for m in _month_strings(left, right)})
        frames = []
        receipts = []
        for month in months:
            frame, receipt = _read_archive_minutes(instrument, month, intervals)
            receipts.append(dict(month=month, **receipt))
            if len(frame):
                frames.append(frame)
        source_receipts[symbol] = receipts
        if not frames:
            continue
        minutes = pd.concat(frames, ignore_index=True).sort_values("open_time", kind="stable")
        duplicated = minutes.open_time.duplicated(keep=False)
        conflicts: Set[int] = set()
        if duplicated.any():
            for stamp, group in minutes.loc[duplicated].groupby("open_time", sort=False):
                if group.confirm.nunique(dropna=False) > 1 or group.base_volume.map(lambda v: round(float(v), 12)).nunique() > 1:
                    conflicts.add(int(stamp))
            minutes = minutes.drop_duplicates("open_time", keep="first")
        minutes["bar_time"] = (minutes.open_time // STEP_12H_MS) * STEP_12H_MS
        wanted = set(missing_times)
        minutes = minutes.loc[minutes.bar_time.isin(wanted)]
        bars: Dict[int, dict] = {}
        for stamp, group in minutes.groupby("bar_time", sort=True):
            minute_times = group.open_time.to_numpy(dtype="int64")
            conflict = any((int(t) in conflicts) for t in minute_times)
            complete = (
                len(group) == 720
                and int(minute_times[0]) == int(stamp)
                and int(minute_times[-1]) == int(stamp) + STEP_12H_MS - STEP_1M_MS
                and bool(np.all(np.diff(minute_times) == STEP_1M_MS))
                and bool(group.confirm.eq("1").all())
                and not conflict
            )
            month_list = sorted(group.source_month.astype(str).unique().tolist())
            bars[int(stamp)] = {
                "base_volume": math.fsum(group.base_volume.to_numpy(dtype="float64")) if complete else None,
                "confirmed": bool(complete),
                "source": "OKX_official_monthly_1m_archive",
                "source_paths": [path for path in sorted(group.source_path.astype(str).unique())],
                "source_months": month_list,
                "conflict": bool(conflict),
                "minute_count": int(group.open_time.nunique()),
                "unconfirmed_minutes": int(group.confirm.ne("1").sum()),
            }
        result[symbol] = bars
    return result, source_receipts


def volume_features(
    parents: pd.DataFrame,
    source_map: Optional[Mapping[str, object]] = None,
    *,
    cache_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """Return causal key-volume / prior-20-median features indexed by event_id.

    ``parents`` needs ``event_id``, ``symbol`` and ``key_open_ms``. If only
    ``entry_ms`` is supplied, the key open is ``entry_ms - 12h``. Existing raw
    native API pages and the read-only shared candle cache are preferred. A
    missing event may be completed only from an already-present OKX official
    minute ZIP, with a complete 720-confirmed-minute requirement per 12H bar.
    ``source_map`` is accepted from the price builder for universe alignment;
    price paths are not used as volume substitutes.
    ``cache_dir`` optionally points at frozen extra API pages under a caller's
    experiment input directory; the function itself does not make network calls.
    """
    frame = _required_key_rows(parents)
    symbols = sorted(frame.symbol.unique().tolist())
    source_map_symbols = set(map(str, source_map)) if source_map is not None else None
    extra_cache = Path(cache_dir) if cache_dir is not None else None
    native = _load_native(symbols, extra_cache)
    event_windows: Dict[str, List[int]] = {
        str(row.event_id): _window_times(int(row.key_open_ms)) for row in frame.itertuples(index=False)
    }
    incomplete: Dict[str, Set[int]] = {}
    for row in frame.itertuples(index=False):
        times = event_windows[str(row.event_id)]
        complete, _, _ = _native_window_status(times, native.get(str(row.symbol), {}))
        if not complete:
            incomplete.setdefault(str(row.symbol), set()).update(times)

    archive, archive_receipts = _archive_bars(incomplete)
    combined: Dict[str, Dict[int, dict]] = {s: dict(bars) for s, bars in native.items()}
    for symbol, bars in archive.items():
        target = combined.setdefault(symbol, {})
        for stamp, archived in bars.items():
            existing = target.get(stamp)
            if existing is None or not existing.get("confirmed") or existing.get("base_volume") is None:
                target[stamp] = archived
            elif archived.get("confirmed") and archived.get("base_volume") is not None:
                if not _close(existing["base_volume"], archived["base_volume"]):
                    existing["conflict"] = True
                    existing["source_paths"].extend(archived.get("source_paths", []))

    receipts_by_symbol = archive_receipts
    receipts_by_symbol_month = {
        symbol: {str(receipt.get("month")): receipt for receipt in receipts}
        for symbol, receipts in archive_receipts.items()
    }
    records: List[dict] = []
    for row in frame.itertuples(index=False):
        event_id, symbol, key = str(row.event_id), str(row.symbol), int(row.key_open_ms)
        times = event_windows[event_id]
        bars = combined.get(symbol, {})
        invalid = []
        conflicts = []
        sources: Set[str] = set()
        volumes: Dict[int, float] = {}
        for stamp in times:
            item = bars.get(stamp)
            if item is None or not item.get("confirmed") or item.get("base_volume") is None:
                invalid.append(stamp)
            elif item.get("conflict"):
                conflicts.append(stamp)
            else:
                volumes[stamp] = float(item["base_volume"])
                sources.add(str(item.get("source", "unknown")))
        reason = ""
        current = volumes.get(key, np.nan)
        prior_values = [volumes.get(stamp, np.nan) for stamp in times[:-1]]
        prior_complete = int(sum(np.isfinite(value) for value in prior_values))
        prior_median = np.nan
        relative = np.nan
        if conflicts:
            reason = "conflicting_native_or_archive_volume"
        elif invalid:
            month_receipts = receipts_by_symbol_month.get(symbol, {})
            relevant = [
                month_receipts.get(month, {}).get("status")
                for stamp in invalid
                for month in _month_strings(stamp, stamp + STEP_12H_MS)
            ]
            if "official_404" in relevant:
                reason = "official_archive_404_or_delisted_gap"
            elif "not_cached" in relevant:
                reason = "required_official_archive_month_not_cached"
            elif "volume_fields_missing" in relevant:
                reason = "archive_base_volume_field_missing"
            elif "archive_read_error" in relevant or "invalid_archive_values" in relevant:
                reason = "archive_read_or_value_error"
            elif any(bars.get(t, {}).get("minute_count", 0) not in (0, 720) for t in invalid):
                reason = "incomplete_or_unconfirmed_12h_archive_bar"
            else:
                reason = "missing_confirmed_native_or_archive_bar"
        else:
            prior_array = np.asarray(prior_values, dtype="float64")
            prior_median = float(np.median(prior_array))
            if prior_median <= 0:
                reason = "zero_prior20_median_volume"
            else:
                relative = float(current / prior_median)
        source = "+".join(sorted(sources))
        records.append({
            "event_id": event_id,
            "symbol": symbol,
            "key_open_ms": key,
            "current_base_volume": current,
            "prior20_median_base_volume": prior_median,
            "prior_complete_bars": prior_complete,
            "missing_bar_count": len(invalid) + len(conflicts),
            "relative_volume": relative,
            "relative_volume_missing": bool(reason),
            "volume_missing_reason": reason,
            "volume_source": source,
            "volume_unit": "base_asset",
            "source_map_present": None if source_map_symbols is None else symbol in source_map_symbols,
        })
    result = pd.DataFrame.from_records(records).set_index("event_id")
    result.index.name = "event_id"
    if not result.index.is_unique:
        raise ValueError("OKX volume adapter output must have unique event_id index")
    return result
