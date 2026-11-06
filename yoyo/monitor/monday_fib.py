"""Monday 1.618 location alerts, independent of entry/exit strategy signals.

Owner: NY calendar Monday, all live OKX USDT perpetuals, first touch on each
side per week. H/L use only the 24 completed hourly bars of Monday (NY midnight
is an integral UTC hour). Intraminute extrema come from OKX candle1m updates.
History establishes consumed sides, never notification authority. SQLite owns
weekly deduplication, independent channel cutovers and conservative receipts.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, time, timedelta
import json
import math
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from yoyo.data.candle_hub import validate

ZONE = ZoneInfo("America/New_York")
HOUR = 3_600_000
MINUTE = 60_000
FRESH_MS = 90_000
NOTIFICATION_MIN_SCORE = 0.4  # Owner 2026-10-30: shared TG/Bark raw-score gate.
# Owner 2026-11-01 "每一个策略出信号也要发，但是不要重复发了 … 我说的live策略": a touch where a live- paper account
# entered is sent even at or below the gate, still once per touch and channel, listing those accounts in the message.
LIVE_NOTES = {"no_slot": "满仓", "no_capacity": "保证金不足", "busy": "已有持仓"}
# Owner 2026-10-31: the score is the backtest's, read at the touch 5m close, so a notification stays fresh for
# FRESH_MS after its score can exist: the later of the observation and that close.
SCORE_BAR_MS = 300_000
READY_SQL = "MAX({t}observed_ms, COALESCE({t}candle_ms/300000*300000+300000, {t}observed_ms))"


def score_ready_ms(event):
    candle = event.get("candle_ms")
    return max(event["observed_ms"], candle//SCORE_BAR_MS*SCORE_BAR_MS+SCORE_BAR_MS if candle is not None else 0)


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
                CREATE TABLE IF NOT EXISTS bark_subscription (
                    id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL,
                    activated_ms INTEGER NOT NULL, generation INTEGER NOT NULL);
                INSERT OR IGNORE INTO bark_subscription VALUES (1,0,0,0);
                CREATE TABLE IF NOT EXISTS bark_changes (
                    id INTEGER PRIMARY KEY, enabled INTEGER, changed_ms INTEGER, generation INTEGER);
                CREATE TABLE IF NOT EXISTS bark_outbox (
                    week TEXT, symbol TEXT, side TEXT, generation INTEGER,
                    status TEXT, due_ms INTEGER, error TEXT, server_timestamp INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(week,symbol,side));
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
            db.execute("UPDATE bark_outbox SET status='unknown',error='interrupted_delivery' WHERE status='sending'")

    @staticmethod
    def _channel_tables(channel):
        if channel == "telegram":
            return "subscription", "changes", "touches"
        if channel == "bark":
            return "bark_subscription", "bark_changes", "bark_outbox"
        raise ValueError("unsupported_channel")

    def subscription(self, channel="telegram"):
        subscription, _, _ = self._channel_tables(channel)
        with self.db() as db:
            row = dict(db.execute(f"SELECT * FROM {subscription} WHERE id=1").fetchone())
        return {"enabled": bool(row["enabled"]), "activated_ms": row["activated_ms"], "generation": row["generation"]}

    def set_enabled(self, enabled, now, channel="telegram"):
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        subscription, changes, outbox = self._channel_tables(channel)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(f"SELECT * FROM {subscription} WHERE id=1").fetchone()
            if bool(row["enabled"]) != enabled:
                generation = row["generation"] + 1
                db.execute(f"UPDATE {subscription} SET enabled=?,activated_ms=?,generation=? WHERE id=1",
                           (int(enabled), now, generation))
                db.execute(f"INSERT INTO {changes}(enabled,changed_ms,generation) VALUES (?,?,?)",
                           (int(enabled), now, generation))
                db.execute(f"UPDATE {outbox} SET status='skipped',error='subscription_changed' WHERE status='pending'")
        return self.subscription(channel)

    @staticmethod
    def _eligible(route, levels, observed_ms, candle_ms, source, known_after_ms):
        return (source == "live" and route["enabled"] and observed_ms > route["activated_ms"]
                and (candle_ms if known_after_ms is None else known_after_ms) >= route["activated_ms"]
                and levels["end"] <= candle_ms < levels["expires"])

    def touch(self, levels, symbol, side, price, observed_ms, candle_ms, source, *, known_after_ms=None):
        """Atomic weekly first touch plus forward-only enqueue; duplicate is inert."""
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            route = db.execute("SELECT * FROM subscription WHERE id=1").fetchone()
            active = self._eligible(route, levels, observed_ms, candle_ms, source, known_after_ms)
            status = "pending" if active else ("history" if source == "history" else "muted")
            cursor = db.execute("INSERT OR IGNORE INTO touches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (levels["week"], symbol, side, levels[side], price, observed_ms, candle_ms,
                                 source, route["generation"], status, observed_ms, None, None))
            inserted = cursor.rowcount == 1
            bark = db.execute("SELECT * FROM bark_subscription WHERE id=1").fetchone()
            if inserted and self._eligible(bark, levels, observed_ms, candle_ms, source, known_after_ms):
                db.execute("INSERT INTO bark_outbox (week,symbol,side,generation,status,due_ms) VALUES (?,?,?,?,?,?)",
                           (levels["week"], symbol, side, bark["generation"], "pending", observed_ms))
            return inserted

    def records(self, week=None, limit=None):
        with self.db() as db:
            if week and limit is None:
                # The all-market weekly status must not silently truncate sides.
                rows = db.execute("SELECT * FROM touches WHERE week=? ORDER BY observed_ms DESC", (week,))
            elif week:
                rows = db.execute("SELECT * FROM touches WHERE week=? ORDER BY observed_ms DESC LIMIT ?", (week, limit))
            else:
                rows = db.execute("SELECT * FROM touches ORDER BY observed_ms DESC LIMIT ?", (limit or 1000,))
            return [dict(row) for row in rows]

    def pending(self, now):
        """Peek without claiming delivery; score readiness is checked outside the DB lock."""
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            route = db.execute("SELECT * FROM subscription WHERE id=1").fetchone()
            db.execute("UPDATE touches SET status='skipped',error='expired_or_disabled' WHERE status='pending' "
                       f"AND ({READY_SQL.format(t='')}<? OR observed_ms>? OR generation<>? OR ?=0)",
                       (now - FRESH_MS, now, route["generation"], route["enabled"]))
            row = db.execute("SELECT * FROM touches WHERE status='pending' AND due_ms<=? "
                             "ORDER BY due_ms,observed_ms LIMIT 1", (now,)).fetchone()
            return dict(row) if row else None

    def claim(self, now, event=None):
        event = event or self.pending(now)
        if not event:
            return None
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            route = db.execute("SELECT * FROM subscription WHERE id=1").fetchone()
            row = db.execute("SELECT * FROM touches WHERE week=? AND symbol=? AND side=? "
                             f"AND status='pending' AND due_ms<=? AND {READY_SQL.format(t='')}>=? AND observed_ms<=? "
                             "AND generation=? AND ?=1",
                             (event["week"], event["symbol"], event["side"], now, now-FRESH_MS, now,
                              route["generation"], route["enabled"])).fetchone()
            if row:
                db.execute("UPDATE touches SET status='sending' WHERE week=? AND symbol=? AND side=?",
                           (row["week"], row["symbol"], row["side"]))
                return dict(row)
        return None

    def finish(self, event, status, error=None, message_id=None, due_ms=0, *, expected="sending"):
        with self.db() as db:
            db.execute("UPDATE touches SET status=?,error=?,message_id=?,due_ms=? "
                       "WHERE week=? AND symbol=? AND side=? AND status=? AND generation=?",
                       (status, error, message_id, due_ms, event["week"], event["symbol"], event["side"], expected, event["generation"]))

    def pending_bark(self, now):
        """Peek independently of Telegram; wait for scoring without a send attempt."""
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            route = db.execute("SELECT * FROM bark_subscription WHERE id=1").fetchone()
            db.execute("UPDATE bark_outbox SET status='skipped',error='expired_or_disabled' WHERE status='pending' "
                       "AND (generation<>? OR ?=0 OR EXISTS (SELECT 1 FROM touches t WHERE "
                       "t.week=bark_outbox.week AND t.symbol=bark_outbox.symbol AND t.side=bark_outbox.side "
                       f"AND ({READY_SQL.format(t='t.')}<? OR t.observed_ms>?)))",
                       (route["generation"], route["enabled"], now - FRESH_MS, now))
            row = db.execute("SELECT t.week,t.symbol,t.side,t.level,t.price,t.observed_ms,t.candle_ms,t.source, "
                             "b.generation,b.attempts FROM bark_outbox b JOIN touches t USING(week,symbol,side) "
                             "WHERE b.status='pending' AND b.due_ms<=? ORDER BY b.due_ms,t.observed_ms LIMIT 1", (now,)).fetchone()
            return dict(row) if row else None

    def claim_bark(self, now, event=None):
        event = event or self.pending_bark(now)
        if not event:
            return None
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            route = db.execute("SELECT * FROM bark_subscription WHERE id=1").fetchone()
            row = db.execute("SELECT t.week,t.symbol,t.side,t.level,t.price,t.observed_ms,t.candle_ms,t.source, "
                             "b.generation,b.attempts FROM bark_outbox b JOIN touches t USING(week,symbol,side) "
                             "WHERE b.week=? AND b.symbol=? AND b.side=? AND b.status='pending' AND b.due_ms<=? "
                             f"AND {READY_SQL.format(t='t.')}>=? AND t.observed_ms<=? AND b.generation=? AND b.generation=? AND ?=1",
                             (event["week"], event["symbol"], event["side"], now, now-FRESH_MS, now,
                              event["generation"], route["generation"], route["enabled"])).fetchone()
            if row:
                db.execute("UPDATE bark_outbox SET status='sending',attempts=attempts+1 WHERE week=? AND symbol=? AND side=?",
                           (row["week"], row["symbol"], row["side"]))
                return {**dict(row), "attempts": row["attempts"] + 1}
        return None

    def finish_bark(self, event, status, error=None, server_timestamp=None, due_ms=0, *, expected="sending"):
        with self.db() as db:
            db.execute("UPDATE bark_outbox SET status=?,error=?,server_timestamp=?,due_ms=? "
                       "WHERE week=? AND symbol=? AND side=? AND status=? AND generation=?",
                       (status, error, server_timestamp, due_ms, event["week"], event["symbol"], event["side"], expected, event["generation"]))

    def bark_status(self):
        with self.db() as db:
            counts = {r["status"]: r["n"] for r in db.execute("SELECT status,COUNT(*) n FROM bark_outbox GROUP BY status")}
            recent = [dict(r) for r in db.execute("SELECT * FROM bark_outbox ORDER BY rowid DESC LIMIT 10")]
        return {**self.subscription("bark"), "counts": counts, "recent": recent,
                "acceptance": "bark_server_accepted_not_device_receipt"}


def _score_value(value):
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1 else None


def _score_label(value, threshold=None):
    value = _score_value(value)
    if value is None:
        return "—"
    label = f"{value:.4f}"
    if (value > NOTIFICATION_MIN_SCORE and float(label) == NOTIFICATION_MIN_SCORE
            or threshold is not None and value < threshold <= float(label)):
        return str(value)  # Rounding must not contradict notification or entry eligibility.
    return label


def notification_text(event, score):
    """Present this touch's recorded entry outcome, not a claim about current holdings.

    Plain-text layout: Stitch 75085c838c684a79a2ca0557b0efad7e. A score alone
    never implies execution; missing rows cannot explain unrecorded rule failures.
    """
    symbol = event["symbol"].removesuffix("-USDT-SWAP")
    side = "上沿" if event["side"] == "upper" else "下沿"
    observed = datetime.fromtimestamp(event["observed_ms"] / 1000, ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M")
    thresholds = event.get("live_thresholds") or {}
    allin_gate, wick_gate = (_score_value(thresholds.get(name)) for name in ("live-allin3", "live-wick"))
    score, live_score = _score_value(score), _score_value(event.get("live_allin3_score"))
    entries = event.get("live_entries") or []
    opened = any(e["taken"] for e in entries)
    heading = "🟢 {symbol} · live开仓" if opened else "🟠 {symbol} · live未开" if entries else "🔔 {symbol} · 触价提醒"
    lines = [heading.format(symbol=symbol)]
    if opened:
        lines.append("模拟开仓 · " + ("做空" if event["side"] == "upper" else "做多"))
    elif "live_entries" in event or thresholds:
        lines.append("live 未开仓")
    lines.extend([f"{side} 1.618 · 点位 {event['level']:.10g}", f"{observed} 北京触价", "",
                  f"5m 收盘分 {_score_label(score, wick_gate)}"])
    if "live_allin3_score" in event or allin_gate is not None:
        lines.append(f"live-allin3 触价前分 {_score_label(live_score, allin_gate)}")
    outcomes = [f"✓ {e['name']} · 已模拟开仓" if e["taken"] else
                f"— {e['name']} · 未开：{LIVE_NOTES.get(e.get('note'), '无入场记录')}" for e in entries]
    for name, value, gate, basis in (("live-allin3", live_score, allin_gate, "触价前分"),
                                     ("live-wick", score, wick_gate, "收盘分")):
        if gate is None or any(e["name"] == name or name == "live-wick" and e["name"].startswith(name) for e in entries):
            continue
        reason = "评分暂缺" if value is None else f"{basis} < {gate:g}" if value < gate else "无入场记录"
        outcomes.append(f"— {name} · 未开：{reason}")
    return "\n".join(lines + ([""] + outcomes if outcomes else []))


def claim_scored_notification(book, event, now, *, score_lookup=None, clock=None, channel="telegram"):
    """Apply one score/freshness policy, then claim only this channel's event.

    The reader joins the persisted score to the full touch identity outside the
    FibBook lock. Pending scoring does not consume transport retry attempts.
    """
    book._channel_tables(channel)  # Reject unsupported routes before any state change.
    finish = book.finish_bark if channel == "bark" else book.finish
    claim = book.claim_bark if channel == "bark" else book.claim
    read_error = None
    try:
        result = score_lookup(event) if score_lookup else None
    except Exception:
        result, read_error = None, "score_read_error"
    ready_at = clock() if clock else now
    if not (event["observed_ms"] <= ready_at and ready_at-score_ready_ms(event) <= FRESH_MS):
        finish(event, "skipped", "expired_before_score", expected="pending")
        return None
    if result is None:
        finish(event, "pending", read_error or "score_pending", due_ms=ready_at+1000, expected="pending")
        return None
    score = result.get("score") if isinstance(result, dict) else None
    entries = result.get("live_entries") if isinstance(result, dict) else None
    entries_valid = isinstance(entries, list) and all(
        isinstance(e, dict) and isinstance(e.get("name"), str) and type(e.get("taken")) is bool for e in entries)
    live = entries_valid and bool(entries)
    if (not isinstance(result, dict) or result.get("status") != "scored"
            or type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1):
        if not live:
            finish(event, "skipped", "score_unavailable", expected="pending")
            return None
        score = None  # A live account entered on its own rule; show the touch without a score.
    elif score <= NOTIFICATION_MIN_SCORE and not live:
        finish(event, "skipped", "score_not_above_0_4", expected="pending")
        return None
    # A score read can race a subscription change or another sender. Revalidate
    # and claim this exact event only after eligibility is known.
    event = claim(ready_at, event)
    if not event:
        return None
    route = book.subscription(channel)
    if not route["enabled"] or route["generation"] != event["generation"]:
        finish(event, "skipped", "subscription_changed")
        return None
    if "live_allin3_score" in result:
        event = dict(event, live_allin3_score=result["live_allin3_score"])
    if entries_valid:
        event = dict(event, live_entries=entries)
    if isinstance(result.get("live_thresholds"), dict):
        event = dict(event, live_thresholds=result["live_thresholds"])
    return event, score, ready_at


def deliver_once(book, creds, sender, now, *, score_lookup=None, clock=None):
    """Wait for the exact stored score; strictly >0.4 or a live- entry. Uncertain sends never auto-retry."""
    if not creds:
        return False
    event = book.pending(now)
    if not event:
        return False
    prepared = claim_scored_notification(book, event, now, score_lookup=score_lookup, clock=clock)
    if prepared is None:
        return True
    event, score, ready_at = prepared
    text = notification_text(event, score)
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
            book.finish(event, "pending", "rate_limited", due_ms=ready_at + delay * 1000)
        else:
            book.finish(event, "failed", "telegram_rejected")
    except Exception:
        book.finish(event, "unknown", "delivery_uncertain_no_automatic_resend")
    return True
