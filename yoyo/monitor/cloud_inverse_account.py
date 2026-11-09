"""Durable, exchange-free paper ledger for the inverse ETH/BTC cloud account.

The account only accepts newly observed signal records and actual public quote
timestamps. It never seeds historic trades or writes to the source signal DB.
Hourly OHLC fills are explicitly labelled with their bar clock and the later
time at which the runner observed the completed bar.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
import threading
from typing import Any


HOUR_MS = 3_600_000
MINUTE_MS = 60_000
STEP_12H_MS = 12 * HOUR_MS
DAY_MS = 24 * HOUR_MS
START_EQUITY = 1_000.0
SLOTS = 9
LEVERAGE = 1.5
HOURLY_CAP = 0.20
TAKER_FEE = 0.0005
MAKER_FEE = 0.0002
TARGET_2R_FRACTION = 2.0 / 3.0
LEG_WEIGHTS = (1.0 / 3.0, 1.0 / 3.0, 1.0 / 6.0, 1.0 / 6.0)
LEG_NAMES = ("target_2r", "target_2r_then_double", "double_runner", "runner")
TRACK_KEY = "live-cloud-v17"

RULE = {
    "track": TRACK_KEY,
    "mode": "paper",
    "start_equity_usdt": START_EQUITY,
    "seed_historical_positions": False,
    "source": "local cloud_signals.sqlite3; immutable source rows only",
    "entry": "new nonhistorical V1.7 signal; inverse ETH/BTC 30d regime; same-clock 90d rankable universe >=10",
    "priority": "same confirmation boundary, ascending prior 12H ATR14/close, then CRC32(symbol), symbol, event id",
    "post_quote_protection": "poll fresh public ticker until post-entry confirmed 1m bars cover the entry hour; exclude the 1m bucket containing the quote",
    "slots": SLOTS,
    "leverage": LEVERAGE,
    "exit": "fixed signal stop; latest confirmed 12H EMA20/EMA50 cross; 2R for 2/3 in first 12h; long double limit for half remaining",
    "hourly_single_name_gross_notional_cap": HOURLY_CAP,
    "fees": {"taker": TAKER_FEE, "maker": MAKER_FEE},
    "funding": "omitted, same as selected historical source",
    "private_or_order_api": False,
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


class CloudInversePaper:
    """Single-writer SQLite account with idempotent source and hourly cursors."""

    def __init__(self, runtime: str | Path, *, clock=None):
        self.runtime = Path(runtime)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.path = self.runtime / "live-cloud-v17-paper.sqlite3"
        self.clock = clock or (lambda: __import__("time").time_ns() // 1_000_000)
        self.write_lock = threading.RLock()
        self._initialize()

    @contextmanager
    def db(self):
        conn = sqlite3.connect(str(self.path), timeout=20)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self):
        now = int(self.clock())
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_events (
                    event_id TEXT PRIMARY KEY,
                    boundary_ms INTEGER NOT NULL,
                    signal_open_ms INTEGER NOT NULL,
                    confirmed_ms INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    side INTEGER NOT NULL,
                    source_detected_ms INTEGER NOT NULL,
                    first_observed_ms INTEGER NOT NULL,
                    rankable_count INTEGER,
                    relative_30d REAL,
                    prior_atr_pct REAL,
                    entry_order INTEGER,
                    status TEXT NOT NULL,
                    reason TEXT,
                    quote_ts_ms INTEGER,
                    entry_price REAL,
                    payload TEXT NOT NULL,
                    created_ms INTEGER NOT NULL,
                    updated_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS source_events_boundary_idx
                    ON source_events(boundary_ms, status, prior_atr_pct);
                CREATE TABLE IF NOT EXISTS positions (
                    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE REFERENCES source_events(event_id),
                    symbol TEXT NOT NULL,
                    side INTEGER NOT NULL,
                    slot INTEGER NOT NULL,
                    signal_open_ms INTEGER NOT NULL,
                    confirmed_ms INTEGER NOT NULL,
                    source_detected_ms INTEGER NOT NULL,
                    first_observed_ms INTEGER NOT NULL,
                    entry_ms INTEGER NOT NULL,
                    quote_ts_ms INTEGER NOT NULL,
                    entry_observed_ms INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_bid REAL NOT NULL,
                    entry_ask REAL NOT NULL,
                    stop_price REAL NOT NULL,
                    risk_distance REAL NOT NULL,
                    leverage REAL NOT NULL,
                    margin_usdt REAL NOT NULL,
                    initial_notional_usdt REAL NOT NULL,
                    initial_qty REAL NOT NULL,
                    remaining_qty REAL NOT NULL,
                    entry_fee REAL NOT NULL,
                    realized_pnl REAL NOT NULL,
                    last_mark REAL NOT NULL,
                    last_mark_ms INTEGER NOT NULL,
                    next_bar_open_ms INTEGER NOT NULL,
                    last_processed_bar_open_ms INTEGER,
                    next_minute_open_ms INTEGER NOT NULL,
                    last_processed_minute_open_ms INTEGER,
                    minute_protection_until_ms INTEGER NOT NULL,
                    first_protection_quote_ms INTEGER,
                    first_protection_observed_ms INTEGER,
                    last_protection_quote_ms INTEGER,
                    target_2r_expiry_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    exit_ms INTEGER,
                    exit_price REAL,
                    exit_reason TEXT,
                    exit_observed_ms INTEGER,
                    created_ms INTEGER NOT NULL,
                    updated_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS positions_status_idx ON positions(status, slot);
                CREATE TABLE IF NOT EXISTS legs (
                    position_id INTEGER NOT NULL REFERENCES positions(position_id),
                    leg_no INTEGER NOT NULL,
                    leg_name TEXT NOT NULL,
                    weight REAL NOT NULL,
                    initial_qty REAL NOT NULL,
                    remaining_qty REAL NOT NULL,
                    target_kind TEXT NOT NULL,
                    PRIMARY KEY(position_id, leg_no)
                );
                CREATE TABLE IF NOT EXISTS fills (
                    fill_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    position_id INTEGER NOT NULL REFERENCES positions(position_id),
                    leg_no INTEGER,
                    time_ms INTEGER NOT NULL,
                    observed_ms INTEGER NOT NULL,
                    bar_open_ms INTEGER,
                    phase TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    side INTEGER NOT NULL,
                    qty REAL NOT NULL,
                    price REAL NOT NULL,
                    fee_rate REAL NOT NULL,
                    fee REAL NOT NULL,
                    net_pnl REAL NOT NULL,
                    metadata TEXT NOT NULL,
                    UNIQUE(event_id, leg_no, time_ms, phase, kind)
                );
                CREATE INDEX IF NOT EXISTS fills_time_idx ON fills(time_ms DESC, fill_id DESC);
                CREATE TABLE IF NOT EXISTS market_bars (
                    symbol TEXT NOT NULL,
                    bar_open_ms INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    observed_ms INTEGER NOT NULL,
                    PRIMARY KEY(symbol, bar_open_ms)
                );
                CREATE TABLE IF NOT EXISTS account_marks (
                    at_ms INTEGER PRIMARY KEY,
                    equity REAL NOT NULL,
                    realized_pnl REAL NOT NULL,
                    unrealized_pnl REAL NOT NULL,
                    open_positions INTEGER NOT NULL,
                    max_equity REAL NOT NULL
                );
            """)
            db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('started_ms',?)", (str(now),))
            db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('start_equity',?)", (str(START_EQUITY),))
            db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('rule',?)", (_json(RULE),))
            db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('last_hour_close_ms','0')")
            db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('max_equity',?)", (str(START_EQUITY),))
            columns = {row[1] for row in db.execute("PRAGMA table_info(positions)")}
            if "next_minute_open_ms" not in columns:
                db.execute("ALTER TABLE positions ADD COLUMN next_minute_open_ms INTEGER NOT NULL DEFAULT 0")
            if "last_processed_minute_open_ms" not in columns:
                db.execute("ALTER TABLE positions ADD COLUMN last_processed_minute_open_ms INTEGER")
            if "minute_protection_until_ms" not in columns:
                db.execute("ALTER TABLE positions ADD COLUMN minute_protection_until_ms INTEGER NOT NULL DEFAULT 0")
            if "first_protection_quote_ms" not in columns:
                db.execute("ALTER TABLE positions ADD COLUMN first_protection_quote_ms INTEGER")
            if "first_protection_observed_ms" not in columns:
                db.execute("ALTER TABLE positions ADD COLUMN first_protection_observed_ms INTEGER")
            if "last_protection_quote_ms" not in columns:
                db.execute("ALTER TABLE positions ADD COLUMN last_protection_quote_ms INTEGER")

    def _meta(self, db, key: str, default: str = "") -> str:
        row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def started_ms(self) -> int:
        with self.db() as db:
            return int(self._meta(db, "started_ms"))

    def open_positions(self) -> list[dict[str, Any]]:
        with self.db() as db:
            rows = db.execute("SELECT * FROM positions WHERE status='open' ORDER BY slot").fetchall()
            return [dict(row) for row in rows]

    def queue_source_event(self, candidate: dict[str, Any], *, first_observed_ms: int) -> bool:
        """Persist a new source row once; old or historical rows are ignored."""
        started = self.started_ms()
        event_id = candidate.get("event_id") or candidate.get("id")
        detected = candidate.get("source_detected_ms")
        required = (event_id, candidate.get("symbol"), candidate.get("side"),
                    candidate.get("signal_open_ms"), candidate.get("confirmed_ms"), detected)
        if any(value is None for value in required):
            return False
        if (candidate.get("historical") is not False or int(detected) < started
                or int(candidate["confirmed_ms"]) < started):
            return False
        now = int(first_observed_ms)
        with self.write_lock, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            inserted = db.execute("""INSERT OR IGNORE INTO source_events(
                event_id,boundary_ms,signal_open_ms,confirmed_ms,symbol,side,source_detected_ms,
                first_observed_ms,status,payload,created_ms,updated_ms)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (str(event_id), int(candidate["confirmed_ms"]), int(candidate["signal_open_ms"]),
                 int(candidate["confirmed_ms"]), str(candidate["symbol"]), int(candidate["side"]),
                 int(detected), now, "queued", _json(candidate), now, now)).rowcount
            return inserted == 1

    def queued_boundaries(self) -> list[int]:
        with self.db() as db:
            return [int(row[0]) for row in db.execute(
                "SELECT DISTINCT boundary_ms FROM source_events WHERE status='queued' ORDER BY boundary_ms")]

    def queued_candidates(self, boundary_ms: int) -> list[dict[str, Any]]:
        with self.db() as db:
            rows = db.execute("SELECT * FROM source_events WHERE boundary_ms=? AND status='queued' ORDER BY event_id",
                              (int(boundary_ms),)).fetchall()
            return [dict(row) for row in rows]

    def decide_event(self, event_id: str, *, status: str, reason: str | None,
                     rankable_count: int | None = None, relative_30d: float | None = None,
                     prior_atr_pct: float | None = None, entry_order: int | None = None,
                     quote: dict[str, Any] | None = None, position: dict[str, Any] | None = None,
                     now_ms: int | None = None) -> dict[str, Any]:
        """Atomically record a terminal decision and optional paper entry."""
        now = int(self.clock() if now_ms is None else now_ms)
        with self.write_lock, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM source_events WHERE event_id=?", (str(event_id),)).fetchone()
            if row is None:
                raise KeyError(event_id)
            if row["status"] != "queued":
                return {"created": False, "status": row["status"], "reason": row["reason"]}
            quote_ts = int(quote["ts_ms"]) if quote else None
            entry_price = float(quote["price"]) if quote else None
            db.execute("""UPDATE source_events SET rankable_count=?,relative_30d=?,prior_atr_pct=?,entry_order=?,
                status=?,reason=?,quote_ts_ms=?,entry_price=?,updated_ms=? WHERE event_id=? AND status='queued'""",
                (rankable_count, relative_30d, prior_atr_pct, entry_order, status, reason,
                 quote_ts, entry_price, now, str(event_id)))
            if position is not None:
                self._open_position_tx(db, row, position, quote, now)
            return {"created": True, "status": status, "reason": reason}

    def _open_position_tx(self, db, event, position, quote, now):
        equity = self._equity_tx(db)
        if equity <= 0:
            raise ValueError("nonpositive_equity")
        open_rows = db.execute("SELECT symbol,slot FROM positions WHERE status='open' ORDER BY slot").fetchall()
        busy_symbols = {row["symbol"] for row in open_rows}
        occupied = {int(row["slot"]) for row in open_rows}
        if event["symbol"] in busy_symbols:
            raise ValueError("same_contract_busy")
        free = sorted(set(range(SLOTS)) - occupied)
        if not free:
            raise ValueError("all_slots_busy")
        slot = free[0]
        side = int(event["side"])
        price = float(quote["price"])
        stop = float(position["stop_price"])
        risk = side * (price - stop)
        if not math.isfinite(risk) or risk <= 0:
            raise ValueError("entry_beyond_signal_stop")
        free_slots = SLOTS - len(open_rows)
        start = float(self._meta(db, "start_equity", str(START_EQUITY)))
        realized = float(db.execute("SELECT COALESCE(sum(realized_pnl),0) FROM positions").fetchone()[0])
        locked_margin = float(db.execute("""SELECT COALESCE(sum(initial_notional_usdt/leverage *
                    remaining_qty/initial_qty),0) FROM positions WHERE status='open'""").fetchone()[0])
        free_cash = max(0., start + realized - locked_margin)
        margin_budget = min(equity / SLOTS, free_cash / free_slots) if free_slots else 0.
        if margin_budget <= 0:
            raise ValueError("insufficient_free_cash")
        # Reserve the collateral-share fee at the configured leverage before
        # translating the ordinary slot margin into underlying notional.
        actual_margin = margin_budget / (1. + LEVERAGE * TAKER_FEE)
        initial_notional = actual_margin * LEVERAGE
        margin = actual_margin
        qty = initial_notional / price
        entry_fee = initial_notional * TAKER_FEE
        quote_ts_ms = int(quote["ts_ms"])
        # The ticker timestamp is its last book update. Record the fill at the
        # calibrated local/OKX clock after the request, preserving both clocks.
        entry_ms = int(now)
        pos_cur = db.execute("""INSERT INTO positions(
            event_id,symbol,side,slot,signal_open_ms,confirmed_ms,source_detected_ms,first_observed_ms,
            entry_ms,quote_ts_ms,entry_observed_ms,entry_price,entry_bid,entry_ask,stop_price,risk_distance,
            leverage,margin_usdt,initial_notional_usdt,initial_qty,remaining_qty,entry_fee,realized_pnl,
            last_mark,last_mark_ms,next_bar_open_ms,target_2r_expiry_ms,status,created_ms,updated_ms,
            next_minute_open_ms,minute_protection_until_ms)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event["event_id"], event["symbol"], side, slot, event["signal_open_ms"], event["confirmed_ms"],
             event["source_detected_ms"], event["first_observed_ms"], entry_ms, quote_ts_ms, now, price,
             float(quote["bid"]), float(quote["ask"]), stop, risk, LEVERAGE, margin, initial_notional, qty, qty,
             entry_fee, -entry_fee, price, entry_ms, (entry_ms // HOUR_MS + 1) * HOUR_MS,
             entry_ms + STEP_12H_MS, "open", now, now,
             (entry_ms // MINUTE_MS + 1) * MINUTE_MS,
             (entry_ms // HOUR_MS + 1) * HOUR_MS))
        position_id = int(pos_cur.lastrowid)
        for leg_no, (weight, name) in enumerate(zip(LEG_WEIGHTS, LEG_NAMES)):
            leg_qty = qty * weight
            target_kind = ("2r_then_double" if leg_no == 1 else
                           "2r" if leg_no == 0 else "double" if leg_no == 2 else "none")
            db.execute("INSERT INTO legs VALUES(?,?,?,?,?,?,?)",
                       (position_id, leg_no, name, weight, leg_qty, leg_qty, target_kind))
        db.execute("""INSERT INTO fills(event_id,position_id,leg_no,time_ms,observed_ms,phase,kind,side,qty,
                   price,fee_rate,fee,net_pnl,metadata) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (event["event_id"], position_id, None, entry_ms, now, "quote", "entry", side, qty,
                    price, TAKER_FEE, entry_fee, -entry_fee,
                    _json({"bid": quote["bid"], "ask": quote["ask"], "quote_ts_ms": quote_ts_ms,
                           "source_detected_ms": event["source_detected_ms"],
                           "first_observed_ms": event["first_observed_ms"], "signal_confirmed_ms": event["confirmed_ms"]})))

    def mark_missed(self, event_id: str, reason: str, *, rankable_count=None,
                    relative_30d=None, prior_atr_pct=None, entry_order=None, now_ms=None):
        return self.decide_event(event_id, status="missed", reason=reason,
                                 rankable_count=rankable_count, relative_30d=relative_30d,
                                 prior_atr_pct=prior_atr_pct, entry_order=entry_order, now_ms=now_ms)

    def mark_skipped(self, event_id: str, reason: str, *, rankable_count=None,
                     relative_30d=None, prior_atr_pct=None, entry_order=None, now_ms=None):
        return self.decide_event(event_id, status="skipped", reason=reason,
                                 rankable_count=rankable_count, relative_30d=relative_30d,
                                 prior_atr_pct=prior_atr_pct, entry_order=entry_order, now_ms=now_ms)

    def attempt_entry(self, candidate: dict[str, Any], quote: dict[str, Any], *,
                      rankable_count: int, relative_30d: float,
                      prior_atr_pct: float, entry_order: int, now_ms: int,
                      max_signal_delay_ms: int, max_quote_age_ms: int) -> dict[str, Any]:
        event_id = str(candidate["event_id"])
        signal_delay = int(now_ms) - int(candidate["confirmed_ms"])
        quote_age = int(now_ms) - int(quote.get("ts_ms", -1)) if quote else None
        if signal_delay < 0 or signal_delay > max_signal_delay_ms:
            return self.mark_missed(event_id, "signal_batch_expired", rankable_count=rankable_count,
                                    relative_30d=relative_30d, prior_atr_pct=prior_atr_pct, now_ms=now_ms)
        if (not quote or not _finite(quote.get("bid")) or not _finite(quote.get("ask"))
                or float(quote["bid"]) <= 0 or float(quote["ask"]) < float(quote["bid"])):
            return self.mark_missed(event_id, "missing_or_invalid_public_quote", rankable_count=rankable_count,
                                    relative_30d=relative_30d, prior_atr_pct=prior_atr_pct, now_ms=now_ms)
        if quote_age is not None and (quote_age < 0 or quote_age > max_quote_age_ms):
            return self.mark_missed(event_id, "ticker_stale_at_observation", rankable_count=rankable_count,
                                    relative_30d=relative_30d, prior_atr_pct=prior_atr_pct, now_ms=now_ms)
        price = float(quote["ask"] if int(candidate["side"]) > 0 else quote["bid"])
        position = {"stop_price": candidate.get("stop_price")}
        try:
            result = self.decide_event(event_id, status="opened", reason=None,
                                       rankable_count=rankable_count, relative_30d=relative_30d,
                                       prior_atr_pct=prior_atr_pct, entry_order=entry_order,
                                       quote={"ts_ms": quote["ts_ms"], "bid": quote["bid"], "ask": quote["ask"], "price": price},
                                       position=position, now_ms=now_ms)
        except ValueError as exc:
            reason = str(exc)
            return self.mark_skipped(event_id, reason, rankable_count=rankable_count,
                                     relative_30d=relative_30d, prior_atr_pct=prior_atr_pct,
                                     entry_order=entry_order, now_ms=now_ms)
        return result

    def _equity_tx(self, db) -> float:
        start = float(self._meta(db, "start_equity", str(START_EQUITY)))
        rows = db.execute("SELECT side,entry_price,remaining_qty,last_mark,realized_pnl,status FROM positions").fetchall()
        realized = sum(float(row["realized_pnl"]) for row in rows)
        unrealized = sum(int(row["side"]) * (float(row["last_mark"]) - float(row["entry_price"]))
                         * float(row["remaining_qty"]) for row in rows if row["status"] == "open")
        return start + realized + unrealized

    def equity(self) -> float:
        with self.db() as db:
            return self._equity_tx(db)

    def post_entry_protection_pending(self, now_ms: int) -> list[dict[str, Any]]:
        """Open entries whose post-quote 1m protection interval is still uncovered."""
        with self.db() as db:
            rows = db.execute("""SELECT * FROM positions WHERE status='open'
                AND minute_protection_until_ms>0 AND ? >= minute_protection_until_ms
                AND next_minute_open_ms < minute_protection_until_ms ORDER BY slot""",
                (int(now_ms),)).fetchall()
            return [dict(row) for row in rows]

    def process_public_quote(self, symbol: str, quote: dict[str, Any], *, observed_ms: int,
                             ema: dict[str, Any] | None = None,
                             max_age_ms: int = 15_000) -> dict[str, Any]:
        """Use a fresh observed public quote to protect positions in their entry hour."""
        now = int(observed_ms)
        try:
            quote_ts, bid, ask = int(quote["ts_ms"]), float(quote["bid"]), float(quote["ask"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return {"processed": 0, "fills": 0, "reason": "invalid_quote"}
        if (not all(math.isfinite(v) and v > 0 for v in (bid, ask)) or ask < bid
                or now < quote_ts or now-quote_ts > max_age_ms):
            return {"processed": 0, "fills": 0, "reason": "stale_or_invalid_quote"}
        ema = ema or {}
        cross, spread = ema.get("cross_line"), ema.get("ema_spread")
        with self.write_lock, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("""SELECT * FROM positions WHERE status='open' AND symbol=?
                AND ? >= entry_ms AND ? < minute_protection_until_ms
                AND (last_protection_quote_ms IS NULL OR last_protection_quote_ms < ?) ORDER BY slot""",
                (str(symbol), now, now, quote_ts)).fetchall()
            before = int(db.execute("SELECT count(*) FROM fills").fetchone()[0])
            processed = 0
            for original in rows:
                pos = db.execute("SELECT * FROM positions WHERE position_id=?", (original["position_id"],)).fetchone()
                if pos["status"] != "open":
                    continue
                side = int(pos["side"])
                executable = bid if side > 0 else ask
                alpha20, alpha50 = 2./21., 2./51.
                ready = _finite(cross) and _finite(spread)
                eps = 0.
                if ready:
                    ema50 = float(cross)+(1.-alpha20)*float(spread)/(alpha20-alpha50)
                    eps = 8.*2.220446049250313e-16*max(1.,abs(ema50),abs(ema50+float(spread)))/(alpha20-alpha50)
                reason = None
                if side*(executable-float(pos["stop_price"])) <= 0:
                    reason = "stop_public_quote"
                elif ready and ((executable-float(cross))*side < -eps or side*float(spread) < 0):
                    reason = "ema_flip_public_quote"
                legs = db.execute("SELECT * FROM legs WHERE position_id=? AND remaining_qty>0 ORDER BY leg_no",
                                  (pos["position_id"],)).fetchall()
                for leg in legs:
                    current = db.execute("SELECT * FROM positions WHERE position_id=?", (pos["position_id"],)).fetchone()
                    if current["status"] != "open":
                        break
                    remaining = float(db.execute("SELECT remaining_qty FROM legs WHERE position_id=? AND leg_no=?",
                                                 (pos["position_id"], leg["leg_no"])).fetchone()[0])
                    if remaining <= 0:
                        continue
                    if reason:
                        self._close_leg_tx(db, pos, int(leg["leg_no"]), remaining, executable,
                                           now, now, reason, "public_quote", TAKER_FEE, None)
                        continue
                    targets = self._leg_targets(pos, leg, quote_ts)
                    hit = [target for target in targets if (executable-target)*side >= 0]
                    if hit:
                        target = min(hit, key=lambda value: side*value)
                        self._close_leg_tx(db, pos, int(leg["leg_no"]), remaining, target,
                                           now, now, "take_profit_public_quote", "public_quote",
                                           MAKER_FEE, None)
                remaining_total = float(db.execute("SELECT COALESCE(sum(remaining_qty),0) FROM legs WHERE position_id=?",
                                                   (pos["position_id"],)).fetchone()[0])
                status = "closed" if remaining_total <= 1e-12 else "open"
                mark = executable
                db.execute("""UPDATE positions SET status=?,last_mark=?,last_mark_ms=?,
                    first_protection_quote_ms=COALESCE(first_protection_quote_ms,?),last_protection_quote_ms=?,
                    first_protection_observed_ms=COALESCE(first_protection_observed_ms,?),
                    exit_observed_ms=CASE WHEN ?='closed' THEN ? ELSE exit_observed_ms END,updated_ms=?
                    WHERE position_id=?""",
                    (status, mark, now, quote_ts, quote_ts, now, status, now, now, pos["position_id"]))
                processed += 1
            after = int(db.execute("SELECT count(*) FROM fills").fetchone()[0])
            equity = self._equity_tx(db)
            realized = float(db.execute("SELECT COALESCE(sum(realized_pnl),0) FROM positions").fetchone()[0])
            max_equity = max(float(self._meta(db, "max_equity", str(START_EQUITY))), equity)
            db.execute("UPDATE meta SET value=? WHERE key='max_equity'", (str(max_equity),))
            db.execute("INSERT OR REPLACE INTO account_marks VALUES(?,?,?,?,?,?)",
                       (now, equity, realized, equity-START_EQUITY-realized,
                        int(db.execute("SELECT count(*) FROM positions WHERE status='open'").fetchone()[0]), max_equity))
            return {"processed": processed, "fills": after-before}

    @staticmethod
    def _leg_targets(pos, leg, at_ms: int) -> list[float]:
        side = int(pos["side"])
        targets = []
        if leg["target_kind"] in ("2r", "2r_then_double") and int(at_ms) < int(pos["target_2r_expiry_ms"]):
            targets.append(float(pos["entry_price"])+side*2.*float(pos["risk_distance"]))
        if side > 0 and leg["target_kind"] in ("2r_then_double", "double"):
            targets.append(2.*float(pos["entry_price"]))
        return targets

    def process_hour_bars(self, bar_open_ms: int, bars_by_symbol: dict[str, dict[str, Any]], *,
                          observed_ms: int, ema_by_symbol: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """Apply one or more completed H1 bars exactly once.

        H1 open is the bucket clock for framework-compatible intrabar fills;
        ``observed_ms`` always records when the runner actually received it.
        Missing bars are not synthesized. A later observed bar is handled as a
        gap markout by the position's previous processed H1 clock.
        """
        if not bars_by_symbol:
            return {"processed": 0, "fills": 0, "cap_skipped": False}
        now = int(observed_ms)
        bar_open = int(bar_open_ms)
        for symbol, bar in bars_by_symbol.items():
            values = [float(bar[k]) for k in ("open", "high", "low", "close")]
            if not all(math.isfinite(v) and v > 0 for v in values):
                raise ValueError("invalid_hourly_price")
            if values[1] < max(values[0], values[2], values[3]) or values[2] > min(values[0], values[1], values[3]):
                raise ValueError("invalid_hourly_ohlc")
        ema_by_symbol = ema_by_symbol or {}
        with self.write_lock, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            open_rows = db.execute("SELECT * FROM positions WHERE status='open' ORDER BY slot").fetchall()
            prior_nav = self._equity_tx(db)
            fills_before = int(db.execute("SELECT count(*) FROM fills").fetchone()[0])
            eligible = [p for p in open_rows if int(p["next_bar_open_ms"]) <= bar_open]
            missing = sorted({p["symbol"] for p in eligible if p["symbol"] not in bars_by_symbol})
            cap_skipped = bool(eligible and missing)
            for pos in eligible:
                raw = bars_by_symbol.get(pos["symbol"])
                if raw is None:
                    continue
                if pos["last_processed_bar_open_ms"] is not None and bar_open <= int(pos["last_processed_bar_open_ms"]):
                    continue
                bar = {k: float(raw[k]) for k in ("open", "high", "low", "close")}
                db.execute("INSERT OR IGNORE INTO market_bars VALUES(?,?,?,?,?,?,?)",
                           (pos["symbol"], bar_open, bar["open"], bar["high"], bar["low"], bar["close"], now))
                features = ema_by_symbol.get(pos["symbol"]) or {}
                self._process_position_open_tx(db, pos, bar_open, bar, now, features)

            # Hourly cap uses previous-close NAV and quantity, then fills at the
            # actual H1 open after open-triggered exits and before intrabar exits.
            if eligible and not missing:
                for original in eligible:
                    pos = db.execute("SELECT * FROM positions WHERE position_id=?", (original["position_id"],)).fetchone()
                    if pos["status"] != "open" or int(pos["last_processed_bar_open_ms"] or -1) >= bar_open:
                        continue
                    previous_price = float(pos["last_mark"])
                    previous_nav = max(0., prior_nav)
                    gross = float(pos["remaining_qty"]) * previous_price
                    cap = previous_nav * HOURLY_CAP
                    if previous_price <= 0 or gross <= cap + 1e-9:
                        continue
                    opening = float(bars_by_symbol[pos["symbol"]]["open"])
                    target_qty = min(float(pos["remaining_qty"]), cap / previous_price)
                    trim_qty = max(0., float(pos["remaining_qty"]) - target_qty)
                    if trim_qty > max(1e-12, float(pos["initial_qty"]) * 1e-12):
                        self._trim_position_tx(db, pos, trim_qty, opening, bar_open, now, prior_nav, bar_open)

            for original in eligible:
                pos = db.execute("SELECT * FROM positions WHERE position_id=?", (original["position_id"],)).fetchone()
                raw = bars_by_symbol.get(pos["symbol"])
                if raw is None or pos["status"] != "open":
                    continue
                if pos["last_processed_bar_open_ms"] is not None and bar_open <= int(pos["last_processed_bar_open_ms"]):
                    continue
                bar = {k: float(raw[k]) for k in ("open", "high", "low", "close")}
                features = ema_by_symbol.get(pos["symbol"]) or {}
                self._process_position_intrabar_tx(db, pos, bar_open, bar, now, features)

            new_equity = self._equity_tx(db)
            realized = float(db.execute("SELECT COALESCE(sum(realized_pnl),0) FROM positions").fetchone()[0])
            unrealized = new_equity - START_EQUITY - realized
            previous_peak = float(self._meta(db, "max_equity", str(START_EQUITY)))
            peak = max(previous_peak, new_equity)
            db.execute("UPDATE meta SET value=? WHERE key='max_equity'", (str(peak),))
            db.execute("INSERT OR REPLACE INTO account_marks VALUES(?,?,?,?,?,?)",
                       (bar_open+HOUR_MS, new_equity, realized, unrealized,
                        int(db.execute("SELECT count(*) FROM positions WHERE status='open'").fetchone()[0]), peak))
            db.execute("UPDATE meta SET value=? WHERE key='last_hour_close_ms'",
                       (str(max(int(self._meta(db, "last_hour_close_ms", "0")), bar_open+HOUR_MS)),))
            fills_after = int(db.execute("SELECT count(*) FROM fills").fetchone()[0])
            return {"processed": sum(1 for p in eligible if p["symbol"] in bars_by_symbol),
                    "fills": fills_after-fills_before, "cap_skipped": cap_skipped,
                    "missing_symbols": missing, "equity": new_equity}

    def process_minute_bars(self, bar_open_ms: int, bars_by_symbol: dict[str, dict[str, Any]], *,
                            observed_ms: int, ema_by_symbol: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """Protect the unclosed entry hour using only 1m bars wholly after entry."""
        if not bars_by_symbol:
            return {"processed": 0, "fills": 0}
        now, bar_open = int(observed_ms), int(bar_open_ms)
        ema_by_symbol = ema_by_symbol or {}
        with self.write_lock, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            before = int(db.execute("SELECT count(*) FROM fills").fetchone()[0])
            rows = db.execute("SELECT * FROM positions WHERE status='open' AND next_minute_open_ms<=? ORDER BY slot",
                              (bar_open,)).fetchall()
            processed = 0
            for pos in rows:
                if bar_open >= int(pos["minute_protection_until_ms"]):
                    continue
                raw = bars_by_symbol.get(pos["symbol"])
                if raw is None:
                    continue
                if pos["last_processed_minute_open_ms"] is not None and bar_open <= int(pos["last_processed_minute_open_ms"]):
                    continue
                bar = {k: float(raw[k]) for k in ("open", "high", "low", "close")}
                db.execute("INSERT OR IGNORE INTO market_bars VALUES(?,?,?,?,?,?,?)",
                           (pos["symbol"], bar_open, bar["open"], bar["high"], bar["low"], bar["close"], now))
                features = ema_by_symbol.get(pos["symbol"]) or {}
                gap = bar_open > int(pos["next_minute_open_ms"])
                self._process_interval_exits_tx(db, pos, bar_open, bar, now, features,
                                                gap=gap, phase="open")
                live = db.execute("SELECT * FROM positions WHERE position_id=?", (pos["position_id"],)).fetchone()
                if live["status"] == "open":
                    self._process_interval_exits_tx(db, live, bar_open, bar, now, features,
                                                    gap=gap, phase="intrabar")
                remaining = db.execute("SELECT COALESCE(sum(remaining_qty),0) FROM legs WHERE position_id=?",
                                       (pos["position_id"],)).fetchone()[0]
                status = "closed" if float(remaining) <= 1e-12 else "open"
                db.execute("""UPDATE positions SET status=?,last_mark=?,last_mark_ms=?,
                            next_minute_open_ms=?,last_processed_minute_open_ms=?,exit_observed_ms=?,
                            updated_ms=? WHERE position_id=?""",
                           (status, bar["close"], bar_open+MINUTE_MS, bar_open+MINUTE_MS, bar_open,
                            now if status == "closed" else None, now, pos["position_id"]))
                processed += 1
            after = int(db.execute("SELECT count(*) FROM fills").fetchone()[0])
            return {"processed": processed, "fills": after-before}

    def _process_position_open_tx(self, db, pos, bar_open, bar, observed, features):
        current = db.execute("SELECT * FROM positions WHERE position_id=?", (pos["position_id"],)).fetchone()
        if current["status"] != "open":
            return
        last = current["last_processed_bar_open_ms"]
        gap = bar_open > int(current["next_bar_open_ms"]) if last is None else bar_open-int(last)>HOUR_MS
        self._process_interval_exits_tx(db, current, bar_open, bar, observed, features,
                                        gap=gap, phase="open")

    def _process_position_intrabar_tx(self, db, pos, bar_open, bar, observed, features):
        current = db.execute("SELECT * FROM positions WHERE position_id=?", (pos["position_id"],)).fetchone()
        if current["status"] != "open":
            return
        last = current["last_processed_bar_open_ms"]
        gap = bar_open > int(current["next_bar_open_ms"]) if last is None else bar_open-int(last)>HOUR_MS
        self._process_interval_exits_tx(db, current, bar_open, bar, observed, features,
                                        gap=gap, phase="intrabar")
        remaining = db.execute("SELECT COALESCE(sum(remaining_qty),0) FROM legs WHERE position_id=?",
                               (current["position_id"],)).fetchone()[0]
        status = "closed" if float(remaining) <= 1e-12 else "open"
        db.execute("""UPDATE positions SET status=?,last_mark=?,last_mark_ms=?,last_processed_bar_open_ms=?,
                    next_bar_open_ms=?,exit_observed_ms=CASE WHEN ?='closed' THEN ? ELSE exit_observed_ms END,
                    updated_ms=? WHERE position_id=?""",
                   (status, bar["close"], bar_open+HOUR_MS, bar_open, bar_open+HOUR_MS,
                    status, observed, observed, current["position_id"]))

    def _process_interval_exits_tx(self, db, pos, bar_open, bar, observed, features, *, gap, phase):
        side = int(pos["side"])
        stale = bool(features.get("stale"))
        cross, spread = features.get("cross_line"), features.get("ema_spread")
        ready = _finite(cross) and _finite(spread)
        eps = 0.
        if ready:
            alpha20, alpha50 = 2./21., 2./51.
            ema50 = float(cross)+(1.-alpha20)*float(spread)/(alpha20-alpha50)
            eps = 8.*2.220446049250313e-16*max(1.,abs(ema50),abs(ema50+float(spread)))/(alpha20-alpha50)
        legs = db.execute("SELECT * FROM legs WHERE position_id=? AND remaining_qty>0 ORDER BY leg_no",
                          (pos["position_id"],)).fetchall()
        for leg in legs:
            live = db.execute("SELECT * FROM positions WHERE position_id=?", (pos["position_id"],)).fetchone()
            if live["status"] != "open":
                break
            remaining = float(db.execute("SELECT remaining_qty FROM legs WHERE position_id=? AND leg_no=?",
                                         (pos["position_id"], leg["leg_no"])).fetchone()[0])
            if remaining <= 0:
                continue
            reason, price, fill_phase, fee = self._leg_exit(pos, leg, bar_open, bar, side,
                gap=gap, stale=stale, cross=cross, spread=spread, ready=ready, eps=eps, phase=phase)
            if reason:
                self._close_leg_tx(db, pos, int(leg["leg_no"]), remaining, price,
                                   bar_open, observed, reason, fill_phase, fee, bar_open)

    def _leg_exit(self, pos, leg, bar_open, bar, side, *, gap, stale, cross, spread, ready, eps, phase):
        op, hi, lo, close = (bar[k] for k in ("open", "high", "low", "close"))
        stop = float(pos["stop_price"])
        targets = []
        if leg["target_kind"] in ("2r", "2r_then_double") and bar_open < int(pos["target_2r_expiry_ms"]):
            targets.append(("2r", float(pos["entry_price"]) + side * 2.0 * float(pos["risk_distance"])))
        if side > 0 and leg["target_kind"] in ("2r_then_double", "double"):
            targets.append(("double", 2.0 * float(pos["entry_price"])))
        # A child with both resting limits exits at the nearest favorable price,
        # including an open gap that crosses both levels.
        target = (min((x[1] for x in targets), key=lambda x: side*x)
                  if targets else None)
        adverse = lo if side > 0 else hi
        favorable = hi if side > 0 else lo
        stop_open = (op-stop)*side <= 0
        flip_open = ready and (op-float(cross))*side < -eps
        trend_flip = ready and bar_open > int(pos["entry_ms"]) and side*float(spread) < 0
        target_open = target is not None and (op-target)*side >= 0
        hit_stop = (adverse-stop)*side <= 0
        hit_flip = ready and (adverse-float(cross))*side < -eps
        hit_target = target is not None and (favorable-target)*side >= 0
        # Mirrors the selected framework's first-bar protection and price ordering.
        if phase == "open":
            if gap:
                return "data_gap_markout", op, "open", TAKER_FEE
            if stale:
                return "stale_12h_markout", op, "open", TAKER_FEE
            if stop_open:
                return "stop_gap_open", op, "open", TAKER_FEE
            if flip_open or trend_flip:
                return "ema_flip_open", op, "open", TAKER_FEE
            if target_open:
                return "take_profit_open", target, "open", MAKER_FEE
            return None, None, None, None
        if phase != "intrabar":
            raise ValueError("invalid_exit_phase")
        if gap or stale or stop_open or flip_open or trend_flip or target_open:
            return None, None, None, None
        if hit_stop or hit_flip:
            cloud_first = hit_flip and (not hit_stop or (float(cross)-stop)*side > 0.)
            return (("ema_flip", float(cross), "intrabar", TAKER_FEE) if cloud_first else
                    ("stop", stop, "intrabar", TAKER_FEE))
        if hit_target:
            return "take_profit", target, "intrabar", MAKER_FEE
        return None, None, None, None

    def _close_leg_tx(self, db, pos, leg_no, qty, price, time_ms, observed_ms,
                      reason, phase, fee_rate, bar_open_ms):
        leg = db.execute("SELECT remaining_qty FROM legs WHERE position_id=? AND leg_no=?",
                          (pos["position_id"], leg_no)).fetchone()
        qty = min(float(qty), float(leg[0]))
        if qty <= 0:
            return
        fee = qty * float(price) * float(fee_rate)
        pnl = int(pos["side"]) * (float(price)-float(pos["entry_price"])) * qty - fee
        db.execute("UPDATE legs SET remaining_qty=max(0,remaining_qty-?) WHERE position_id=? AND leg_no=?",
                   (qty, pos["position_id"], leg_no))
        db.execute("""UPDATE positions SET remaining_qty=max(0,remaining_qty-?),realized_pnl=realized_pnl+?,
                    exit_ms=CASE WHEN remaining_qty-?<=1e-12 THEN ? ELSE exit_ms END,
                    exit_price=CASE WHEN remaining_qty-?<=1e-12 THEN ? ELSE exit_price END,
                    exit_reason=CASE WHEN remaining_qty-?<=1e-12 THEN ? ELSE exit_reason END,
                    exit_observed_ms=CASE WHEN remaining_qty-?<=1e-12 THEN ? ELSE exit_observed_ms END,
                    updated_ms=? WHERE position_id=?""",
                   (qty, pnl, qty, int(time_ms), qty, float(price), qty, reason,
                    qty, int(observed_ms), int(observed_ms), pos["position_id"]))
        db.execute("""INSERT OR IGNORE INTO fills(event_id,position_id,leg_no,time_ms,observed_ms,bar_open_ms,
                    phase,kind,side,qty,price,fee_rate,fee,net_pnl,metadata) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (pos["event_id"], pos["position_id"], leg_no, int(time_ms), int(observed_ms),
                    int(bar_open_ms) if bar_open_ms is not None else None, phase, reason, int(pos["side"]), qty,
                    float(price), float(fee_rate), fee, pnl,
                    _json({"fill_clock_semantics": "quote_observation_time" if bar_open_ms is None else
                               "bar_open_bucket; observed at completed bar",
                           "observed_ms": int(observed_ms)})))

    def _trim_position_tx(self, db, pos, trim_qty, price, time_ms, observed_ms, prior_nav, bar_open_ms):
        total = float(pos["remaining_qty"])
        if total <= 0:
            return
        # The original account applies the same proportional reduction to all four children.
        ratio = min(1., max(0., float(trim_qty)/total))
        legs = db.execute("SELECT leg_no,remaining_qty FROM legs WHERE position_id=? AND remaining_qty>0 ORDER BY leg_no",
                          (pos["position_id"],)).fetchall()
        for leg in legs:
            amount = float(leg["remaining_qty"]) * ratio
            if amount > 0:
                self._close_leg_tx(db, pos, int(leg["leg_no"]), amount, price, time_ms,
                                   observed_ms, "hourly_cap_trim", "close", TAKER_FEE, bar_open_ms)

    def snapshot(self, *, phase="running", health=None, market=None, error=None) -> dict[str, Any]:
        with self.db() as db:
            start = float(self._meta(db, "start_equity", str(START_EQUITY)))
            started_ms = int(self._meta(db, "started_ms"))
            open_rows = db.execute("SELECT * FROM positions WHERE status='open' ORDER BY slot").fetchall()
            recent_rows = db.execute("SELECT * FROM positions ORDER BY entry_ms DESC LIMIT 30").fetchall()
            fill_rows = db.execute("SELECT * FROM fills ORDER BY time_ms DESC,fill_id DESC LIMIT 80").fetchall()
            decision_rows = db.execute("""SELECT symbol,side,confirmed_ms,status,reason,source_detected_ms,
                first_observed_ms,quote_ts_ms,entry_price FROM source_events
                ORDER BY first_observed_ms DESC,event_id DESC LIMIT 20""").fetchall()
            event_rows = db.execute("SELECT status,count(*) AS n FROM source_events GROUP BY status").fetchall()
            total_realized = float(db.execute("SELECT COALESCE(sum(realized_pnl),0) FROM positions").fetchone()[0])
            unrealized = sum(int(r["side"]) * (float(r["last_mark"])-float(r["entry_price"])) *
                             float(r["remaining_qty"]) for r in open_rows)
            equity = start + total_realized + unrealized
            maximum = max(float(self._meta(db, "max_equity", str(start))), equity)
            last_hour = int(self._meta(db, "last_hour_close_ms", "0"))
            closed_positions = [dict(row) for row in recent_rows]
            positions = [self._position_public(row) for row in open_rows]
            recent_trades = [self._position_public(row) for row in closed_positions]
            fills = [{"event_id": r["event_id"], "symbol": next((p["symbol"] for p in closed_positions if p["event_id"]==r["event_id"]), None),
                      "time_ms": int(r["time_ms"]), "observed_ms": int(r["observed_ms"]),
                      "bar_open_ms": r["bar_open_ms"], "phase": r["phase"], "kind": r["kind"],
                      "side": int(r["side"]), "qty": float(r["qty"]), "price": float(r["price"]),
                      "fee": float(r["fee"]), "net_pnl": float(r["net_pnl"])} for r in fill_rows]
            counts = {str(r["status"]): int(r["n"]) for r in event_rows}
            return _clean({
                "schema_version": "live-cloud-v17.v1",
                "strategy_id": TRACK_KEY,
                "mode": "paper",
                "phase": phase,
                "error": error,
                "heartbeat_ms": int(self.clock()),
                "started_ms": started_ms,
                "rule": RULE,
                "health": health or {},
                "market": market or {},
                "account": {
                    "currency": "USDT", "start_equity": start, "equity": equity,
                    "realized_pnl": total_realized, "unrealized_pnl": unrealized,
                    "net_pnl": equity-start, "return_pct": 100.*(equity/start-1.),
                    "max_equity": maximum,
                    "drawdown_pct": 100.*(1.-equity/maximum) if maximum > 0 else None,
                    "open_position_count": len(open_rows), "slot_count": SLOTS,
                    "available_slots": max(0,SLOTS-len(open_rows)), "leverage": LEVERAGE,
                    "last_hour_close_ms": last_hour, "source_event_counts": counts,
                },
                "positions": positions,
                "recent_trades": recent_trades,
                "recent_fills": fills,
                "recent_decisions": [{"symbol": r["symbol"], "side": int(r["side"]),
                    "confirmed_ms": int(r["confirmed_ms"]), "status": r["status"], "reason": r["reason"],
                    "source_detected_ms": int(r["source_detected_ms"]),
                    "first_observed_ms": int(r["first_observed_ms"]),
                    "quote_ts_ms": r["quote_ts_ms"], "entry_price": r["entry_price"]}
                    for r in decision_rows],
            })

    @staticmethod
    def _position_public(row) -> dict[str, Any]:
        return {"event_id": row["event_id"], "symbol": row["symbol"], "side": int(row["side"]),
                "slot": int(row["slot"]), "status": row["status"],
                "signal_open_ms": int(row["signal_open_ms"]), "confirmed_ms": int(row["confirmed_ms"]),
                "source_detected_ms": int(row["source_detected_ms"]),
                "first_observed_ms": int(row["first_observed_ms"]),
                "entry_ms": int(row["entry_ms"]), "quote_ts_ms": int(row["quote_ts_ms"]),
                "entry_observed_ms": int(row["entry_observed_ms"]),
                "quote_to_entry_delay_ms": int(row["entry_ms"])-int(row["quote_ts_ms"]),
                "first_protection_quote_ms": row["first_protection_quote_ms"],
                "first_protection_observed_ms": row["first_protection_observed_ms"],
                "first_protection_latency_ms": (int(row["first_protection_observed_ms"])-int(row["entry_ms"])
                    if row["first_protection_observed_ms"] is not None else None),
                "minute_protection_until_ms": int(row["minute_protection_until_ms"]),
                "first_post_entry_minute_open_ms": int(row["next_minute_open_ms"]),
                "entry_price": float(row["entry_price"]), "entry_bid": float(row["entry_bid"]),
                "entry_ask": float(row["entry_ask"]), "stop_price": float(row["stop_price"]),
                "risk_distance": float(row["risk_distance"]), "leverage": float(row["leverage"]),
                "margin_usdt": float(row["margin_usdt"]),
                "initial_notional_usdt": float(row["initial_notional_usdt"]),
                "initial_qty": float(row["initial_qty"]), "remaining_qty": float(row["remaining_qty"]),
                "entry_fee": float(row["entry_fee"]), "realized_pnl": float(row["realized_pnl"]),
                "mark_price": float(row["last_mark"]), "mark_ms": int(row["last_mark_ms"]),
                "exit_ms": row["exit_ms"], "exit_price": row["exit_price"],
                "exit_reason": row["exit_reason"], "exit_observed_ms": row["exit_observed_ms"],
                "target_2r_expiry_ms": int(row["target_2r_expiry_ms"])}
