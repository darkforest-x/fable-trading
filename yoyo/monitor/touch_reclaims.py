"""Asynchronous, read-only 1H/4H/12H reclaim projection for touch ledgers.

Touch records stay immutable in their source books. This projection reads the
first matching candle whose close follows the recorded touch time, using the
shared chart history reader and its persistent CandleHub cache. Missing history
is never interpreted as a negative reclaim.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import re
import threading
import time
from typing import Literal, Optional

from fastapi import HTTPException, Query

from yoyo.data.candle_hub import validate
from .chart_periods import native_bar, open_time, step_ms, candles as display_candles
from .month_first_week_1618 import SYMBOL_RE, month_window
from .monday_fib import week_window

PERIODS = ("1H", "4H", "12H")
DEFAULT_LIMIT = 1000
MAX_LIMIT = 2000
FAILURE_RETRY_MS = 30_000
MAX_CACHED_BARS = 20_000
MAX_TARGET_RANGE_BARS = 36
MAX_CANDLE_READ_BARS = 300
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class TouchReclaimReader:
    """Batch closed-candle reads by symbol and period without blocking HTTP."""

    def __init__(self, history, *, max_workers=2):
        self.history = history
        self.lock = threading.RLock()
        self.idle = threading.Condition(self.lock)
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="touch-reclaims")
        self.bars = OrderedDict()  # (symbol,period,open_ms) -> confirmed display candle
        self.failures = {}        # target key -> (retry_ms, reason)
        self.pending = {}         # (symbol,period) -> set[open_ms]
        self.active_targets = {}  # (symbol,period) -> targets currently being read
        self.running = set()      # (symbol,period)
        self.closed = False

    def close(self):
        with self.lock:
            self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)

    def wait_for_idle(self, timeout=5.0):
        """Wait for current reads; primarily useful to deterministic tests."""
        deadline = time.monotonic() + timeout
        with self.idle:
            while self.running:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.idle.wait(remaining)
            return True

    @staticmethod
    def _target(event, period):
        """Return target bar timestamps from the event's recorded touch time.

        The originating 1m candle is authoritative for both live and recovered
        records. In recovered history it is the source H1 observation; in live
        records it avoids shifting a delayed WebSocket observation into the next
        H1 candle. The target close is always strictly after that timestamp.
        """
        # candle_ms identifies the actual 1m touch candle. `occurred_ms` /
        # `observed_ms` can be received at the next boundary after a delayed
        # WebSocket update, so prefer the originating candle whenever present.
        touch_ms = event.get("candle_ms")
        if type(touch_ms) is not int or touch_ms <= 0:
            touch_ms = event.get("occurred_ms", event.get("observed_ms"))
        if type(touch_ms) is not int or touch_ms <= 0:
            return None
        bar_ms = open_time(touch_ms, period)
        close_ms = bar_ms + step_ms(period)
        if close_ms <= touch_ms:
            bar_ms = open_time(touch_ms + 1, period)
            close_ms = bar_ms + step_ms(period)
        return bar_ms, close_ms

    def project(self, events, now_ms):
        """Add three non-persisted outcomes to a page of touch facts."""
        results = []
        requested = {}
        targets_by_event = {}
        for event in events:
            event_id = event["event_id"]
            targets = {}
            for period in PERIODS:
                target = self._target(event, period)
                if target is None:
                    targets[period] = None
                    continue
                bar_ms, close_ms = target
                targets[period] = target
                if close_ms <= now_ms:
                    group = (event["symbol"], period)
                    requested.setdefault(group, set()).add(bar_ms)
            targets_by_event[event_id] = targets

        for group, target_opens in requested.items():
            self._queue(group, target_opens, now_ms)

        for event in events:
            reclaims = {}
            for period in PERIODS:
                target = targets_by_event[event["event_id"]].get(period)
                if target is None:
                    reclaims[period] = {"bar": period, "status": "unavailable",
                                        "close_ms": None, "close": None,
                                        "reason": "touch_time_unavailable", "source": "OKX"}
                    continue
                bar_ms, close_ms = target
                row = self._result(event, period, bar_ms, close_ms, now_ms)
                reclaims[period] = row
            results.append({**event, "reclaims": reclaims})
        return results

    def _queue(self, group, target_opens, now_ms):
        with self.lock:
            if self.closed:
                return
            missing = set()
            active = self.active_targets.get(group, set())
            for bar_ms in target_opens:
                key = (*group, bar_ms)
                if key in self.bars or bar_ms in active:
                    continue
                failure = self.failures.get(key)
                if failure and now_ms < failure[0]:
                    continue
                missing.add(bar_ms)
            if not missing:
                return
            self.pending.setdefault(group, set()).update(missing)
            if group in self.running:
                return
            self.running.add(group)
            self.pool.submit(self._drain, group)

    def _drain(self, group):
        while True:
            with self.lock:
                targets = self.pending.pop(group, set())
                if not targets:
                    self.running.discard(group)
                    self.idle.notify_all()
                    return
                self.active_targets[group] = set(targets)
            try:
                found, failures = self._read_targets(group, targets)
            except Exception:
                found, failures = {}, {stamp: "history_read_failed" for stamp in targets}
            with self.lock:
                now_ms = self.history.candles.client.clock()
                self.active_targets.pop(group, None)
                for bar_ms in targets:
                    key = (*group, bar_ms)
                    candle = found.get(bar_ms)
                    if candle is not None:
                        self.bars[key] = candle
                        self.bars.move_to_end(key)
                        self.failures.pop(key, None)
                    else:
                        self.failures[key] = (now_ms + FAILURE_RETRY_MS,
                                              failures.get(bar_ms, "closed_candle_missing"))
                while len(self.bars) > MAX_CACHED_BARS:
                    self.bars.popitem(last=False)

    def _read_targets(self, group, targets):
        """Read exact target ranges through the shared client/CandleHub cache."""
        symbol, period = group
        client = self.history.candles.client
        now_ms = client.clock()
        bar = native_bar(period)
        step = step_ms(period)
        targets = sorted(stamp for stamp in set(targets) if stamp + step <= now_ms)
        found = {}
        failures = {}
        if not targets:
            return found, failures

        # Cluster nearby event targets so one symbol/period reads each short
        # interval once. Long gaps become separate exact CandleHub ranges.
        clusters = []
        cluster = [targets[0]]
        for stamp in targets[1:]:
            if (stamp - cluster[0]) // step + 1 <= MAX_TARGET_RANGE_BARS:
                cluster.append(stamp)
            else:
                clusters.append(cluster)
                cluster = [stamp]
        clusters.append(cluster)

        for cluster in clusters:
            first, last = cluster[0], cluster[-1]
            count = min(MAX_CANDLE_READ_BARS, (last - first) // step + 1)
            try:
                raw = client.get("/api/v5/market/history-candles", {
                    "instId": symbol, "bar": bar, "limit": str(count),
                    # OKX's after cursor is exclusive; last+step includes `last`.
                    "after": str(last + step),
                })
                parsed = validate(raw, bar, now_ms)
                confirmed = {t: row for t, row in parsed.items()
                             if first <= t <= last and row[8] == "1" and t + step <= now_ms}
                candles = display_candles(confirmed, period)
                by_time = {row["time"]: row for row in candles}
                for stamp in cluster:
                    candle = by_time.get(stamp)
                    if candle is None:
                        failures[stamp] = "closed_candle_missing"
                    else:
                        found[stamp] = candle
            except Exception:
                for stamp in cluster:
                    failures[stamp] = "history_read_failed"
        return found, failures

    def _result(self, event, period, bar_ms, close_ms, now_ms):
        base = {"bar": period, "bar_open_ms": bar_ms, "close_ms": close_ms,
                "close": None, "source": "OKX"}
        if close_ms > now_ms:
            return {**base, "status": "pending", "reason": "candle_not_closed"}
        key = (event["symbol"], period, bar_ms)
        with self.lock:
            candle = self.bars.get(key)
            failure = self.failures.get(key)
            in_flight = ((event["symbol"], period) in self.running
                         or bar_ms in self.pending.get((event["symbol"], period), set()))
        if candle is not None:
            close = float(candle["close"])
            level = float(event["level"])
            reclaimed = close < level if event["side"] == "upper" else close > level
            return {**base, "close": close,
                    "status": "reclaimed" if reclaimed else "not_reclaimed",
                    "reason": None}
        if failure and now_ms < failure[0]:
            return {**base, "status": "unavailable", "reason": failure[1]}
        reason = "history_fetch_pending" if in_flight else "history_fetch_queued"
        return {**base, "status": "pending", "reason": reason}


def _event_id(kind, row):
    if kind == "monday":
        return f"{row['week']}|{row['symbol']}|{row['side']}"
    return f"{row['month']}|{row['symbol']}|{row['side']}"


def install(app, *, monday_book, month_book, history, owner=None):
    """Install the shared touch outcome API for Monday and month-first-week."""
    reader = TouchReclaimReader(history)
    app.state.touch_reclaim_reader = reader
    if owner is not None:
        owner.touch_reclaim_reader = reader

    @app.get("/api/touch-reclaims")
    def touch_reclaims(kind: Literal["monday", "month_first_week"],
                       week: Optional[str] = Query(None, max_length=10),
                       month: Optional[str] = Query(None, max_length=7),
                       limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
                       after: Optional[str] = Query(None, min_length=1, max_length=96)):
        now_ms = history.candles.client.clock()
        if kind == "monday":
            if month is not None:
                raise HTTPException(422, "周一触价接口不能同时传 month")
            if week is None:
                week_value = week_window(now_ms)["week"]
            else:
                try:
                    parsed_week = date.fromisoformat(week)
                except ValueError:
                    raise HTTPException(422, "week 必须是 YYYY-MM-DD") from None
                if parsed_week.isoformat() != week or parsed_week.weekday() != 0:
                    raise HTTPException(422, "week 必须是纽约时区周一日期")
                week_value = week
            records = monday_book.records(week_value)
            period_key = "week"
            period_value = week_value
        else:
            if week is not None:
                raise HTTPException(422, "月首周触价接口不能同时传 week")
            if month is None:
                month_value = month_window(now_ms)["month"]
            elif MONTH_RE.fullmatch(month):
                month_value = month
            else:
                raise HTTPException(422, "month 必须是 YYYY-MM")
            records = month_book.events(month_value)
            period_key = "month"
            period_value = month_value

        rows = []
        for raw in records:
            row = dict(raw)
            if not SYMBOL_RE.fullmatch(str(row.get("symbol", ""))) or row.get("side") not in {"upper", "lower"}:
                continue
            row["event_id"] = _event_id(kind, row)
            if after is None or row["event_id"] > after:
                rows.append(row)
        rows.sort(key=lambda row: row["event_id"])
        page = rows[:limit]
        items = reader.project(page, now_ms)
        has_more = len(rows) > len(page)
        return {"schema": "touch_reclaims.v1", "kind": kind, period_key: period_value,
                "period": period_value, "now_ms": now_ms, "limit": limit,
                "count": len(items), "has_more": has_more, "truncated": has_more,
                "next_cursor": page[-1]["event_id"] if has_more and page else None,
                "items": items}

    return reader


__all__ = ["TouchReclaimReader", "install"]
