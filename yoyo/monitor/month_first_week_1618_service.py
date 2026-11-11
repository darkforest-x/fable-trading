"""Live adapter and loopback API for the monthly first-week 1.618 monitor.

The adapter is a listener on the established MondayFibService candle1m stream.
Its bounded H1 catch-up uses the same OKX client/CandleHub and background
request budget. WebSocket callbacks only validate/cache/enqueue in memory; a
worker batches durable writes away from the existing market stream thread.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode
import json
import math
import threading
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, StrictBool
import requests

from yoyo.data.candle_hub import validate
from .month_first_week_1618 import (EVENT_TYPE, FRESH_MS, HOUR_MS, MINUTE_MS,
                                    MonthFirstWeekBook, closed_hour_touches,
                                    make_anchor, month_window)
from .telegram import credentials as telegram_credentials
from .monday_fib_bark import configured_credentials as bark_credentials

BEIJING = ZoneInfo("Asia/Shanghai")
HISTORY_LIMIT = 1000  # 31 calendar days plus DST; OKX pages are still bounded to 5 x 300.
MINUTE_HISTORY_LIMIT = 70  # Current NY-independent UTC hour only, targeted per reconnecting symbol.
ANCHOR_RETRY_MS = 5 * 60_000
WORKER_INTERVAL_SECONDS = 1.0
MAX_SYMBOLS_PER_STEP = 5
MINUTE_RING_MS = 2 * HOUR_MS


def _clock(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(BEIJING).strftime("%m-%d %H:%M:%S")


def _valid_quote(quote):
    try:
        candle_ms = int(quote["candle_ms"])
        received_ms = int(quote["received_ms"])
        high, low, close = float(quote["high"]), float(quote["low"]), float(quote["price"])
        confirmed = quote.get("candle_confirmed", False)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if (not all(math.isfinite(x) and x > 0 for x in (high, low, close)) or high < low
            or received_ms < candle_ms or received_ms - candle_ms > 2 * MINUTE_MS
            or type(confirmed) is not bool):
        return None
    return {"candle_ms": candle_ms, "received_ms": received_ms, "high": high,
            "low": low, "price": close, "candle_confirmed": confirmed}


def _first_line(event):
    symbol = event["symbol"].removesuffix("-USDT-SWAP")
    side = "上沿" if event["side"] == "upper" else "下沿"
    return f"月首周 1.618 · {symbol} {side}触价"


def _event_text(event):
    return (f"{_first_line(event)}\n"
            f"纽约首周 {event['month']} · 点位 {event['level']:.10g}\n"
            f"当前分钟极值 {event['candle_extreme']:.10g}\n"
            f"{_clock(event['occurred_ms'])} 北京时间\n"
            "仅为触价提醒，不代表交易或下单")


class MonthFirstWeek1618Service:
    def __init__(self, runtime, *, quote_source, client=None, sender=None):
        self.book = MonthFirstWeekBook(runtime)
        self.quote_source = quote_source
        self.client = client or quote_source.client
        self.include_open = getattr(self.client, "include_open", False) is True
        self.sender = sender or requests.post
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.thread = None
        self.send_threads = {}
        self.last_cycle_ms = 0
        self.error = None
        self._retry_at = {}
        self._reconcile = set()
        self._anchors = {}
        self._consumed = set()
        self._quote_watermarks = {}
        self._reconcile_generation = {}
        self._live_after_by_symbol = {}
        self._subscribed_ms = 0
        self._pending_watermarks = {}
        self._pending_anchors = {}
        self._pending_events = {}
        self._minute_bars = {}
        self._latest_quotes = {}
        self._active_month = None
        self._closed = False

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self._closed = False
        self.stop.clear()
        self.book.recover()
        now_ms = self.client.clock()
        self._subscribed_ms = now_ms
        self._live_after_by_symbol.clear()
        self._load_month(month_window(now_ms), recovering=True)
        with self.quote_source.lock:
            if self not in self.quote_source.listeners:
                self.quote_source.listeners.append(self)
        self.thread = threading.Thread(target=self._run, name="month-first-week-1618", daemon=True)
        self.thread.start()
        for channel in ("telegram", "bark"):
            worker = threading.Thread(target=self._deliver_loop, args=(channel,),
                                      name="month-first-week-1618-" + channel, daemon=True)
            self.send_threads[channel] = worker
            worker.start()

    def close(self):
        self._closed = True
        self.stop.set()
        with self.quote_source.lock:
            if self in self.quote_source.listeners:
                self.quote_source.listeners.remove(self)
        if self.thread:
            self.thread.join(timeout=3)
        for worker in self.send_threads.values():
            worker.join(timeout=3)

    def _load_month(self, window, *, recovering=False):
        loaded = self.book.load_state(window["month"])
        with self.lock:
            self._active_month = window["month"]
            self._anchors = {row["symbol"]: dict(row) for row in loaded["anchors"]}
            self._consumed = {row["event_id"] for row in loaded["touches"]}
            self._quote_watermarks = {row["symbol"]: dict(row) for row in loaded["quote_cursors"]}
            self._pending_anchors.clear()
            self._pending_events.clear()
            self._pending_watermarks.clear()
            self._reconcile.clear()
            self._retry_at.clear()
            self._reconcile_generation.clear()
            if recovering:
                for symbol, anchor in self._anchors.items():
                    if anchor.get("high") is not None:
                        anchor.update(state="recovering", coverage_complete=False, armed_sides=[])
                        self._pending_anchors[symbol] = dict(anchor)
                        self._reconcile.add(symbol)

    def on_touch(self, *args, **kwargs):
        # The existing Monday-first-touch ledger is unrelated. Only its
        # validated candle1m quote stream feeds this calendar-month strategy.
        return None

    def on_quote(self, symbol, quote):
        """Keep callbacks CPU-only: update rolling minute state and queue facts."""
        if not isinstance(symbol, str):
            return
        value = _valid_quote(quote)
        if value is None:
            return
        try:
            now_ms = self.client.clock()
        except Exception:
            return
        if not 0 <= now_ms - value["received_ms"] <= 2 * MINUTE_MS:
            return
        window = month_window(value["received_ms"])
        stamp = value["candle_ms"]
        with self.lock:
            if window["month"] != self._active_month:
                return
            prior_quote = self._latest_quotes.get(symbol)
            if prior_quote and (stamp < prior_quote["candle_ms"]
                                or value["received_ms"] < prior_quote["received_ms"]):
                return
            buffer = self._minute_bars.setdefault(symbol, {})
            current = buffer.get(stamp)
            if current:
                value = {**value, "high": max(current["high"], value["high"]),
                         "low": min(current["low"], value["low"]),
                         "candle_confirmed": current["candle_confirmed"] or value["candle_confirmed"]}
            buffer[stamp] = value
            cutoff = value["received_ms"] - MINUTE_RING_MS
            for old_stamp in [key for key in buffer if key < cutoff]:
                del buffer[old_stamp]
            self._latest_quotes[symbol] = value

            previous = self._quote_watermarks.get(symbol)
            self._live_after_by_symbol.setdefault(
                symbol, (self._subscribed_ms // MINUTE_MS + 1) * MINUTE_MS)
            gap = (previous is None or stamp > previous["candle_ms"] + MINUTE_MS
                   or value["received_ms"] - previous["received_ms"] > FRESH_MS)
            if previous is None or stamp > previous["candle_ms"] or gap:
                cursor = {"symbol": symbol, "candle_ms": stamp, "received_ms": value["received_ms"]}
                self._quote_watermarks[symbol] = cursor
                self._pending_watermarks[symbol] = cursor
            anchor = self._anchors.get(symbol)
            if gap and anchor and anchor.get("high") is not None:
                self._reconcile_generation[symbol] = self._reconcile_generation.get(symbol, 0) + 1
                self._live_after_by_symbol[symbol] = (value["received_ms"] // MINUTE_MS + 1) * MINUTE_MS
                anchor = dict(anchor, state="recovering", coverage_complete=False, armed_sides=[])
                self._anchors[symbol] = anchor
                self._pending_anchors[symbol] = dict(anchor)
                self._reconcile.add(symbol)
                self._retry_at[symbol] = 0
            if not anchor or anchor.get("state") != "ready" or not anchor.get("coverage_complete"):
                return
            self._enqueue_live_locked(anchor, symbol, value, now_ms)

    def _enqueue_live_locked(self, anchor, symbol, quote, now_ms):
        stamp, received = quote["candle_ms"], quote["received_ms"]
        if not anchor["ready_ms"] <= stamp < anchor["expires_ms"]:
            return
        if not received <= now_ms or now_ms - received > FRESH_MS:
            return
        armed = set(anchor.get("armed_sides", []))
        touched = []
        for side, hit, extreme in (("upper", quote["high"] >= anchor["upper"], quote["high"]),
                                    ("lower", quote["low"] <= anchor["lower"], quote["low"])):
            event_id = f"{anchor['month']}|{symbol}|{side}"
            if hit and side in armed and event_id not in self._consumed:
                event = {"event_id": event_id, "event_type": EVENT_TYPE, "month": anchor["month"],
                         "symbol": symbol, "side": side, "level": anchor[side], "price": anchor[side],
                         "candle_extreme": extreme, "occurred_ms": received, "candle_ms": stamp,
                         "created_ms": now_ms, "expires_ms": anchor["expires_ms"], "source": "live",
                         "touch_confirmed": True, "candle_confirmed": quote["candle_confirmed"]}
                self._pending_events[event_id] = event
                self._consumed.add(event_id)
                armed.discard(side)
                touched.append(side)
        if touched:
            anchor = dict(anchor, armed_sides=sorted(armed))
            self._anchors[symbol] = anchor
            self._pending_anchors[symbol] = dict(anchor)

    def _minute_buffer(self, symbol, window_start, current_hour_start, now_ms):
        """Return current-hour 1m coverage merged from REST and the shared WS tail."""
        if not self.include_open:
            raise RuntimeError("current_minute_source_excludes_open_bar")
        raw = self.client.get("/api/v5/market/candles", {
            "instId": symbol, "bar": "1m", "limit": str(MINUTE_HISTORY_LIMIT)})
        parsed = validate(raw, "1m", now_ms)
        minute_open = now_ms // MINUTE_MS * MINUTE_MS
        result = {}
        for stamp, row in parsed.items():
            if current_hour_start <= stamp <= minute_open:
                result[stamp] = {"t": stamp, "h": float(row[2]), "l": float(row[3]),
                                 "confirm": str(row[8]) == "1", "received_ms": None}
        with self.lock:
            buffered = dict(self._minute_bars.get(symbol, {}))
        for stamp, bar in buffered.items():
            if current_hour_start <= stamp <= minute_open:
                item = result.setdefault(stamp, {"t": stamp, "h": bar["high"], "l": bar["low"],
                                                  "confirm": bar["candle_confirmed"],
                                                  "received_ms": bar["received_ms"]})
                item["h"] = max(item["h"], bar["high"])
                item["l"] = min(item["l"], bar["low"])
                item["confirm"] = item["confirm"] or bar["candle_confirmed"]
                item["received_ms"] = bar["received_ms"]
        expected = list(range(current_hour_start, minute_open + MINUTE_MS, MINUTE_MS))
        missing = [stamp for stamp in expected if stamp not in result]
        return [result[stamp] for stamp in expected if stamp in result], missing

    def _build_reconciliation(self, symbol, window, now_ms):
        now_ms = self.client.clock()
        window = month_window(now_ms)
        needed = max(window["expected_1h_bars"],
                     (now_ms // HOUR_MS * HOUR_MS - window["start_ms"]) // HOUR_MS)
        rows, source_gaps = self.client.candles(symbol, "1H", limit=min(HISTORY_LIMIT, max(1, needed)))
        anchor = make_anchor(rows, window, now_ms)
        old = self._anchors.get(symbol)
        if anchor["state"] != "ready":
            if old and old.get("high") is not None:
                anchor.update({k: old[k] for k in ("high", "low", "upper", "lower")})
                anchor["state"] = "recovering"
                anchor["history_gaps"] = max(int(old.get("history_gaps") or 0), int(source_gaps or 0))
            anchor.update(symbol=symbol, armed_sides=[], coverage_complete=False,
                          recovery_error="first_week_hours_missing")
            return anchor, []

        current_hour_start = now_ms // HOUR_MS * HOUR_MS
        coverage_end = min(anchor["expires_ms"], current_hour_start)
        expected_after = range(anchor["ready_ms"], coverage_end, HOUR_MS)
        row_times = {int(row["t"]) for row in rows if anchor["ready_ms"] <= int(row["t"]) < coverage_end}
        missing_hours = [stamp for stamp in expected_after if stamp not in row_times]
        if missing_hours:
            anchor.update(symbol=symbol, state="coverage_incomplete", missing_hours=len(missing_hours),
                          history_gaps=len(missing_hours), coverage_complete=False, armed_sides=[],
                          first_missing_ms=missing_hours[0], last_missing_ms=missing_hours[-1],
                          recovery_error="closed_hour_history_gap")
            return anchor, []

        # Closed H1 rows after ready are authoritative recovery facts. If any
        # expected bar is absent, none of these may consume a first touch.
        recovered = []
        consumed_sides = set()
        for item in closed_hour_touches(rows, anchor, now_ms):
            consumed_sides.add(item["side"])
            recovered.append({"event_id": f"{anchor['month']}|{symbol}|{item['side']}",
                              "event_type": EVENT_TYPE, "month": anchor["month"], "symbol": symbol,
                              "side": item["side"], "level": anchor[item["side"]], "price": item["price"],
                              "candle_extreme": item["candle_extreme"], "occurred_ms": item["occurred_ms"],
                              "candle_ms": item["candle_ms"], "created_ms": now_ms,
                              "expires_ms": anchor["expires_ms"], "source": "history",
                              "touch_confirmed": True, "candle_confirmed": True})

        minutes, missing_minutes = self._minute_buffer(symbol, anchor["ready_ms"], current_hour_start, now_ms)
        if missing_minutes:
            anchor.update(symbol=symbol, state="recovering", missing_hours=0,
                          history_gaps=len(missing_minutes), coverage_complete=False, armed_sides=[],
                          recovery_error="current_hour_minute_prefix_gap",
                          first_missing_ms=missing_minutes[0], last_missing_ms=missing_minutes[-1])
            return anchor, recovered

        for side, is_upper in (("upper", True), ("lower", False)):
            if side in consumed_sides:
                continue
            candidate = None
            for row in minutes:
                hit = row["h"] >= anchor["upper"] if is_upper else row["l"] <= anchor["lower"]
                if hit:
                    candidate = row
                    break
            if candidate is None:
                continue
            ws_value = None
            with self.lock:
                ws_value = self._minute_bars.get(symbol, {}).get(candidate["t"])
            live_received = ws_value.get("received_ms") if ws_value else None
            is_fresh_live = (ws_value is not None and live_received is not None
                             and 0 <= now_ms - live_received <= FRESH_MS
                             and candidate["t"] >= self._live_after_by_symbol.get(symbol, self._subscribed_ms + 2 * MINUTE_MS))
            source = "live" if is_fresh_live else "history"
            occurred = int(live_received if is_fresh_live else candidate["t"])
            recovered.append({"event_id": f"{anchor['month']}|{symbol}|{side}",
                              "event_type": EVENT_TYPE, "month": anchor["month"], "symbol": symbol,
                              "side": side, "level": anchor[side], "price": anchor[side],
                              "candle_extreme": candidate["h"] if is_upper else candidate["l"],
                              "occurred_ms": occurred, "candle_ms": candidate["t"],
                              "created_ms": now_ms, "expires_ms": anchor["expires_ms"],
                              "source": source, "touch_confirmed": True,
                              "candle_confirmed": bool(candidate["confirm"])})
            consumed_sides.add(side)

        armed = sorted({"upper", "lower"} - consumed_sides)
        anchor.update(symbol=symbol, state="ready", missing_hours=0, history_gaps=0,
                      coverage_complete=True, armed_sides=armed, recovery_error=None)
        return anchor, recovered

    def _flush_pending(self, now_ms):
        with self.lock:
            anchors = [dict(row) for row in self._pending_anchors.values()]
            touches = [dict(row) for row in self._pending_events.values()]
            cursors = [dict(row) for row in self._pending_watermarks.values()]
        inserted = self.book.commit_cycle(anchors=anchors, touches=touches,
                                          quote_cursors=cursors, now_ms=now_ms)
        with self.lock:
            for row in anchors:
                current = self._pending_anchors.get(row["symbol"])
                if current and current.get("state") == row.get("state") and current.get("armed_sides") == row.get("armed_sides"):
                    self._pending_anchors.pop(row["symbol"], None)
            for row in touches:
                if self._pending_events.get(row["event_id"]) == row:
                    self._pending_events.pop(row["event_id"], None)
            for row in cursors:
                if self._pending_watermarks.get(row["symbol"]) == row:
                    self._pending_watermarks.pop(row["symbol"], None)
            self._consumed.update(row["event_id"] for row in inserted)
        return inserted

    def step(self, now_ms=None, *, max_symbols=MAX_SYMBOLS_PER_STEP, deliver=False):
        """Run one bounded batch. ``deliver`` exists for mocked focused QA only."""
        now_ms = self.client.clock() if now_ms is None else int(now_ms)
        window = month_window(now_ms)
        if window["month"] != self._active_month:
            self._load_month(window)
        try:
            symbols = tuple(self.quote_source.symbols)
            processed = 0
            updates, recovered_events = [], []
            succeeded, failed = [], []
            captured_generation = {}
            if window["complete"]:
                with self.lock:
                    forced = set(self._reconcile)
                    anchors = dict(self._anchors)
                todo = []
                for symbol in symbols:
                    anchor = anchors.get(symbol)
                    retry_due = self._retry_at.get(symbol, 0) <= now_ms
                    needs_work = symbol in forced or anchor is None or anchor.get("state") != "ready"
                    if needs_work and retry_due:
                        todo.append(symbol)
                todo.sort(key=lambda symbol: (symbol not in forced, symbol))
                with self.lock:
                    captured_generation = {symbol: self._reconcile_generation.get(symbol, 0) for symbol in todo}
                for symbol in todo[:max(1, int(max_symbols))]:
                    if self.stop.is_set():
                        break
                    try:
                        anchor, events = self._build_reconciliation(symbol, window, now_ms)
                        updates.append(anchor)
                        recovered_events.extend(events)
                        succeeded.append((symbol, anchor, captured_generation.get(symbol, 0)))
                        retry_delay = (30_000 if anchor.get("recovery_error") == "current_hour_minute_prefix_gap"
                                       else ANCHOR_RETRY_MS)
                        self._retry_at[symbol] = now_ms + retry_delay
                        processed += 1
                    except Exception as exc:
                        old = anchors.get(symbol)
                        stale = dict(old or window, symbol=symbol, state="recovering", coverage_complete=False,
                                     armed_sides=[], recovery_error=type(exc).__name__)
                        if old is None:
                            stale.update(high=None, low=None, upper=None, lower=None,
                                         missing_hours=0, history_gaps=0)
                        updates.append(stale)
                        succeeded.append((symbol, stale, captured_generation.get(symbol, 0)))
                        self._retry_at[symbol] = now_ms + ANCHOR_RETRY_MS
                        failed.append((symbol, type(exc).__name__))
            cycle_error = failed[0][1] if failed else None
            # Apply a fetched result only if no newer stream gap arrived while
            # its network reads were in flight. Forced work still respects the
            # per-symbol retry time; a newly observed gap clears that backoff.
            recovered_by_symbol = {}
            for event in recovered_events:
                recovered_by_symbol.setdefault(event["symbol"], []).append(event)
            ready_to_publish = []
            stale_updates = []
            stale_symbols = set()
            with self.lock:
                for symbol, anchor, generation in succeeded:
                    if self._reconcile_generation.get(symbol, 0) != generation:
                        stale_symbols.add(symbol)
                        current = self._anchors.get(symbol)
                        if current:
                            stale_updates.append(dict(current, state="recovering", coverage_complete=False,
                                                     armed_sides=[]))
                        self._retry_at[symbol] = now_ms + 1000
                        continue
                    ready_to_publish.append((symbol, anchor))
                    self._anchors[symbol] = dict(anchor)
                    self._pending_anchors[symbol] = dict(anchor)
                    for event in recovered_by_symbol.get(symbol, []):
                        self._pending_events[event["event_id"]] = dict(event)
                        self._consumed.add(event["event_id"])
                    if anchor.get("state") == "ready":
                        self._reconcile.discard(symbol)
                        latest = self._latest_quotes.get(symbol)
                        if latest:
                            self._enqueue_live_locked(anchor, symbol, latest, self.client.clock())
                for stale in stale_updates:
                    self._anchors[stale["symbol"]] = stale
                    self._pending_anchors[stale["symbol"]] = stale
                for symbol, error in failed:
                    if not any(item[0] == symbol for item in succeeded):
                        old = self._anchors.get(symbol)
                        if old:
                            stale = dict(old, state="recovering", coverage_complete=False,
                                         armed_sides=[], recovery_error=error)
                            self._anchors[symbol] = stale
                            self._pending_anchors[symbol] = stale
                anchors_to_save = [dict(row) for row in self._pending_anchors.values()]
                touches_to_save = [dict(row) for row in self._pending_events.values()]
                cursors_to_save = [dict(row) for row in self._pending_watermarks.values()]
            # One short transaction publishes H1 history + anchor state and
            # flushes listener facts. No database call runs on the WS callback.
            inserted = self.book.commit_cycle(anchors=anchors_to_save,
                                              touches=touches_to_save,
                                              quote_cursors=cursors_to_save,
                                              now_ms=self.client.clock())
            with self.lock:
                for row in anchors_to_save:
                    current = self._pending_anchors.get(row["symbol"])
                    if current == row:
                        self._pending_anchors.pop(row["symbol"], None)
                for row in touches_to_save:
                    if self._pending_events.get(row["event_id"]) == row:
                        self._pending_events.pop(row["event_id"], None)
                for row in cursors_to_save:
                    if self._pending_watermarks.get(row["symbol"]) == row:
                        self._pending_watermarks.pop(row["symbol"], None)
                self._consumed.update(row["event_id"] for row in inserted)
            self.last_cycle_ms = now_ms
            self.error = cycle_error
            self.book.mark_worker(now_ms, self.error)
            if deliver:
                for channel in ("telegram", "bark"):
                    self.deliver_once(channel, now_ms)
            return processed
        except Exception as exc:
            self.error = type(exc).__name__
            self.book.mark_worker(now_ms, self.error)
            return 0

    def _run(self):
        while not self.stop.is_set():
            self.step()
            self.stop.wait(WORKER_INTERVAL_SECONDS)

    def _deliver_loop(self, channel):
        while not self.stop.is_set():
            try:
                self.deliver_once(channel, self.client.clock())
            except Exception:
                # Never expose a credential, URL or upstream response in state/logs.
                pass
            self.stop.wait(0.2)

    def deliver_once(self, channel, now_ms=None):
        now_ms = self.client.clock() if now_ms is None else int(now_ms)
        event = self.book.pending_notice(channel, now_ms)
        if not event:
            return False
        creds = telegram_credentials() if channel == "telegram" else bark_credentials(self.book)
        if not creds:
            self.book.finish_notice(event["notice_id"], "failed", error="channel_not_configured", expected="pending")
            return True
        claimed = self.book.claim_notice(event["notice_id"], channel, now_ms)
        if not claimed:
            return True
        self._send_notice(channel, claimed, creds, now_ms)
        return True

    def _send_notice(self, channel, event, creds, now_ms):
        text = _event_text(event)
        chart_url = "https://spikeok.com/#monday-fib?" + urlencode(
            {"symbol": event["symbol"], "monthEvent": event["event_id"]})
        try:
            if channel == "telegram":
                token, chat = creds
                tv_symbol = event["symbol"].removesuffix("-SWAP").replace("-", "") + ".P"
                response = self.sender("https://api.telegram.org/bot" + token + "/sendMessage",
                                       json={"chat_id": chat, "text": text, "disable_web_page_preview": True,
                                             "reply_markup": {"inline_keyboard": [[{"text": "看 1.618 图表",
                                                 "url": chart_url}, {"text": "打开 TradingView ↗",
                                                 "url": f"https://www.tradingview.com/chart/?symbol=OKX%3A{tv_symbol}&interval=60"}]]}},
                                       timeout=(6, 15), allow_redirects=False)
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("invalid_response")
                if payload.get("ok") is True and response.status_code == 200:
                    result = payload.get("result")
                    message_id = result.get("message_id") if isinstance(result, dict) else None
                    if type(message_id) is not int or message_id <= 0:
                        raise ValueError("receipt_missing")
                    self.book.finish_notice(event["notice_id"], "sent", message_id=message_id)
                    return
                if response.status_code == 429 or payload.get("error_code") == 429:
                    params = payload.get("parameters") or {}
                    delay = params.get("retry_after", 30) if isinstance(params, dict) else 30
                    delay = max(5, min(3600, int(delay))) if str(delay).isdigit() else 30
                    if event["attempts"] < 5 and self.client.clock() <= event["fresh_until_ms"]:
                        self.book.finish_notice(event["notice_id"], "pending", error="telegram_rate_limited",
                                                due_ms=now_ms + delay * 1000)
                    else:
                        self.book.finish_notice(event["notice_id"], "failed", error="telegram_rate_limit_exhausted")
                    return
                if response.status_code >= 500 or response.status_code == 200:
                    raise ValueError("delivery_uncertain")
                self.book.finish_notice(event["notice_id"], "failed", error="telegram_rejected")
                return

            from .bark import SERVER
            tv_symbol = event["symbol"].removesuffix("-SWAP").replace("-", "") + ".P"
            title, body = text.split("\n", 1)
            response = self.sender(SERVER + "/push", json={"device_key": creds, "title": title, "body": body,
                                       "group": "SPIKE 月首周 1.618", "level": "active", "isArchive": "1",
                                       "copy": f"OKX:{tv_symbol}", "url": chart_url},
                                  timeout=(6, 15), allow_redirects=False)
            if response.status_code == 429:
                retry = response.headers.get("Retry-After", "30")
                delay = max(5, min(3600, int(retry))) if str(retry).isdigit() else 30
                if event["attempts"] < 5 and self.client.clock() <= event["fresh_until_ms"]:
                    self.book.finish_notice(event["notice_id"], "pending", error="bark_rate_limited",
                                            due_ms=now_ms + delay * 1000)
                else:
                    self.book.finish_notice(event["notice_id"], "failed", error="bark_rate_limit_exhausted")
                return
            if 400 <= response.status_code < 500:
                self.book.finish_notice(event["notice_id"], "failed", error="bark_rejected")
                return
            payload = response.json()
            if (response.status_code != 200 or not isinstance(payload, dict)
                    or type(payload.get("code")) is not int or payload["code"] != 200
                    or type(payload.get("timestamp")) is not int or payload["timestamp"] <= 0):
                raise ValueError("acceptance_not_confirmed")
            self.book.finish_notice(event["notice_id"], "sent", server_timestamp=payload["timestamp"])
        except Exception:
            # A timeout/invalid receipt is ambiguous; never automatically resend.
            self.book.finish_notice(event["notice_id"], "unknown", error="delivery_uncertain_no_automatic_resend")

    def _configured(self, channel):
        try:
            return bool(telegram_credentials() if channel == "telegram" else bark_credentials(self.book))
        except Exception:
            return False

    def snapshot(self):
        now_ms = self.client.clock()
        window = month_window(now_ms)
        symbols = tuple(self.quote_source.symbols)
        loaded = self.book.load_state(window["month"])
        anchors = {row["symbol"]: row for row in loaded["anchors"]}
        events = loaded["touches"]
        events.sort(key=lambda event: (event["occurred_ms"], event["id"]), reverse=True)
        statuses = self.book.notice_statuses(window["month"])
        touch_map = {}
        for event in events:
            event["channels"] = statuses.get(event["event_id"], {})
            touch_map.setdefault(event["symbol"], {})[event["side"]] = event
        items = []
        for symbol in symbols:
            anchor = anchors.get(symbol)
            if anchor is None:
                state = "developing" if not window["complete"] else "pending"
                item = {"symbol": symbol, "state": state, "high": None, "low": None,
                        "upper": None, "lower": None, "updated_ms": 0,
                        "missing_hours": 0, "history_gaps": 0, "coverage_complete": False,
                        "armed_sides": []}
            else:
                item = {"symbol": symbol, "state": anchor["state"], "high": anchor["high"],
                        "low": anchor["low"], "upper": anchor["upper"], "lower": anchor["lower"],
                        "updated_ms": anchor["updated_ms"], "missing_hours": anchor["missing_hours"],
                        "history_gaps": anchor["history_gaps"],
                        "coverage_complete": anchor["coverage_complete"],
                        "armed_sides": anchor["armed_sides"],
                        "recovery_error": anchor.get("error") or anchor.get("recovery_error")}
            item["touches"] = touch_map.get(symbol, {})
            items.append(item)
        item_symbols = set(symbols)
        for symbol, anchor in anchors.items():
            if symbol not in item_symbols:
                items.append({"symbol": symbol, "state": anchor["state"], "high": anchor["high"],
                              "low": anchor["low"], "upper": anchor["upper"], "lower": anchor["lower"],
                              "updated_ms": anchor["updated_ms"], "missing_hours": anchor["missing_hours"],
                              "history_gaps": anchor["history_gaps"],
                              "coverage_complete": anchor["coverage_complete"],
                              "armed_sides": anchor["armed_sides"],
                              "recovery_error": anchor.get("error"),
                              "touches": touch_map.get(symbol, {})})
        ready = sum(1 for row in items if row["state"] == "ready" and row["coverage_complete"])
        incomplete = sum(1 for row in items if row["state"] in {"incomplete", "invalid_range", "coverage_incomplete"})
        recovering = sum(1 for row in items if row["state"] in {"recovering", "fetch_error"})
        pending = max(0, len(items) - ready - incomplete - recovering)
        worker_db = self.book.worker_state()
        channels = {channel: self.book.channel_status(channel, configured=self._configured(channel))
                    for channel in ("telegram", "bark")}
        return {
            "schema": "month_first_week_1618.v1",
            "now_ms": now_ms,
            "timezone": "America/New_York",
            "month": window["month"],
            "window": {k: window[k] for k in ("month", "first_monday", "start_ms", "ready_ms", "expires_ms", "complete")},
            "coverage": {"universe_size": len(items), "ready_symbols": ready,
                         "incomplete_symbols": incomplete, "recovering_symbols": recovering,
                         "pending_symbols": pending,
                         "expected_1h_bars": window["expected_1h_bars"],
                         "history_gap_symbols": sum(1 for row in items if row.get("history_gaps", 0)),
                         "source": "OKX confirmed 1H anchor + shared OKX candle1m stream + targeted current-hour 1m recovery"},
            "stream": {"connected": bool(getattr(self.quote_source, "connected", False)),
                       "last_message_ms": int(getattr(self.quote_source, "last_message_ms", 0) or 0),
                       "error": getattr(self.quote_source, "stream_error", None)},
            "worker": {"alive": bool(self.thread and self.thread.is_alive()),
                       "last_cycle_ms": worker_db["last_cycle_ms"],
                       "error": self.error or worker_db["error"],
                       "symbol_count": len(items), "ready_count": ready,
                       "incomplete_count": incomplete, "recovering_count": recovering,
                       "pending_count": pending},
            "channels": channels,
            "touches": events,
            "items": items,
        }

    def events_after(self, after_id=0, limit=500):
        return self.book.events_after(after_id, limit)

    def latest_event_id(self):
        return self.book.latest_event_id()


class ChannelSetting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


def install(app, service: MonthFirstWeek1618Service):
    """Install loopback read, event-cursor and independently gated TG/Bark APIs."""
    app.state.month_first_week_1618 = service

    @app.get("/api/month-first-week-1618")
    def month_first_week_status():
        return service.snapshot()

    @app.get("/api/month-first-week-1618/events")
    def month_first_week_events(after_id: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=500)):
        return {"schema": "month_first_week_1618.events.v1", "latest_id": service.latest_event_id(),
                "events": service.events_after(after_id, limit)}

    def update_subscription(channel, action, payload, request):
        if (request.headers.get("origin") != f"{request.url.scheme}://{request.url.netloc}"
                or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
                or request.headers.get("x-spike-action") != action):
            raise HTTPException(403, "请从月首周 1.618 同源入口修改通知设置")
        if payload.enabled and not service._configured(channel):
            raise HTTPException(409, f"本机尚未配置月首周 1.618 {channel} 通知")
        service.book.set_enabled(channel, payload.enabled, service.client.clock())
        return service.snapshot()["channels"][channel]

    @app.put("/api/month-first-week-1618/telegram")
    def month_first_week_telegram(payload: ChannelSetting, request: Request):
        return update_subscription("telegram", "update-month-first-week-1618-telegram", payload, request)

    @app.put("/api/month-first-week-1618/bark")
    def month_first_week_bark(payload: ChannelSetting, request: Request):
        return update_subscription("bark", "update-month-first-week-1618-bark", payload, request)


__all__ = ["MonthFirstWeek1618Service", "install"]
