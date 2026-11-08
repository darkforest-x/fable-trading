"""Public Binance USD-M source for the forward-only breadth-ema20 paper account.

This module owns market-data ingestion and causal feature assembly only. It
never places orders or mutates the account ledger. Historical first touches are
seeded from the frozen breadth source through 2026-10-27, then extended from
the live hourly cache and exact native 5m first-touch hours.

The strategy's Monday range is the complete New York Monday (00:00 through
Tuesday 00:00 local): lower = low - 0.618 * range, upper = high + 0.618 *
range. A touch is the native 5m low <= lower or high >= upper, matching the
source study. Cache timestamps are UTC candle-open milliseconds.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import gzip
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd
from yoyo.monitor.breadth_ema20_funding import funding_at
from yoyo.monitor.breadth_ema20_cache import ensure_closed_window


ROOT = Path(__file__).resolve().parents[2]
CACHE_ROOT = ROOT / "data/research/breadth_ema20_binance_usdm"
HOUR_CACHE = CACHE_ROOT / "1h"
FIVE_CACHE = CACHE_ROOT / "5m"
FIRST_HOURS_CACHE = CACHE_ROOT / "first-hours"
FUNDING_CACHE = CACHE_ROOT / "funding"

REST_ROOT = "https://fapi.binance.com"
EXCHANGE_INFO_URL = REST_ROOT + "/fapi/v1/exchangeInfo"
KLINES_URL = REST_ROOT + "/fapi/v1/klines"
FUNDING_RATE_URL = REST_ROOT + "/fapi/v1/fundingRate"
PREMIUM_INDEX_URL = REST_ROOT + "/fapi/v1/premiumIndex"

BAR_MS = 5 * 60_000
HOUR_MS = 60 * 60_000
FOUR_HOURS_MS = 4 * HOUR_MS
TWELVE_HOURS_MS = 12 * HOUR_MS
DAY_MS = 24 * HOUR_MS
LEVEL_EXTENSION = 0.618
HISTORY_DAYS = 28
ANCHOR_LOOKBACK_DAYS = 35
FIVE_MINUTE_CACHE_HOURS = 36
REQUEST_TIMEOUT_S = 15
MAX_RETRIES = 3
MAX_WORKERS = 48
MAX_KLINE_LIMIT = 1000
ALT_EXCLUDED = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "USTCUSDT"}
NY = "America/New_York"
OHLC = ("open", "high", "low", "close")
OCTOBER_TOUCHES = ROOT / "experiments/active/exp-monday-limit-live-october-20261101-v1/events.csv.gz"
FUNDING_TIME_TOLERANCE_MS = 60_000
_HTTP_SEMAPHORE = threading.BoundedSemaphore(MAX_WORKERS)


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _utc_ms(value: Any) -> int:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return int(stamp.value // 1_000_000)


def _get_json(url: str, *, timeout: float = REQUEST_TIMEOUT_S) -> Any:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "fable-trading-breadth-ema20/1.0", "Accept": "application/json"},
    )
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            with _HTTP_SEMAPHORE:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:300]
            last_error = RuntimeError(f"Binance HTTP {exc.code}: {body or exc.reason}")
            if exc.code not in (418, 429, 500, 502, 503, 504) or attempt == MAX_RETRIES - 1:
                break
            time.sleep(0.4 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == MAX_RETRIES - 1:
                break
            time.sleep(0.25 * (attempt + 1))
    raise RuntimeError(f"Binance public request failed: {last_error}") from last_error


def _query(url: str, params: Mapping[str, Any]) -> str:
    return url + "?" + urllib.parse.urlencode(params)


def _read_ohlc_cache(path: Path, *, interval_ms: int | None = None) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame(columns=["open_ms", *OHLC])
    try:
        frame = pd.read_csv(path, compression="infer", usecols=["ts", *OHLC])
    except (OSError, ValueError, pd.errors.EmptyDataError):
        return pd.DataFrame(columns=["open_ms", *OHLC])
    frame = frame.rename(columns={"ts": "open_ms"})
    frame["open_ms"] = pd.to_numeric(frame.open_ms, errors="coerce")
    for name in OHLC:
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame = frame.dropna(subset=["open_ms", *OHLC]).copy()
    frame["open_ms"] = frame.open_ms.astype("int64")
    frame = frame.drop_duplicates("open_ms", keep="last").sort_values("open_ms")
    values = frame.loc[:, list(OHLC)].to_numpy(dtype=float)
    valid = np.isfinite(values).all(axis=1) & (values > 0).all(axis=1)
    valid &= values[:, 1] >= np.maximum(values[:, 0], values[:, 3])
    valid &= values[:, 2] <= np.minimum(values[:, 0], values[:, 3])
    if interval_ms is not None:
        valid &= frame.open_ms.mod(interval_ms).eq(0).to_numpy()
    return frame.loc[valid, ["open_ms", *OHLC]].reset_index(drop=True)


def _atomic_write_ohlc(path: Path, rows: pd.DataFrame) -> None:
    if rows.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_ohlc_cache(path)
    merged = pd.concat([existing, rows.loc[:, ["open_ms", *OHLC]]], ignore_index=True)
    merged = merged.drop_duplicates("open_ms", keep="last").sort_values("open_ms")
    fd, raw_name = tempfile.mkstemp(prefix=".bars-", suffix=".csv.gz", dir=path.parent)
    os.close(fd)
    temp = Path(raw_name)
    try:
        merged.rename(columns={"open_ms": "ts"}).to_csv(temp, index=False, compression="gzip")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _parse_kline_rows(payload: Any, interval_ms: int) -> pd.DataFrame:
    if not isinstance(payload, list):
        raise ValueError(f"Unexpected Binance kline response: {str(payload)[:200]}")
    rows: list[tuple[int, float, float, float, float]] = []
    for item in payload:
        try:
            stamp = int(item[0])
            vals = tuple(float(item[i]) for i in (1, 2, 3, 4))
        except (IndexError, TypeError, ValueError):
            continue
        op, hi, lo, close = vals
        if stamp % interval_ms or not np.isfinite(vals).all() or min(vals) <= 0:
            continue
        if hi < max(op, close) or lo > min(op, close) or lo > hi:
            continue
        rows.append((stamp, op, hi, lo, close))
    out = pd.DataFrame(rows, columns=["open_ms", *OHLC])
    if out.empty:
        return out
    return out.drop_duplicates("open_ms", keep="last").sort_values("open_ms").reset_index(drop=True)


def fetch_klines(
    symbol: str,
    interval: str,
    *,
    start_ms: int,
    end_ms: int,
    limit: int = 1000,
    rest_get: Callable[[str], Any] | None = None,
) -> pd.DataFrame:
    """Fetch public native candles, inclusive of the requested open times.

    The kline API identifies candles by open time. Rows are always returned as
    UTC open milliseconds; no incomplete current candle is filtered here.
    """
    interval_ms = {"5m": BAR_MS, "1h": HOUR_MS}.get(interval)
    if interval_ms is None:
        raise ValueError(f"Unsupported feed interval: {interval}")
    if not 1 <= int(limit) <= MAX_KLINE_LIMIT:
        raise ValueError("limit must be between 1 and 1000")
    if end_ms < start_ms:
        raise ValueError("end_ms must be >= start_ms")
    payload = (rest_get or _get_json)(_query(KLINES_URL, {
        "symbol": str(symbol).upper(), "interval": interval,
        "startTime": int(start_ms), "endTime": int(end_ms), "limit": int(limit),
    }))
    frame = _parse_kline_rows(payload, interval_ms)
    return frame.loc[frame.open_ms.between(start_ms, end_ms)].reset_index(drop=True)


def week_window_utc(week_monday: date | str) -> tuple[int, int, int]:
    """Return NY Monday open, Tuesday open, and next Monday open as UTC ms.

    Constructing local midnights before conversion preserves New York DST
    boundaries; a New York calendar day is not assumed to be a fixed UTC date.
    """
    day = pd.Timestamp(week_monday).date()
    if day.weekday() != 0:
        raise ValueError("week_monday must be a Monday date")
    monday = pd.Timestamp(datetime.combine(day, datetime.min.time())).tz_localize(NY)
    tuesday = monday + pd.DateOffset(days=1)
    next_monday = monday + pd.DateOffset(days=7)
    return tuple(_utc_ms(stamp) for stamp in (monday, tuesday, next_monday))  # type: ignore[return-value]


def weekly_levels_from_hourly(symbol: str, hourly: pd.DataFrame) -> list[dict[str, Any]]:
    """Build auditable NY-Monday levels from complete 24-candle 1h anchors."""
    if hourly.empty:
        return []
    data = hourly.copy()
    if "open_ms" not in data:
        if isinstance(data.index, pd.DatetimeIndex):
            data = data.reset_index().rename(columns={data.index.name or "index": "open_ms"})
        else:
            raise ValueError("hourly bars require open_ms")
    data["open_ms"] = pd.to_numeric(data.open_ms, errors="coerce")
    data = data.dropna(subset=["open_ms", *OHLC]).copy()
    data["open_ms"] = data.open_ms.astype("int64")
    data = data.drop_duplicates("open_ms", keep="last").sort_values("open_ms")
    data = data.loc[data.open_ms.mod(HOUR_MS).eq(0)].copy()
    local = pd.to_datetime(data.open_ms, unit="ms", utc=True).dt.tz_convert(NY)
    data["local_date"] = local.dt.date
    data["local_weekday"] = local.dt.dayofweek
    monday_dates = sorted(set(data.loc[data.local_weekday.eq(0), "local_date"]))
    result: list[dict[str, Any]] = []
    for monday in monday_dates:
        start_ms, tuesday_ms, next_monday_ms = week_window_utc(monday)
        expected = np.arange(start_ms, tuesday_ms, HOUR_MS, dtype=np.int64)
        part = data.loc[data.open_ms.between(start_ms, tuesday_ms - HOUR_MS)]
        observed = part.open_ms.to_numpy(dtype=np.int64)
        complete = len(observed) == 24 and np.array_equal(observed, expected)
        high = float(part.high.max()) if complete else math.nan
        low = float(part.low.min()) if complete else math.nan
        width = high - low
        valid = bool(complete and np.isfinite(width) and width > 0 and low > 0)
        result.append({
            "symbol": str(symbol).upper(), "week": monday.isoformat(),
            "monday_start_ms": start_ms, "tuesday_start_ms": tuesday_ms,
            "next_monday_ms": next_monday_ms, "monday_high": high if valid else None,
            "monday_low": low if valid else None,
            "lower": low - LEVEL_EXTENSION * width if valid else None,
            "upper": high + LEVEL_EXTENSION * width if valid else None,
            "complete": valid, "anchor_hours": int(len(observed)),
        })
    return result


def first_touch_from_hour(symbol: str, hour_open_ms: int, level: float, side: int,
                          bars: pd.DataFrame) -> dict[str, Any] | None:
    """Find the first native 5m touch within a verified 1h candidate window."""
    if side not in (-1, 1):
        raise ValueError("side must be +1 for lower or -1 for upper")
    hour_open_ms = int(hour_open_ms)
    if hour_open_ms % HOUR_MS:
        raise ValueError("candidate hour must be UTC-hour aligned")
    frame = bars.copy()
    if "open_ms" not in frame:
        raise ValueError("5m bars require open_ms")
    frame["open_ms"] = pd.to_numeric(frame.open_ms, errors="coerce")
    frame = frame.dropna(subset=["open_ms", *OHLC]).copy()
    frame["open_ms"] = frame.open_ms.astype("int64")
    frame = frame.loc[frame.open_ms.between(hour_open_ms, hour_open_ms + HOUR_MS - BAR_MS)]
    frame = frame.drop_duplicates("open_ms", keep="last").sort_values("open_ms")
    expected = np.arange(hour_open_ms, hour_open_ms + HOUR_MS, BAR_MS, dtype=np.int64)
    if not np.array_equal(frame.open_ms.to_numpy(dtype=np.int64), expected):
        return None
    hit = frame.low.le(float(level)) if side == 1 else frame.high.ge(float(level))
    found = frame.loc[hit]
    if found.empty:
        return None
    row = found.iloc[0]
    return {
        "symbol": str(symbol).upper(), "touch_ms": int(row.open_ms),
        "side": int(side), "week": None, "level": float(level),
        "bar_open_ms": int(row.open_ms), "open": float(row.open),
        "high": float(row.high), "low": float(row.low), "close": float(row.close),
        "source_hour_ms": hour_open_ms,
    }


def four_hour_ema20(hourly: pd.DataFrame) -> pd.DataFrame:
    """Build causal 4h closes/EMA20 from complete consecutive UTC 1h candles.

    Inputs: hourly UTC-open OHLC; each 4h output aggregates exactly four 1h
    candles. EMA20 sees only completed 4h closes, requires 20 consecutive
    complete outputs, and restarts after a missing/incomplete 4h group.
    """
    if hourly.empty:
        return pd.DataFrame(columns=["bar_close_ms", "close", "ema20", "complete"])
    data = hourly.copy()
    if "open_ms" not in data:
        raise ValueError("hourly bars require open_ms")
    data["open_ms"] = pd.to_numeric(data.open_ms, errors="coerce")
    data = data.dropna(subset=["open_ms", *OHLC]).copy()
    data["open_ms"] = data.open_ms.astype("int64")
    data = data.drop_duplicates("open_ms", keep="last").set_index("open_ms").sort_index()
    grid = np.arange(int(data.index.min()), int(data.index.max()) + HOUR_MS, HOUR_MS, dtype=np.int64)
    dense = data.reindex(grid)
    dense["group_close_ms"] = (dense.index // FOUR_HOURS_MS + 1) * FOUR_HOURS_MS
    rows: list[dict[str, Any]] = []
    for close_ms, group in dense.groupby("group_close_ms", sort=True):
        indices = group.index.to_numpy(dtype=np.int64)
        expected = np.arange(int(close_ms) - FOUR_HOURS_MS, int(close_ms), HOUR_MS, dtype=np.int64)
        complete = bool(np.array_equal(indices, expected) and group.loc[:, list(OHLC)].notna().all().all())
        rows.append({
            "bar_close_ms": int(close_ms),
            "open": float(group.open.iloc[0]) if complete else math.nan,
            "high": float(group.high.max()) if complete else math.nan,
            "low": float(group.low.min()) if complete else math.nan,
            "close": float(group.close.iloc[-1]) if complete else math.nan,
            "complete": complete,
        })
    out = pd.DataFrame(rows).sort_values("bar_close_ms").reset_index(drop=True)
    out["segment"] = (~out.complete).cumsum()
    out["ema20"] = out.close.where(out.complete).groupby(out.segment).transform(
        lambda values: values.ewm(span=20, adjust=False, min_periods=20).mean()
    )
    out.loc[~out.complete, "ema20"] = np.nan
    return out.drop(columns="segment")


def asof_4h_ema20(frame: pd.DataFrame, asof_ms: int) -> dict[str, Any]:
    """Select the last complete 4h close whose close time is <= asof_ms."""
    if frame.empty:
        return {"bar_close_ms": None, "asof_ms": int(asof_ms), "close": None,
                "ema20": None, "complete": False, "valid": False}
    available = frame.loc[frame.bar_close_ms.le(int(asof_ms))]
    if available.empty:
        return {"bar_close_ms": None, "asof_ms": int(asof_ms), "close": None,
                "ema20": None, "complete": False, "valid": False}
    row = available.iloc[-1]
    valid = bool(row.complete and np.isfinite(row.ema20) and np.isfinite(row.close))
    return {
        "bar_close_ms": int(row.bar_close_ms), "asof_ms": int(asof_ms),
        "close": float(row.close) if row.complete else None,
        "ema20": float(row.ema20) if valid else None,
        "complete": bool(row.complete), "valid": valid,
    }


def trailing28_unique_count(touches: Iterable[Mapping[str, Any]], known_ms: int) -> int:
    """Count unique symbols with either-side first touches known in prior 28d.

    A touch becomes observable only when its native five-minute bar closes.
    The interval is [known-28d, known] on available touch-bar closes.
    """
    left = int(known_ms) - 28 * DAY_MS
    symbols: set[str] = set()
    for row in touches:
        touch_ms = int(row.get("touch_ms", row.get("touch", -1)))
        if left <= touch_ms + BAR_MS <= int(known_ms):
            symbols.add(str(row["symbol"]).upper())
    return len(symbols)


@dataclass
class _UniverseSymbol:
    symbol: str
    onboard_ms: int
    status: str


class BinanceBreadthEma20Feed:
    """Public Binance data source consumed by ``breadth_ema20_runner``.

    ``warmup`` loads causal hourly anchors, frozen first-touch history and exact
    native 5m first-touch hours. ``iter_closed_events`` yields one event per
    requested UTC 5m close; the runner persists the exclusive close cursor and
    applies its own 90-second entry-freshness gate.
    """

    def __init__(self, *, cache_root: Path | str = CACHE_ROOT,
                 rest_get: Callable[[str], Any] | None = None,
                 max_workers: int = MAX_WORKERS):
        self.cache_root = Path(cache_root)
        self.hour_cache = self.cache_root / "1h"
        self.five_cache = self.cache_root / "5m"
        self.first_hours_cache = self.cache_root / "first-hours"
        self.funding_cache = self.cache_root / "funding"
        self._rest_get = rest_get or _get_json
        self._max_workers = max(1, int(max_workers))
        self._symbols: dict[str, _UniverseSymbol] = {}
        self._hourly: dict[str, pd.DataFrame] = {}
        self._levels: dict[tuple[str, str], dict[str, Any]] = {}
        self._touches: dict[tuple[str, str, int], dict[str, Any]] = {}
        self._hourly_ema20: pd.DataFrame = pd.DataFrame()
        self._five_rows: dict[str, dict[int, dict[str, float]]] = {}
        self._last_universe_refresh = 0
        self._warmed = False
        self._warmup_status: dict[str, Any] = {"complete": False, "total": 0, "covered": 0}
        self._frozen_ledger_loaded = False
        self._source_failures: dict[str, str] = {}
        self._touch_start_ms = 0
        self._history_start_ms = 0
        self._market_seeded_through_ms = 0
        self._funding_schedule: dict[str, dict[str, int]] = {}

    def seed_saved_touches(self, records: Iterable[Mapping[str, Any]]) -> int:
        """Restore the runner's durable first-touch ledger before warmup.

        Idempotency identity is the original (symbol, NY-week, side); conflicting
        timestamps are rejected instead of silently changing the frozen event.
        """
        seeded = 0
        for raw in records:
            try:
                symbol = str(raw["symbol"]).upper()
                week = str(raw["week"])
                side = int(raw["side"])
                touch_ms = int(raw.get("touch_ms", raw.get("t_ms")))
                level = float(raw["level"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid saved first-touch row: {raw!r}") from exc
            if not symbol.endswith("USDT") or side not in (-1, 1) or touch_ms < 0 or not math.isfinite(level) or level <= 0:
                raise ValueError(f"Invalid saved first-touch row: {raw!r}")
            identity = (symbol, week, side)
            existing = self._touches.get(identity)
            if existing is not None:
                if int(existing["touch_ms"]) != touch_ms:
                    raise ValueError(f"Conflicting saved first touch for {identity}")
                continue
            record = {"symbol": symbol, "week": week, "side": side,
                      "touch_ms": touch_ms, "level": level,
                      "source": str(raw.get("source") or "runner_saved_touch"),
                      "verified_5m": bool(raw.get("verified_5m", True))}
            self._touches[identity] = record
            seeded += 1
        return seeded

    def recent_touches(self, *, as_of_ms: int | None = None, days: int = 35) -> list[dict[str, Any]]:
        """Return bounded touch-ledger rows for the runner's durable cache."""
        now = int(as_of_ms if as_of_ms is not None else
                  (self._market_seeded_through_ms or (_now_ms() // BAR_MS * BAR_MS)))
        start = max(0, now - int(days) * DAY_MS - BAR_MS)
        return sorted(
            (dict(row) for row in self._touches.values()
             if int(row["touch_ms"]) + BAR_MS >= start),
            key=lambda row: (int(row["touch_ms"]), str(row["symbol"]), int(row["side"])),
        )

    def _universe(self, *, force: bool = False) -> dict[str, _UniverseSymbol]:
        if self._symbols and not force and _now_ms() - self._last_universe_refresh < 6 * HOUR_MS:
            return self._symbols
        payload = self._rest_get(EXCHANGE_INFO_URL)
        if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
            raise RuntimeError("Binance exchangeInfo response has no symbols array")
        symbols: dict[str, _UniverseSymbol] = {}
        for item in payload["symbols"]:
            if (item.get("quoteAsset") != "USDT" or item.get("contractType") != "PERPETUAL"
                    or item.get("status") != "TRADING"):
                continue
            name = str(item.get("symbol", "")).upper()
            if not name.endswith("USDT") or not name[:-4].isalnum():
                continue
            try:
                onboard_ms = int(item.get("onboardDate", 0))
            except (TypeError, ValueError):
                onboard_ms = 0
            symbols[name] = _UniverseSymbol(name, onboard_ms, "TRADING")
        if not symbols:
            raise RuntimeError("Binance exchangeInfo returned no active USDT perpetuals")
        previous = set(self._symbols)
        self._symbols = symbols
        self._last_universe_refresh = _now_ms()
        additions = sorted(set(symbols) - previous)
        if previous and additions and self._warmed:
            self._prime_new_symbols(additions)
        return symbols

    def _prime_new_symbols(self, symbols: Sequence[str]) -> None:
        """Load only newly listed contracts when the six-hour universe refresh runs."""
        hour_end = _now_ms() // HOUR_MS * HOUR_MS
        five_end = _now_ms() // BAR_MS * BAR_MS

        def repair(symbol: str):
            item = self._symbols[symbol]
            hour_start = max(self._history_start_ms,
                             ((item.onboard_ms + HOUR_MS - 1) // HOUR_MS) * HOUR_MS)
            five_start = max(five_end - FIVE_MINUTE_CACHE_HOURS * HOUR_MS,
                             ((item.onboard_ms + BAR_MS - 1) // BAR_MS) * BAR_MS)
            path_hour = self.hour_cache / f"{symbol}.csv.gz"
            path_five = self.five_cache / f"{symbol}.csv.gz"

            def fetch(name: str, interval: str, *, start_ms: int, end_ms: int, limit: int):
                return fetch_klines(name, interval, start_ms=start_ms, end_ms=end_ms,
                                    limit=limit, rest_get=self._rest_get)

            hourly = ensure_closed_window(path_hour, symbol, "1h", hour_start, hour_end,
                                          fetch=fetch, read=_read_ohlc_cache, write=_atomic_write_ohlc)
            five = ensure_closed_window(path_five, symbol, "5m", five_start, five_end,
                                        fetch=fetch, read=_read_ohlc_cache, write=_atomic_write_ohlc)
            return symbol, hourly, five

        errors = []
        with ThreadPoolExecutor(max_workers=min(self._max_workers, len(symbols))) as pool:
            futures = [pool.submit(repair, symbol) for symbol in symbols]
            for future in as_completed(futures):
                try:
                    symbol, hourly, five = future.result()
                    self._hourly[symbol] = hourly
                    self._five_rows[symbol] = {
                        int(row.open_ms): {name: float(getattr(row, name)) for name in OHLC}
                        for row in five.itertuples(index=False)
                    }
                except Exception as exc:
                    errors.append(f"{type(exc).__name__}: {exc}")
        if errors:
            self._source_failures["new_symbol_cache"] = "; ".join(errors)
            self._warmed = False
            return
        self._seed_hourly_touch_ledger(self._touch_start_ms, None, symbols=symbols)
        self._seed_recent_five_touch_ledger(hour_end, five_end)
        self._market_seeded_through_ms = max(self._market_seeded_through_ms, five_end)

    def _read_hourly(self, symbol: str) -> pd.DataFrame:
        frame = _read_ohlc_cache(self.hour_cache / f"{symbol}.csv.gz", interval_ms=HOUR_MS)
        self._hourly[symbol] = frame
        return frame

    def _read_five(self, symbol: str) -> dict[int, dict[str, float]]:
        path = self.five_cache / f"{symbol}.csv.gz"
        frame = _read_ohlc_cache(path, interval_ms=BAR_MS)
        out = {
            int(row.open_ms): {name: float(getattr(row, name)) for name in OHLC}
            for row in frame.itertuples(index=False)
        }
        self._five_rows[symbol] = out
        return out

    def _read_five_frame(self, symbol: str) -> pd.DataFrame:
        return _read_ohlc_cache(self.five_cache / f"{symbol}.csv.gz", interval_ms=BAR_MS)

    def _first_hour(self, symbol: str, hour_ms: int) -> pd.DataFrame:
        path = self.first_hours_cache / symbol / f"{int(hour_ms)}.csv.gz"
        frame = _read_ohlc_cache(path, interval_ms=BAR_MS)
        if len(frame) == 12 and np.array_equal(
            frame.open_ms.to_numpy(dtype=np.int64),
            np.arange(hour_ms, hour_ms + HOUR_MS, BAR_MS, dtype=np.int64),
        ):
            return frame
        # The prefetch cache is preferred. A missing hour is repaired from the
        # public USD-M kline API and atomically stored under the same lineage.
        bars = fetch_klines(symbol, "5m", start_ms=hour_ms,
                            end_ms=hour_ms + HOUR_MS - BAR_MS, limit=12,
                            rest_get=self._rest_get)
        if len(bars) == 12:
            _atomic_write_ohlc(path, bars)
        return bars

    def _seed_frozen_ledger(self, start_ms: int) -> None:
        if self._frozen_ledger_loaded:
            return
        try:
            if not OCTOBER_TOUCHES.is_file():
                raise FileNotFoundError(OCTOBER_TOUCHES)
            ledger = pd.read_csv(OCTOBER_TOUCHES, usecols=["symbol", "week", "side", "level", "t"])
            ledger["t"] = pd.to_datetime(ledger.t, utc=True, errors="coerce")
            ledger["side"] = pd.to_numeric(ledger.side, errors="coerce")
            ledger["level"] = pd.to_numeric(ledger.level, errors="coerce")
            ledger = ledger.dropna(subset=["t", "side", "level"])
        except Exception as exc:
            self._source_failures["october_touch_ledger"] = f"{type(exc).__name__}: {exc}"
            return
        ledger = ledger.loc[(ledger.t.astype("int64") // 1_000_000 + BAR_MS) >= start_ms]
        for row in ledger.itertuples(index=False):
            record = {
                "symbol": str(row.symbol).upper(),
                "touch_ms": _utc_ms(row.t),
                "side": int(row.side),
                "week": str(row.week),
                "level": float(row.level),
                "source": "october_touch_extension",
                "verified_5m": True,
            }
            identity = (record["symbol"], record["week"], record["side"])
            existing = self._touches.get(identity)
            if existing is not None and int(existing["touch_ms"]) != int(record["touch_ms"]):
                self._source_failures[f"frozen_touch_conflict:{identity}"] = "runner_saved_vs_october_touch_mismatch"
                continue
            self._touches[identity] = record
        self._frozen_ledger_loaded = True

    def _seed_hourly_touch_ledger(self, start_ms: int, progress: Callable[[dict[str, Any]], None] | None,
                                  *, symbols: Iterable[str] | None = None) -> tuple[int, int]:
        expected_windows = 0
        verified_windows = 0
        names = sorted(self._symbols if symbols is None else {str(value).upper() for value in symbols})
        total = len(names)
        for done, symbol in enumerate(names, 1):
            hourly = self._hourly.get(symbol)
            if hourly is None:
                hourly = self._read_hourly(symbol)
            if hourly.empty:
                if progress:
                    progress({"done": done, "total": total, "symbol": symbol, "status": "missing_hourly"})
                continue
            for level_row in weekly_levels_from_hourly(symbol, hourly):
                key = (symbol, level_row["week"])
                self._levels[key] = level_row
                if not level_row["complete"]:
                    continue
                # Find only the first possible hour per side. The exact native
                # 5m first-touch time is resolved from first-hours/{symbol}.
                # Resolve the actual first touched hour across the whole
                # Tue→Mon observation window first. Filtering the source hours
                # at the rolling cutoff before selecting the first hit would
                # incorrectly turn a later second touch into a new first touch.
                active = hourly.loc[
                    hourly.open_ms.ge(level_row["tuesday_start_ms"])
                    & hourly.open_ms.lt(level_row["next_monday_ms"])
                ]
                for side, field, compare in ((1, "low", "lower"), (-1, "high", "upper")):
                    level = float(level_row[compare])
                    hit_mask = active[field].le(level) if side == 1 else active[field].ge(level)
                    hits = active.loc[hit_mask]
                    if hits.empty:
                        continue
                    hour_ms = int(hits.open_ms.iloc[0])
                    # If even the earliest possible 5m touch in this 1h
                    # candidate is older than the 28d cutoff, it cannot affect
                    # today's rolling universe and needs no exact 5m repair.
                    if hour_ms + HOUR_MS <= self._touch_start_ms:
                        continue
                    expected_windows += 1
                    touch_key = (symbol, level_row["week"], side)
                    frozen = self._touches.get(touch_key)
                    # Frozen source is authoritative where it has an event;
                    # still read matching current windows to verify parity.
                    window = self._first_hour(symbol, hour_ms)
                    exact = first_touch_from_hour(symbol, hour_ms, level, side, window)
                    if exact is None:
                        self._source_failures[f"first_hour:{symbol}:{hour_ms}:{side}"] = "incomplete_or_no_exact_touch"
                        continue
                    verified_windows += 1
                    exact["week"] = level_row["week"]
                    exact["level"] = level
                    exact["source"] = "binance_native_5m_first_hour"
                    exact["verified_5m"] = True
                    if frozen is None:
                        self._touches[touch_key] = exact
                    else:
                        if int(frozen["touch_ms"]) != int(exact["touch_ms"]):
                            self._source_failures[f"parity:{symbol}:{level_row['week']}:{side}"] = "frozen_vs_native_first_touch_mismatch"
            if progress:
                progress({"done": done, "total": total, "symbol": symbol,
                          "status": "covered" if len(hourly) else "missing_hourly",
                          "rows": int(len(hourly))})
        return expected_windows, verified_windows

    def warmup(self, *, progress: Callable[[dict[str, Any]], None] | None = None,
               lookback_days: int = HISTORY_DAYS, as_of_ms: int | None = None) -> dict[str, Any]:
        """Repair recent public caches, then seed levels and first-touch ledger.

        The rolling breadth window is ``lookback_days``; hourly anchors begin a
        full local week earlier so a first touch before the left edge cannot be
        mistaken for a later touch. The exact native 5m hours are fetched only
        for the true first hourly touch whose 5m bar close can enter the window.
        """
        now = int(as_of_ms if as_of_ms is not None else _now_ms())
        hour_end_exclusive = now // HOUR_MS * HOUR_MS
        five_end_exclusive = now // BAR_MS * BAR_MS
        recent_start = five_end_exclusive - FIVE_MINUTE_CACHE_HOURS * HOUR_MS
        cutoff_known_ms = five_end_exclusive
        self._touch_start_ms = max(0, cutoff_known_ms - int(lookback_days) * DAY_MS - BAR_MS)
        self._history_start_ms = max(
            0, ((self._touch_start_ms - 7 * DAY_MS) // HOUR_MS) * HOUR_MS
        )
        self._source_failures = {}
        try:
            universe = self._universe(force=True)
        except Exception as exc:
            self._warmup_status = {"complete": False, "total": len(self._symbols), "covered": 0,
                                   "error": f"{type(exc).__name__}: {exc}"}
            return self._warmup_status

        def ensure_symbol(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
            path = (self.hour_cache if interval == "1h" else self.five_cache) / f"{symbol}.csv.gz"

            def fetch(name: str, unit: str, *, start_ms: int, end_ms: int, limit: int):
                return fetch_klines(name, unit, start_ms=start_ms, end_ms=end_ms,
                                    limit=limit, rest_get=self._rest_get)

            return ensure_closed_window(path, symbol, interval, start_ms, end_ms,
                                        fetch=fetch, read=_read_ohlc_cache,
                                        write=_atomic_write_ohlc)

        def repair_symbol(symbol: str) -> tuple[str, pd.DataFrame, pd.DataFrame, str | None]:
            item = universe[symbol]
            first_hour = max(self._history_start_ms,
                             ((item.onboard_ms + HOUR_MS - 1) // HOUR_MS) * HOUR_MS)
            first_five = max(recent_start,
                             ((item.onboard_ms + BAR_MS - 1) // BAR_MS) * BAR_MS)
            try:
                hourly = ensure_symbol(symbol, "1h", first_hour, hour_end_exclusive) if first_hour < hour_end_exclusive else self._read_hourly(symbol)
                five = ensure_symbol(symbol, "5m", first_five, five_end_exclusive) if first_five < five_end_exclusive else self._read_five_frame(symbol)
                return symbol, hourly, five, None
            except Exception as exc:
                return symbol, self._read_hourly(symbol), self._read_five_frame(symbol), f"{type(exc).__name__}: {exc}"

        repaired: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {pool.submit(repair_symbol, symbol): symbol for symbol in sorted(universe)}
            for done, future in enumerate(as_completed(futures), 1):
                symbol, hourly, five, error = future.result()
                repaired[symbol] = (hourly, five)
                if error:
                    self._source_failures[f"cache:{symbol}"] = error
                if progress:
                    progress({"done": done, "total": len(universe), "symbol": symbol,
                              "status": "cache_repaired" if error is None else "cache_error"})

        hourly_covered = 0
        five_covered = 0
        for symbol, item in universe.items():
            hourly, five = repaired.get(symbol, (pd.DataFrame(columns=["open_ms", *OHLC]),
                                                  pd.DataFrame(columns=["open_ms", *OHLC])))
            self._hourly[symbol] = hourly
            five_map = {int(row.open_ms): {name: float(getattr(row, name)) for name in OHLC}
                        for row in five.itertuples(index=False)}
            self._five_rows[symbol] = five_map

            hourly_start = max(self._history_start_ms,
                               ((item.onboard_ms + HOUR_MS - 1) // HOUR_MS) * HOUR_MS)
            expected_hourly = np.arange(hourly_start, hour_end_exclusive, HOUR_MS, dtype=np.int64)
            observed_hourly = hourly.loc[hourly.open_ms.ge(hourly_start)
                                         & hourly.open_ms.lt(hour_end_exclusive), "open_ms"].to_numpy(dtype=np.int64)
            hourly_ok = bool(len(observed_hourly) == len(expected_hourly)
                             and np.array_equal(observed_hourly, expected_hourly))
            hourly_covered += int(hourly_ok)

            five_start = max(recent_start,
                             ((item.onboard_ms + BAR_MS - 1) // BAR_MS) * BAR_MS)
            expected_five = np.arange(five_start, five_end_exclusive, BAR_MS, dtype=np.int64)
            observed_five = five.loc[five.open_ms.ge(five_start)
                                     & five.open_ms.lt(five_end_exclusive), "open_ms"].to_numpy(dtype=np.int64)
            five_ok = bool(len(observed_five) == len(expected_five)
                           and np.array_equal(observed_five, expected_five))
            five_covered += int(five_ok)

        self._seed_frozen_ledger(self._touch_start_ms)
        touch_windows, verified_windows = self._seed_hourly_touch_ledger(self._touch_start_ms, None)
        self._seed_recent_five_touch_ledger(hour_end_exclusive, five_end_exclusive)
        self._build_btc_ema20()
        self._market_seeded_through_ms = five_end_exclusive

        complete = bool(hourly_covered == len(universe) and five_covered == len(universe)
                        and verified_windows == touch_windows and self._frozen_ledger_loaded
                        and not self._source_failures)
        self._warmed = complete
        self._warmup_status = {
            "complete": complete, "total": len(universe), "covered": hourly_covered,
            "active_symbols": len(universe), "hourly_lookback_start_ms": self._history_start_ms,
            "hourly_end_exclusive_ms": hour_end_exclusive,
            "recent_5m_start_ms": recent_start,
            "recent_5m_end_exclusive_ms": five_end_exclusive,
            "recent_5m_symbols": five_covered,
            "historical_first_touch_windows": touch_windows,
            "verified_first_touch_windows": verified_windows,
            "trailing28_any_touch_unique_n": trailing28_unique_count(self._touches.values(), cutoff_known_ms),
            "frozen_touch_rows": sum(1 for row in self._touches.values()
                                      if row.get("source") == "october_touch_extension"),
            "source_failures": dict(self._source_failures),
            "universe_asof_ms": now,
            "universe_status": "active_USDT_perpetual_snapshot_plus_frozen_recent_delisted_touches",
        }
        return self._warmup_status

    def _seed_recent_five_touch_ledger(self, start_ms: int, end_ms: int) -> None:
        """Ingest recent cached 5m touches so restart catch-up cannot miss them."""
        for symbol, rows in self._five_rows.items():
            for bar_open_ms in sorted(ts for ts in rows if start_ms <= ts < end_ms):
                self._record_current_touches({symbol: {
                    "bar_open_ms": bar_open_ms, **rows[bar_open_ms], "bar_available": True,
                }}, bar_open_ms)

    def _build_btc_ema20(self) -> None:
        hourly = self._hourly.get("BTCUSDT")
        if hourly is None:
            hourly = self._read_hourly("BTCUSDT")
        self._hourly_ema20 = four_hour_ema20(hourly)

    def _fetch_live_symbol(self, symbol: str, bar_open_ms: int, next_open_ms: int) -> tuple[str, dict[str, Any]]:
        try:
            payload = self._rest_get(_query(KLINES_URL, {
                "symbol": symbol, "interval": "5m", "startTime": int(bar_open_ms),
                "endTime": int(next_open_ms), "limit": 2,
            }))
            frame = _parse_kline_rows(payload, BAR_MS)
            by_time = {int(row.open_ms): {name: float(getattr(row, name)) for name in OHLC}
                       for row in frame.itertuples(index=False)}
            closed = by_time.get(int(bar_open_ms))
            next_candle = by_time.get(int(next_open_ms))
            result = {"bar_open_ms": int(bar_open_ms), "open": None, "high": None,
                      "low": None, "close": None, "next_open_ms": int(next_open_ms),
                      "next_open": None, "bar_available": closed is not None,
                      "next_open_available": next_candle is not None,
                      "source": "binance_fapi_5m_rest"}
            if closed:
                for key in OHLC:
                    result[key] = closed[key]
                self._five_rows.setdefault(symbol, {})[int(bar_open_ms)] = closed
            if next_candle:
                result["next_open"] = float(next_candle["open"])
                # The next candle has only just opened; its OHLC must not be
                # reused as a complete bar during a later replay.
            return symbol, result
        except Exception as exc:
            return symbol, {"bar_open_ms": int(bar_open_ms), "next_open_ms": int(next_open_ms),
                            "bar_available": False, "next_open_available": False,
                            "source": "unavailable", "error": f"{type(exc).__name__}: {exc}"}

    def _live_bars(self, bar_open_ms: int, next_open_ms: int,
                   extra_symbols: Iterable[str] = (), *, full_universe: bool = True) -> dict[str, dict[str, Any]]:
        names = set(self._symbols) if full_universe else set()
        symbols = sorted(names | {str(value).upper() for value in extra_symbols})
        result: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = [pool.submit(self._fetch_live_symbol, symbol, bar_open_ms, next_open_ms)
                       for symbol in symbols]
            for future in as_completed(futures):
                symbol, row = future.result()
                result[symbol] = row
        return result

    def _rolling_hourly_update(self, close_ms: int) -> None:
        """Persist complete 1h candles composed from the just-closed 5m bars."""
        if int(close_ms) % HOUR_MS:
            return
        hour_open_ms = int(close_ms) - HOUR_MS
        for symbol in sorted(set(self._symbols) | {"BTCUSDT"}):
            rows = self._five_rows.get(symbol)
            if not rows:
                continue
            opens = np.arange(hour_open_ms, int(close_ms), BAR_MS, dtype=np.int64)
            candles = [rows.get(int(stamp)) for stamp in opens]
            if any(row is None for row in candles):
                continue
            data = [row for row in candles if row is not None]
            hourly = pd.DataFrame([{
                "open_ms": hour_open_ms, "open": data[0]["open"],
                "high": max(row["high"] for row in data),
                "low": min(row["low"] for row in data), "close": data[-1]["close"],
            }])
            _atomic_write_ohlc(self.hour_cache / f"{symbol}.csv.gz", hourly)
            prior = self._hourly.get(symbol)
            merged = hourly if prior is None else pd.concat([prior, hourly], ignore_index=True)
            merged = merged.drop_duplicates("open_ms", keep="last").sort_values("open_ms").reset_index(drop=True)
            self._hourly[symbol] = merged
            for level in weekly_levels_from_hourly(symbol, merged):
                self._levels[(symbol, level["week"])] = level
        self._build_btc_ema20()

    def _prune_five_rows(self, close_ms: int) -> None:
        cutoff = int(close_ms) - FIVE_MINUTE_CACHE_HOURS * HOUR_MS
        for rows in self._five_rows.values():
            for stamp in [value for value in rows if value < cutoff]:
                del rows[stamp]

    def _level_for_current_week(self, symbol: str, bar_open_ms: int) -> dict[str, Any] | None:
        stamp = pd.Timestamp(bar_open_ms, unit="ms", tz="UTC").tz_convert(NY)
        monday_date = (stamp.date() - timedelta(days=stamp.weekday()))
        key = (symbol, monday_date.isoformat())
        row = self._levels.get(key)
        if row is not None:
            return row
        hourly = self._hourly.get(symbol)
        if hourly is None:
            hourly = self._read_hourly(symbol)
        for item in weekly_levels_from_hourly(symbol, hourly):
            self._levels[(symbol, item["week"])] = item
        return self._levels.get(key)

    def _record_current_touches(self, bars: Mapping[str, Mapping[str, Any]], bar_open_ms: int) -> list[dict[str, Any]]:
        current: list[dict[str, Any]] = []
        for symbol, bar in bars.items():
            if not bar.get("bar_available"):
                continue
            levels = self._level_for_current_week(symbol, bar_open_ms)
            if not levels or not levels.get("complete") or bar_open_ms < int(levels["tuesday_start_ms"]):
                continue
            if bar_open_ms >= int(levels["next_monday_ms"]):
                continue
            for side, field, key in ((1, "low", "lower"), (-1, "high", "upper")):
                level = float(levels[key])
                hit = float(bar[field]) <= level if side == 1 else float(bar[field]) >= level
                identity = (symbol, str(levels["week"]), side)
                if hit and identity not in self._touches:
                    record = {"symbol": symbol, "touch_ms": int(bar_open_ms), "side": side,
                              "week": str(levels["week"]), "level": level,
                              "source": "binance_live_5m", "verified_5m": True}
                    self._touches[identity] = record
                    current.append(record)
        return current

    def _touches_in(self, start_ms: int, end_ms: int, *, side: int = 1) -> list[dict[str, Any]]:
        rows = [row for row in self._touches.values()
                if int(row["side"]) == side and int(start_ms) <= int(row["touch_ms"]) < int(end_ms)]
        return sorted(rows, key=lambda row: (int(row["touch_ms"]), str(row["symbol"])))

    def _candle(self, symbol: str, open_ms: int) -> dict[str, float] | None:
        rows = self._five_rows.get(symbol)
        if rows is None:
            rows = self._read_five(symbol)
        return rows.get(int(open_ms))

    def _member_rows(self, initial: Sequence[Mapping[str, Any]], known_ms: int,
                     decision_ms: int, bars: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        members: list[dict[str, Any]] = []
        observed_returns: list[float] = []
        reclaim_values: list[bool] = []
        eligible = [touch for touch in initial
                    if str(touch["symbol"]).upper() not in ALT_EXCLUDED]
        for touch in eligible:
            symbol = str(touch["symbol"]).upper()
            level = float(touch["level"])
            before = self._candle(symbol, int(known_ms) - BAR_MS)
            after = self._candle(symbol, int(decision_ms) - BAR_MS)
            # The next bar is sourced independently from the current event's
            # native 5m open; this must never be substituted with a close.
            if int(decision_ms) == int(bars.get(symbol, {}).get("next_open_ms", -1)):
                next_open = bars[symbol].get("next_open")
                next_open_available = bool(bars[symbol].get("next_open_available"))
            else:
                next_candle = self._candle(symbol, int(decision_ms))
                next_open = float(next_candle["open"]) if next_candle else None
                next_open_available = next_candle is not None
            valid = before is not None and after is not None
            move = (100.0 * (float(after["close"]) / float(before["close"]) - 1.0)
                    if valid and float(before["close"]) > 0 else math.nan)
            reclaimed = bool(valid and float(after["close"]) > level)
            if valid:
                observed_returns.append(move)
                reclaim_values.append(reclaimed)
            members.append({
                "symbol": symbol, "first_touch_ms": int(touch["touch_ms"]),
                "level": level, "known_close_ms": int(known_ms),
                "known_close": float(before["close"]) if before else None,
                "decision_close_ms": int(decision_ms) - BAR_MS,
                "decision_close": float(after["close"]) if after else None,
                "observed_return_pct": move if np.isfinite(move) else None,
                "reclaimed": reclaimed if valid else None,
                "next_open_ms": int(decision_ms), "next_open": next_open,
                "next_open_available": bool(next_open_available),
                "bar_open_ms": int(decision_ms) - BAR_MS,
                "open": float(after["open"]) if after else None,
                "high": float(after["high"]) if after else None,
                "low": float(after["low"]) if after else None,
                "close": float(after["close"]) if after else None,
                "data_complete": bool(valid and next_open_available),
            })
        expected = len(eligible)
        valid_n = len(observed_returns)
        alt_complete = valid_n == expected
        summary = {
            "member_n": expected, "observed_n": valid_n,
            "data_complete": valid_n == expected and all(row["next_open_available"] for row in members),
            "reclaim_fraction": float(np.mean(reclaim_values)) if valid_n == expected and expected else None,
            "alt_expected_n": expected, "alt_observed_n": valid_n,
            "alt_data_complete": alt_complete,
            "alt_observed_move_pct": float(np.median(observed_returns)) if alt_complete and observed_returns else None,
            "median_observed_move_pct": float(np.median(observed_returns)) if valid_n == expected and observed_returns else None,
        }
        return members, summary

    def _candidate(self, branch: str, known_ms: int, decision_ms: int,
                   initial: Sequence[Mapping[str, Any]], new: Sequence[Mapping[str, Any]],
                   bars: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        universe_n = trailing28_unique_count(self._touches.values(), known_ms)
        members, summary = self._member_rows(initial, known_ms, decision_ms, bars)
        initial_alt = [row for row in initial if str(row["symbol"]).upper() not in ALT_EXCLUDED]
        # Breadth share is based on the raw lower-touch cohort; major coins are
        # excluded only from the equal-weight ALT basket and its member features.
        share_n = len(initial)
        share = 100.0 * share_n / universe_n if universe_n else None
        lower_new = sorted({str(row["symbol"]).upper() for row in new
                            if str(row["symbol"]).upper() not in {str(item["symbol"]).upper() for item in initial}})
        reclaim = summary["reclaim_fraction"]
        alt_move = summary["alt_observed_move_pct"]
        btc_bar = bars.get("BTCUSDT", {})
        btc_snapshot = {
            "symbol": "BTCUSDT", "bar_open_ms": int(decision_ms) - BAR_MS,
            "open": btc_bar.get("open"), "high": btc_bar.get("high"),
            "low": btc_bar.get("low"), "close": btc_bar.get("close"),
            "next_open_ms": int(decision_ms), "next_open": btc_bar.get("next_open"),
            "bar_available": bool(btc_bar.get("bar_available")),
            "next_open_available": bool(btc_bar.get("next_open_available")),
        }
        btc_data_complete = bool(btc_snapshot["bar_available"] and btc_snapshot["next_open_available"])
        if branch == "BTC_ordinary":
            passes = (len(initial) >= 2 and not lower_new and reclaim is not None and reclaim >= 0.75)
            event_id = f"BTC_ordinary:{known_ms}:{decision_ms}"
            rule = {"window_min": 720, "wait_min": 10, "initial_min": 2,
                    "new_coin_rule": "zero", "reclaim_min": 0.75}
        elif branch == "BTC_case":
            passes = (len(initial) >= 1 and share is not None and share >= 5.0
                      and 4 * len(lower_new) <= len(initial)
                      and alt_move is not None and alt_move >= 2.0)
            event_id = f"BTC_case:{known_ms}:{decision_ms}"
            rule = {"window_min": 5, "wait_min": 30, "initial_min": 1,
                    "share_min_pct": 5.0, "new_to_initial_max": 0.25,
                    "alt_observed_move_min_pct": 2.0}
        else:
            passes = bool(len(initial) >= 2 and initial_alt and share is not None and share >= 5.0)
            event_id = f"ALT:{known_ms}:{decision_ms}"
            rule = {"window_min": 5, "wait_min": 0, "initial_min": 2, "share_min_pct": 5.0,
                    "diffusion": "any", "confirmation": "none"}
        btc_gate = asof_4h_ema20(self._hourly_ema20, decision_ms)
        required_members_complete = bool(summary["data_complete"])
        if branch in {"BTC_case", "BTC_ordinary"}:
            # The BTC leg is priced independently from the close, while member
            # closes only support the ALT reclaim/move feature calculation.
            data_complete = bool(btc_data_complete and summary["alt_data_complete"])
        else:
            data_complete = bool(btc_data_complete and required_members_complete
                                 and len(members) == len(initial_alt))
        return {
            "branch": branch, "event_id": event_id, "known_ms": int(known_ms),
            "decision_ms": int(decision_ms), "entry_ms": int(decision_ms),
            "signal_close_ms": int(decision_ms), "initial_n": int(len(initial)),
            "bar_open_ms": int(decision_ms) - BAR_MS,
            "open": btc_snapshot["open"], "high": btc_snapshot["high"],
            "low": btc_snapshot["low"], "close": btc_snapshot["close"],
            "next_open_ms": btc_snapshot["next_open_ms"], "next_open": btc_snapshot["next_open"],
            "initial_symbols": sorted({str(row["symbol"]).upper() for row in initial}),
            "new_n": int(len(lower_new)), "new_symbols": lower_new,
            "trailing28_any_touch_unique_n": int(universe_n),
            "share28_pct": share, "reclaim_fraction": reclaim,
            "alt_observed_move_pct": alt_move,
            "observed_member_n": int(summary["observed_n"]),
            "alt_observed_member_n": int(summary["alt_observed_n"]),
            "alt_expected_member_n": int(summary["alt_expected_n"]),
            "expected_member_n": int(len(members)),
            "members": members, "btc": btc_snapshot, "btc_4h": btc_gate,
            "ema_required": branch == "BTC_ordinary", "rule": rule,
            "rule_pass": bool(passes),
            "data_complete": data_complete,
            "can_open": bool(passes and data_complete),
        }

    def _candidates_at(self, close_ms: int, bars: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
        bar_open_ms = int(close_ms) - BAR_MS
        result: list[dict[str, Any]] = []
        # W=5/X=0 ALT uses the current completed native 5m touch bar.
        initial_5m = self._touches_in(bar_open_ms, close_ms)
        if initial_5m:
            result.append(self._candidate("ALT", close_ms, close_ms, initial_5m, [], bars))
        # The source's BTC urgent case is W=5/X=30, not the X=0 shorthand.
        case_known = close_ms - 30 * 60_000
        case_initial = self._touches_in(case_known - BAR_MS, case_known)
        case_new = self._touches_in(case_known, close_ms)
        if case_initial:
            result.append(self._candidate("BTC_case", case_known, close_ms, case_initial, case_new, bars))
        # Ordinary BTC signal is W=12h/X=10m, aligned to UTC 12h closes.
        decision_known = close_ms - 10 * 60_000
        if decision_known % TWELVE_HOURS_MS == 0:
            ordinary_initial = self._touches_in(decision_known - TWELVE_HOURS_MS, decision_known)
            ordinary_new = self._touches_in(decision_known, close_ms)
            if ordinary_initial:
                result.append(self._candidate("BTC_ordinary", decision_known, close_ms,
                                              ordinary_initial, ordinary_new, bars))
        return result

    def iter_closed_events(self, after_close_ms: int, through_close_ms: int | None = None,
                           *, position_symbols: Iterable[str] = ()) -> Iterator[dict[str, Any]]:
        """Yield closed 5m market events after an exclusive close-time cursor.

        The requested close is only emitted when it is already closed according
        to UTC wall time. A fresh event reads the whole active USDT-perpetual
        universe in bounded parallel REST calls so no market breadth is inferred
        from the current position basket alone.
        """
        if not self._warmed:
            self.warmup()
        self._universe()
        now = _now_ms()
        latest_closed = (now // BAR_MS) * BAR_MS
        through = int(through_close_ms if through_close_ms is not None else latest_closed)
        if through % BAR_MS:
            raise ValueError("through_close_ms must be aligned to UTC 5m close boundaries")
        if through > latest_closed:
            return
        held = {str(value).upper() for value in position_symbols}
        boundary = ((int(after_close_ms) // BAR_MS) + 1) * BAR_MS
        while boundary <= through:
            bar_open = boundary - BAR_MS
            realtime = (now - boundary) <= 90_000 and now >= boundary
            rebuild_market = realtime or boundary > self._market_seeded_through_ms
            if rebuild_market:
                market = self._live_bars(bar_open, boundary, held, full_universe=True)
            else:
                # Old replay closes already have touch-ledger coverage from
                # warmup; only fetch BTC and held symbols for position upkeep.
                market = self._live_bars(bar_open, boundary, held | {"BTCUSDT"},
                                         full_universe=False)

            inserted_touches = self._record_current_touches(market, bar_open) if rebuild_market else []
            candidates = self._candidates_at(boundary, market) if realtime else []
            self._rolling_hourly_update(boundary)
            if boundary % HOUR_MS == 0:
                self._prune_five_rows(boundary)
            current_touches = sorted(
                (dict(row) for row in self._touches.values()
                 if int(row["touch_ms"]) == bar_open),
                key=lambda row: (str(row["symbol"]), int(row["side"])),
            )

            for symbol in held:
                market.setdefault(symbol, {
                    "bar_open_ms": bar_open, "next_open_ms": boundary,
                    "bar_available": False, "next_open_available": False,
                    "source": "unavailable",
                })

            funding_symbols = set(held)
            for candidate in candidates:
                if not (candidate.get("rule_pass") and candidate.get("data_complete")):
                    continue
                if candidate["branch"] == "ALT":
                    funding_symbols.update(str(member["symbol"]).upper()
                                            for member in candidate.get("members", []))
                else:
                    funding_symbols.add("BTCUSDT")
            funding_open_events, funding_open_coverage = funding_at(
                funding_symbols, bar_open, self._rest_get
            )
            funding_events, funding_coverage = funding_at(
                funding_symbols, boundary, self._rest_get
            )
            funding_open_due = [dict(data, symbol=symbol)
                                for symbol, data in funding_open_coverage.items()]
            funding_due = [dict(data, symbol=symbol)
                           for symbol, data in funding_coverage.items()]

            btc = market.get("BTCUSDT", {})
            btc_ema = asof_4h_ema20(self._hourly_ema20, boundary)
            active_observed = sum(bool(market.get(symbol, {}).get("bar_available"))
                                  for symbol in self._symbols)
            active_missing = [symbol for symbol in sorted(self._symbols)
                              if not market.get(symbol, {}).get("bar_available")]
            market_rebuild_complete = not rebuild_market or not active_missing
            if market_rebuild_complete:
                self._market_seeded_through_ms = max(self._market_seeded_through_ms, boundary)
            trailing_n = trailing28_unique_count(self._touches.values(), boundary)
            event = {
                "event_id": f"binance-usdm-5m:{bar_open}",
                "bar_open_ms": int(bar_open), "close_ms": int(boundary),
                "open": btc.get("open"), "high": btc.get("high"),
                "low": btc.get("low"), "close": btc.get("close"),
                "next_open_ms": int(boundary), "next_open": btc.get("next_open"),
                "bar_available": bool(btc.get("bar_available")),
                "next_open_available": bool(btc.get("next_open_available")),
                "bars_by_symbol": market,
                "new_first_touches": current_touches,
                "inserted_first_touches": inserted_touches,
                "candidates": candidates,
                "btc_4h": btc_ema,
                "funding_open_events": funding_open_events,
                "funding_open_due": funding_open_due,
                "funding_open_coverage": funding_open_coverage,
                "funding_events": funding_events,
                "funding_due": funding_due,
                "funding_coverage": funding_coverage,
                "source": "binance_usdm_public",
                "is_realtime": bool(realtime), "is_replay": not bool(realtime),
                "market_symbols_expected": len(self._symbols),
                "market_symbols_observed": active_observed,
                "market_missing_symbols": active_missing,
                "trailing28_any_touch_unique_n": trailing_n,
                "market": {"active_symbols": len(self._symbols), "observed_symbols": active_observed,
                           "missing_symbols": active_missing, "seen_28d": trailing_n,
                           "first_touch_rows_at_bar": len(current_touches)},
                "market_rebuild_complete": bool(market_rebuild_complete),
                "source_coverage_complete": bool(btc.get("bar_available") and btc.get("next_open_available")
                                                  and market_rebuild_complete),
                "generated_at_ms": _now_ms(),
            }
            yield event
            boundary += BAR_MS


# Alias kept short for the monitor's import site and for focused test fixtures.
BreadthEma20Feed = BinanceBreadthEma20Feed
