"""Hourly read-only feed for confirmed 4H trendline breaks.

The worker reuses the monitor's OKX ``CandleHub`` client and its shared
background request budget.  A frozen 4H research seed supplies long history;
current native 4H and 1H candles are merged in the worker.  HTTP handlers only
serve the already-computed in-memory snapshot and never fetch candles or run
the event rules.
"""
from __future__ import annotations

from array import array
import csv
import json
import logging
import math
import re
import sqlite3
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Union

from fastapi import FastAPI, Query


ROOT = Path(__file__).resolve().parents[2]
SEED_ROOT = ROOT / "data/crypto/market/okx_swap_4h_20261109"
H1_SEED_ROOT = ROOT / "data/crypto/market/okx_swap_1h_windows_20261109"
BAR_MS = {"1H": 60 * 60_000, "4H": 4 * 60 * 60_000}
LOOKBACK_DAYS = 30
FOUR_HOUR_LIMIT = 3_000
ONE_HOUR_LIMIT = 2_000
MAX_PAGE_SIZE = 10_000
PAGE_SIZE = 300
VALIDITY_SCHEMA = 2
PREBREAK_LOW_BARS = 11
RALLY_MULTIPLIER = Decimal("1.10")
LOG = logging.getLogger("fable.monitor.trendline_break_feed")
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,32}-USDT-SWAP$")


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("invalid_timestamp")
    result = int(value)
    if result < 0:
        raise ValueError("invalid_timestamp")
    return result


def _valid_bar(time_ms: Any, opened: Any, high: Any, low: Any, close: Any, step: int) -> dict[str, Any]:
    stamp = _as_int(time_ms)
    values = tuple(float(x) for x in (opened, high, low, close))
    op, hi, lo, cl = values
    if (stamp % step or not all(math.isfinite(x) and x > 0 for x in values)
            or hi < max(op, cl) or lo > min(op, cl) or lo > hi):
        raise ValueError("invalid_ohlc_bar")
    return {"t": stamp, "o": op, "h": hi, "l": lo, "c": cl}


def _dedupe_rows(rows: Iterable[Mapping[str, Any]], step: int) -> list[dict[str, Any]]:
    by_time: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = _valid_bar(row["t"], row["o"], row["h"], row["l"], row["c"], step)
        previous = by_time.get(item["t"])
        if previous is not None and previous != item:
            raise ValueError("conflicting_confirmed_candles")
        by_time[item["t"]] = item
    return [by_time[key] for key in sorted(by_time)]


def _gaps(rows: Sequence[Mapping[str, Any]], step: int) -> int:
    return sum(1 for left, right in zip(rows, rows[1:]) if right["t"] - left["t"] != step)


def _pack(rows: Sequence[Mapping[str, Any]], limit: int) -> tuple[array, array, array, array, array]:
    """Store a bounded OHLC window compactly; a scan never retains Python dicts for every market."""
    selected = rows[-limit:]
    packed = (array("q"), array("d"), array("d"), array("d"), array("d"))
    for row in selected:
        for target, key in zip(packed, ("t", "o", "h", "l", "c")):
            target.append(int(row[key]) if key == "t" else float(row[key]))
    return packed


def _unpack(rows: Optional[tuple[array, array, array, array, array]]) -> list[dict[str, Any]]:
    if rows is None:
        return []
    stamps, opens, highs, lows, closes = rows
    return [{"t": stamps[i], "o": opens[i], "h": highs[i], "l": lows[i], "c": closes[i]}
            for i in range(len(stamps))]


def _rule_bars(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{"time": int(row["t"]), "open": float(row["o"]), "high": float(row["h"]),
             "low": float(row["l"]), "close": float(row["c"])} for row in rows]


def _first_touch(bar: Mapping[str, Any], target: float, floor: float) -> Optional[str]:
    """A bar's open precedes its extrema. OHLC alone cannot order two later touches."""
    if bar["o"] >= target:
        return "rally"
    if bar["o"] < floor:
        return "low"
    rallied, broke = bar["h"] >= target, bar["l"] < floor
    if rallied and broke:
        return "ambiguous"
    if rallied:
        return "rally"
    return "low" if broke else None


def _terminal(event: Mapping[str, Any]) -> bool:
    return (event.get("validity_schema") == VALIDITY_SCHEMA
            and (event.get("validity_status") == "invalid"
                 or (event.get("validity_status") == "valid"
                     and event.get("validity_reason") == "rally_target_reached")))


def verify_event_validity(event: Mapping[str, Any], hourly: Sequence[Mapping[str, Any]],
                          asof_ms: int, *, resolve_hour: Optional[Callable] = None) -> dict[str, Any]:
    """Freeze the signal close and 11-bar low, then walk subsequent closed 1H bars in order."""
    result = dict(event)
    cutoff = _as_int(asof_ms)
    candle_ms = _as_int(result.get("candle_ms"))
    confirmed_ms = _as_int(result.get("confirmed_ms"))
    start_ms = candle_ms - (PREBREAK_LOW_BARS - 1) * BAR_MS["1H"]
    step = BAR_MS["1H"]
    bars = _dedupe_rows(hourly, step)
    closed = [bar for bar in bars if bar["t"] + step <= cutoff]
    by_time = {bar["t"]: bar for bar in closed}
    expected_latest = (cutoff // step) * step - step

    result.update(validity_schema=VALIDITY_SCHEMA, validity_status="unverified",
                  validity_checked_ms=cutoff, validity_reason="insufficient_prebreak_history",
                  prebreak_low=None, prebreak_low_start_ms=start_ms,
                  prebreak_low_end_ms=candle_ms, prebreak_low_bars=0)
    result.pop("invalidated_ms", None)
    result.pop("invalidated_low", None)
    result.pop("rally_confirmed_ms", None)
    # Preserve inclusive decimal price equality (e.g. 100 -> 110).
    target = float(Decimal(str(result["close"])) * RALLY_MULTIPLIER)
    if not math.isfinite(target) or target <= 0:
        raise ValueError("invalid_breakout_close")
    result["rally_target"] = target

    expected_prebreak = range(start_ms, candle_ms + step, step)
    prebreak = [by_time[stamp] for stamp in expected_prebreak if stamp in by_time]
    result["prebreak_low_bars"] = len(prebreak)
    if (confirmed_ms != candle_ms + step or confirmed_ms > cutoff
            or len(prebreak) != PREBREAK_LOW_BARS):
        if confirmed_ms != candle_ms + step or confirmed_ms > cutoff:
            result["validity_reason"] = "event_not_confirmed"
        return result

    floor = min(bar["l"] for bar in prebreak)
    result["prebreak_low"] = floor

    for stamp in range(confirmed_ms, expected_latest + step, step):
        bar = by_time.get(stamp)
        if bar is None:
            result["validity_reason"] = "stale_hourly_data" if stamp == expected_latest else "gap_after_breakout"
            return result
        first = _first_touch(bar, target, floor)
        if first == "ambiguous" and resolve_hour is not None:
            first = resolve_hour(result["symbol"], bar, target, floor)
        if first == "rally":
            result.update(validity_status="valid", validity_reason="rally_target_reached",
                          rally_confirmed_ms=stamp + step)
            return result
        if first == "low":
            result.update(validity_status="invalid", validity_reason="postbreak_low_broken",
                          invalidated_ms=stamp + step, invalidated_low=bar["l"])
            return result
        if first == "ambiguous":
            # No evidence of a low-first invalidation. Keep listed and retry its ordering.
            result.update(validity_status="valid", validity_reason="intrabar_order_pending")
            return result

    result.update(validity_status="valid", validity_reason="validated")
    return result


class TrendlineBreakFeed:
    """Background OHLC loader and event cache for ``/api/trendline-breaks``."""

    def __init__(self, runtime: Union[str, Path], *, client: Any = None,
                 symbols_source: Optional[Callable[[], Sequence[str]]] = None,
                 event_fn: Optional[Callable[..., Iterable[Mapping[str, Any]]]] = None,
                 seed_root: Optional[Union[str, Path]] = None,
                 h1_seed_root: Optional[Union[str, Path]] = None,
                 lookback_days: int = LOOKBACK_DAYS):
        self.runtime = Path(runtime)
        self.client = client
        self.symbols_source = symbols_source
        self.event_fn = event_fn
        self.seed_root = Path(seed_root) if seed_root is not None else SEED_ROOT
        self.h1_seed_root = Path(h1_seed_root) if h1_seed_root is not None else H1_SEED_ROOT
        self.lookback_days = max(1, int(lookback_days))
        self.database = self.runtime / "trendline-break-feed.sqlite3"
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.db_lock = threading.RLock()
        self.thread: Optional[threading.Thread] = None
        self.db: Optional[sqlite3.Connection] = None
        self._loaded_seed: set[str] = set()
        self._seed_bars: dict[str, dict[str, Optional[tuple[array, array, array, array, array]]]] = {}
        self._bars: dict[str, dict[str, tuple[array, array, array, array, array]]] = {}
        self._events_by_symbol: dict[str, list[dict[str, Any]]] = {}
        self._symbol_state: dict[str, dict[str, Any]] = {}
        self._items: list[dict[str, Any]] = []
        self._cycle_asof_ms: Optional[int] = None
        self._last_scan_asof_ms: Optional[int] = None
        self._last_complete_asof_ms: Optional[int] = None
        self._state = "warming"
        self._coverage: dict[str, Any] = self._empty_coverage()
        self._progress: dict[str, Any] = {"done": 0, "total": 0, "symbol": None,
                                          "stage": "waiting_for_universe", "percent": 0}
        self._last_error: Optional[str] = None
        self._load_saved_snapshot()

    def _current_cutoff_ms(self) -> int:
        """Return the current UTC hourly close boundary without scanning markets."""
        source = getattr(self.client, "clock", None) if self.client is not None else None
        try:
            current = int(source()) if callable(source) else now_ms()
        except Exception:
            current = now_ms()
        return current // BAR_MS["1H"] * BAR_MS["1H"]

    def _empty_coverage(self) -> dict[str, Any]:
        return {"symbols_total": 0, "covered": 0, "errors": 0, "no_data": 0,
                "gap_symbols": 0, "insufficient_history": 0, "seed_symbols": 0,
                "seed_1h_symbols": 0,
                "missing_4h": 0, "missing_1h": 0, "stale_4h": 0, "stale_1h": 0,
                "lookback_days": self.lookback_days, "lookback_start_ms": None,
                "latest_4h_close_ms": None, "latest_1h_close_ms": None,
                "oldest_covered_4h_close_ms": None, "oldest_covered_1h_close_ms": None,
                "h1_history_bars": ONE_HOUR_LIMIT, "h4_history_bars": FOUR_HOUR_LIMIT,
                "issues": [], "short_history_symbols": []}

    def _load_saved_snapshot(self) -> None:
        if not self.database.is_file():
            return
        try:
            connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True, timeout=2)
            try:
                rows = connection.execute("SELECT symbol,payload FROM events ORDER BY confirmed_ms DESC,symbol,event_id")
                for symbol, payload in rows:
                    event = json.loads(payload)
                    if int(event.get("confirmed_ms", 0)) >= now_ms() - self.lookback_days * 24 * 60 * 60_000:
                        self._events_by_symbol.setdefault(symbol, []).append(event)
                meta = dict(connection.execute("SELECT key,value FROM meta"))
            finally:
                connection.close()
            self._last_scan_asof_ms = _as_int(meta["last_scan_asof_ms"]) if meta.get("last_scan_asof_ms") else None
            self._last_complete_asof_ms = _as_int(meta["last_complete_asof_ms"]) if meta.get("last_complete_asof_ms") else None
            if meta.get("coverage"):
                cached_coverage = json.loads(meta["coverage"])
                if isinstance(cached_coverage, dict):
                    self._coverage = cached_coverage
            self._items = self._sorted_events(self._events_by_symbol, self._current_cutoff_ms())
        except (OSError, sqlite3.Error, ValueError, TypeError, json.JSONDecodeError):
            LOG.warning("Ignoring unreadable saved trendline feed snapshot at %s", self.database)

    def _db(self) -> sqlite3.Connection:
        with self.db_lock:
            if self.db is None:
                self.runtime.mkdir(parents=True, exist_ok=True)
                self.db = sqlite3.connect(str(self.database), timeout=10, check_same_thread=False)
                self.db.execute("PRAGMA journal_mode=WAL")
                self.db.executescript("""
                    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS events (
                      event_id TEXT NOT NULL, symbol TEXT NOT NULL, confirmed_ms INTEGER NOT NULL,
                      payload TEXT NOT NULL, PRIMARY KEY(symbol,event_id));
                    CREATE INDEX IF NOT EXISTS events_by_time ON events(confirmed_ms DESC, symbol);
                    CREATE TABLE IF NOT EXISTS symbol_state (
                      symbol TEXT PRIMARY KEY, updated_ms INTEGER NOT NULL, h4_latest_ms INTEGER,
                      h1_latest_ms INTEGER, coverage TEXT NOT NULL, error TEXT);
                """)
                self.db.commit()
            return self.db

    def _persist_symbol(self, symbol: str, events: Sequence[Mapping[str, Any]],
                        coverage: Mapping[str, Any], error: Optional[str] = None) -> None:
        connection = self._db()
        event_rows = [(str(item["event_id"]), symbol, int(item["confirmed_ms"]),
                       json.dumps(item, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
                      for item in events]
        with self.db_lock, connection:
            connection.execute("DELETE FROM events WHERE symbol=?", (symbol,))
            connection.executemany("INSERT INTO events(event_id,symbol,confirmed_ms,payload) VALUES (?,?,?,?)",
                                   event_rows)
            connection.execute("INSERT OR REPLACE INTO symbol_state VALUES (?,?,?,?,?,?)",
                               (symbol, now_ms(), coverage.get("h4_latest_ms"), coverage.get("h1_latest_ms"),
                                json.dumps(dict(coverage), allow_nan=False), error))

    def _persist_complete(self, asof_ms: int) -> None:
        connection = self._db()
        with self.db_lock, connection:
            connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)",
                               ("last_complete_asof_ms", str(int(asof_ms))))

    def _persist_cycle(self, asof_ms: int, coverage: Mapping[str, Any]) -> None:
        connection = self._db()
        with self.db_lock, connection:
            connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)",
                               ("last_scan_asof_ms", str(int(asof_ms))))
            connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)",
                               ("coverage", json.dumps(dict(coverage), allow_nan=False)))

    def _prune_inactive(self, symbols: Sequence[str]) -> None:
        active = set(symbols)
        with self.lock:
            stale = set(self._events_by_symbol) - active
            for symbol in stale:
                self._events_by_symbol.pop(symbol, None)
                self._symbol_state.pop(symbol, None)
            if stale:
                self._items = self._sorted_events(self._events_by_symbol)
        if not stale or not self.database.is_file():
            return
        connection = self._db()
        with self.db_lock, connection:
            connection.executemany("DELETE FROM events WHERE symbol=?", [(symbol,) for symbol in stale])
            connection.executemany("DELETE FROM symbol_state WHERE symbol=?", [(symbol,) for symbol in stale])

    def _expire_events(self, start_ms: int) -> None:
        with self.lock:
            for symbol, events in list(self._events_by_symbol.items()):
                kept = [event for event in events if int(event.get("confirmed_ms", 0)) >= start_ms]
                if kept:
                    self._events_by_symbol[symbol] = kept
                else:
                    self._events_by_symbol.pop(symbol, None)
            self._items = self._sorted_events(self._events_by_symbol)
        if self.database.is_file():
            connection = self._db()
            with self.db_lock, connection:
                connection.execute("DELETE FROM events WHERE confirmed_ms<?", (start_ms,))

    def _get_client(self):
        if self.client is None:
            from yoyo.monitor.okx import OKX
            self.client = OKX(runtime=self.runtime, priority="background")
        return self.client

    def _get_event_fn(self):
        if self.event_fn is None:
            from .trendline_break_rules import events
            self.event_fn = events
        return self.event_fn

    def _get_symbols(self) -> tuple[str, ...]:
        if self.symbols_source is None:
            client = self._get_client()
            instruments = client.instruments()
            result = [row.get("instId", "") for row in instruments
                      if row.get("state") == "live" and row.get("instType") == "SWAP"
                      and row.get("settleCcy") == "USDT"]
        else:
            result = list(self.symbols_source() or ())
        return tuple(sorted({str(symbol).upper() for symbol in result if _SYMBOL_RE.fullmatch(str(symbol).upper())}))

    def _load_seed(self, symbol: str, timeframe: str) -> Optional[tuple[array, array, array, array, array]]:
        key = symbol + "|" + timeframe
        if key in self._loaded_seed:
            return self._seed_bars.get(symbol, {}).get(timeframe)
        self._loaded_seed.add(key)
        base = symbol.removesuffix("-USDT-SWAP")
        if timeframe == "4H":
            candidates = sorted(self.seed_root.glob(f"okx_{base}_USDT_SWAP_4H_*.csv"))
        else:
            candidates = sorted(self.h1_seed_root.glob(f"okx_{base}_USDT_SWAP_1H.csv"))
        if not candidates:
            self._seed_bars.setdefault(symbol, {})[timeframe] = None
            return None
        # The date-stamped seed has one file per market. If an older duplicate
        # is present, use the largest validated source without touching it.
        path = max(candidates, key=lambda item: item.stat().st_size)
        rows: dict[int, dict[str, Any]] = {}
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"ts", "open", "high", "low", "close"}
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                raise ValueError("invalid_%s_seed_columns" % timeframe.lower())
            for raw in reader:
                row = _valid_bar(raw["ts"], raw["open"], raw["high"], raw["low"], raw["close"], BAR_MS[timeframe])
                if row["t"] in rows and rows[row["t"]] != row:
                    raise ValueError("conflicting_%s_seed_rows" % timeframe.lower())
                rows[row["t"]] = row
        limit = FOUR_HOUR_LIMIT if timeframe == "4H" else ONE_HOUR_LIMIT
        ordered = [rows[key] for key in sorted(rows)][-limit:]
        packed = _pack(ordered, limit)
        self._seed_bars.setdefault(symbol, {})[timeframe] = packed
        return packed

    def _parse_page(self, raw_rows: Any, timeframe: str) -> list[dict[str, Any]]:
        from yoyo.monitor.okx import parse_rows
        return _dedupe_rows(parse_rows(raw_rows, timeframe, self._get_client().clock()), BAR_MS[timeframe])

    def _fetch_pages(self, symbol: str, timeframe: str, asof_ms: int,
                     limit: int) -> tuple[list[dict[str, Any]], int]:
        client = self._get_client()
        step = BAR_MS[timeframe]
        by_time: dict[int, dict[str, Any]] = {}
        after: Optional[int] = None
        # Empty pages are the only reliable end marker: CandleHub can return a
        # short page when a cached window contains a gap, while older history
        # may still exist behind the cursor.
        while True:
            params: dict[str, str] = {"instId": symbol, "bar": timeframe, "limit": str(PAGE_SIZE)}
            if after is not None:
                params["after"] = str(after)
            raw = client.get("/api/v5/market/history-candles", params)
            parsed = self._parse_page(raw, timeframe)
            eligible = [row for row in parsed if row["t"] + step <= asof_ms]
            for row in eligible:
                previous = by_time.get(row["t"])
                if previous is not None and previous != row:
                    raise ValueError("conflicting_confirmed_candles")
                by_time[row["t"]] = row
            raw_count = len(raw) if isinstance(raw, list) else 0
            if raw_count == 0 or len(by_time) >= limit:
                break
            # The newest native row can still be open and therefore absent
            # from ``parsed``; continue by the raw page cursor when 299 closed
            # candles were returned.
            oldest = min(int(row[0]) for row in raw)
            if after is not None and oldest >= after:
                break
            after = oldest
        ordered = [by_time[key] for key in sorted(by_time)][-limit:]
        return ordered, _gaps(ordered, step)

    def _current_page(self, symbol: str, timeframe: str, asof_ms: int) -> list[dict[str, Any]]:
        client = self._get_client()
        raw = client.get("/api/v5/market/history-candles",
                         {"instId": symbol, "bar": timeframe, "limit": str(PAGE_SIZE)})
        step = BAR_MS[timeframe]
        return [row for row in self._parse_page(raw, timeframe) if row["t"] + step <= asof_ms]

    def _load_or_update(self, symbol: str, timeframe: str, asof_ms: int,
                        limit: int, previous: Optional[tuple[array, array, array, array, array]],
                        seed: Optional[tuple[array, array, array, array, array]] = None
                        ) -> tuple[list[dict[str, Any]], int]:
        step = BAR_MS[timeframe]
        rows = _unpack(previous if previous is not None else seed)
        expected_open = (asof_ms // step) * step - step
        rows = [row for row in rows if row["t"] + step <= asof_ms]
        if rows and rows[-1]["t"] >= expected_open:
            return rows[-limit:], _gaps(rows[-limit:], step)
        if not rows:
            return self._fetch_pages(symbol, timeframe, asof_ms, limit)

        # The 1H research windows are useful seeds but some have holes. Ask
        # CandleHub for the bounded recent horizon only when the seed itself
        # shows a gap; merge the result so older seed bars are never discarded.
        if previous is None and seed is not None and _gaps(rows[-limit:], step):
            fresh, _ = self._fetch_pages(symbol, timeframe, asof_ms, limit)
            by_time = {row["t"]: row for row in rows}
            for row in fresh:
                prior = by_time.get(row["t"])
                if prior is not None and prior != row:
                    raise ValueError("exchange_revised_confirmed_candle")
                by_time[row["t"]] = row
            merged = [by_time[key] for key in sorted(by_time)][-limit:]
            return merged, _gaps(merged, step)

        newest = self._current_page(symbol, timeframe, asof_ms)
        by_time = {row["t"]: row for row in rows}
        for row in newest:
            prior = by_time.get(row["t"])
            if prior is not None and prior != row:
                raise ValueError("exchange_revised_confirmed_candle")
            by_time[row["t"]] = row
        merged = [by_time[key] for key in sorted(by_time)][-limit:]
        gap_count = _gaps(merged, step)
        return merged, gap_count

    def _resolve_minute(self, symbol: str, minute: Mapping[str, Any], target: float, floor: float) -> str:
        """Order a dual-touch minute by OKX trade IDs, checking the complete minute against OHLC."""
        start = minute["t"]
        end = start + 60_000
        prices: dict[int, float] = {}
        cursor, cursor_type = end, "2"  # exclusive timestamp, then exclusive trade IDs
        while not self.stop.is_set():
            page = self._get_client().get("/api/v5/market/history-trades", {
                "instId": symbol, "type": cursor_type, "after": str(cursor), "limit": "100"})
            if not page:
                break
            for trade in page:
                if trade["instId"] != symbol:
                    return "ambiguous"
                if start <= int(trade["ts"]) < end:
                    prices[int(trade["tradeId"])] = float(trade["px"])
            if min(int(trade["ts"]) for trade in page) < start or len(page) < 100:
                break
            oldest = min(int(trade["tradeId"]) for trade in page)
            if cursor_type == "1" and oldest >= cursor:
                return "ambiguous"
            cursor, cursor_type = oldest, "1"
        else:
            return "ambiguous"
        ordered = [prices[key] for key in sorted(prices)]
        if (not ordered or (ordered[0], max(ordered), min(ordered), ordered[-1]) !=
                (minute["o"], minute["h"], minute["l"], minute["c"])):
            return "ambiguous"
        return next(("rally" if price >= target else "low" for price in ordered
                     if price >= target or price < floor), "ambiguous")

    def _resolve_hour(self, symbol: str, hour: Mapping[str, Any], target: float, floor: float) -> str:
        """Fetch confirmed minutes only when an hourly bar crossed both boundaries."""
        start = hour["t"]
        end = start + BAR_MS["1H"]
        try:
            raw = self._get_client().get("/api/v5/market/history-candles", {
                "instId": symbol, "bar": "1m", "after": str(end), "limit": "60"})
            minutes = _dedupe_rows(
                (_valid_bar(row[0], *row[1:5], 60_000) for row in raw
                 if str(row[8]) == "1" and start <= int(row[0]) < end), 60_000)
        except Exception as error:
            LOG.warning("Trendline first-touch minutes unavailable for %s at %s: %s", symbol, start, error)
            return "ambiguous"
        if (len(minutes) != 60 or minutes[0]["o"] != hour["o"] or minutes[-1]["c"] != hour["c"]
                or max(row["h"] for row in minutes) != hour["h"]
                or min(row["l"] for row in minutes) != hour["l"]):
            return "ambiguous"
        for row in minutes:
            first = _first_touch(row, target, floor)
            if first == "ambiguous":
                try:
                    return self._resolve_minute(symbol, row, target, floor)
                except Exception as error:
                    LOG.warning("Trendline first-touch trades unavailable for %s at %s: %s", symbol, row["t"], error)
                    return "ambiguous"
            if first is not None:
                return first
        return "ambiguous"

    def _normalize_events(self, symbol: str, raw_events: Iterable[Mapping[str, Any]],
                          hourly: Sequence[Mapping[str, Any]], asof_ms: int,
                          updated_ms: int) -> list[dict[str, Any]]:
        start_ms = asof_ms - self.lookback_days * 24 * 60 * 60_000
        result: dict[str, dict[str, Any]] = {}
        # Original confirmed triggers survive a moving 4H seed. Re-evaluate them as well
        # as new triggers, including invalidations stored by the previous rule.
        prior = {item["event_id"]: item for item in self._events_by_symbol.get(symbol, ())}
        for raw in [*prior.values(), *raw_events]:
            if not isinstance(raw, Mapping):
                raise ValueError("invalid_rule_event")
            event = dict(raw)
            event_id = str(event.get("event_id", ""))
            if not event_id:
                raise ValueError("rule_event_missing_id")
            event_symbol = str(event.get("symbol", symbol)).upper()
            if event_symbol != symbol:
                raise ValueError("rule_event_symbol_mismatch")
            confirmed_ms = _as_int(event["confirmed_ms"])
            candle_ms = _as_int(event["candle_ms"])
            a_ms, b_ms = _as_int(event["a_ms"]), _as_int(event["b_ms"])
            numeric = {key: float(event[key]) for key in ("level", "a_price", "b_price")}
            if (not all(math.isfinite(value) and value > 0 for value in numeric.values())
                    or confirmed_ms > asof_ms or candle_ms > asof_ms):
                raise ValueError("invalid_rule_event_values")
            if not start_ms <= confirmed_ms <= asof_ms:
                continue
            if event_id in result:
                continue
            if _terminal(event):
                result[event_id] = event
                continue
            event.update(event_id=event_id, symbol=symbol, confirmed_ms=confirmed_ms, candle_ms=candle_ms,
                         a_ms=a_ms, a_price=numeric["a_price"], b_ms=b_ms,
                         b_price=numeric["b_price"], level=numeric["level"],
                         feed_asof_ms=asof_ms, feed_updated_ms=updated_ms)
            result[event_id] = verify_event_validity(event, hourly, asof_ms, resolve_hour=self._resolve_hour)
        return sorted(result.values(), key=lambda item: (-item["confirmed_ms"], item["symbol"], item["event_id"]))

    def _sorted_events(self, by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
                       cutoff_ms: Optional[int] = None) -> list[dict[str, Any]]:
        cutoff = self._current_cutoff_ms() if cutoff_ms is None else int(cutoff_ms)
        rows: list[dict[str, Any]] = []
        for values in by_symbol.values():
            for item in values:
                if item.get("validity_schema") != VALIDITY_SCHEMA or item.get("validity_status") != "valid":
                    continue
                try:
                    candle_ms = int(item["candle_ms"])
                    confirmed_ms = int(item["confirmed_ms"])
                    checked_ms = int(item["validity_checked_ms"])
                    low = float(item["prebreak_low"])
                    reason = item.get("validity_reason")
                    locked = (reason == "rally_target_reached"
                              and confirmed_ms < int(item["rally_confirmed_ms"]) <= checked_ms)
                    verified = ((locked or checked_ms >= cutoff)
                                and cutoff - self.lookback_days * 24 * BAR_MS["1H"] <= confirmed_ms <= cutoff
                                and confirmed_ms == candle_ms + BAR_MS["1H"]
                                and confirmed_ms <= checked_ms
                                and int(item["prebreak_low_bars"]) == PREBREAK_LOW_BARS
                                and math.isfinite(low) and low > 0
                                and (locked or reason in {"validated", "intrabar_order_pending"})
                                and int(item["prebreak_low_end_ms"]) == candle_ms
                                and int(item["prebreak_low_start_ms"]) ==
                                    candle_ms - (PREBREAK_LOW_BARS - 1) * BAR_MS["1H"])
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
                if verified:
                    rows.append(dict(item))
        rows.sort(key=lambda item: (-int(item["confirmed_ms"]), str(item["symbol"]), str(item["event_id"])))
        return rows

    def _merge_event_history(self, symbol: str, fresh: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Retain terminal decisions from this version, in either direction."""
        previous = {str(item.get("event_id")): dict(item)
                    for item in self._events_by_symbol.get(symbol, ())}
        merged: dict[str, dict[str, Any]] = {}
        for source in fresh:
            event = dict(source)
            event_id = str(event["event_id"])
            prior = previous.pop(event_id, None)
            if prior is not None and _terminal(prior):
                merged[event_id] = prior
            else:
                merged[event_id] = event
        for event_id, prior in previous.items():
            if _terminal(prior):
                merged[event_id] = prior
            else:
                prior.update(validity_status="unverified", validity_checked_ms=None,
                             validity_reason="not_emitted_in_current_scan")
                merged[event_id] = prior
        return sorted(merged.values(), key=lambda item: (-int(item.get("confirmed_ms", 0)),
                                                          str(item.get("symbol", symbol)),
                                                          str(item.get("event_id", ""))))

    def _evaluate_symbol(self, symbol: str, asof_ms: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        client = self._get_client()
        seed = self._load_seed(symbol, "4H")
        seed_1h = self._load_seed(symbol, "1H")
        cache = self._bars.setdefault(symbol, {})
        h4_previous = cache.get("4H")
        h4, h4_gap_count = self._load_or_update(symbol, "4H", asof_ms, FOUR_HOUR_LIMIT,
                                                h4_previous, seed=seed)
        h1_previous = cache.get("1H")
        h1, h1_gap_count = self._load_or_update(symbol, "1H", asof_ms, ONE_HOUR_LIMIT,
                                                h1_previous, seed=seed_1h)
        h4 = _dedupe_rows(h4, BAR_MS["4H"])
        h1 = _dedupe_rows(h1, BAR_MS["1H"])
        h4 = [row for row in h4 if row["t"] + BAR_MS["4H"] <= asof_ms]
        h1 = [row for row in h1 if row["t"] + BAR_MS["1H"] <= asof_ms]
        cache["4H"] = _pack(h4, FOUR_HOUR_LIMIT)
        cache["1H"] = _pack(h1, ONE_HOUR_LIMIT)

        expected_h4 = (asof_ms // BAR_MS["4H"]) * BAR_MS["4H"] - BAR_MS["4H"]
        expected_h1 = (asof_ms // BAR_MS["1H"]) * BAR_MS["1H"] - BAR_MS["1H"]
        h4_latest = h4[-1]["t"] if h4 else None
        h1_latest = h1[-1]["t"] if h1 else None
        if not h4:
            raise RuntimeError("no_confirmed_4h_data")
        if not h1:
            raise RuntimeError("no_confirmed_1h_data")
        cached_coverage = self._symbol_state.get(symbol, {})
        if (cached_coverage.get("computed_asof_ms") == asof_ms
                and cached_coverage.get("h4_latest_ms") == h4[-1]["t"]
                and cached_coverage.get("h1_latest_ms") == h1[-1]["t"]
                and symbol in self._events_by_symbol):
            return self._events_by_symbol[symbol], cached_coverage
        event_fn = self._get_event_fn()
        computed = event_fn(symbol, _rule_bars(h4), _rule_bars(h1), asof_ms)
        now = client.clock()
        normalized = self._normalize_events(symbol, computed, h1, asof_ms, now)
        start_ms = asof_ms - self.lookback_days * 24 * 60 * 60_000
        short_history = len(h1) < ONE_HOUR_LIMIT or len(h4) < min(FOUR_HOUR_LIMIT,
                         self.lookback_days * 6 + 360)
        coverage = {"h4_latest_ms": h4_latest, "h1_latest_ms": h1_latest,
                    "h4_rows": len(h4), "h1_rows": len(h1),
                    "h4_gaps": h4_gap_count, "h1_gaps": h1_gap_count,
                    "h4_current": h4_latest == expected_h4, "h1_current": h1_latest == expected_h1,
                    "h1_first_ms": h1[0]["t"], "h4_first_ms": h4[0]["t"],
                    "lookback_start_ms": start_ms, "short_history": short_history,
                    "seed_4h": seed is not None, "seed_1h": seed_1h is not None,
                    "computed_asof_ms": asof_ms}
        return normalized, coverage

    def run_once(self, *, asof_ms: Optional[int] = None) -> dict[str, Any]:
        """Perform one serialized hourly scan; exposed for functional QA and controlled warmup."""
        client = self._get_client()
        if asof_ms is None:
            clock = int(client.clock())
            asof_ms = clock // BAR_MS["1H"] * BAR_MS["1H"]
        asof_ms = _as_int(asof_ms)
        with self.lock:
            self._cycle_asof_ms = asof_ms
            self._state = "warming"
            self._last_error = None
            self._progress = {"done": 0, "total": 0, "symbol": None, "stage": "universe", "percent": 0,
                              "asof_ms": asof_ms, "started_ms": now_ms()}
            self._coverage = self._empty_coverage()
        try:
            symbols = self._get_symbols()
        except Exception as error:
            with self.lock:
                self._state = "degraded" if self._items else "warming"
                self._last_error = "universe_unavailable"
                self._progress.update(stage="universe_error", finished_ms=now_ms())
            LOG.warning("Trendline feed could not read active OKX universe: %s", error)
            return self.snapshot()
        if not symbols:
            with self.lock:
                self._state = "warming"
                self._last_error = "universe_not_ready"
                self._progress.update(stage="waiting_for_universe", finished_ms=now_ms())
            return self.snapshot()

        prior_valid_symbols = {str(item.get("symbol"))
                               for values in self._events_by_symbol.values() for item in values
                               if item.get("validity_status") == "valid"}
        symbols = tuple(sorted(symbols, key=lambda symbol: symbol not in prior_valid_symbols))

        self._prune_inactive(symbols)

        lookback_start_ms = asof_ms - self.lookback_days * 24 * 60 * 60_000
        self._expire_events(lookback_start_ms)
        coverage = self._empty_coverage()
        coverage.update(symbols_total=len(symbols), lookback_start_ms=lookback_start_ms,
                        asof_ms=asof_ms, seed_symbols=0, covered=0)
        issues: list[dict[str, str]] = []
        short_symbols: list[str] = []
        done = 0
        update_started_ms = now_ms()
        with self.lock:
            self._state = "scanning"
            self._progress = {"done": 0, "total": len(symbols), "symbol": None, "stage": "loading",
                              "percent": 0, "asof_ms": asof_ms, "started_ms": update_started_ms}

        try:
            self._get_event_fn()
        except Exception as error:
            with self.lock:
                self._state = "warming" if not self._items else "degraded"
                self._last_error = "rules_unavailable"
                self._progress.update(stage="rules_unavailable", finished_ms=now_ms())
            LOG.warning("Trendline rule module is not ready: %s", error)
            return self.snapshot()

        for symbol in symbols:
            if self.stop.is_set():
                break
            done += 1
            with self.lock:
                self._progress.update(done=done, symbol=symbol, stage="loading_ohlc",
                                      percent=round(done * 100 / len(symbols), 1), updated_ms=now_ms())
            try:
                events, item_coverage = self._evaluate_symbol(symbol, asof_ms)
                events = self._merge_event_history(symbol, events)
                self._persist_symbol(symbol, events, item_coverage)
                with self.lock:
                    self._events_by_symbol[symbol] = events
                    self._symbol_state[symbol] = item_coverage
                    self._items = self._sorted_events(self._events_by_symbol, asof_ms)
                coverage["covered"] += 1
                coverage["seed_symbols"] += int(item_coverage["seed_4h"])
                coverage["seed_1h_symbols"] += int(item_coverage["seed_1h"])
                coverage["gap_symbols"] += int(bool(item_coverage["h4_gaps"] or item_coverage["h1_gaps"]))
                coverage["stale_4h"] += int(not item_coverage["h4_current"])
                coverage["stale_1h"] += int(not item_coverage["h1_current"])
                coverage["insufficient_history"] += int(item_coverage["short_history"])
                if item_coverage["h4_rows"] == 0:
                    coverage["missing_4h"] += 1
                if item_coverage["h1_rows"] == 0:
                    coverage["missing_1h"] += 1
                if item_coverage["short_history"]:
                    short_symbols.append(symbol)
                for key, oldest_key, value in (
                    ("latest_4h_close_ms", "oldest_covered_4h_close_ms", item_coverage["h4_latest_ms"] + BAR_MS["4H"]),
                    ("latest_1h_close_ms", "oldest_covered_1h_close_ms", item_coverage["h1_latest_ms"] + BAR_MS["1H"])):
                    prior_latest = coverage.get(key)
                    prior_oldest = coverage.get(oldest_key)
                    coverage[key] = value if prior_latest is None else max(prior_latest, value)
                    coverage[oldest_key] = value if prior_oldest is None else min(prior_oldest, value)
            except Exception as error:
                coverage["errors"] += 1
                error_code = self._error_code(error)
                issues.append({"symbol": symbol, "stage": "evaluate", "code": error_code})
                coverage["no_data"] += int(error_code in {"no_confirmed_4h_data", "no_confirmed_1h_data"})
                coverage["missing_4h"] += int(error_code == "no_confirmed_4h_data")
                coverage["missing_1h"] += int(error_code == "no_confirmed_1h_data")
                LOG.warning("Trendline feed failed for %s: %s", symbol, error)
            with self.lock:
                self._coverage = dict(coverage, issues=issues[:40], short_history_symbols=short_symbols[:40])
                self._progress.update(stage="loading_and_computing", done=done, updated_ms=now_ms())

        complete = (done == len(symbols) and coverage["covered"] == len(symbols)
                    and coverage["errors"] == 0 and coverage["gap_symbols"] == 0
                    and coverage["insufficient_history"] == 0
                    and coverage["stale_4h"] == 0 and coverage["stale_1h"] == 0)
        with self.lock:
            self._coverage = dict(coverage, issues=issues[:40], short_history_symbols=short_symbols[:40],
                                  elapsed_ms=now_ms() - update_started_ms)
            if complete:
                self._state = "ready"
                self._last_complete_asof_ms = asof_ms
                self._persist_complete(asof_ms)
            elif self._items:
                self._state = "partial" if coverage["errors"] < len(symbols) else "degraded"
            else:
                self._state = "degraded" if coverage["errors"] else "partial"
            self._last_error = "symbol_coverage_incomplete" if not complete else None
            self._last_scan_asof_ms = asof_ms
            self._progress.update(done=done, total=len(symbols), stage="complete" if complete else "partial",
                                  percent=round(done * 100 / len(symbols), 1) if symbols else 100,
                                  finished_ms=now_ms(), elapsed_ms=now_ms() - update_started_ms)
            self._persist_cycle(asof_ms, self._coverage)
        return self.snapshot()

    @staticmethod
    def _error_code(error: Exception) -> str:
        message = str(error).strip().lower().replace(" ", "_")
        if message and re.fullmatch(r"[a-z0-9_:-]{1,80}", message):
            return message
        return type(error).__name__.lower()

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        self.thread = threading.Thread(target=self._run, name="trendline-break-feed", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self._state = "stopped"
        with self.db_lock:
            if self.db is not None and (self.thread is None or not self.thread.is_alive()):
                self.db.close()
                self.db = None

    def _run(self) -> None:
        while not self.stop.is_set():
            try:
                result = self.run_once()
                stage = result.get("progress", {}).get("stage")
                clock = int(self._get_client().clock())
                if stage in {"universe_error", "waiting_for_universe", "rules_unavailable"}:
                    wait_ms = 15_000
                else:
                    next_hour = (clock // BAR_MS["1H"] + 1) * BAR_MS["1H"] + 2_000
                    wait_ms = max(1_000, next_hour - clock)
            except Exception as error:
                LOG.warning("Trendline feed cycle failed: %s", error, exc_info=True)
                with self.lock:
                    self._state = "degraded" if self._items else "warming"
                    self._last_error = self._error_code(error)
                clock = int(self._get_client().clock())
                next_hour = (clock // BAR_MS["1H"] + 1) * BAR_MS["1H"] + 2_000
                wait_ms = max(1_000, next_hour - clock)
            self.stop.wait(wait_ms / 1000)

    def snapshot(self, *, limit: Optional[int] = None, offset: int = 0) -> dict[str, Any]:
        if offset < 0:
            raise ValueError("offset must be nonnegative")
        if limit is not None and not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        cutoff_ms = self._current_cutoff_ms()
        with self.lock:
            all_items = self._sorted_events(self._events_by_symbol, cutoff_ms)
            total = len(all_items)
            end = total if limit is None else min(total, offset + limit)
            items = [dict(item) for item in all_items[offset:end]]
            has_more = end < total
            coverage = dict(self._coverage)
            progress = dict(self._progress)
            state = self._state
            asof_ms = (self._cycle_asof_ms if self._cycle_asof_ms is not None else
                       self._last_scan_asof_ms if self._last_scan_asof_ms is not None else self._last_complete_asof_ms)
            last_complete = self._last_complete_asof_ms
            last_error = self._last_error
        return {"items": items, "total": total, "offset": offset, "limit": limit,
                "next_offset": end if has_more else None, "has_more": has_more,
                "state": state, "coverage": coverage, "progress": progress,
                "asof_ms": asof_ms, "last_complete_asof_ms": last_complete,
                "last_error": last_error, "lookback_days": self.lookback_days}


def install(app: FastAPI, feed: TrendlineBreakFeed) -> None:
    @app.get("/api/trendline-breaks")
    def trendline_breaks(limit: Optional[int] = Query(None, ge=1, le=MAX_PAGE_SIZE),
                         offset: int = Query(0, ge=0)):
        # By default serve the full 30-day result. Larger lists can be read in
        # bounded pages; the worker owns every fetch and event calculation.
        return feed.snapshot(limit=limit, offset=offset)

