"""Read per-symbol Binance USD-M 5m prices for Monday breadth studies.

The reader keeps memory bounded to one symbol at a time. It scans the source
files that overlap the requested UTC range and returns only observed bars; it
never synthesizes candles. Local observations are retained across the full
``[start, end]`` range. Optional REST repair is narrower: it is attempted only
inside the explicitly supplied holding intervals.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
RECLAIM_EXPERIMENT = ROOT / "experiments/active/exp-monday-limit-reclaim-20261024-v1"
OCTOBER_EXPERIMENT = ROOT / "experiments/active/exp-monday-limit-live-october-20261101-v1"
BASE_SERIES = ROOT / "data/crypto/market/kline_preholdout_binance_um5m/series"
FORWARD_SERIES = ROOT / "data/crypto/market/kline_binance_um5m_forward_20260922/series"
RECENT_SERIES = ROOT / "data/crypto/market/research-spike_v128_recent_20260923/series"
BULK_SERIES = ROOT / "data/crypto/market/binance_um5m_20260923_20261031/series"
BULK_MANIFEST = BULK_SERIES.parent / "manifest.json"
RECENT_MANIFEST = RECENT_SERIES.parent / "manifest.json"
REST_CACHE_SERIES = ROOT / "data/crypto/market/binance_um5m_rest_fallback/series"
ALLOWED_LOCAL_SOURCE_KINDS = {
    "preholdout_base",
    "forward",
    "recent",
    "october_new_bars",
    "bulk_extension",
    "rest_cache",
}

BAR_MS = 5 * 60 * 1000
BAR_FREQ = "5min"
CSV_CHUNK_ROWS = 100_000
REST_URL = "https://fapi.binance.com/fapi/v1/klines"
REST_LIMIT = 1000
REST_MAX_PAGES_PER_GAP = 12
REST_MIN_INTERVAL_SECONDS = 0.25
REST_TIMEOUT_SECONDS = 20

_rest_gate = threading.Lock()
_last_rest_start = 0.0
_not_listed_symbols: set[str] = set()
_not_listed_lock = threading.Lock()


def _iso_utc(value: Any) -> str:
    return pd.Timestamp(value).tz_convert("UTC").isoformat()


def _to_utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        # Date-only and naive inputs are treated as UTC for this data API.
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return stamp


def _ms_to_iso(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    return pd.to_datetime(int(value), unit="ms", utc=True).isoformat()


def _resolve_recorded_path(raw: str) -> Path:
    path = Path(raw)
    if path.exists():
        return path
    marker = "/fable-trading/"
    if marker in raw:
        candidate = ROOT / raw.split(marker, 1)[1]
        if candidate.exists():
            return candidate
    return path


@lru_cache(maxsize=8)
def _json(path_string: str) -> Any:
    path = Path(path_string)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=4)
def _manifest_by_symbol(path_string: str) -> dict[str, dict[str, Any]]:
    path = Path(path_string)
    if not path.is_file():
        return {}
    obj = _json(str(path))
    rows = obj.get("streams", []) if isinstance(obj, dict) else []
    return {str(row.get("symbol", "")).upper(): row for row in rows if row.get("symbol")}


@lru_cache(maxsize=2)
def _october_jobs() -> dict[str, dict[str, Any]]:
    path = OCTOBER_EXPERIMENT / "jobs.json"
    if not path.is_file():
        return {}
    obj = _json(str(path))
    return {str(key).upper(): value for key, value in obj.items()} if isinstance(obj, dict) else {}


def _recorded_paths(
    symbol: str,
) -> tuple[list[tuple[str, Path, int, list[str]]], list[dict[str, str]]]:
    """Return whitelisted 5m sources, collapsing path aliases by file identity."""
    symbol = symbol.upper()
    candidates: list[tuple[str, Path, int, str]] = []
    excluded: list[dict[str, str]] = []

    def add(raw_path: str, *, recorded: bool) -> None:
        path = _resolve_recorded_path(raw_path) if recorded else Path(raw_path)
        kind = _source_kind(path)
        if kind not in ALLOWED_LOCAL_SOURCE_KINDS:
            if recorded:
                excluded.append({"path": raw_path, "reason": "source_not_in_5m_whitelist"})
            return
        if kind == "preholdout_base" and "_5m_" not in path.name:
            if recorded:
                excluded.append({"path": raw_path, "reason": "base_filename_not_5m"})
            return
        candidates.append((kind, path, _priority(kind), raw_path))

    identity_path = RECLAIM_EXPERIMENT / "symbols" / symbol / "identity.json"
    if identity_path.is_file():
        try:
            identity = _json(str(identity_path))
            for item in identity.get("inputs", []):
                raw = item.get("path")
                if raw:
                    add(str(raw), recorded=True)
            for raw in identity.get("job", {}).get("paths", []):
                add(str(raw), recorded=True)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # Fixed-name sources below still provide a usable fallback.
            pass

    job = _october_jobs().get(symbol, {})
    for raw in job.get("paths", []):
        add(str(raw), recorded=True)

    # Construct known per-symbol paths so all 626 symbols are covered even
    # when an older job manifest contains only a subset.
    if BASE_SERIES.is_dir():
        for path in BASE_SERIES.glob(f"binance_um_{symbol}_5m_*.csv"):
            add(str(path), recorded=False)
    for kind, path in (
        ("forward", FORWARD_SERIES / f"{symbol}.csv"),
        ("recent", RECENT_SERIES / f"{symbol}.csv.gz"),
        ("october_new_bars", OCTOBER_EXPERIMENT / "symbols" / symbol / "new_bars.csv.gz"),
        ("bulk_extension", BULK_SERIES / f"{symbol}.csv.gz"),
    ):
        add(str(path), recorded=False)
    cache_dir = REST_CACHE_SERIES / symbol
    if cache_dir.is_dir():
        for path in cache_dir.glob("*.csv"):
            add(str(path), recorded=False)

    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for kind, path, priority, alias in candidates:
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            resolved = str(path.resolve())
            key: tuple[Any, ...] = ("inode", stat.st_dev, stat.st_ino) if stat.st_ino else ("path", resolved)
        except OSError:
            key = ("path", str(path))
            resolved = str(path)
        current = unique.get(key)
        if current is None:
            unique[key] = {"kind": kind, "path": path, "priority": priority, "aliases": [alias], "resolved": resolved}
            continue
        if alias not in current["aliases"]:
            current["aliases"].append(alias)
        if priority > current["priority"] or (
            priority == current["priority"] and _canonical_path_score(path) < _canonical_path_score(current["path"])
        ):
            current.update(kind=kind, path=path, priority=priority, resolved=resolved)
    result = [
        (row["kind"], row["path"], row["priority"], sorted(row["aliases"]))
        for row in unique.values()
    ]
    # Keep one excluded record per path spelling; alias groups for allowed
    # files are carried on the actual source receipt above.
    excluded = [dict(row) for row in { (item["path"], item["reason"]): item for item in excluded }.values()]
    return sorted(result, key=lambda row: (row[2], str(row[1]))), excluded


def _canonical_path_score(path: Path) -> tuple[int, int, str]:
    """Prefer the workspace-facing spelling while retaining all aliases."""
    in_workspace = path == ROOT or ROOT in path.parents
    return (0 if in_workspace else 1, len(str(path)), str(path))


def _source_kind(path: Path) -> str:
    text = str(path)
    if path.name == "new_bars.csv.gz":
        return "october_new_bars"
    if "binance_um5m_rest_fallback" in text:
        return "rest_cache"
    if "binance_um5m_20260923_20261031" in text:
        return "bulk_extension"
    if "research-spike_v128_recent_20260923" in text:
        return "recent"
    if "kline_binance_um5m_forward_20260922" in text:
        return "forward"
    if "kline_preholdout_binance_um5m" in text:
        return "preholdout_base"
    return "other_local"


def _priority(kind: str) -> int:
    return {
        "preholdout_base": 10,
        "forward": 20,
        "recent": 30,
        "october_new_bars": 35,
        "bulk_extension": 40,
        "rest_cache": 50,
        "other_local": 15,
    }.get(kind, 15)


def _manifest_bounds(kind: str, symbol: str) -> Optional[tuple[int, int]]:
    if kind == "recent":
        row = _manifest_by_symbol(str(RECENT_MANIFEST)).get(symbol.upper())
        if row:
            first = row.get("first_ms")
            last = row.get("last_ms")
            if first is not None and last is not None:
                return int(first), int(last)
    if kind == "bulk_extension":
        row = _manifest_by_symbol(str(BULK_MANIFEST)).get(symbol.upper())
        if row:
            first = row.get("first_ts", row.get("first_ms"))
            last = row.get("last_ts", row.get("last_ms"))
            if first is not None and last is not None:
                return int(first), int(last)
    return None


@lru_cache(maxsize=4096)
def _peek_csv_bounds(path_string: str) -> Optional[tuple[int, int]]:
    """Read the first and last timestamp of a sorted, uncompressed CSV."""
    path = Path(path_string)
    if path.suffix == ".gz" or not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            header_line = handle.readline()
            first_line = handle.readline()
            if not first_line:
                return None
            header = next(csv.reader([header_line.decode("utf-8-sig").strip()]))
            first = next(csv.reader([first_line.decode("utf-8").strip()]))
            ts_index = header.index("ts")
            first_ms = int(float(first[ts_index]))

            handle.seek(0, 2)
            end = handle.tell()
            if end <= len(header_line) + len(first_line):
                return first_ms, first_ms
            pos = end - 1
            handle.seek(pos)
            if handle.read(1) == b"\n":
                pos -= 1
            while pos > 0:
                handle.seek(pos)
                if handle.read(1) == b"\n":
                    break
                pos -= 1
            handle.seek(pos + 1)
            last_line = handle.readline().decode("utf-8").strip()
            last = next(csv.reader([last_line]))
            last_ms = int(float(last[ts_index]))
            return first_ms, last_ms
    except (OSError, ValueError, UnicodeDecodeError, csv.Error, IndexError):
        return None


def _source_bounds(kind: str, path: Path, symbol: str) -> Optional[tuple[int, int]]:
    if kind == "rest_cache":
        try:
            left, right = path.stem.split("_", 1)
            return int(left), int(right)
        except (ValueError, TypeError):
            return None
    manifest_bounds = _manifest_bounds(kind, symbol)
    if manifest_bounds is not None:
        return manifest_bounds
    if kind == "october_new_bars":
        # This archive is the late-September/October extension from the
        # October experiment; exact observed rows are still read from file.
        return (
            int(pd.Timestamp("2026-09-23", tz="UTC").timestamp() * 1000),
            int(pd.Timestamp("2026-10-31 23:55", tz="UTC").timestamp() * 1000),
        )
    if path.suffix != ".gz":
        return _peek_csv_bounds(str(path))
    return None


def _read_local_source(path: Path, start_ms: int, end_ms: int) -> tuple[pd.DataFrame, int]:
    """Stream one source and keep only bars in the requested local range."""
    kept: list[pd.DataFrame] = []
    invalid_geometry_rows = 0
    reader = pd.read_csv(
        path,
        compression="infer",
        usecols=["ts", "open", "high", "close"],
        dtype={"ts": "int64", "open": "float64", "high": "float64", "close": "float64"},
        chunksize=CSV_CHUNK_ROWS,
    )
    with reader:
        for chunk in reader:
            if chunk.empty:
                continue
            low = int(chunk["ts"].iloc[0])
            high = int(chunk["ts"].iloc[-1])
            if high < start_ms:
                continue
            if low > end_ms:
                break
            aligned = chunk["ts"] % BAR_MS == 0
            invalid_geometry_rows += int((~aligned).sum())
            chunk = chunk.loc[aligned & chunk["ts"].between(start_ms, end_ms)]
            if chunk.empty:
                continue
            index = pd.to_datetime(chunk.pop("ts"), unit="ms", utc=True)
            chunk.index = pd.DatetimeIndex(index, name="timestamp")
            kept.append(chunk.loc[:, ["open", "high", "close"]])
    if not kept:
        return _empty_frame(), invalid_geometry_rows
    return pd.concat(kept).sort_index(), invalid_geometry_rows


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {"open": pd.Series(dtype="float64"), "high": pd.Series(dtype="float64"), "close": pd.Series(dtype="float64")},
        index=pd.DatetimeIndex([], tz="UTC", name="timestamp"),
    )


def _normalize_windows(
    start: pd.Timestamp,
    end: pd.Timestamp,
    needed_intervals: Optional[Sequence[Sequence[Any]]],
) -> list[tuple[int, int]]:
    if not needed_intervals:
        return []
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    windows: list[tuple[int, int]] = []
    for item in needed_intervals:
        if len(item) != 2:
            raise ValueError("Each needed_interval must be a (start, end) pair")
        left, right = _to_utc(item[0]), _to_utc(item[1])
        if right < left:
            raise ValueError(f"needed_interval end precedes start: {item!r}")
        left_ms = max(start_ms, int(left.timestamp() * 1000))
        right_ms = min(end_ms, int(right.timestamp() * 1000))
        if left_ms <= right_ms:
            windows.append((left_ms, right_ms))
    windows.sort()
    merged: list[list[int]] = []
    for left, right in windows:
        if merged and left <= merged[-1][1] + BAR_MS:
            merged[-1][1] = max(merged[-1][1], right)
        else:
            merged.append([left, right])
    return [(left, right) for left, right in merged]


def _grid_bounds(left_ms: int, right_ms: int) -> Optional[tuple[int, int]]:
    first = ((left_ms + BAR_MS - 1) // BAR_MS) * BAR_MS
    last = (right_ms // BAR_MS) * BAR_MS
    return (first, last) if first <= last else None


def _missing_ranges(index: pd.DatetimeIndex, windows: Sequence[tuple[int, int]]) -> tuple[list[tuple[int, int]], list[dict[str, Any]]]:
    missing_ranges: list[tuple[int, int]] = []
    window_audit: list[dict[str, Any]] = []
    observed = pd.DatetimeIndex(index).as_unit("ms").asi8
    observed_set = set(int(value) for value in observed)
    for left, right in windows:
        grid = _grid_bounds(left, right)
        if grid is None:
            window_audit.append({"start_utc": _ms_to_iso(left), "end_utc": _ms_to_iso(right), "expected_bars": 0, "missing_bars": 0})
            continue
        first, last = grid
        expected_count = ((last - first) // BAR_MS) + 1
        missing = [stamp for stamp in range(first, last + 1, BAR_MS) if stamp not in observed_set]
        window_audit.append(
            {
                "start_utc": _ms_to_iso(first),
                "end_utc": _ms_to_iso(last),
                "expected_bars": expected_count,
                "missing_bars": len(missing),
            }
        )
        if not missing:
            continue
        run_start = previous = missing[0]
        for stamp in missing[1:]:
            if stamp != previous + BAR_MS:
                missing_ranges.append((run_start, previous))
                run_start = stamp
            previous = stamp
        missing_ranges.append((run_start, previous))
    return missing_ranges, window_audit


def _pace_rest() -> None:
    global _last_rest_start
    with _rest_gate:
        now = time.monotonic()
        delay = _last_rest_start + REST_MIN_INTERVAL_SECONDS - now
        if delay > 0:
            time.sleep(delay)
        _last_rest_start = time.monotonic()


def _rest_page(symbol: str, start_ms: int, end_ms: int) -> tuple[list[list[Any]], Optional[str], Optional[str]]:
    """Return Binance klines, an HTTP/API status, and a bounded error string."""
    with _not_listed_lock:
        if symbol in _not_listed_symbols:
            return [], "symbol_rejected_previously", "symbol was already rejected by Binance"

    query = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "interval": "5m",
            "startTime": int(start_ms),
            "endTime": int(end_ms),
            "limit": REST_LIMIT,
        }
    )
    request = urllib.request.Request(
        f"{REST_URL}?{query}",
        headers={"User-Agent": "fable-trading-monday-breadth/1.0", "Accept": "application/json"},
    )
    last_error: Optional[str] = None
    for attempt in range(3):
        _pace_rest()
        try:
            with urllib.request.urlopen(request, timeout=REST_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict):
                code = payload.get("code")
                message = str(payload.get("msg", payload))[:500]
                if code in (-1121, "-1121"):
                    with _not_listed_lock:
                        _not_listed_symbols.add(symbol)
                return [], f"api_error_{code}", message
            if not isinstance(payload, list):
                return [], "unexpected_response", repr(payload)[:500]
            return payload, None, None
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            last_error = f"HTTP {exc.code}: {body or exc.reason}"
            if exc.code in (418, 429, 500, 502, 503, 504) and attempt < 2:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    wait_seconds = min(10.0, max(1.0, float(retry_after))) if retry_after else 1.0 + attempt
                except ValueError:
                    wait_seconds = 1.0 + attempt
                time.sleep(wait_seconds)
                continue
            return [], f"http_{exc.code}", last_error
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"[:500]
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            return [], "request_error", last_error
    return [], "request_error", last_error


def _fetch_gap(symbol: str, left_ms: int, right_ms: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[tuple[int, float, float, float]] = []
    page_receipts: list[dict[str, Any]] = []
    cursor = left_ms
    pages = 0
    stop_reason = "range_complete"
    invalid_geometry_rows = 0
    while cursor <= right_ms and pages < REST_MAX_PAGES_PER_GAP:
        pages += 1
        response, status, error = _rest_page(symbol, cursor, right_ms)
        request_receipt: dict[str, Any] = {
            "start_utc": _ms_to_iso(cursor),
            "end_utc": _ms_to_iso(right_ms),
            "returned_rows": 0,
        }
        if status:
            request_receipt.update(status=status, error=error)
            page_receipts.append(request_receipt)
            stop_reason = status
            break
        valid: list[tuple[int, float, float, float]] = []
        for candle in response:
            try:
                ts = int(candle[0])
                if ts % BAR_MS != 0:
                    invalid_geometry_rows += 1
                    continue
                if cursor <= ts <= right_ms:
                    valid.append((ts, float(candle[1]), float(candle[2]), float(candle[4])))
            except (IndexError, TypeError, ValueError):
                continue
        valid.sort(key=lambda row: row[0])
        rows.extend(valid)
        request_receipt["returned_rows"] = len(valid)
        if valid:
            request_receipt["first_utc"] = _ms_to_iso(valid[0][0])
            request_receipt["last_utc"] = _ms_to_iso(valid[-1][0])
        page_receipts.append(request_receipt)
        if not valid:
            stop_reason = "empty_response"
            break
        next_cursor = valid[-1][0] + BAR_MS
        if next_cursor <= cursor:
            stop_reason = "nonadvancing_response"
            break
        if len(valid) < REST_LIMIT or next_cursor > right_ms:
            stop_reason = "range_complete"
            break
        cursor = next_cursor
    if pages >= REST_MAX_PAGES_PER_GAP and cursor <= right_ms:
        stop_reason = "page_cap_reached"
    if rows:
        frame = pd.DataFrame(rows, columns=["ts", "open", "high", "close"])
        frame.index = pd.to_datetime(frame.pop("ts"), unit="ms", utc=True)
        frame.index.name = "timestamp"
        frame = frame.loc[~frame.index.duplicated(keep="last")].sort_index()
    else:
        frame = _empty_frame()
    return frame, {
        "start_utc": _ms_to_iso(left_ms),
        "end_utc": _ms_to_iso(right_ms),
        "returned_rows": int(len(frame)),
        "invalid_geometry_rows": int(invalid_geometry_rows),
        "pages": page_receipts,
        "stop_reason": stop_reason,
    }


def _persist_rest_cache(symbol: str, frame: pd.DataFrame) -> dict[str, Any]:
    """Persist actual REST candles so price peaks can be reviewed later."""
    if frame.empty:
        return {"status": "no_rows_to_cache", "path": None, "rows": 0}
    index_ms = frame.index.as_unit("ms").asi8
    left_ms, right_ms = int(index_ms.min()), int(index_ms.max())
    target_dir = REST_CACHE_SERIES / symbol
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{left_ms}_{right_ms}.csv"
    if target.is_file():
        return {"status": "already_cached", "path": str(target), "rows": int(len(frame))}
    fd, temp_name = tempfile.mkstemp(prefix=f".{left_ms}_{right_ms}.", suffix=".tmp", dir=target_dir)
    os.close(fd)
    temp = Path(temp_name)
    try:
        out = frame.loc[:, ["open", "high", "close"]].copy()
        out.insert(0, "ts", index_ms)
        out.to_csv(temp, index=False)
        os.replace(temp, target)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    stat = target.stat()
    return {
        "status": "written",
        "path": str(target),
        "rows": int(len(frame)),
        "bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def load_quotes(
    symbol: str,
    start: Any,
    end: Any,
    *,
    needed_intervals: Optional[Sequence[Sequence[Any]]] = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load observed Binance USD-M 5m ``open/high/close`` bars for one symbol.

    Args:
        symbol: Binance USD-M symbol such as ``BTCUSDT``.
        start: Inclusive lower timestamp for local observations.
        end: Inclusive upper timestamp for local observations.
        needed_intervals: Optional disjoint holding windows. These windows are
            the only ranges eligible for public REST repair. If omitted, this
            function is local-only and makes no network requests.

    Returns:
        A sorted frame indexed by UTC 5m open time, plus a JSON-serializable
        receipt containing source lineage, per-file observations, REST calls,
        duplicate/conflict counts, and remaining gaps in requested windows.
    """
    symbol = str(symbol).strip().upper()
    if not symbol or any(not (char.isalnum() or char == "_") for char in symbol):
        raise ValueError(f"Invalid Binance USD-M symbol: {symbol!r}")
    start_utc, end_utc = _to_utc(start), _to_utc(end)
    if end_utc < start_utc:
        raise ValueError("end must be at or after start")
    start_ms = int(start_utc.timestamp() * 1000)
    end_ms = int(end_utc.timestamp() * 1000)
    rest_windows = _normalize_windows(start_utc, end_utc, needed_intervals)

    frames: list[pd.DataFrame] = []
    sources_receipt: list[dict[str, Any]] = []
    source_candidates, excluded_lineage = _recorded_paths(symbol)
    for kind, path, priority, aliases in source_candidates:
        bounds = _source_bounds(kind, path, symbol)
        if bounds is not None and (bounds[1] < start_ms or bounds[0] > end_ms):
            sources_receipt.append(
                {
                    "kind": kind,
                    "path": str(path),
                    "aliases": aliases,
                    "status": "outside_requested_range",
                    "source_first_utc": _ms_to_iso(bounds[0]),
                    "source_last_utc": _ms_to_iso(bounds[1]),
                }
            )
            continue
        try:
            frame, invalid_geometry_rows = _read_local_source(path, start_ms, end_ms)
            if not frame.empty:
                frame["__source_priority"] = priority
                frames.append(frame)
            stat = path.stat()
            sources_receipt.append(
                {
                    "kind": kind,
                    "path": str(path),
                    "aliases": aliases,
                    "resolved_path": str(path.resolve()),
                    "status": "read" if len(frame) else "no_rows_in_requested_range",
                    "rows_in_requested_range": int(len(frame)),
                    "invalid_5m_geometry_rows": int(invalid_geometry_rows),
                    "first_utc": frame.index.min().isoformat() if len(frame) else None,
                    "last_utc": frame.index.max().isoformat() if len(frame) else None,
                    "bytes": int(stat.st_size),
                    "mtime_ns": int(stat.st_mtime_ns),
                }
            )
        except (OSError, ValueError, pd.errors.ParserError, KeyError, EOFError) as exc:
            sources_receipt.append(
                {
                    "kind": kind,
                    "path": str(path),
                    "aliases": aliases,
                    "status": "read_error",
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
            )

    if frames:
        combined = pd.concat(frames).sort_index(kind="stable")
        duplicate_mask = combined.index.duplicated(keep=False)
        duplicate_rows = int(duplicate_mask.sum())
        conflicting_timestamps = 0
        if duplicate_rows:
            duplicated = combined.loc[duplicate_mask]
            conflicting_timestamps = int(
                duplicated[["open", "high", "close"]]
                .groupby(level=0, sort=False)
                .nunique()
                .gt(1)
                .any(axis=1)
                .sum()
            )
        combined = combined.sort_values("__source_priority", kind="stable")
        combined = combined.loc[~combined.index.duplicated(keep="last"), ["open", "high", "close"]]
        combined = combined.sort_index()
        combined.index = pd.DatetimeIndex(combined.index, tz="UTC", name="timestamp")
    else:
        combined = _empty_frame()
        duplicate_rows = 0
        conflicting_timestamps = 0

    local_row_count = int(len(combined))
    missing_ranges, window_audit = _missing_ranges(combined.index, rest_windows)
    rest_receipts: list[dict[str, Any]] = []
    rest_frames: list[pd.DataFrame] = []
    for left_ms, right_ms in missing_ranges:
        repaired, audit = _fetch_gap(symbol, left_ms, right_ms)
        audit["cache"] = _persist_rest_cache(symbol, repaired)
        rest_receipts.append(audit)
        if not repaired.empty:
            rest_frames.append(repaired)

    if rest_frames:
        rest_combined = pd.concat(rest_frames).sort_index()
        combined = pd.concat([combined, rest_combined]).sort_index(kind="stable")
        combined = combined.loc[~combined.index.duplicated(keep="last"), ["open", "high", "close"]]
        combined.index = pd.DatetimeIndex(combined.index, tz="UTC", name="timestamp")

    remaining_ranges, final_window_audit = _missing_ranges(combined.index, rest_windows)
    receipt = {
        "symbol": symbol,
        "venue": "Binance USD-M",
        "interval": "5m",
        "columns": ["open", "high", "close"],
        "index": "UTC candle open time",
        "requested_range_utc": {"start": start_utc.isoformat(), "end": end_utc.isoformat(), "inclusive": True},
        "needed_intervals_utc": [
            {"start": _ms_to_iso(left), "end": _ms_to_iso(right)} for left, right in rest_windows
        ],
        "local_sources": sources_receipt,
        "source_lineage_paths": sorted(
            {alias for source in sources_receipt for alias in source.get("aliases", [source["path"]])}
        ),
        "excluded_source_lineage": excluded_lineage,
        "local_rows": local_row_count,
        "local_observed_range_utc": {
            "start": combined.index.min().isoformat() if len(combined) else None,
            "end": combined.index.max().isoformat() if len(combined) else None,
        },
        "duplicate_local_rows_before_resolution": duplicate_rows,
        "conflicting_duplicate_timestamps": conflicting_timestamps,
        "rest": {
            "attempted": bool(rest_windows),
            "endpoint": REST_URL if rest_windows else None,
            "requests": rest_receipts,
            "rows_added": int(sum(len(frame) for frame in rest_frames)),
            "windows_before_repair": window_audit,
            "windows_after_repair": final_window_audit,
            "remaining_missing_ranges_utc": [
                {"start": _ms_to_iso(left), "end": _ms_to_iso(right)} for left, right in remaining_ranges
            ],
            "skipped_reason": None if rest_windows else "needed_intervals_not_supplied_or_empty",
        },
    }
    return combined.loc[:, ["open", "high", "close"]], receipt


__all__ = ["load_quotes"]
