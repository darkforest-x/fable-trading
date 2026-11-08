"""Rebuild ChartPrime's AUTO lower-timeframe POC for a UTC 12H FVG.

The frozen ``qujivabv.pine`` source under the ChartPrime audit uses
``int(chart_minutes / 10)`` as the AUTO timeframe. On a 12H chart this is 72
minutes. The native 72m sequence is
reconstructed from confirmed OKX 3m candles (10 buckets, 24 inputs each), then
the frozen Pine close/volume binning is applied to the supplied FVG bounds.

Only the exact AUTO 72m profile is returned here. A failed/incomplete source is
reported as unavailable; it is never replaced with a guessed or partial POC.
Raw verified windows are cached under ``target_dir`` so repeated zones in the
same 12H candle reuse the same market data.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Iterable

import requests
from yoyo.evaluation.top20_1618_rank import request as okx_request


BAR = "3m"
BAR_MS = 3 * 60_000
MIDDLE_MS = 12 * 60 * 60_000
BUCKET_MS = 72 * 60_000
BUCKETS = 10
ROWS_PER_BUCKET = BUCKET_MS // BAR_MS
BINS = 15
PAGE_LIMIT = 300
MAX_PAGES = 4  # Four pages still cover a conservative 60-row server cap.
_locks_guard = threading.Lock()
_key_locks: dict[str, threading.Lock] = {}


def _key_lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _key_locks.setdefault(key, threading.Lock())


def _inst_id(symbol: str) -> str:
    value = str(symbol).strip().upper().replace("_", "-")
    if value.endswith("-USDT-SWAP"):
        inst_id = value
    elif value and "-" not in value:
        inst_id = f"{value}-USDT-SWAP"
    else:
        raise ValueError(f"expected a base symbol or OKX USDT swap, got {symbol!r}")
    if not inst_id.removesuffix("-USDT-SWAP") or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789." for ch in inst_id.removesuffix("-USDT-SWAP")):
        raise ValueError(f"invalid OKX instrument identity: {symbol!r}")
    return inst_id


def _validate_bounds(middle_open_ms: int, lo: float, hi: float) -> tuple[int, float, float]:
    try:
        middle = int(middle_open_ms)
        lower, upper = float(lo), float(hi)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("middle_open_ms, lo, and hi must be numeric") from exc
    try:
        exact_integer = float(middle_open_ms) == middle
    except (TypeError, ValueError, OverflowError):
        exact_integer = False
    if not exact_integer or middle < 0 or middle % MIDDLE_MS:
        raise ValueError("middle_open_ms must be a UTC 12H candle open in milliseconds")
    if not (math.isfinite(lower) and math.isfinite(upper) and 0 < lower < upper):
        raise ValueError("FVG bounds must be finite positive prices with hi > lo")
    return middle, lower, upper


def _parse_confirmed_rows(raw_rows: Iterable[list], start_ms: int, end_ms: int) -> list[tuple[int, float, float]]:
    """Validate native OKX rows and retain only the requested 12H interval."""
    found: dict[int, tuple[float, float, float, float, float]] = {}
    for raw in raw_rows:
        if not isinstance(raw, (list, tuple)) or len(raw) < 9:
            raise ValueError("OKX candle row is malformed")
        try:
            stamp = int(raw[0])
            o, h, low, close, volume = (float(raw[i]) for i in range(1, 6))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("OKX candle row has nonnumeric fields") from exc
        confirm = str(raw[8])
        if confirm not in {"0", "1"}:
            raise ValueError("OKX candle confirmation flag is invalid")
        if not (start_ms <= stamp < end_ms):
            continue
        if confirm != "1":
            continue
        if stamp % BAR_MS:
            raise ValueError(f"off-grid {BAR} candle timestamp: {stamp}")
        if not all(math.isfinite(x) for x in (o, h, low, close, volume)):
            raise ValueError(f"nonfinite OKX candle at {stamp}")
        if min(o, h, low, close) <= 0 or volume < 0 or h < max(o, low, close) or low > min(o, close):
            raise ValueError(f"invalid OHLCV geometry at {stamp}")
        item = (o, h, low, close, volume)
        prior = found.get(stamp)
        if prior is not None and prior != item:
            raise ValueError(f"conflicting duplicate confirmed candle at {stamp}")
        found[stamp] = item

    expected = list(range(start_ms, end_ms, BAR_MS))
    if len(expected) != 240:
        raise AssertionError("UTC 12H / 3m source grid must contain 240 rows")
    missing = [stamp for stamp in expected if stamp not in found]
    if missing:
        raise ValueError(f"incomplete confirmed 3m grid: {len(missing)} missing; first={missing[0]}")
    return [(stamp, found[stamp][3], found[stamp][4]) for stamp in expected]


def _bucket_72m(rows: list[tuple[int, float, float]], start_ms: int) -> list[tuple[float, float]]:
    """Return (last close, summed contract volume) for the ten AUTO buckets."""
    if len(rows) != BUCKETS * ROWS_PER_BUCKET:
        raise ValueError("72m aggregation requires exactly 240 confirmed 3m bars")
    buckets: list[tuple[float, float]] = []
    for bucket_index in range(BUCKETS):
        chunk = rows[bucket_index * ROWS_PER_BUCKET:(bucket_index + 1) * ROWS_PER_BUCKET]
        bucket_start = start_ms + bucket_index * BUCKET_MS
        expected_times = list(range(bucket_start, bucket_start + BUCKET_MS, BAR_MS))
        if [row[0] for row in chunk] != expected_times:
            raise ValueError(f"72m bucket {bucket_index} has an incomplete or misaligned source grid")
        buckets.append((chunk[-1][1], math.fsum(row[2] for row in chunk)))
    if len(buckets) != BUCKETS:
        raise AssertionError("AUTO 12H profile must contain ten 72m buckets")
    return buckets


def _poc_from_buckets(buckets: Iterable[tuple[float, float]], lo: float, hi: float) -> float | None:
    """Match Pine bins: full volume to every close-neighbor bin, first max wins."""
    width = (hi - lo) / BINS
    if not math.isfinite(width) or width <= 0:
        raise ValueError("FVG bin width must be finite and positive")
    profile = [0.0] * BINS
    values = list(buckets)
    if len(values) != BUCKETS:
        raise ValueError("ChartPrime AUTO profile requires ten 72m buckets")
    for close, volume in values:
        if not (math.isfinite(close) and math.isfinite(volume)) or volume < 0:
            raise ValueError("invalid 72m close/volume")
        for index in range(BINS):
            midpoint = lo + width * index + width / 2
            if abs(close - midpoint) <= width:
                profile[index] += volume
    maximum = max(profile)
    if maximum <= 0:
        return None
    first_maximum = profile.index(maximum)
    return lo + width * first_maximum + width / 2


def _cache_path(target_dir: str | os.PathLike, inst_id: str, middle_open_ms: int) -> Path:
    safe_id = inst_id.replace("-", "_")
    return Path(target_dir) / ".cloud_fvg_poc_cache" / f"{safe_id}_{middle_open_ms}_3m.json"


def _read_cache(path: Path, inst_id: str, start_ms: int) -> list[tuple[int, float, float]] | None:
    try:
        document = json.loads(path.read_text())
        if (document.get("inst_id") != inst_id or document.get("bar") != BAR
                or document.get("middle_open_ms") != start_ms
                or document.get("end_exclusive_ms") != start_ms + MIDDLE_MS):
            return None
        return _parse_confirmed_rows(document["rows"], start_ms, start_ms + MIDDLE_MS)
    except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError):
        return None


def _write_cache(path: Path, inst_id: str, start_ms: int,
                 raw_rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "inst_id": inst_id,
        "bar": BAR,
        "middle_open_ms": start_ms,
        "end_exclusive_ms": start_ms + MIDDLE_MS,
        "source": "OKX public history-candles; confirm=1; native 3m; volume=contracts",
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": raw_rows,
    }
    temp = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    temp.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n")
    temp.replace(path)


def _fetch_public(inst_id: str, start_ms: int, target_dir: str | os.PathLike) -> list[tuple[int, float, float]]:
    cache = _cache_path(target_dir, inst_id, start_ms)
    with _key_lock(str(cache.resolve())):
        cached = _read_cache(cache, inst_id, start_ms)
        if cached is not None:
            return cached

        end_ms = start_ms + MIDDLE_MS
        cursor = end_ms
        pages = 0
        raw_rows: list[list] = []
        while cursor > start_ms and pages < MAX_PAGES:
            payload = okx_request({
                "instId": inst_id,
                "bar": BAR,
                "after": str(cursor),
                "limit": str(PAGE_LIMIT),
            })
            if not isinstance(payload, dict) or str(payload.get("code")) != "0":
                raise RuntimeError(f"OKX history-candles rejected {inst_id} {BAR}: {payload}")
            page = payload.get("data")
            if not isinstance(page, list):
                raise RuntimeError("OKX history-candles response has no data array")
            pages += 1
            if not page:
                break
            page_stamps = []
            for row in page:
                if not isinstance(row, (list, tuple)) or len(row) < 9:
                    raise ValueError("OKX history-candles returned a malformed row")
                try:
                    stamp = int(row[0])
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError("OKX history-candles returned a bad timestamp") from exc
                page_stamps.append(stamp)
                if start_ms <= stamp < end_ms:
                    raw_rows.append(row)
            oldest = min(page_stamps)
            if oldest >= cursor:
                raise ValueError(f"OKX history-candles pagination did not move: {cursor} -> {oldest}")
            cursor = oldest
            if oldest <= start_ms:
                break
        parsed = _parse_confirmed_rows(raw_rows, start_ms, end_ms)
        _write_cache(cache, inst_id, start_ms, raw_rows)
        return parsed


def profile(symbol: str, middle_open_ms: int, lo: float, hi: float, target_dir) -> dict:
    """Return the verified ChartPrime AUTO 72m POC for one 12H FVG.

    ``middle_open_ms`` must be the UTC 12H middle candle's open. Public API
    failures, unconfirmed bars, page caps, and gaps return ``poc=None`` with
    ``profile_status='unavailable'``. They do not trigger an alternate-timeframe
    estimate.
    """
    middle, lower, upper = _validate_bounds(middle_open_ms, lo, hi)
    if target_dir is None:
        raise ValueError("target_dir is required for verified-window caching")
    if middle + MIDDLE_MS > time.time() * 1000:
        return {"poc": None, "profile_source": "none", "profile_status": "unavailable"}
    inst_id = _inst_id(symbol)
    try:
        rows = _fetch_public(inst_id, middle, target_dir)
        buckets = _bucket_72m(rows, middle)
        poc = _poc_from_buckets(buckets, lower, upper)
        if poc is None:
            return {"poc": None, "profile_source": "okx_public_history_3m_contracts", "profile_status": "no_positive_bins"}
        return {"poc": float(poc), "profile_source": "okx_public_history_3m_contracts", "profile_status": "auto_72m"}
    except (requests.RequestException, OSError, ValueError, RuntimeError, KeyError, TypeError, json.JSONDecodeError):
        return {"poc": None, "profile_source": "none", "profile_status": "unavailable"}
