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
be checked without waiting for a crash.
"""
from __future__ import annotations

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
import requests

MINUTE = 60_000
WINDOW_MS, THRESHOLD, COOLDOWN_MS = 5*MINUTE, 25, 30*MINUTE
SIDE_TEXT = {"lower": "下方 1.618（急跌，做多方向）", "upper": "上方 1.618（急涨，做空方向）"}
ACTIVE_MS = 6*60*MINUTE


def alert_text(alert):
    at = datetime.fromtimestamp(alert["last_ms"]/1000, ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M")
    names = [s.split("-")[0] for s in alert["symbols"]]
    shown = "、".join(names[:12])+(f" 等 {len(names)} 个" if len(names) > 12 else "")
    return (f"全市场插针报警\n5 分钟内 {alert['n']} 个币同时触及{SIDE_TEXT[alert['side']]}\n"
            f"时间 {at}（北京时间）\n{shown}\n只通知，不动仓位；模拟盘照常按规则处理")


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
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY, side TEXT, first_ms INTEGER, last_ms INTEGER,
                          n INTEGER, symbols TEXT, created_ms INTEGER, telegram TEXT, bark TEXT, error TEXT)""")
            columns = {r["name"] for r in db.execute("PRAGMA table_info(alerts)")}
            for name, spec in {"ack_ms": "INTEGER", "test": "INTEGER NOT NULL DEFAULT 0"}.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE alerts ADD COLUMN {name} {spec}")
            self.last_alert = {s: (db.execute("SELECT MAX(last_ms) m FROM alerts WHERE side=? AND test=0", (s,)).fetchone()["m"] or -10**15)
                               for s in SIDE_TEXT}

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
            alert["id"] = db.execute("INSERT INTO alerts (side,first_ms,last_ms,n,symbols,created_ms,telegram,bark) VALUES (?,?,?,?,?,?,?,?)",
                                     (side, alert["first_ms"], candle_ms, alert["n"], json.dumps(symbols), self.clock(),
                                      "pending" if self.telegram_creds else "no_channel", "pending" if self.bark_key else "no_channel")).lastrowid
        try:
            self.events.put_nowait(alert)
        except queue.Full:
            pass

    def on_quote(self, symbol, quote):
        return None

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

    def snapshot(self, limit=50):
        now = self.clock()
        with self.db() as db:
            rows = [dict(r, symbols=json.loads(r["symbols"]), text=None) for r in db.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))]
        for r in rows:
            r["text"] = alert_text(r)
            r["active"] = r["ack_ms"] is None and now-r["created_ms"] <= ACTIVE_MS
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
            return dict(db.execute("SELECT id, ack_ms FROM alerts WHERE id=?", (alert_id,)).fetchone())

    def test_alert(self, side="lower"):
        """Local-only alert for checking the page; never pushed and never starts the cooldown."""
        now = self.clock()
        symbols = [f"TEST{k}-USDT-SWAP" for k in range(self.threshold)]
        with self.db() as db:
            aid = db.execute("INSERT INTO alerts (side,first_ms,last_ms,n,symbols,created_ms,telegram,bark,test) VALUES (?,?,?,?,?,?,?,?,1)",
                             (side, now, now, len(symbols), json.dumps(symbols), now, "test", "test")).lastrowid
        return dict(id=aid, test=True)


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
