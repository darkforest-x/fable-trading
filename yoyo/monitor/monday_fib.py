"""Monday 1.618 location alerts, independent of entry/exit strategy signals.

Owner: NY calendar Monday, all live OKX USDT perpetuals, first touch on each
side per week. H/L use only the 24 completed hourly bars of Monday (NY midnight
is an integral UTC hour). Intraminute extrema come from OKX candle1m updates.
History establishes consumed sides, never notification authority. SQLite owns
weekly deduplication, subscription cutovers and conservative Telegram receipts.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, time, timedelta
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from yoyo.data.candle_hub import validate

ZONE = ZoneInfo("America/New_York")
HOUR = 3_600_000
MINUTE = 60_000
FRESH_MS = 90_000


def week_window(now):
    day = datetime.fromtimestamp(now / 1000, ZONE).date()
    monday = day - timedelta(days=day.weekday())
    stamp = lambda d: int(datetime.combine(d, time.min, ZONE).timestamp() * 1000)
    return {"week": monday.isoformat(), "start": stamp(monday),
            "end": stamp(monday + timedelta(days=1)), "expires": stamp(monday + timedelta(days=7))}


def baseline(raw, now):
    """Require complete Monday and subsequent hours through the current hour.

    The current incomplete hour is used solely to consume past touches, never
    to compute the Monday anchor. Missing history leaves a side unarmed.
    """
    window = week_window(now)
    if now < window["end"]:
        return {**window, "state": "developing"}
    rows = validate(raw, "1H", now)
    monday = list(range(window["start"], window["end"], HOUR))
    if any(t not in rows or rows[t][8] != "1" for t in monday):
        return {**window, "state": "missing_monday"}
    high = max(float(rows[t][2]) for t in monday)
    low = min(float(rows[t][3]) for t in monday)
    if high <= low:
        return {**window, "state": "invalid_range"}
    result = {**window, "high": high, "low": low,
              "upper": high + .618 * (high - low), "lower": low - .618 * (high - low)}
    after = list(range(window["end"], now // HOUR * HOUR + 1, HOUR))
    if any(t not in rows for t in after):
        return {**result, "state": "missing_history"}
    touches = {}
    for t in after:
        for side, hit in (("upper", float(rows[t][2]) >= result["upper"]),
                          ("lower", float(rows[t][3]) <= result["lower"])):
            if hit and side not in touches:
                touches[side] = t
    return {**result, "state": "ready", "historical_touches": touches, "baseline_ms": now}


class FibBook:
    def __init__(self, runtime):
        self.path = Path(runtime) / "monday-fib.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS subscription (
                    id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL,
                    activated_ms INTEGER NOT NULL, generation INTEGER NOT NULL);
                INSERT OR IGNORE INTO subscription VALUES (1,0,0,0);
                CREATE TABLE IF NOT EXISTS changes (
                    id INTEGER PRIMARY KEY, enabled INTEGER, changed_ms INTEGER, generation INTEGER);
                CREATE TABLE IF NOT EXISTS touches (
                    week TEXT, symbol TEXT, side TEXT, level REAL, price REAL,
                    observed_ms INTEGER, candle_ms INTEGER, source TEXT,
                    generation INTEGER, status TEXT, due_ms INTEGER, error TEXT,
                    message_id INTEGER, PRIMARY KEY(week,symbol,side));
            """)
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(str(self.path), timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE touches SET status='unknown',error='interrupted_delivery' WHERE status='sending'")

    def subscription(self):
        with self.db() as db:
            row = dict(db.execute("SELECT * FROM subscription WHERE id=1").fetchone())
        return {"enabled": bool(row["enabled"]), "activated_ms": row["activated_ms"], "generation": row["generation"]}

    def set_enabled(self, enabled, now):
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM subscription WHERE id=1").fetchone()
            if bool(row["enabled"]) != enabled:
                generation = row["generation"] + 1
                db.execute("UPDATE subscription SET enabled=?,activated_ms=?,generation=? WHERE id=1",
                           (int(enabled), now, generation))
                db.execute("INSERT INTO changes(enabled,changed_ms,generation) VALUES (?,?,?)",
                           (int(enabled), now, generation))
                db.execute("UPDATE touches SET status='skipped',error='subscription_changed' WHERE status='pending'")
        return self.subscription()

    def touch(self, levels, symbol, side, price, observed_ms, candle_ms, source, *, known_after_ms=None):
        """Atomic weekly first touch plus forward-only enqueue; duplicate is inert."""
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            route = db.execute("SELECT * FROM subscription WHERE id=1").fetchone()
            active = (source == "live" and route["enabled"] and observed_ms > route["activated_ms"]
                      and (candle_ms if known_after_ms is None else known_after_ms) >= route["activated_ms"]
                      and levels["end"] <= candle_ms < levels["expires"])
            status = "pending" if active else ("history" if source == "history" else "muted")
            cursor = db.execute("INSERT OR IGNORE INTO touches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (levels["week"], symbol, side, levels[side], price, observed_ms, candle_ms,
                                 source, route["generation"], status, observed_ms, None, None))
            return cursor.rowcount == 1

    def records(self, week=None, limit=1000):
        with self.db() as db:
            if week:
                rows = db.execute("SELECT * FROM touches WHERE week=? ORDER BY observed_ms DESC LIMIT ?", (week, limit))
            else:
                rows = db.execute("SELECT * FROM touches ORDER BY observed_ms DESC LIMIT ?", (limit,))
            return [dict(row) for row in rows]

    def claim(self, now):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            route = db.execute("SELECT * FROM subscription WHERE id=1").fetchone()
            db.execute("UPDATE touches SET status='skipped',error='expired_or_disabled' WHERE status='pending' "
                       "AND (observed_ms<? OR generation<>? OR ?=0)",
                       (now - FRESH_MS, route["generation"], route["enabled"]))
            row = db.execute("SELECT * FROM touches WHERE status='pending' AND due_ms<=? ORDER BY observed_ms LIMIT 1", (now,)).fetchone()
            if row:
                db.execute("UPDATE touches SET status='sending' WHERE week=? AND symbol=? AND side=?",
                           (row["week"], row["symbol"], row["side"]))
                return dict(row)
        return None

    def finish(self, event, status, error=None, message_id=None, due_ms=0):
        with self.db() as db:
            db.execute("UPDATE touches SET status=?,error=?,message_id=?,due_ms=? "
                       "WHERE week=? AND symbol=? AND side=? AND status='sending'",
                       (status, error, message_id, due_ms, event["week"], event["symbol"], event["side"]))


def deliver_once(book, creds, sender, now):
    """Reuse local Telegram credentials; uncertain HTTP outcomes never auto-retry."""
    if not creds:
        return False
    event = book.claim(now)
    if not event:
        return False
    route = book.subscription()
    if not route["enabled"] or route["generation"] != event["generation"]:
        book.finish(event, "skipped", "subscription_changed")
        return True
    side = "上方" if event["side"] == "upper" else "下方"
    observed = datetime.fromtimestamp(event["observed_ms"] / 1000, ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M:%S")
    text = (f"周一 1.618 · {event['symbol']}\n{side} 1.618 本周首次触及\n"
            f"点位 {event['level']:.10g} · 最新价 {event['price']:.10g}\n"
            f"周一 {event['week']}（纽约时间）\n观测 {observed}（北京时间）\n位置提醒，不代表入场确认")
    tv = event["symbol"].removesuffix("-SWAP").replace("-", "") + ".P"
    try:
        response = sender("https://api.telegram.org/bot" + creds[0] + "/sendMessage",
                          json={"chat_id": creds[1], "text": text, "disable_web_page_preview": True,
                                "reply_markup": {"inline_keyboard": [[{"text": "查看图表 ↗", "url":
                                f"https://www.tradingview.com/chart/?symbol=OKX%3A{tv}&interval=15"}]]}},
                          timeout=(6, 15), allow_redirects=False)
        payload = response.json()
        if not isinstance(payload, dict) or response.status_code >= 500:
            raise ValueError("uncertain")
        if payload.get("ok") is True:
            mid = payload.get("result", {}).get("message_id")
            if response.status_code != 200 or type(mid) is not int or mid <= 0:
                raise ValueError("missing_receipt")
            book.finish(event, "sent", message_id=mid)
        elif payload.get("error_code") == 429:
            delay = max(5, min(3600, int(payload.get("parameters", {}).get("retry_after", 30))))
            book.finish(event, "pending", "rate_limited", due_ms=now + delay * 1000)
        else:
            book.finish(event, "failed", "telegram_rejected")
    except Exception:
        book.finish(event, "unknown", "delivery_uncertain_no_automatic_resend")
    return True
