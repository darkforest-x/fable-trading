"""Build causal Binance funding pressure for every frozen 12H V1.7 signal.

The feature uses only native realized fundingRate records available before the
signal's funding minute: the latest 24h sum minus the mean of the preceding
seven 24h sums, multiplied by signal side. A lower value is more favorable to
that direction. This is a descriptive input panel; it does not simulate fills
or select events by their subsequent account outcome.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import threading
import time
import zipfile

import numpy as np
import pandas as pd
import requests

from yoyo.data.altseason_funding import FundingClient, collect


ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-cloud-close-funding-pressure-20261109-v1"
CONFIG_PATH = EXP / "config.json"
SOURCE = ROOT / "experiments/active/exp-cloud-close-score-priority-20261109-v1/candidate_order.csv.gz"
OUTPUT = EXP / "signal_pressure.csv.gz"
CACHE_ROOT = ROOT / "data/research/cloud_close_funding_pressure_20261109"
MONTHLY_RAW = CACHE_ROOT / "monthly"
MONTHLY_RECEIPTS = CACHE_ROOT / "monthly_receipts"
API_ROOT = CACHE_ROOT / "funding_api"
SOURCE_AUDIT = EXP / "symbol_month_sources.csv"
SUMMARY = EXP / "summary.json"

LOCAL_ARCHIVE_ROOTS = (
    ROOT / "experiments/active/exp-monday-1618-flow-20261023-v1/inputs/funding",
    ROOT / "data/research/monday_breadth_optimization_20261108/funding_archives/funding",
)
LOCAL_API_ROOT = ROOT / "data/research/cloud_close_funding_ledger_20261109/funding_api"

DAY_MS = 86_400_000
HOUR_MS = 3_600_000
MINUTE_MS = 60_000
ASOF_LAG_MS = 0
MAX_GAP_MS = 8 * HOUR_MS + 120_000
MONTHLY_URL = "https://data.binance.vision/data/futures/um/monthly/fundingRate/{symbol}/{filename}"
RATE_COLUMNS = ["funding_time_ms", "funding_rate", "interval_hours", "source"]
OUTPUT_COLUMNS = [
    "event_id", "symbol", "entry_ms", "side", "asof_funding_time_ms",
    "recent_24h_rate_sum", "prior_7d_mean_daily_rate", "raw_difference", "pressure",
    "available", "availability_reason", "available_day_bins", "settlement_count_recent_24h",
    "support_settlement_count", "support_max_gap_ms", "cadence_validation", "source_months_json",
]


def _month_start_ms(month: str) -> int:
    return int(pd.Timestamp(month + "-01T00:00:00Z").value // 1_000_000)


def _month_after(month: str) -> str:
    return str(pd.Period(month, freq="M") + 1)


def _parse_signal(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"event_id", "symbol", "entry_ms"}
    if required - set(frame.columns):
        raise ValueError(f"Candidate source missing fields: {sorted(required - set(frame.columns))}")
    if frame.event_id.isna().any() or frame.symbol.isna().any() or frame.entry_ms.isna().any():
        raise ValueError("Candidate source contains null event identity or time")
    out = frame.copy()
    out["event_id"] = out.event_id.astype(str)
    out["symbol"] = out.symbol.astype(str)
    out["entry_ms"] = pd.to_numeric(out.entry_ms, errors="raise").astype("int64")
    if out.event_id.duplicated().any():
        raise ValueError("Candidate source has duplicate event IDs")
    sides, ids_symbols, ids_times = [], [], []
    for event_id in out.event_id:
        parts = event_id.split("|")
        if len(parts) != 4 or parts[0] != "12H":
            raise ValueError(f"Unexpected V1.7 12H event ID: {event_id}")
        if parts[-1] not in ("-1", "1"):
            raise ValueError(f"Event ID side suffix is not +/-1: {event_id}")
        sides.append(int(parts[-1]))
        ids_symbols.append(parts[1].removesuffix("USDT"))
        ids_times.append(int(parts[2]) // 1_000_000)
    out["side"] = np.asarray(sides, dtype=np.int8)
    if not np.array_equal(out.symbol.to_numpy(), np.asarray(ids_symbols)):
        raise ValueError("Candidate symbol disagrees with its confirmed event ID")
    if not np.array_equal(out.entry_ms.to_numpy(), np.asarray(ids_times, dtype=np.int64)):
        raise ValueError("Candidate entry_ms disagrees with nanosecond event ID")
    if (out.entry_ms % (12 * HOUR_MS) != 0).any():
        raise ValueError("Expected native UTC 12H signal clocks")
    out["contract_symbol"] = out.symbol + "USDT"
    return out


def load_candidates() -> pd.DataFrame:
    frame = pd.read_csv(SOURCE, dtype={"event_id": str, "symbol": str})
    out = _parse_signal(frame)
    if len(out) != 23_262 or out.symbol.nunique() != 657:
        raise ValueError(
            f"Frozen candidate roster changed: rows={len(out)}, symbols={out.symbol.nunique()}"
        )
    return out


def _event_support(entry_ms: int) -> tuple[int, int, int]:
    # Exclude the full nominal UTC minute containing the decision. The open
    # upper boundary is floor(entry_ms, one minute), so that minute is not used.
    cutoff = (entry_ms // MINUTE_MS) * MINUTE_MS - ASOF_LAG_MS
    feature_start = cutoff - 8 * DAY_MS
    support_start = feature_start - 9 * HOUR_MS
    return support_start, feature_start, cutoff


def _required_months(candidates: pd.DataFrame) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for row in candidates[["symbol", "entry_ms"]].itertuples(index=False):
        support_start, _, cutoff = _event_support(int(row.entry_ms))
        first_month = pd.Timestamp(support_start, unit="ms", tz="UTC").strftime("%Y-%m")
        last_month = pd.Timestamp(cutoff - 1, unit="ms", tz="UTC").strftime("%Y-%m")
        for month in pd.period_range(first_month, last_month, freq="M").astype(str):
            keys.add((str(row.symbol) + "USDT", month))
    return keys


def _archive_paths(symbol: str, month: str) -> list[tuple[Path, str]]:
    filename = f"{symbol}-fundingRate-{month}.zip"
    paths = [(root / symbol / filename, "existing_binance_monthly_archive") for root in LOCAL_ARCHIVE_ROOTS]
    paths.append((MONTHLY_RAW / symbol / filename, "official_monthly_fetched"))
    return [(path, source) for path, source in paths if path.is_file()]


def _api_paths(symbol: str, month: str) -> list[tuple[Path, str]]:
    name = f"{symbol}_funding.csv.gz"
    candidates = [
        (API_ROOT / symbol / month / "normalized/binance" / name, "official_rest_fetched"),
        (LOCAL_API_ROOT / symbol / month / "normalized/binance" / name, "existing_official_rest_cache"),
    ]
    return [(path, source) for path, source in candidates if path.is_file()]


def _read_monthly(path: Path, symbol: str, month: str, source: str) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        csv_members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(csv_members) != 1:
            raise ValueError(f"Expected one CSV in Binance funding archive: {path}")
        with archive.open(csv_members[0]) as handle:
            frame = pd.read_csv(handle)
    required = {"calc_time", "last_funding_rate"}
    if required - set(frame.columns):
        raise ValueError(f"Unexpected monthly funding schema at {path}")
    times = pd.to_numeric(frame.calc_time, errors="raise").astype("int64")
    rates = pd.to_numeric(frame.last_funding_rate, errors="coerce")
    interval = pd.to_numeric(frame.get("funding_interval_hours", np.nan), errors="coerce")
    start, end = _month_start_ms(month), _month_start_ms(_month_after(month))
    keep = (times >= start) & (times < end)
    out = pd.DataFrame({
        "funding_time_ms": times.loc[keep].to_numpy(dtype=np.int64),
        "funding_rate": rates.loc[keep].to_numpy(dtype=float),
        "interval_hours": np.asarray(interval)[keep.to_numpy()] if np.ndim(interval) else np.full(int(keep.sum()), np.nan),
        "source": source + ":" + str(path),
    })
    if out.funding_rate.isna().any() or not np.isfinite(out.funding_rate.to_numpy()).all():
        raise ValueError(f"Nonfinite realized funding rate in {path}")
    return out


def _read_api(path: Path, symbol: str, month: str, source: str) -> tuple[pd.DataFrame, dict | None]:
    manifest_path = path.with_suffix(".manifest.json")
    if not manifest_path.is_file():
        return pd.DataFrame(columns=RATE_COLUMNS), None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    spec = manifest.get("request_spec", {})
    if (spec.get("venue") != "binance" or spec.get("symbol") != symbol
            or not manifest.get("api_range_traversed")):
        raise ValueError(f"Unverified normalized Binance REST source: {path}")
    frame = pd.read_csv(path, keep_default_na=True)
    required = {"funding_time", "realized_rate"}
    if required - set(frame.columns):
        raise ValueError(f"Unexpected normalized API schema: {path}")
    start, end = _month_start_ms(month), _month_start_ms(_month_after(month))
    times = pd.to_numeric(frame.funding_time, errors="raise").astype("int64")
    rates = pd.to_numeric(frame.realized_rate, errors="coerce")
    keep = (times >= start) & (times < end)
    interval = pd.Series(np.nan, index=frame.index, dtype=float)
    out = pd.DataFrame({
        "funding_time_ms": times.loc[keep].to_numpy(dtype=np.int64),
        "funding_rate": rates.loc[keep].to_numpy(dtype=float),
        "interval_hours": interval.loc[keep].to_numpy(dtype=float),
        "source": source + ":" + str(path),
    })
    if out.funding_rate.isna().any() or not np.isfinite(out.funding_rate.to_numpy()).all():
        raise ValueError(f"Nonfinite realized funding rate in {path}")
    audit = {
        "kind": source, "path": str(path), "rows": len(out),
        "request_start_ms": int(spec["start_ms"]), "request_end_ms": int(spec["end_ms"]),
        "api_range_traversed": bool(manifest.get("api_range_traversed")),
    }
    return out, audit


def _load_month(symbol: str, month: str) -> tuple[pd.DataFrame, dict]:
    frame: pd.DataFrame | None = None
    audit: dict | None = None
    archive_paths = _archive_paths(symbol, month)
    if archive_paths:
        path, source = archive_paths[0]
        frame = _read_monthly(path, symbol, month, source)
        audit = {"kind": source, "path": str(path), "rows": len(frame)}
    else:
        for path, source in _api_paths(symbol, month):
            candidate, candidate_audit = _read_api(path, symbol, month, source)
            if candidate_audit is not None:
                frame, audit = candidate, candidate_audit
                break
    if frame is None or audit is None:
        return pd.DataFrame(columns=RATE_COLUMNS), {
            "symbol": symbol, "month": month, "row_count": 0, "source": "missing", "source_detail": "[]",
        }
    all_rows = frame.copy()
    all_rows["funding_time_ms"] = pd.to_numeric(all_rows.funding_time_ms, errors="raise").astype("int64")
    all_rows["funding_rate"] = pd.to_numeric(all_rows.funding_rate, errors="raise").astype(float)
    if all_rows.funding_time_ms.duplicated().any():
        raise ValueError(f"Duplicate native settlement timestamps in official source for {symbol} {month}")
    all_rows = all_rows.sort_values("funding_time_ms").reset_index(drop=True)
    interval_values = sorted({float(value) for value in all_rows.interval_hours.dropna().unique()})
    return all_rows, {
        "symbol": symbol, "month": month, "row_count": len(all_rows),
        "first_funding_ms": int(all_rows.funding_time_ms.min()) if len(all_rows) else None,
        "last_funding_ms": int(all_rows.funding_time_ms.max()) if len(all_rows) else None,
        "funding_interval_hours": json.dumps(interval_values, separators=(",", ":")),
        "source": audit["kind"],
        "source_detail": json.dumps([audit], ensure_ascii=False, separators=(",", ":")),
    }


def _local_inventory(keys: set[tuple[str, str]]) -> dict:
    available_archive = set()
    available_api = set()
    for symbol, month in keys:
        if any(path.is_file() for path, _ in _archive_paths(symbol, month)):
            available_archive.add((symbol, month))
        elif any(path.is_file() for path, _ in _api_paths(symbol, month)):
            available_api.add((symbol, month))
    return {
        "required_symbol_months": len(keys),
        "local_monthly_archive_keys": len(available_archive),
        "additional_local_api_keys": len(available_api),
        "local_missing_keys": len(keys - available_archive - available_api),
    }


def _key_query_bounds(candidates: pd.DataFrame) -> dict[tuple[str, str], tuple[int, int]]:
    bounds: dict[tuple[str, str], tuple[int, int]] = {}
    for row in candidates[["contract_symbol", "entry_ms"]].itertuples(index=False):
        support_start, _, cutoff = _event_support(int(row.entry_ms))
        month = pd.Timestamp(support_start, unit="ms", tz="UTC").strftime("%Y-%m")
        last = pd.Timestamp(cutoff - 1, unit="ms", tz="UTC").strftime("%Y-%m")
        for period in pd.period_range(month, last, freq="M"):
            key = (str(row.contract_symbol), str(period))
            month_start, month_end = _month_start_ms(str(period)), _month_start_ms(_month_after(str(period)))
            lower, upper = max(support_start, month_start), min(cutoff, month_end)
            existing_lower, existing_upper = bounds.get(key, (month_end, month_start))
            bounds[key] = min(existing_lower, lower), max(existing_upper, upper)
    return bounds


def _fetch_month_zip(key: tuple[str, str]) -> dict:
    symbol, month = key
    filename = f"{symbol}-fundingRate-{month}.zip"
    destination = MONTHLY_RAW / symbol / filename
    receipt_path = MONTHLY_RECEIPTS / symbol / f"{month}.json"
    if destination.is_file():
        return {"symbol": symbol, "month": month, "status": 200, "cached": True, "path": str(destination)}
    url = MONTHLY_URL.format(symbol=symbol, filename=filename)
    last_error = None
    session = requests.Session()
    session.trust_env = False
    try:
        for attempt in range(4):
            try:
                response = session.get(url, timeout=45)
                status = int(response.status_code)
                record = {
                    "symbol": symbol, "month": month, "url": url, "status": status,
                    "attempt": attempt + 1, "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                    "bytes": len(response.content),
                }
                if status == 200:
                    try:
                        with zipfile.ZipFile(__import__("io").BytesIO(response.content)) as archive:
                            if len([n for n in archive.namelist() if n.lower().endswith(".csv")]) != 1:
                                raise ValueError("Monthly ZIP did not contain exactly one CSV")
                    except Exception as exc:
                        raise ValueError(f"Invalid official monthly ZIP {url}: {exc}") from exc
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    tmp = destination.with_name(destination.name + ".tmp")
                    tmp.write_bytes(response.content)
                    tmp.replace(destination)
                    record["path"] = str(destination)
                    _atomic_json(receipt_path, record)
                    return record
                if status == 404:
                    record["body_preview"] = response.text[:300]
                    _atomic_json(receipt_path, record)
                    return record
                if status < 500 and status not in (418, 429):
                    record["body_preview"] = response.text[:300]
                    _atomic_json(receipt_path, record)
                    return record
                last_error = f"HTTP {status}: {response.text[:200]}"
            except (requests.RequestException, ValueError) as exc:
                last_error = str(exc)
            if attempt < 3:
                time.sleep(min(20, 2 ** (attempt + 1)))
    finally:
        session.close()
    record = {"symbol": symbol, "month": month, "url": url, "status": None,
              "fetched_at_utc": datetime.now(timezone.utc).isoformat(), "error": last_error}
    _atomic_json(receipt_path, record)
    return record


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _api_covers(path: Path, symbol: str, bounds: tuple[int, int]) -> bool:
    manifest_path = path.with_suffix(".manifest.json")
    if not path.is_file() or not manifest_path.is_file():
        return False
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    spec = manifest.get("request_spec", {})
    return bool(
        manifest.get("api_range_traversed")
        and spec.get("venue") == "binance"
        and spec.get("symbol") == symbol
        and int(spec.get("start_ms", 2**63 - 1)) <= bounds[0]
        and int(spec.get("end_ms", -1)) >= bounds[1]
    )


def _has_local_source(key: tuple[str, str], bounds: tuple[int, int]) -> bool:
    symbol, _ = key
    if any(path.is_file() for path, _ in _archive_paths(*key)):
        return True
    return any(_api_covers(path, symbol, bounds) for path, _ in _api_paths(*key))


def _fetch(keys: set[tuple[str, str]], candidates: pd.DataFrame, workers: int) -> dict:
    bounds = _key_query_bounds(candidates)
    missing = [key for key in sorted(keys) if not _has_local_source(key, bounds[key])]
    if missing:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_fetch_month_zip, key) for key in missing]
            for future in as_completed(futures):
                future.result()
    need_api = [key for key in sorted(keys)
                if not any(path.is_file() for path, _ in _archive_paths(*key))
                and not any(_api_covers(path, key[0], bounds[key]) for path, _ in _api_paths(*key))]
    api_results = []
    if need_api:
        # A single shared client owns Binance's funding endpoint request budget.
        client = FundingClient("binance", API_ROOT)
        for symbol, month in need_api:
            query_start, query_end = bounds[(symbol, month)]
            query_start = (query_start // 1000) * 1000
            query_end = ((query_end + 999) // 1000) * 1000
            client.root = API_ROOT / symbol / month
            try:
                manifest = collect(client, symbol, query_start, query_end)
                api_results.append({"symbol": symbol, "month": month, "api_range_traversed": manifest.get("api_range_traversed"),
                                    "row_count": manifest.get("row_count"), "stop_reason": manifest.get("stop_reason")})
            except Exception as exc:
                api_results.append({"symbol": symbol, "month": month, "error": str(exc)})
    return {"monthly_zip_attempts": len(missing), "rest_fallback_keys": len(need_api),
            "rest_successes": sum("error" not in r for r in api_results), "rest_errors": [r for r in api_results if "error" in r]}


def _month_source_json(source_rows: list[dict]) -> str:
    return json.dumps(source_rows, ensure_ascii=False, separators=(",", ":"))


def _score_event(row, history: pd.DataFrame, source_map: dict[tuple[str, str], dict]) -> dict:
    event_id, symbol, entry_ms, side = str(row.event_id), str(row.contract_symbol), int(row.entry_ms), int(row.side)
    support_start, feature_start, cutoff = _event_support(entry_ms)
    months = []
    cursor = pd.Timestamp(support_start, unit="ms", tz="UTC").strftime("%Y-%m")
    last_month = pd.Timestamp(cutoff - 1, unit="ms", tz="UTC").strftime("%Y-%m")
    for month in pd.period_range(cursor, last_month, freq="M").astype(str):
        months.append(source_map.get((symbol, month), {"month": month, "source": "missing", "row_count": 0}))
    base = {
        "event_id": event_id, "symbol": symbol.removesuffix("USDT"), "entry_ms": entry_ms, "side": side,
        "asof_funding_time_ms": cutoff - 1, "recent_24h_rate_sum": np.nan,
        "prior_7d_mean_daily_rate": np.nan, "raw_difference": np.nan, "pressure": np.nan,
        "available": False, "availability_reason": "missing_or_unverified_history",
        "available_day_bins": 0, "settlement_count_recent_24h": 0,
        "support_settlement_count": 0, "support_max_gap_ms": np.nan,
        "cadence_validation": "not_checked",
        "source_months_json": _month_source_json(months),
    }
    if history.empty:
        return base
    times = history.funding_time_ms.to_numpy(dtype=np.int64)
    rates = history.funding_rate.to_numpy(dtype=float)
    interval_values = (pd.to_numeric(history.interval_hours, errors="coerce").to_numpy(dtype=float)
                       if "interval_hours" in history else np.full(len(history), np.nan))
    in_support = (times >= support_start) & (times < cutoff)
    ts, vals, intervals = times[in_support], rates[in_support], interval_values[in_support]
    base["support_settlement_count"] = int(len(ts))
    if len(ts) < 2:
        base["availability_reason"] = "insufficient_support_rows"
        return base
    gaps = np.diff(ts)
    max_gap = int(gaps.max()) if len(gaps) else 0
    prior_start = cutoff - 8 * DAY_MS
    # A settlement within one maximum cadence before the feature start is
    # needed to verify support at the left edge; do not forward-fill it.
    predecessors = ts[ts < prior_start]
    if len(predecessors) == 0:
        base["availability_reason"] = "no_verified_predecessor_at_window_start"
        return base
    left_gap = prior_start - int(predecessors[-1])
    right_gap = cutoff - int(ts[-1])
    max_gap = max(max_gap, left_gap, right_gap)
    base["support_max_gap_ms"] = max_gap
    if max_gap > MAX_GAP_MS:
        base["availability_reason"] = "native_settlement_gap_over_8h"
        return base
    same_interval = (np.isfinite(intervals[:-1]) & np.isfinite(intervals[1:])
                     & np.isclose(intervals[:-1], intervals[1:], rtol=0, atol=1e-12))
    if same_interval.any():
        expected_gap = intervals[:-1][same_interval] * HOUR_MS + 120_000
        if np.any(gaps[same_interval] > expected_gap):
            base["availability_reason"] = "native_interval_gap_in_monthly_archive"
            base["cadence_validation"] = "monthly_interval_hours_gap_detected"
            return base
        base["cadence_validation"] = "monthly_interval_hours_checked_on_constant_segments"
    else:
        base["cadence_validation"] = "8h_max_gap_only_rest_or_interval_missing"
    day_sums = []
    bin_counts = []
    for i in range(8):
        end = cutoff - (7 - i) * DAY_MS
        start = end - DAY_MS
        mask = (ts >= start) & (ts < end)
        count = int(mask.sum())
        bin_counts.append(count)
        day_sums.append(float(vals[mask].sum()) if count else np.nan)
    available_bins = int(sum(count > 0 for count in bin_counts))
    base["available_day_bins"] = available_bins
    base["settlement_count_recent_24h"] = bin_counts[-1]
    if available_bins != 8:
        base["availability_reason"] = "one_or_more_24h_bins_empty"
        return base
    recent, prior_mean = float(day_sums[-1]), float(np.mean(day_sums[:-1]))
    difference = recent - prior_mean
    base.update({
        "recent_24h_rate_sum": recent,
        "prior_7d_mean_daily_rate": prior_mean,
        "raw_difference": difference,
        "pressure": side * difference,
        "available": True,
        "availability_reason": "complete_native_8d_window",
    })
    return base


def build_panel(candidates: pd.DataFrame, month_frames: dict[tuple[str, str], pd.DataFrame], source_rows: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    source_map = {(row["symbol"], row["month"]): row for row in source_rows}
    by_symbol: dict[str, pd.DataFrame] = {}
    if month_frames:
        grouped: dict[str, list[pd.DataFrame]] = {}
        for (symbol, _), frame in month_frames.items():
            if not frame.empty:
                grouped.setdefault(symbol, []).append(frame)
        for symbol, frames in grouped.items():
            full = pd.concat(frames, ignore_index=True).sort_values("funding_time_ms")
            if full.funding_time_ms.duplicated().any():
                full = full.drop_duplicates("funding_time_ms", keep="first")
            by_symbol[symbol] = full.reset_index(drop=True)
    panel = []
    for row in candidates.itertuples(index=False):
        panel.append(_score_event(row, by_symbol.get(row.contract_symbol, pd.DataFrame()), source_map))
    result = pd.DataFrame(panel, columns=OUTPUT_COLUMNS)
    audit = pd.DataFrame(source_rows).sort_values(["symbol", "month"]).reset_index(drop=True)
    if len(result) != len(candidates) or result.event_id.duplicated().any():
        raise ValueError("Funding-pressure panel does not preserve the candidate roster one-to-one")
    if not np.array_equal(result.event_id.to_numpy(), candidates.event_id.to_numpy()):
        raise ValueError("Funding-pressure panel event order changed")
    return result, audit


def _load_all_local(keys: set[tuple[str, str]]) -> tuple[dict, list[dict]]:
    frames, audits = {}, []
    for symbol, month in sorted(keys):
        frame, audit = _load_month(symbol, month)
        frames[(symbol, month)] = frame
        audits.append(audit)
    return frames, audits


def _assert_frozen() -> None:
    for path in (Path(__file__).resolve(), CONFIG_PATH.resolve()):
        relative = str(path.relative_to(ROOT))
        subprocess.run(["git", "ls-files", "--error-unmatch", relative], cwd=ROOT, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", relative], cwd=ROOT).returncode:
            raise RuntimeError("Commit the funding-pressure builder/config before collection")


def _self_check() -> dict:
    t = int(pd.Timestamp("2025-01-09T12:00:00Z").value // 1_000_000)
    _, start, cutoff = _event_support(t)
    stamps = np.arange(start - 8 * HOUR_MS, cutoff, 8 * HOUR_MS, dtype=np.int64)
    rates = np.where(stamps >= cutoff - DAY_MS, 0.001, 0.0001)
    synthetic = pd.DataFrame({"funding_time_ms": stamps, "funding_rate": rates})
    row = type("Signal", (), {"event_id": "12H|XMRUSDT|1736424000000000000|1", "symbol": "XMR",
                              "contract_symbol": "XMRUSDT",
                              "entry_ms": t, "side": 1})()
    score = _score_event(row, synthetic, {})
    expected_recent = float(rates[stamps >= cutoff - DAY_MS].sum())
    expected_prior = float(np.mean([rates[(stamps >= cutoff - i * DAY_MS) & (stamps < cutoff - (i - 1) * DAY_MS)].sum()
                                    for i in range(2, 9)]))
    if not score["available"] or not np.isclose(score["pressure"], expected_recent - expected_prior, atol=1e-14):
        raise AssertionError("Funding feature formula/side check failed")
    if score["asof_funding_time_ms"] >= (t // MINUTE_MS) * MINUTE_MS:
        raise AssertionError("Feature includes the nominal signal funding minute")
    broken = synthetic.copy()
    broken.loc[len(broken)] = {"funding_time_ms": cutoff + 30_000, "funding_rate": 99.0}
    after_boundary = _score_event(row, broken, {})
    if not after_boundary["available"] or after_boundary["pressure"] != score["pressure"]:
        raise AssertionError("Same-minute post-boundary settlement was not excluded")
    sparse = synthetic.loc[synthetic.funding_time_ms < start + 20 * HOUR_MS].copy()
    if _score_event(row, sparse, {})["available"]:
        raise AssertionError("Incomplete native settlement history was accepted")
    source = pd.DataFrame([{"event_id": row.event_id, "symbol": "XMR", "entry_ms": t}])
    parsed = _parse_signal(source)
    if int(parsed.side.iloc[0]) != 1:
        raise AssertionError("Side parsing from original event ID failed")
    return {"self_check": "passed", "synthetic_settlements": len(stamps),
            "available_bins": score["available_day_bins"], "same_minute_excluded": True,
            "incomplete_history_rejected": True, "event_id_side_parsed": int(parsed.side.iloc[0])}


def preflight() -> dict:
    candidates = load_candidates()
    keys = _required_months(candidates)
    bounds = _key_query_bounds(candidates)
    inventory = _local_inventory(keys)
    signal_months = {(row.contract_symbol, pd.Timestamp(row.entry_ms, unit="ms", tz="UTC").strftime("%Y-%m"))
                     for row in candidates[["contract_symbol", "entry_ms"]].itertuples(index=False)}
    return {
        "experiment": EXP.name, "candidate_rows": len(candidates), "symbols": candidates.symbol.nunique(),
        "first_signal_utc": pd.to_datetime(candidates.entry_ms.min(), unit="ms", utc=True).isoformat(),
        "last_signal_utc": pd.to_datetime(candidates.entry_ms.max(), unit="ms", utc=True).isoformat(),
        "signal_symbol_months": len(signal_months), "required_support_symbol_months": len(keys),
        "local_sources": inventory, "unique_month_query_range_keys": len(bounds),
        "output": str(OUTPUT.relative_to(ROOT)), "self_check": _self_check(),
    }


def run(fetch: bool, workers: int) -> dict:
    _assert_frozen()
    candidates = load_candidates()
    keys = _required_months(candidates)
    fetch_summary = _fetch(keys, candidates, workers) if fetch else None
    frames, source_rows = _load_all_local(keys)
    missing = [row for row in source_rows if row["source"] == "missing"]
    panel, audit = build_panel(candidates, frames, source_rows)
    EXP.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT.with_name(OUTPUT.name + ".tmp")
    panel.to_csv(tmp, index=False, compression={"method": "gzip", "mtime": 0}, float_format="%.17g")
    tmp.replace(OUTPUT)
    audit.to_csv(SOURCE_AUDIT, index=False)
    summary = {
        "experiment": EXP.name, "candidate_rows": len(candidates), "symbols": int(candidates.symbol.nunique()),
        "required_support_symbol_months": len(keys), "source_months_loaded": len(source_rows),
        "missing_month_keys": len(missing), "available_events": int(panel.available.sum()),
        "unavailable_events": int((~panel.available).sum()),
        "availability_reasons": panel.availability_reason.value_counts(dropna=False).to_dict(),
        "pressure_nonnull": int(panel.pressure.notna().sum()), "fetch": fetch_summary,
        "feature_formula": "side * (sum of funding rates in latest causal 24h - mean of seven preceding causal 24h sums)",
        "causal_cutoff": "exclude full UTC minute containing entry; 8 exact 24h bins end at the open UTC minute boundary floor(entry_ms, 1 minute)",
        "continuity": {"required_nonempty_24h_bins": 8, "max_native_gap_ms": MAX_GAP_MS,
                       "support_predecessor_max_gap_ms": MAX_GAP_MS,
                       "monthly_archive_interval_hours_check": "constant adjacent interval_hours segments are checked against interval + 120s",
                       "rest_fallback_limit": "fundingRate REST has no native interval field; an omitted 1h/4h record can evade the 8h maximum-gap guard"},
        "output": str(OUTPUT.relative_to(ROOT)), "source_audit": str(SOURCE_AUDIT.relative_to(ROOT)),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_json(SUMMARY, summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true", help="Read candidate and local source inventory only")
    parser.add_argument("--self-check", action="store_true", help="Run deterministic formula and boundary checks")
    parser.add_argument("--run", action="store_true", help="Build the output using currently cached sources")
    parser.add_argument("--fetch", action="store_true", help="Fetch required official monthly ZIPs, REST-fallback 404s, then build output")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        parser.error("workers must be in 1..4")
    if sum((args.preflight, args.self_check, args.run, args.fetch)) != 1:
        parser.error("Choose exactly one of --preflight, --self-check, --run, or --fetch")
    if args.preflight:
        result = preflight()
    elif args.self_check:
        result = _self_check()
    else:
        result = run(fetch=args.fetch, workers=args.workers)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
