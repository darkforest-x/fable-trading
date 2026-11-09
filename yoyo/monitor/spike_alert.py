"""Market-wide spike alert on Monday 1.618 touches (owner 2026-10-27).

Owner: "加一个全市场插针报警：5 分钟内同方向触及 1.618 的币 ≥ 8 个就推送，先只通知不动仓位". Backtest context
(exp-monday-limit-spike-hw-20261026-v1): the biggest spike days filled dozens of alts inside one 5m bar, too fast
for a manual entry; the alert is information only and never touches paper or real positions.
Listener on MondayFibService live first touches (history and backfill are ignored): per side, distinct symbols
whose touch candle falls in the last 5 one-minute candles; at >= 8 an alert is written to its own SQLite book and
sent once to Telegram (local credentials, as monday_fib) and Bark (this monitor's bark.json when enabled). One
alert per side per 30 minutes, kept across restarts. Callbacks only enqueue; a worker thread sends. Uncertain HTTP
outcomes are recorded as unknown and never retried automatically, like the other channels.
Owner 2026-10-27: threshold raised from 8 to 25 coins after exp-monday-spike-alert-quality-20261027-v1 (at 8 it would
fire about twice a week on OKX-listed coins; 25+ marks the extreme crashes where v3 and spike-HW trades did better).
Owner 2026-10-27: the web page should show a full-screen alarm with sound. The page itself goes through Stitch and
Codex; this module only serves the data: unacknowledged alerts of the last ACTIVE_MS are "active", a same-origin POST
acknowledges one (shared by every open page), and a same-origin test alert (local only, never pushed) lets the page
be checked without waiting for a crash. Owner 2026-10-27: no 5-second polling; GET /api/spike-alerts/stream is a
server-sent event stream that pushes the active list on connect and whenever an alert is created, acknowledged or
tested. Owner then asked not to load the backend: each open page waits on its own asyncio queue that the alert
thread wakes with call_soon_threadsafe, so an idle page costs one sleeping coroutine and a comment heartbeat every
25 seconds; the database is read only on connect and on change. At most MAX_STREAMS pages stream at once.
Owner 2026-11-05 ("报警响应台"): the full-screen page shows what happens after the alarm. For each active alert the
stream also carries the same-side live first touches from the alert window to LIVE_WINDOW_MS after it (read-only from
the monitor's monday-fib touch book, so a restart keeps them), the latest 1m close per touched coin and BTC/ETH prices
captured when the alert fired, and how many positions the live- paper accounts opened in that window (read-only from the
paper book). Quotes and touches only set a flag; the worker pushes at most every PUSH_EVERY_MS while an alert is less
than HOT_MS old, so an idle monitor still costs nothing. Display only: nothing here places, sizes or closes a position.
Owner 2026-11-09 ("底线是25个，但是显示的应该是完整的，有多少显示多少"): the 11-09 05:55 alert froze at the 25th
coin, while a 26th touched in the same 5m candle and a 27th four minutes later. 25 stays the trigger; the shown alert is
complete: `n`/`symbols` are the trigger coins plus every same-side live first touch from the alert's first touch until
its cooldown ends (no new alert can fire on that side before then, so those coins would otherwise never be reported).
They are read from the monitor's touch book (written before listeners run) and frozen into `all_symbols` once the
window has closed. `trigger_n`/`trigger_symbols` keep the firing moment; the Telegram/Bark text is sent once at firing
and now names every trigger coin instead of the first 12.
"""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import queue
import sqlite3
import threading
import time
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
import requests

MINUTE = 60_000
WINDOW_MS, THRESHOLD, COOLDOWN_MS = 5*MINUTE, 25, 30*MINUTE
SIDE_TEXT = {"lower": "下方 1.618（急跌，做多方向）", "upper": "上方 1.618（急涨，做空方向）"}
ACTIVE_MS = 6*60*MINUTE
MAX_STREAMS = 20
LIVE_WINDOW_MS = 30*MINUTE  # same window as exp-monday-limit-alarm-entry-20261029-v1
HOT_MS = 60*MINUTE
PUSH_EVERY_MS = 3000
PRICE_FRESH_MS = 5*MINUTE
REFERENCE = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
LIVE_TRACKS = {7060: "live-allin3", 8060: "live-wick6x3", 8160: "live-wick6x3hi", 9060: "live-wick3x3",
               9160: "live-wick3x3far", 8260: "live-wick6x3mid"}
SETTLE_MS = 2*MINUTE  # late 1m deliveries for the last candle of the follow window land before it is frozen


def alert_text(alert):
    """Push text at firing time: the trigger count and every trigger coin (owner 2026-11-09: no 12-name cut)."""
    at = datetime.fromtimestamp(alert["last_ms"]/1000, ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M")
    names = "、".join(s.split("-")[0] for s in alert["symbols"])
    return (f"全市场插针报警\n5 分钟内 {alert['n']} 个币同时触及{SIDE_TEXT[alert['side']]}\n"
            f"时间 {at}（北京时间）\n{names}\n只通知，不动仓位；模拟盘照常按规则处理")


class SpikeAlert:
    def __init__(self, runtime, threshold=THRESHOLD, window_ms=WINDOW_MS, cooldown_ms=COOLDOWN_MS,
                 telegram_creds=None, bark_key=None, sender=None, clock=None):
        from yoyo.monitor import bark, telegram
        self.runtime = Path(runtime)
        self.path = self.runtime/"spike-alerts.sqlite3"
        self.threshold, self.window_ms, self.cooldown_ms = threshold, window_ms, cooldown_ms
        self.telegram_creds = telegram_creds if telegram_creds is not None else telegram.credentials()
        self.bark_key = bark_key if bark_key is not None else bark.credentials(self.runtime)
        self.sender = sender or requests.post
        self.clock = clock or (lambda: int(time.time()*1000))
        self.recent = {"lower": deque(), "upper": deque()}
        self.lock = threading.Lock()
        self.events = queue.Queue(maxsize=1000)
        self.stop = threading.Event()
        self.thread = None
        self.version = 0  # bumped on every create, acknowledge or test
        self.subscribers = set()  # (event loop, asyncio.Queue) per open stream
        self.prices = {}  # symbol -> (last 1m close, candle_ms); written from the quote callback only
        self.hot_until_ms, self.hot_symbols, self.dirty, self.last_push_ms = 0, set(REFERENCE), False, 0
        self.touch_book = self.runtime/"monday-fib.sqlite3"
        self.paper_book = self.runtime/"monday-reclaim-paper.sqlite3"
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY, side TEXT, first_ms INTEGER, last_ms INTEGER,
                          n INTEGER, symbols TEXT, created_ms INTEGER, telegram TEXT, bark TEXT, error TEXT)""")
            columns = {r["name"] for r in db.execute("PRAGMA table_info(alerts)")}
            for name, spec in {"ack_ms": "INTEGER", "test": "INTEGER NOT NULL DEFAULT 0", "ref_prices": "TEXT", "all_symbols": "TEXT"}.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE alerts ADD COLUMN {name} {spec}")
            self.last_alert = {s: (db.execute("SELECT MAX(last_ms) m FROM alerts WHERE side=? AND test=0", (s,)).fetchone()["m"] or -10**15)
                               for s in SIDE_TEXT}
            hot = db.execute("SELECT MAX(last_ms) m FROM alerts WHERE ack_ms IS NULL").fetchone()["m"]
            self.hot_until_ms = (hot or 0) + HOT_MS if hot else 0

    @contextmanager
    def db(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    # MondayFibService listener interface: never block the stream.
    def on_touch(self, levels, symbol, side, price, observed_ms, candle_ms, source):
        if source != "live" or side not in self.recent:
            return
        if self.clock() <= self.hot_until_ms:
            self.hot_symbols.add(symbol)
            self.dirty = True
        with self.lock:
            q = self.recent[side]
            q.append((candle_ms, symbol))
            while q and q[0][0] <= candle_ms-self.window_ms:
                q.popleft()
            symbols = sorted({s for _, s in q})
            if len(symbols) < self.threshold or candle_ms-self.last_alert[side] < self.cooldown_ms:
                return
            self.last_alert[side] = candle_ms
            alert = dict(side=side, first_ms=min(c for c, _ in q), last_ms=candle_ms, n=len(symbols), symbols=symbols)
        with self.db() as db:
            alert["id"] = db.execute("INSERT INTO alerts (side,first_ms,last_ms,n,symbols,created_ms,telegram,bark,ref_prices) VALUES (?,?,?,?,?,?,?,?,?)",
                                     (side, alert["first_ms"], candle_ms, alert["n"], json.dumps(symbols), self.clock(),
                                      "pending" if self.telegram_creds else "no_channel", "pending" if self.bark_key else "no_channel",
                                      json.dumps(self._reference()))).lastrowid
        self._heat(candle_ms, symbols)
        self._changed()
        try:
            self.events.put_nowait(alert)
        except queue.Full:
            pass

    def on_quote(self, symbol, quote):
        """Called under the Monday service lock for every 1m update: store and flag only."""
        try:
            self.prices[symbol] = (float(quote["price"]), int(quote["candle_ms"]))
        except (KeyError, TypeError, ValueError):
            return
        if symbol in self.hot_symbols and self.hot_until_ms:
            self.dirty = True

    def _reference(self):
        now = self.clock()
        return {s: p for s in REFERENCE if (p := self._price(s, now)) is not None}

    def _price(self, symbol, now):
        value = self.prices.get(symbol)
        return value[0] if value and now - value[1] <= PRICE_FRESH_MS else None

    def _heat(self, last_ms, symbols):
        self.hot_until_ms = max(self.hot_until_ms, max(last_ms, self.clock()) + HOT_MS)
        self.hot_symbols = set(REFERENCE) | set(symbols)
        self.dirty = True

    def tick(self):
        """Worker-side push of live data; bounded by PUSH_EVERY_MS and only while an alert is hot."""
        now = self.clock()
        if not self.dirty or now > self.hot_until_ms or now - self.last_push_ms < PUSH_EVERY_MS:
            return False
        self.dirty, self.last_push_ms = False, now
        if self.subscribers:
            self._changed()
        return True

    def _live(self, row, now):
        """Read-only window facts for one active alert (display only)."""
        lo, hi = row["first_ms"] - self.window_ms, row["last_ms"] + LIVE_WINDOW_MS
        touches, paper = [], []
        if not row["test"] and self.touch_book.exists():
            try:
                book = sqlite3.connect(f"file:{self.touch_book}?mode=ro", uri=True, timeout=2)
                try:
                    found = book.execute("SELECT symbol, level, price, observed_ms, candle_ms FROM touches WHERE source='live' AND side=? "
                                         "AND candle_ms BETWEEN ? AND ? ORDER BY candle_ms DESC, observed_ms DESC LIMIT 300",
                                         (row["side"], lo, hi)).fetchall()
                finally:
                    book.close()
                for symbol, level, price, observed_ms, candle_ms in found:
                    self.hot_symbols.add(symbol)
                    touches.append(dict(symbol=symbol, level=level, touch_price=price, observed_ms=observed_ms, candle_ms=candle_ms,
                                        price=self._price(symbol, now)))
            except sqlite3.Error:
                touches = []
        if not row["test"] and self.paper_book.exists():
            try:
                book = sqlite3.connect(f"file:{self.paper_book}?mode=ro", uri=True, timeout=2)
                try:
                    marks = ",".join("?" * len(LIVE_TRACKS))
                    counts = dict(book.execute(f"SELECT track, COUNT(*) FROM positions WHERE taken=1 AND track IN ({marks}) "
                                               "AND fill_ms BETWEEN ? AND ? GROUP BY track", (*LIVE_TRACKS, lo, hi)).fetchall())
                finally:
                    book.close()
                paper = [dict(track=name, opened=int(counts.get(track, 0))) for track, name in LIVE_TRACKS.items()]
            except sqlite3.Error:
                paper = []
        try:
            ref = json.loads(row["ref_prices"] or "{}")
        except (TypeError, ValueError):
            ref = {}
        reference = {s: dict(at=ref.get(s), now=self._price(s, now)) for s in REFERENCE}
        return dict(window_end_ms=hi, touches=touches, reference=reference, paper=paper)

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name="spike-alert", daemon=True)
            self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=3)

    def _run(self):
        while not self.stop.is_set():
            try:
                self.deliver(self.events.get(timeout=1))
            except queue.Empty:
                pass
            try:
                self.tick()
            except Exception:  # a display push must never stop alert delivery
                pass

    def deliver(self, alert):
        text, status, errors = alert_text(alert), {}, []
        if self.telegram_creds:
            try:
                r = self.sender("https://api.telegram.org/bot"+self.telegram_creds[0]+"/sendMessage",
                                json={"chat_id": self.telegram_creds[1], "text": text, "disable_web_page_preview": True},
                                timeout=(6, 15), allow_redirects=False)
                ok = r.status_code == 200 and isinstance(r.json(), dict) and r.json().get("ok") is True
                status["telegram"] = "sent" if ok else ("unknown" if r.status_code >= 500 else "failed")
            except Exception as err:
                status["telegram"] = "unknown"; errors.append(f"telegram {type(err).__name__}")
        if self.bark_key:
            lines = text.split("\n")
            try:
                r = self.sender("https://api.day.app/push", json={"title": lines[0], "body": "\n".join(lines[1:]),
                                "group": "spike-alert", "device_key": self.bark_key}, timeout=(6, 15), allow_redirects=False)
                ok = r.status_code == 200 and isinstance(r.json(), dict) and r.json().get("code") == 200
                status["bark"] = "sent" if ok else ("unknown" if r.status_code >= 500 else "failed")
            except Exception as err:
                status["bark"] = "unknown"; errors.append(f"bark {type(err).__name__}")
        with self.db() as db:
            for ch, st in status.items():
                db.execute(f"UPDATE alerts SET {ch}=? WHERE id=?", (st, alert["id"]))
            if errors:
                db.execute("UPDATE alerts SET error=? WHERE id=?", ("; ".join(errors), alert["id"]))
        return status

    def _members(self, row, now):
        """(complete coin list, final) for one alert: trigger coins plus same-side live first touches until the cooldown ends.

        Ordered by touch time; a trigger coin missing from the touch book is kept at the end. Final once the follow
        window has closed and the book was readable; the caller then freezes the list into `all_symbols`."""
        if row.get("all_symbols"):
            try:
                return json.loads(row["all_symbols"]), True
            except (TypeError, ValueError):
                pass
        trigger = list(row["symbols"])
        if row["test"] or not self.touch_book.exists():
            return trigger, bool(row["test"])
        end = row["last_ms"] + self.cooldown_ms
        try:
            book = sqlite3.connect(f"file:{self.touch_book}?mode=ro", uri=True, timeout=2)
            try:
                found = book.execute("SELECT symbol FROM touches WHERE source='live' AND side=? AND candle_ms >= ? AND candle_ms < ? "
                                     "ORDER BY observed_ms, candle_ms, symbol", (row["side"], row["first_ms"], end)).fetchall()
            finally:
                book.close()
        except sqlite3.Error:
            return trigger, False
        members = dict.fromkeys(s for (s,) in found)
        members.update(dict.fromkeys(trigger))
        return list(members), now >= end + SETTLE_MS

    def snapshot(self, limit=50):
        now = self.clock()
        with self.db() as db:
            rows = [dict(r, symbols=json.loads(r["symbols"]), text=None) for r in db.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))]
            for r in rows:
                members, final = self._members(r, now)
                if final and not r["test"] and not r["all_symbols"]:
                    db.execute("UPDATE alerts SET all_symbols=? WHERE id=? AND all_symbols IS NULL", (json.dumps(members), r["id"]))
                r["text"] = alert_text(r)  # what was pushed at firing
                r["trigger_n"], r["trigger_symbols"] = r["n"], r["symbols"]
                r["n"], r["symbols"] = len(members), members
                r["follow_end_ms"], r["complete"] = r["last_ms"] + self.cooldown_ms, final
                r.pop("all_symbols", None)
        for r in rows:
            r["active"] = r["ack_ms"] is None and now-r["created_ms"] <= ACTIVE_MS
            if r["active"]:
                r["live"] = self._live(r, now)
            r.pop("ref_prices", None)
        return dict(now_ms=now, threshold=self.threshold, window_minutes=self.window_ms//MINUTE, cooldown_minutes=self.cooldown_ms//MINUTE,
                    active_hours=ACTIVE_MS//(60*MINUTE), channels=dict(telegram=bool(self.telegram_creds), bark=bool(self.bark_key)),
                    notify_only=True, worker_alive=bool(self.thread and self.thread.is_alive()),
                    active=[r for r in rows if r["active"]], alerts=rows)

    def acknowledge(self, alert_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
            if row is None:
                return None
            if row["ack_ms"] is None:
                db.execute("UPDATE alerts SET ack_ms=? WHERE id=?", (self.clock(), alert_id))
                changed = True
            else:
                changed = False
            out = dict(db.execute("SELECT id, ack_ms FROM alerts WHERE id=?", (alert_id,)).fetchone())
        if changed:
            with self.db() as db:
                if db.execute("SELECT COUNT(*) n FROM alerts WHERE ack_ms IS NULL AND created_ms >= ?", (self.clock()-ACTIVE_MS,)).fetchone()["n"] == 0:
                    self.hot_until_ms = 0
            self._changed()
        return out

    def test_alert(self, side="lower"):
        """Local-only alert for checking the page; never pushed and never starts the cooldown."""
        now = self.clock()
        symbols = [f"TEST{k}-USDT-SWAP" for k in range(self.threshold)]
        with self.db() as db:
            aid = db.execute("INSERT INTO alerts (side,first_ms,last_ms,n,symbols,created_ms,telegram,bark,test,ref_prices) VALUES (?,?,?,?,?,?,?,?,1,?)",
                             (side, now, now, len(symbols), json.dumps(symbols), now, "test", "test", json.dumps(self._reference()))).lastrowid
        self._heat(now, [])
        self._changed()
        return dict(id=aid, test=True)

    def _changed(self):
        """Wake every open stream from any thread; nothing runs when no page is open."""
        self.version += 1
        for loop, q in list(self.subscribers):
            try:
                loop.call_soon_threadsafe(q.put_nowait, self.version)
            except RuntimeError:  # loop already closed
                self.subscribers.discard((loop, q))

    def _payload(self):
        snap = self.snapshot(limit=20)
        data = json.dumps(dict(version=self.version, now_ms=snap["now_ms"], active=snap["active"]), ensure_ascii=False)
        return f"event: alerts\ndata: {data}\n\n"

    async def event_stream(self, disconnected, heartbeat=25.0):
        """Server-sent events: the active list on connect and on every change, a comment heartbeat otherwise."""
        q = asyncio.Queue()
        entry = (asyncio.get_running_loop(), q)
        self.subscribers.add(entry)
        try:
            yield self._payload()
            while not await disconnected():
                try:
                    await asyncio.wait_for(q.get(), timeout=heartbeat)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                while not q.empty():
                    q.get_nowait()
                yield self._payload()
        finally:
            self.subscribers.discard(entry)


def install(app, engine):
    app.state.spike_alert = engine

    def same_origin(request, action):
        if (request.headers.get("origin") != f"{request.url.scheme}://{request.url.netloc}"
                or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
                or request.headers.get("x-spike-action") != action):
            raise HTTPException(403, "请从监控页面操作")

    @app.get("/api/spike-alerts")
    def snapshot():
        return engine.snapshot()

    @app.get("/api/spike-alerts/stream")
    async def stream(request: Request):
        if len(engine.subscribers) >= MAX_STREAMS:
            raise HTTPException(503, "报警推送连接已满")
        return StreamingResponse(engine.event_stream(request.is_disconnected), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/spike-alerts/{alert_id}/ack")
    def acknowledge(alert_id: int, request: Request):
        same_origin(request, "ack-spike-alert")
        result = engine.acknowledge(alert_id)
        if result is None:
            raise HTTPException(404, "报警不存在")
        return result

    @app.post("/api/spike-alerts/test")
    def test_alert(request: Request):
        same_origin(request, "test-spike-alert")
        return engine.test_alert()
