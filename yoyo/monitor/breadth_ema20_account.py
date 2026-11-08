"""Independent forward paper account for the breadth-EMA20 BTC/ALT streams.

The event adapter accepts one closed UTC 5-minute event at a time. Signal closes and
the next bar's open are kept separate: an entry is only recorded at ``next_open``.
The SQLite ledger is the source of truth across runner restarts. This module does not
reuse or mutate the SOL ``live-breadth`` account.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
import threading
from typing import Any


MINUTE = 60_000
HOUR = 60 * MINUTE
DAY = 24 * HOUR
BAR_MS = 5 * MINUTE
BTC_HOLD_MS = 6 * DAY
ALT_HOLD_MS = 6 * HOUR
ALT_GAP_MS = DAY
ALT_MIN_INITIAL_N = 2
START_EQUITY = 1_000.0
BTC_START = 500.0
ALT_START = 500.0
TAKER_FEE = 0.0005
MAKER_FEE = 0.0002
BTC_CASE_TARGET_POINTS = 10_000.0
BTC_CASE_STOP_FRACTION = 0.06
BTC_SYMBOL = "BTCUSDT"
ALT_EXCLUDED = {"BTC", "ETH", "SOL", "USTC"}
TRACK_KEY = "breadth-ema20"
RULE = {
    "track": TRACK_KEY,
    "start_equity": START_EQUITY,
    "sleeves": {"BTC": BTC_START, "ALT": ALT_START},
    "allocation": "independent 50/50 BTC and ALT sleeves; no transfers or rebalancing",
    "btc_ordinary": {
        "source": "freq36_BTC ordinary",
        "ema_gate": "latest completed UTC 4h BTC close <= EMA20",
        "ema_veto_occupies_shared_reservation": True,
        "hold_hours": 144,
        "entry_fee": "OKX Lv1 taker 0.05%",
        "exit_fee": "OKX Lv1 taker 0.05%",
    },
    "btc_case": {
        "source": "case_target_funded_BTC W5/X30 threshold 5%",
        "same_clock_priority": "case before ordinary",
        "hold_hours": 144,
        "take_profit_fraction": 0.5,
        "take_profit_net_points": BTC_CASE_TARGET_POINTS,
        "stop_pct": 6.0,
        "limit_fee": "OKX Lv1 maker 0.02%",
        "market_fee": "OKX Lv1 taker 0.05%",
    },
    "alt": {
        "source": "freq36_ALT W5/X0 share_5",
        "initial_min": ALT_MIN_INITIAL_N,
        "initial_count_includes_excluded_majors": True,
        "minimum_spacing_hours": 24,
        "holding_hours": 6,
        "basket": "equal weight across the original eligible members",
        "entry_fee": "OKX Lv1 taker 0.05%",
        "exit_fee": "OKX Lv1 taker 0.05%",
    },
    "funding": "exchange settlement rates and mark prices are applied when covered; missing required settlements remain explicit and block cursor advancement until retry",
    "paper_only": True,
}


class RetryableBarError(RuntimeError):
    """A bar cannot be consumed until required position prices/funding arrive."""

    def __init__(self, symbol: str, time_ms: int, reason: str = "missing_entry_funding"):
        self.symbol = symbol
        self.time_ms = time_ms
        self.reason = reason
        super().__init__(f"{reason} for {symbol} at {time_ms}")


def _base(symbol: str) -> str:
    return symbol.split("-")[0].replace("USDT", "").replace("USDC", "").upper()


def _finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _as_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _candidate_branch(candidate: dict[str, Any]) -> str | None:
    raw = str(candidate.get("branch") or candidate.get("stream") or candidate.get("source") or "").lower()
    if raw in {"btc_ordinary", "ordinary", "medium", "btc", "btc-medium"}:
        return "BTC_ordinary"
    if raw in {"btc_case", "case", "urgent", "btc_case_target", "btc-case", "case_target_funded_btc"}:
        return "BTC_case"
    if raw in {"alt", "alt24h", "alt_basket", "freq36_alt"}:
        return "ALT"
    return None


class BreadthEma20Paper:
    """Durable causal paper ledger for BTC ordinary, BTC case, and ALT24h.

    ``process_closed_bar`` consumes normalized feed events with these top-level keys:

    * ``bar_open_ms``, ``close_ms`` and ``bars_by_symbol`` (OHLC plus separate
      ``next_open_ms`` / ``next_open`` for candidate entries)
    * ``candidates``: candidate records with ``branch``, ``decision_ms``,
      ``known_ms``, and, for ALT, the frozen ``members`` list
    * ``funding_events`` and optional ``funding_due`` / ``funding_coverage`` rows
    * ``is_replay`` / ``is_realtime`` supplied by the feed runner

    A retryable failure returns ``processed=False`` without persisting the bar or
    moving the durable cursor. The caller can retry the same event after repairing
    the missing input.
    """

    def __init__(self, runtime, clock=None, start_equity: float = START_EQUITY):
        self.runtime = Path(runtime)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.path = self.runtime / "breadth-ema20-paper.sqlite3"
        self.clock = clock or (lambda: int(__import__("time").time() * 1000))
        self.start_equity = float(start_equity)
        if not math.isfinite(self.start_equity) or self.start_equity <= 0:
            raise ValueError("start_equity must be positive and finite")
        self.btc_start = self.start_equity / 2.0
        self.alt_start = self.start_equity / 2.0
        self.write_lock = threading.RLock()
        self._initialize()

    @contextmanager
    def db(self):
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS account (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    started_ms INTEGER NOT NULL,
                    start_equity REAL NOT NULL,
                    btc_equity REAL NOT NULL,
                    alt_equity REAL NOT NULL,
                    btc_reserved_until_ms INTEGER NOT NULL DEFAULT 0,
                    btc_reserved_event_id TEXT,
                    ordinary_source_until_ms INTEGER NOT NULL DEFAULT 0,
                    case_source_until_ms INTEGER NOT NULL DEFAULT 0,
                    alt_reserved_until_ms INTEGER NOT NULL DEFAULT 0,
                    last_entry_ms INTEGER,
                    cursor_close_ms INTEGER
                );
                CREATE TABLE IF NOT EXISTS bar_events (
                    close_ms INTEGER PRIMARY KEY,
                    bar_open_ms INTEGER NOT NULL,
                    event_id TEXT,
                    is_replay INTEGER NOT NULL,
                    processed_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bars (
                    symbol TEXT NOT NULL,
                    bar_open_ms INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    next_open_ms INTEGER,
                    next_open REAL,
                    PRIMARY KEY(symbol, bar_open_ms)
                );
                CREATE TABLE IF NOT EXISTS candidates (
                    event_id TEXT PRIMARY KEY,
                    branch TEXT NOT NULL,
                    known_ms INTEGER,
                    decision_ms INTEGER NOT NULL,
                    entry_ms INTEGER,
                    status TEXT NOT NULL,
                    reason TEXT,
                    candidate_json TEXT NOT NULL,
                    created_ms INTEGER NOT NULL,
                    updated_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS candidates_decision_idx ON candidates(decision_ms DESC);
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    member_index INTEGER NOT NULL DEFAULT 0,
                    member_count INTEGER NOT NULL DEFAULT 1,
                    notional REAL NOT NULL,
                    entry_ms INTEGER NOT NULL,
                    entry REAL NOT NULL,
                    initial_qty REAL NOT NULL,
                    remaining_qty REAL NOT NULL,
                    deadline_ms INTEGER NOT NULL,
                    stop_price REAL,
                    target_price REAL,
                    first_exit_ms INTEGER,
                    first_exit_price REAL,
                    exit_ms INTEGER,
                    exit_price REAL,
                    exit_reason TEXT,
                    stage TEXT NOT NULL,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    entry_fee REAL NOT NULL DEFAULT 0,
                    exit_fee REAL NOT NULL DEFAULT 0,
                    funding_pnl REAL NOT NULL DEFAULT 0,
                    funding_complete INTEGER NOT NULL DEFAULT 1,
                    funding_missing_n INTEGER NOT NULL DEFAULT 0,
                    price_coverage_complete INTEGER NOT NULL DEFAULT 1,
                    last_bar_open_ms INTEGER,
                    fee_bp REAL,
                    error TEXT,
                    UNIQUE(event_id, symbol)
                );
                CREATE INDEX IF NOT EXISTS positions_open_idx ON positions(stage, symbol, entry_ms);
                CREATE TABLE IF NOT EXISTS fills (
                    id INTEGER PRIMARY KEY,
                    position_id INTEGER NOT NULL,
                    time_ms INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    qty REAL NOT NULL,
                    price REAL NOT NULL,
                    fee_rate REAL NOT NULL,
                    fee REAL NOT NULL,
                    price_pnl REAL NOT NULL,
                    net_pnl REAL NOT NULL,
                    FOREIGN KEY(position_id) REFERENCES positions(id)
                );
                CREATE TABLE IF NOT EXISTS funding (
                    id INTEGER PRIMARY KEY,
                    position_id INTEGER NOT NULL,
                    time_ms INTEGER NOT NULL,
                    rate REAL,
                    mark_price REAL,
                    payment REAL,
                    status TEXT NOT NULL,
                    source_json TEXT NOT NULL,
                    UNIQUE(position_id, time_ms),
                    FOREIGN KEY(position_id) REFERENCES positions(id)
                );
                CREATE TABLE IF NOT EXISTS latest_marks (
                    symbol TEXT PRIMARY KEY,
                    bar_open_ms INTEGER NOT NULL,
                    close REAL NOT NULL
                );
            """)
            existing = db.execute("SELECT id FROM account WHERE id=1").fetchone()
            if existing is None:
                now = int(self.clock())
                db.execute("INSERT INTO account (id,started_ms,start_equity,btc_equity,alt_equity) VALUES (1,?,?,?,?)",
                           (now, self.start_equity, self.btc_start, self.alt_start))
            else:
                acct = db.execute("SELECT * FROM account WHERE id=1").fetchone()
                self.start_equity = float(acct["start_equity"])
                self.btc_start = self.start_equity / 2.0
                self.alt_start = self.start_equity / 2.0

    def cursor_ms(self) -> int | None:
        """Last durably consumed event ``close_ms``; pass as exclusive feed cursor."""
        with self.db() as db:
            row = db.execute("SELECT cursor_close_ms FROM account WHERE id=1").fetchone()
        return _as_int(row["cursor_close_ms"])

    def open_symbols(self) -> list[str]:
        """Symbols whose next bars are required to manage currently open positions."""
        with self.db() as db:
            rows = db.execute("SELECT DISTINCT symbol FROM positions WHERE stage='open' ORDER BY symbol").fetchall()
        return [str(row["symbol"]) for row in rows]

    def is_fresh_event(self, event: dict[str, Any]) -> bool:
        """Only runner-certified real-time events may create new entries."""
        if bool(event.get("is_replay", event.get("replay", False))):
            return False
        return bool(event.get("is_realtime", False))

    @staticmethod
    def _bars(event: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw = event.get("bars_by_symbol") or event.get("bars") or {}
        if isinstance(raw, list):
            rows = raw
        elif isinstance(raw, dict):
            rows = [dict(value, symbol=key) if isinstance(value, dict) else {} for key, value in raw.items()]
        else:
            rows = []
        out: dict[str, dict[str, Any]] = {}
        top_open = _as_int(event.get("bar_open_ms"))
        for row in rows:
            symbol = str(row.get("symbol") or "")
            stamp = _as_int(row.get("bar_open_ms"), top_open)
            if not symbol or stamp is None or (top_open is not None and stamp != top_open):
                continue
            try:
                open_, high, low, close = (float(row[k]) for k in ("open", "high", "low", "close"))
            except (KeyError, TypeError, ValueError):
                continue
            if not all(math.isfinite(v) and v > 0 for v in (open_, high, low, close)):
                continue
            if high < max(open_, close, low) or low > min(open_, close, high):
                continue
            out[symbol] = {
                "symbol": symbol,
                "bar_open_ms": stamp,
                "open": open_, "high": high, "low": low, "close": close,
                "next_open_ms": _as_int(row.get("next_open_ms")),
                "next_open": float(row["next_open"]) if _finite(row.get("next_open")) else None,
            }
        return out

    @staticmethod
    def _funding_records(event: dict[str, Any], edge: str = "close") -> list[dict[str, Any]]:
        if edge not in {"open", "close"}:
            raise ValueError("funding edge must be 'open' or 'close'")
        event_key = "funding_open_events" if edge == "open" else "funding_events"
        due_key = "funding_open_due" if edge == "open" else "funding_due"
        coverage_key = "funding_open_coverage" if edge == "open" else "funding_coverage"
        raw_events = event.get(event_key) or []
        if isinstance(raw_events, dict):
            rows = [dict(value, symbol=key) if isinstance(value, dict) else {} for key, value in raw_events.items()]
        else:
            rows = list(raw_events)
        coverage = event.get(due_key) or event.get(coverage_key) or []
        if isinstance(coverage, dict):
            coverage = [dict(value, symbol=key) if isinstance(value, dict) else
                        {"symbol": key, "due": value if value is None else bool(value)}
                        for key, value in coverage.items()]
        for row in coverage:
            due = row.get("due", row.get("expected", False)) if isinstance(row, dict) else False
            unknown = due is None and isinstance(row, dict) and row.get("available") is False
            if isinstance(row, dict) and (due is True or unknown):
                rows.append(row)
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = dict(row)
            item["time_ms"] = _as_int(item.get("time_ms", item.get("funding_ms")))
            item["symbol"] = str(item.get("symbol") or "")
            out.append(item)
        return out

    @staticmethod
    def _candidate_sort(candidate: dict[str, Any]) -> tuple[int, int, str]:
        branch = _candidate_branch(candidate) or ""
        decision = _as_int(candidate.get("decision_ms", candidate.get("close_ms")), 0) or 0
        priority = 0 if branch == "BTC_case" else 1 if branch == "BTC_ordinary" else 2
        return decision, priority, str(candidate.get("event_id") or "")

    def _coverage_rows(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        result = []
        for key in ("funding_due", "funding_coverage", "funding_open_due", "funding_open_coverage"):
            is_open_edge = key.startswith("funding_open_")
            edge_ms = _as_int(event.get("bar_open_ms" if is_open_edge else "close_ms"))
            raw = event.get(key) or []
            if isinstance(raw, dict):
                for symbol, value in raw.items():
                    if isinstance(value, dict):
                        row = dict(value, symbol=symbol)
                    else:
                        row = {"symbol": symbol, "due": value if value is None else bool(value)}
                    row.setdefault("time_ms", edge_ms)
                    result.append(row)
            else:
                for source in raw:
                    if isinstance(source, dict):
                        row = dict(source)
                        row.setdefault("time_ms", edge_ms)
                        result.append(row)
        return result

    def _has_funding_coverage(self, symbol: str, time_ms: int, event: dict[str, Any],
                              fundings: list[dict[str, Any]]) -> tuple[bool, bool]:
        """Return (is_due, is_complete) when the feed explicitly describes coverage."""
        records = [r for r in fundings if r.get("symbol") == symbol and r.get("time_ms") == time_ms]
        if records:
            if any(r.get("available") is not False and _finite(r.get("rate")) and _finite(r.get("mark_price"))
                   for r in records):
                return True, True
            return True, False
        for row in self._coverage_rows(event):
            row_symbol = str(row.get("symbol") or "")
            row_time = _as_int(row.get("time_ms", row.get("funding_ms")))
            if row_symbol == symbol and (row_time is None or row_time == time_ms):
                raw_due = row.get("due", row.get("expected", False))
                if raw_due is None:
                    return True, False
                due = bool(raw_due)
                available = bool(row.get("available", _finite(row.get("rate")) and _finite(row.get("mark_price"))))
                available = available and _finite(row.get("rate")) and _finite(row.get("mark_price"))
                return due, (not due) or available
        return False, True

    def _required_input_failure(self, db: sqlite3.Connection, event: dict[str, Any], bars: dict[str, dict[str, Any]],
                                fundings: list[dict[str, Any]], bar_open_ms: int,
                                close_ms: int) -> dict[str, Any] | None:
        opened = [dict(r) for r in db.execute("SELECT * FROM positions WHERE stage='open'")]
        missing_bars = sorted({p["symbol"] for p in opened if p["symbol"] not in bars})
        if missing_bars:
            return {"processed": False, "retryable": True, "reason": "missing_open_bar", "symbols": missing_bars,
                    "cursor_close_ms": self.cursor_ms()}
        cursor = db.execute("SELECT cursor_close_ms FROM account WHERE id=1").fetchone()["cursor_close_ms"]
        if cursor is not None and bar_open_ms != int(cursor):
            return {"processed": False, "retryable": True, "reason": "bar_gap",
                    "expected_after_close_ms": int(cursor), "cursor_close_ms": int(cursor)}
        for p in opened:
            row = bars[p["symbol"]]
            deadline = int(p["deadline_ms"])
            next_ms = _as_int(row.get("next_open_ms"))
            if deadline < bar_open_ms:
                return {"processed": False, "retryable": True, "reason": "missed_exit_open",
                        "symbols": [p["symbol"]], "exit_ms": deadline, "cursor_close_ms": self.cursor_ms()}
            if deadline == bar_open_ms and not _finite(row.get("open")):
                return {"processed": False, "retryable": True, "reason": "missing_exit_open",
                        "symbols": [p["symbol"]], "exit_ms": deadline, "cursor_close_ms": self.cursor_ms()}
            if bar_open_ms < deadline <= close_ms and (
                    deadline != close_ms or next_ms != deadline or not _finite(row.get("next_open"))):
                return {"processed": False, "retryable": True, "reason": "missing_exit_open",
                        "symbols": [p["symbol"]], "exit_ms": deadline, "cursor_close_ms": self.cursor_ms()}
        for funding in fundings:
            time_ms = funding.get("time_ms")
            if time_ms is None:
                continue
            for symbol in {p["symbol"] for p in opened}:
                due, complete = self._has_funding_coverage(symbol, time_ms, event, fundings)
                if due and not complete:
                    return {"processed": False, "retryable": True, "reason": "missing_funding",
                            "symbols": [symbol], "funding_ms": time_ms, "cursor_close_ms": self.cursor_ms()}
        # If coverage lists a due settlement but omitted the event entirely, it is still required data.
        open_symbols = {p["symbol"] for p in opened}
        for coverage in self._coverage_rows(event):
            raw_due = coverage.get("due", coverage.get("expected", False))
            if raw_due is False:
                continue
            symbol = str(coverage.get("symbol") or "")
            time_ms = _as_int(coverage.get("time_ms", coverage.get("funding_ms")))
            if symbol not in open_symbols or time_ms is None:
                continue
            _, complete = self._has_funding_coverage(symbol, time_ms, event, fundings)
            if not complete:
                return {"processed": False, "retryable": True, "reason": "missing_funding",
                        "symbols": [symbol], "funding_ms": time_ms, "cursor_close_ms": self.cursor_ms()}
        return None

    def process_closed_bar(self, event: dict[str, Any]) -> dict[str, Any]:
        """Process one normalized closed-bar event exactly once.

        Returns a structured retry response without committing any state when a
        currently open position lacks required OHLC/open or due funding data.
        """
        if not isinstance(event, dict):
            raise TypeError("event must be a dict")
        bar_open_ms = _as_int(event.get("bar_open_ms"))
        close_ms = _as_int(event.get("close_ms"))
        if bar_open_ms is None or close_ms is None or close_ms != bar_open_ms + BAR_MS:
            raise ValueError("event requires a UTC 5m bar_open_ms and close_ms = bar_open_ms + 300000")
        bars = self._bars(event)
        open_fundings = self._funding_records(event, "open")
        close_fundings = self._funding_records(event, "close")
        fundings = open_fundings + close_fundings
        candidates = [c for c in (event.get("candidates") or []) if isinstance(c, dict)]
        candidates.sort(key=self._candidate_sort)
        fresh = self.is_fresh_event(event)
        now = int(self.clock())
        event_id = str(event.get("event_id") or f"breadth-ema20-5m:{bar_open_ms}")

        with self.write_lock:
            try:
                with self.db() as db:
                    old = db.execute("SELECT 1 FROM bar_events WHERE close_ms=?", (close_ms,)).fetchone()
                    duplicate_event = bool(old)
                    if old:
                        # Permit a candidate-only retry for a bar whose OHLC was already committed.
                        existing_cursor = db.execute("SELECT cursor_close_ms FROM account WHERE id=1").fetchone()[0]
                        if existing_cursor is not None and close_ms < existing_cursor:
                            return {"processed": False, "duplicate": True, "retryable": False,
                                    "cursor_close_ms": int(existing_cursor), "candidate_results": []}
                        process_prices = False
                    else:
                        failure = self._required_input_failure(db, event, bars, fundings, bar_open_ms, close_ms)
                        if failure:
                            return failure
                        process_prices = True

                    results: list[dict[str, Any]] = []
                    if process_prices:
                        db.execute("INSERT INTO bar_events (close_ms,bar_open_ms,event_id,is_replay,processed_ms) VALUES (?,?,?,?,?)",
                                   (close_ms, bar_open_ms, event_id, int(not fresh), now))
                        self._record_bars(db, bars)
                        # A settlement at this candle's open is applied before its
                        # OHLC path. The preceding event's close settlement is
                        # persisted at exactly this same boundary, so the event is
                        # idempotent if the feed supplies both edges.
                        self._apply_funding(db, event, open_fundings)
                        open_settles = {str(f.get("symbol")) for f in open_fundings
                                        if f.get("time_ms") == bar_open_ms}
                        # Recover an exact boundary deadline at the candle open before
                        # allowing any intrabar stop/limit path to run.
                        self._settle_deadlines(db, bars, bar_open_ms, edge="open")
                        self._process_positions(db, bars, bar_open_ms, open_settles)
                        # Close-edge funding comes after this bar's stop/limit path
                        # and before time exits at next_open_ms == close_ms.
                        self._apply_funding(db, event, close_fundings)
                        self._settle_deadlines(db, bars, close_ms, edge="close")
                        db.execute("UPDATE account SET cursor_close_ms=CASE WHEN cursor_close_ms IS NULL OR cursor_close_ms<? THEN ? ELSE cursor_close_ms END WHERE id=1",
                                   (close_ms, close_ms))

                    for candidate in candidates:
                        result = self._accept_candidate(db, event, candidate, bars, fundings, fresh, now)
                        results.append(result)
                    cursor = close_ms if process_prices else existing_cursor
                    return {"processed": True, "retryable": False, "is_realtime": fresh,
                            "duplicate": duplicate_event, "cursor_close_ms": _as_int(cursor), "candidate_results": results}
            except RetryableBarError as err:
                # The connection context rolls the whole bar and all reservation writes back.
                failure = {"processed": False, "retryable": True, "reason": err.reason,
                           "symbols": [err.symbol], "cursor_close_ms": self.cursor_ms()}
                field = ("exit_ms" if err.reason == "missing_exit_open" else
                         "entry_ms" if err.reason == "missing_entry_open" else "funding_ms")
                failure[field] = err.time_ms
                return failure

    def _record_bars(self, db: sqlite3.Connection, bars: dict[str, dict[str, Any]]) -> None:
        for symbol, row in bars.items():
            db.execute("INSERT OR IGNORE INTO bars (symbol,bar_open_ms,open,high,low,close,next_open_ms,next_open) VALUES (?,?,?,?,?,?,?,?)",
                       (symbol, row["bar_open_ms"], row["open"], row["high"], row["low"], row["close"],
                        row["next_open_ms"], row["next_open"]))
            db.execute("INSERT INTO latest_marks (symbol,bar_open_ms,close) VALUES (?,?,?) "
                       "ON CONFLICT(symbol) DO UPDATE SET bar_open_ms=excluded.bar_open_ms,close=excluded.close "
                       "WHERE excluded.bar_open_ms>=latest_marks.bar_open_ms",
                       (symbol, row["bar_open_ms"], row["close"]))

    def _equity_field(self, branch: str) -> str:
        return "alt_equity" if branch == "ALT" else "btc_equity"

    def _apply_funding(self, db: sqlite3.Connection, event: dict[str, Any], fundings: list[dict[str, Any]]) -> None:
        for item in fundings:
            symbol = str(item.get("symbol") or "")
            time_ms = item.get("time_ms")
            if not symbol or time_ms is None:
                continue
            active = [int(r[0]) for r in db.execute(
                "SELECT id FROM positions WHERE stage='open' AND symbol=? AND entry_ms<=?", (symbol, time_ms))]
            for p in active:
                self._apply_one_funding(db, p, item)

    @staticmethod
    def _target_price(entry: float, paid_points: float, tick_size: float = 1.0) -> float:
        # Reproduce the frozen policy-4 net target: entry taker + 10,000 net points,
        # with both the target maker exit and the eventual runner taker exit reserved.
        raw = (entry * (1.0 + TAKER_FEE) + BTC_CASE_TARGET_POINTS + paid_points) / (1.0 - MAKER_FEE - TAKER_FEE)
        return math.ceil(raw / tick_size) * tick_size

    def _preflight_candidate_funding(self, candidate: dict[str, Any], event: dict[str, Any],
                                     fundings: list[dict[str, Any]]) -> tuple[str, int] | None:
        branch = _candidate_branch(candidate)
        entry_ms = _as_int(candidate.get("entry_ms", candidate.get("decision_ms")))
        if entry_ms is None:
            return None
        members = candidate.get("members") or candidate.get("alt_members") or []
        symbols = [BTC_SYMBOL] if branch in {"BTC_case", "BTC_ordinary"} else [
            str(member.get("symbol") or "") for member in members if isinstance(member, dict)
        ]
        for symbol in symbols:
            due, complete = self._has_funding_coverage(symbol, entry_ms, event, fundings)
            if due and not complete:
                return symbol, entry_ms
        return None

    def _accept_candidate(self, db: sqlite3.Connection, event: dict[str, Any], candidate: dict[str, Any],
                          bars: dict[str, dict[str, Any]], fundings: list[dict[str, Any]], fresh: bool,
                          now: int) -> dict[str, Any]:
        branch = _candidate_branch(candidate)
        if branch is None:
            return {"event_id": candidate.get("event_id"), "status": "ignored", "reason": "unknown_branch"}
        decision_ms = _as_int(candidate.get("decision_ms", event.get("close_ms")))
        if decision_ms is None:
            return {"event_id": candidate.get("event_id"), "status": "ignored", "reason": "missing_decision_ms"}
        known_ms = _as_int(candidate.get("known_ms"))
        entry_ms = _as_int(candidate.get("entry_ms", decision_ms))
        source_event_id = str(candidate.get("event_id") or "")
        cid = f"{branch}|{decision_ms}" + (f"|{source_event_id}" if source_event_id else "")
        existing = db.execute("SELECT status,reason FROM candidates WHERE event_id=?", (cid,)).fetchone()
        if existing:
            return {"event_id": cid, "branch": branch, "status": existing["status"], "reason": existing["reason"], "duplicate": True}
        status, reason = "pending", None
        account = dict(db.execute("SELECT * FROM account WHERE id=1").fetchone())
        if decision_ms < int(account["started_ms"]):
            status, reason = "ignored", "pre_start_signal"
        elif not fresh:
            status, reason = "ignored", "non_realtime_or_replay"
        elif entry_ms is None or entry_ms != decision_ms or decision_ms != _as_int(event.get("close_ms")):
            status, reason = "ignored", "invalid_entry_time"
        elif branch == "ALT":
            initial_n = _as_int(candidate.get("initial_n"))
            if initial_n is None or initial_n < ALT_MIN_INITIAL_N:
                status, reason = "ignored", "alt_initial_n_below_2"
            elif decision_ms < int(account["alt_reserved_until_ms"]):
                status, reason = "blocked", "alt_24h_spacing"
            else:
                db.execute("UPDATE account SET alt_reserved_until_ms=? WHERE id=1", (decision_ms + ALT_GAP_MS,))
                missing = self._preflight_candidate_funding(candidate, event, fundings)
                if missing:
                    raise RetryableBarError(*missing)
                missing_price = self._preflight_candidate_price(candidate, branch, entry_ms, bars)
                if missing_price:
                    raise RetryableBarError(*missing_price, reason="missing_entry_open")
                result = self._open_candidate(db, cid, branch, self._normalize_candidate_funding(candidate, fundings), entry_ms, bars, now)
                status, reason = result["status"], result.get("reason")
        else:
            source_col = "case_source_until_ms" if branch == "BTC_case" else "ordinary_source_until_ms"
            if decision_ms < int(account[source_col]):
                status, reason = "blocked", "source_6d_reservation"
            else:
                # First reserve each source's independent six-day path.
                db.execute(f"UPDATE account SET {source_col}=? WHERE id=1", (decision_ms + BTC_HOLD_MS,))
                if decision_ms < int(account["btc_reserved_until_ms"]):
                    status, reason = "blocked", "btc_shared_6d_reservation"
                else:
                    # The shared BTC slot is occupied before the ordinary EMA veto.
                    db.execute("UPDATE account SET btc_reserved_until_ms=?,btc_reserved_event_id=? WHERE id=1",
                               (decision_ms + BTC_HOLD_MS, cid))
                    if branch == "BTC_ordinary" and not self._ema_passes(candidate, event):
                        status, reason = "vetoed", "ema20_veto_slot_reserved"
                    else:
                        missing = self._preflight_candidate_funding(candidate, event, fundings)
                        if missing:
                            # Do not consume a candidate/cursor with an incomplete settlement at entry.
                            raise RetryableBarError(*missing)
                        missing_price = self._preflight_candidate_price(candidate, branch, entry_ms, bars)
                        if missing_price:
                            raise RetryableBarError(*missing_price, reason="missing_entry_open")
                        result = self._open_candidate(db, cid, branch, self._normalize_candidate_funding(candidate, fundings), entry_ms, bars, now)
                        status, reason = result["status"], result.get("reason")
        db.execute("INSERT INTO candidates (event_id,branch,known_ms,decision_ms,entry_ms,status,reason,candidate_json,created_ms,updated_ms) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (cid, branch, known_ms, decision_ms, entry_ms, status, reason, _json(candidate), now, now))
        return {"event_id": cid, "branch": branch, "status": status, "reason": reason}

    @staticmethod
    def _ema_passes(candidate: dict[str, Any], event: dict[str, Any]) -> bool:
        snapshot = candidate.get("btc_4h") or candidate.get("ema_filter") or event.get("btc_4h") or {}
        close = candidate.get("btc_4h_close", snapshot.get("close"))
        ema = candidate.get("btc_4h_ema20", snapshot.get("ema20"))
        bar_close = _as_int(candidate.get("btc_4h_bar_close_ms", candidate.get("btc_4h_close_ms",
                                 snapshot.get("bar_close_ms"))))
        asof = _as_int(candidate.get("btc_4h_asof_ms", candidate.get("btc_4h_close_asof_ms",
                           snapshot.get("asof_ms", snapshot.get("close_asof_ms")))))
        complete = candidate.get("btc_4h_complete", snapshot.get("complete"))
        decision = _as_int(candidate.get("decision_ms", event.get("close_ms")))
        return (complete is True and _finite(close) and _finite(ema) and bar_close is not None and asof is not None
                and decision is not None and bar_close <= decision and asof <= decision and float(close) <= float(ema))

    def _candidate_members(self, candidate: dict[str, Any], bars: dict[str, dict[str, Any]], entry_ms: int,
                           branch: str) -> tuple[list[tuple[str, float]], str | None]:
        if branch != "ALT":
            symbol = str(candidate.get("symbol") or BTC_SYMBOL)
            row = bars.get(symbol)
            if row is None:
                return [], f"missing_entry_bar:{symbol}"
            if row.get("next_open_ms") != entry_ms or not _finite(row.get("next_open")):
                # Some feeds attach entry prices directly to the candidate to retain source lineage.
                direct_ms = _as_int(candidate.get("next_open_ms"))
                direct_price = candidate.get("next_open", candidate.get("entry_price"))
                if direct_ms != entry_ms or not _finite(direct_price):
                    return [], f"missing_next_open:{symbol}"
                return [(symbol, float(direct_price))], None
            return [(symbol, float(row["next_open"]))], None

        raw = candidate.get("members") or candidate.get("alt_members") or []
        members: dict[str, dict[str, Any]] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "")
            if not symbol or _base(symbol) in ALT_EXCLUDED:
                continue
            if symbol in members:
                return [], f"duplicate_member:{symbol}"
            members[symbol] = item
        expected = _as_int(candidate.get("expected_member_n", candidate.get("expected_members")))
        if len(members) < 1 or (expected is not None and expected != len(members)):
            return [], "incomplete_alt_members"
        priced: list[tuple[str, float]] = []
        for symbol, member in sorted(members.items()):
            row = bars.get(symbol, member)
            next_ms = _as_int(row.get("next_open_ms"))
            next_open = row.get("next_open")
            if next_ms != entry_ms or not _finite(next_open):
                return [], f"missing_next_open:{symbol}"
            priced.append((symbol, float(next_open)))
        return priced, None

    def _preflight_candidate_price(self, candidate: dict[str, Any], branch: str, entry_ms: int,
                                   bars: dict[str, dict[str, Any]]) -> tuple[str, int] | None:
        _, error = self._candidate_members(candidate, bars, entry_ms, branch)
        if error is None:
            return None
        if error.startswith("missing_entry_bar:") or error.startswith("missing_next_open:"):
            return error.split(":", 1)[1], entry_ms
        if error == "incomplete_alt_members":
            return "ALT", entry_ms
        return None

    def _open_candidate(self, db: sqlite3.Connection, cid: str, branch: str, candidate: dict[str, Any],
                        entry_ms: int, bars: dict[str, dict[str, Any]], now: int) -> dict[str, Any]:
        members, error = self._candidate_members(candidate, bars, entry_ms, branch)
        if error:
            return {"status": "skipped", "reason": error}
        equity_field = self._equity_field(branch)
        equity = float(db.execute(f"SELECT {equity_field} FROM account WHERE id=1").fetchone()[0])
        if not math.isfinite(equity) or equity <= 0:
            return {"status": "skipped", "reason": "nonpositive_sleeve_equity"}
        total = equity
        per_member = total / len(members)
        deadline = entry_ms + (ALT_HOLD_MS if branch == "ALT" else BTC_HOLD_MS)
        created_ids = []
        for index, (symbol, price) in enumerate(members):
            if not math.isfinite(price) or price <= 0:
                return {"status": "skipped", "reason": f"invalid_entry_price:{symbol}"}
            notional = per_member
            qty = notional / price
            entry_fee = notional * TAKER_FEE
            stop = price * (1.0 - BTC_CASE_STOP_FRACTION) if branch == "BTC_case" else None
            target = self._target_price(price, 0.0) if branch == "BTC_case" else None
            cur = db.execute("""INSERT INTO positions (event_id,branch,symbol,member_index,member_count,notional,entry_ms,entry,
                       initial_qty,remaining_qty,deadline_ms,stop_price,target_price,stage,entry_fee,realized_pnl,last_bar_open_ms,
                       fee_bp) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?,?,?,?)""",
                       (cid, branch, symbol, index, len(members), notional, entry_ms, price, qty, qty, deadline,
                        stop, target, entry_fee, -entry_fee, entry_ms - BAR_MS, 2 * TAKER_FEE * 10_000))
            pid = int(cur.lastrowid)
            created_ids.append(pid)
            db.execute("INSERT INTO fills (position_id,time_ms,kind,qty,price,fee_rate,fee,price_pnl,net_pnl) VALUES (?,?,?,?,?,?,?,?,?)",
                       (pid, entry_ms, "entry_market", qty, price, TAKER_FEE, entry_fee, 0.0, -entry_fee))
            db.execute(f"UPDATE account SET {equity_field}={equity_field}-? WHERE id=1", (entry_fee,))
            # A settlement exactly on entry is charged conservatively when its actual
            # rate and mark are already present in the same closed-bar event payload.
            for funding_row in candidate.get("_entry_funding", []):
                if str(funding_row.get("symbol")) == symbol and _as_int(funding_row.get("time_ms")) == entry_ms:
                    self._apply_one_funding(db, pid, funding_row)
        db.execute("UPDATE account SET last_entry_ms=? WHERE id=1", (entry_ms,))
        return {"status": "open", "reason": None, "position_ids": created_ids, "notional": total}

    def _apply_one_funding(self, db: sqlite3.Connection, position_id: int, item: dict[str, Any]) -> None:
        p = dict(db.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone())
        time_ms = _as_int(item.get("time_ms"))
        if time_ms is None or db.execute("SELECT 1 FROM funding WHERE position_id=? AND time_ms=?", (position_id, time_ms)).fetchone():
            return
        if item.get("available") is False or not _finite(item.get("rate")) or not _finite(item.get("mark_price")):
            db.execute("INSERT INTO funding (position_id,time_ms,rate,mark_price,payment,status,source_json) VALUES (?,?,?,?,?,?,?)",
                       (position_id, time_ms, item.get("rate"), item.get("mark_price"), None, "missing", _json(item)))
            db.execute("UPDATE positions SET funding_complete=0,funding_missing_n=funding_missing_n+1 WHERE id=?", (position_id,))
            return
        payment = -float(p["remaining_qty"]) * float(item["mark_price"]) * float(item["rate"])
        # Match the source study's adverse ordering when a settlement shares an
        # entry or exit boundary: charge a cost, but do not count a boundary receipt.
        boundary = time_ms in {int(p["entry_ms"]), int(p["deadline_ms"])}
        if boundary and payment > 0:
            db.execute("INSERT INTO funding (position_id,time_ms,rate,mark_price,payment,status,source_json) VALUES (?,?,?,?,?,?,?)",
                       (position_id, time_ms, float(item["rate"]), float(item["mark_price"]), 0.0,
                        "boundary_income_excluded", _json(item)))
            return
        db.execute("INSERT INTO funding (position_id,time_ms,rate,mark_price,payment,status,source_json) VALUES (?,?,?,?,?,?,?)",
                   (position_id, time_ms, float(item["rate"]), float(item["mark_price"]), payment, "applied", _json(item)))
        db.execute("UPDATE positions SET funding_pnl=funding_pnl+? WHERE id=?", (payment, position_id))
        field = self._equity_field(p["branch"])
        db.execute(f"UPDATE account SET {field}={field}+? WHERE id=1", (payment,))
        if p["branch"] == "BTC_case":
            current = dict(db.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone())
            self._finalize_case_target(db, current)

    def _process_positions(self, db: sqlite3.Connection, bars: dict[str, dict[str, Any]], bar_open_ms: int,
                           funding_settles: set[str] | None = None) -> None:
        funding_settles = funding_settles or set()
        positions = [dict(r) for r in db.execute("SELECT * FROM positions WHERE stage='open' ORDER BY id")]
        for p in positions:
            if bar_open_ms < int(p["entry_ms"]):
                continue
            row = bars.get(p["symbol"])
            if row is None:
                continue  # preflight already returned a retry response
            prior = _as_int(p.get("last_bar_open_ms"))
            if prior is not None and bar_open_ms > prior + BAR_MS:
                db.execute("UPDATE positions SET price_coverage_complete=0,error=COALESCE(error,'missing_5m_bars') WHERE id=?", (p["id"],))
            if p["branch"] in {"BTC_ordinary", "ALT"}:
                db.execute("UPDATE positions SET last_bar_open_ms=? WHERE id=?", (bar_open_ms, p["id"]))
                continue
            pause_from_ledger = db.execute(
                "SELECT 1 FROM funding WHERE position_id=? AND time_ms=? AND status IN ('applied','boundary_income_excluded') LIMIT 1",
                (p["id"], bar_open_ms),
            ).fetchone() is not None
            self._process_case_bar(db, p, row, bar_open_ms,
                                   pause_limit=p["symbol"] in funding_settles or pause_from_ledger)

    def _settle_deadlines(self, db: sqlite3.Connection, bars: dict[str, dict[str, Any]], boundary_ms: int,
                          *, edge: str = "close") -> None:
        """Settle exact six-hour/six-day deadlines using the corresponding real open."""
        if edge not in {"open", "close"}:
            raise ValueError("edge must be open or close")
        due = [dict(r) for r in db.execute("SELECT * FROM positions WHERE stage='open' AND deadline_ms=? ORDER BY id",
                                           (boundary_ms,))]
        for p in due:
            row = bars.get(p["symbol"])
            if row is None:
                raise RetryableBarError(p["symbol"], boundary_ms, reason="missing_exit_open")
            if edge == "open":
                price, stamp = row.get("open"), row.get("bar_open_ms")
            else:
                price, stamp = row.get("next_open"), row.get("next_open_ms")
            if _as_int(stamp) != boundary_ms or not _finite(price):
                raise RetryableBarError(p["symbol"], boundary_ms, reason="missing_exit_open")
            reason = "time_6h" if p["branch"] == "ALT" else "time_6d"
            self._close_position(db, p, boundary_ms, float(price), reason, TAKER_FEE)

    def _process_case_bar(self, db: sqlite3.Connection, p: dict[str, Any], row: dict[str, Any], bar_open_ms: int,
                          *, pause_limit: bool = False) -> None:
        entry = float(p["entry"])
        stop = float(p["stop_price"])
        target = float(p["target_price"]) if _finite(p.get("target_price")) else math.inf
        op, high, low = float(row["open"]), float(row["high"]), float(row["low"])
        stop_hit = low <= stop
        target_hit = not pause_limit and p["first_exit_ms"] is None and high >= target
        if op <= stop:
            self._close_position(db, p, bar_open_ms, op, "stop_6pct_gap", TAKER_FEE)
        elif op >= target and target_hit:
            self._partial_case_target(db, p, bar_open_ms, target)
            # A target gap fills the maker limit at the target, then the remaining
            # half continues through the same candle's protective stop check.
            current = dict(db.execute("SELECT * FROM positions WHERE id=?", (p["id"],)).fetchone())
            if current["stage"] == "open" and low <= float(current["stop_price"]):
                self._close_position(db, current, bar_open_ms, float(current["stop_price"]), "stop_6pct", TAKER_FEE)
        elif stop_hit:
            # If stop and limit both touch in one 5m candle, the frozen native study
            # resolves the ambiguity conservatively in favor of the protective stop.
            self._close_position(db, p, bar_open_ms, stop, "stop_6pct", TAKER_FEE)
        elif target_hit:
            self._partial_case_target(db, p, bar_open_ms, target)
        else:
            db.execute("UPDATE positions SET last_bar_open_ms=? WHERE id=?", (bar_open_ms, p["id"]))

    def _partial_case_target(self, db: sqlite3.Connection, p: dict[str, Any], time_ms: int, price: float) -> None:
        qty = min(float(p["remaining_qty"]), float(p["initial_qty"]) * 0.5)
        if qty <= 0:
            return
        fee = qty * price * MAKER_FEE
        gross = qty * (price - float(p["entry"]))
        pnl = gross - fee
        remaining = float(p["remaining_qty"]) - qty
        field = self._equity_field(p["branch"])
        db.execute(f"UPDATE account SET {field}={field}+? WHERE id=1", (pnl,))
        db.execute("INSERT INTO fills (position_id,time_ms,kind,qty,price,fee_rate,fee,price_pnl,net_pnl) VALUES (?,?,?,?,?,?,?,?,?)",
                   (p["id"], time_ms, "target_limit_maker", qty, price, MAKER_FEE, fee, gross, pnl))
        db.execute("UPDATE positions SET remaining_qty=?,first_exit_ms=?,first_exit_price=?,exit_fee=exit_fee+?,realized_pnl=realized_pnl+?,last_bar_open_ms=?,target_price=NULL WHERE id=?",
                   (remaining, time_ms, price, fee, pnl, time_ms, p["id"]))

    def _close_position(self, db: sqlite3.Connection, p: dict[str, Any], time_ms: int, price: float,
                        reason: str, fee_rate: float) -> None:
        qty = float(p["remaining_qty"])
        if qty <= 1e-15:
            db.execute("UPDATE positions SET stage='closed',exit_ms=?,exit_price=?,exit_reason=?,remaining_qty=0 WHERE id=?",
                       (time_ms, price, reason, p["id"]))
            return
        fee = qty * price * fee_rate
        gross = qty * (price - float(p["entry"]))
        pnl = gross - fee
        field = self._equity_field(p["branch"])
        db.execute(f"UPDATE account SET {field}={field}+? WHERE id=1", (pnl,))
        db.execute("INSERT INTO fills (position_id,time_ms,kind,qty,price,fee_rate,fee,price_pnl,net_pnl) VALUES (?,?,?,?,?,?,?,?,?)",
                   (p["id"], time_ms, reason, qty, price, fee_rate, fee, gross, pnl))
        db.execute("UPDATE positions SET stage='closed',remaining_qty=0,exit_ms=?,exit_price=?,exit_reason=?,exit_fee=exit_fee+?,realized_pnl=realized_pnl+?,last_bar_open_ms=? WHERE id=?",
                   (time_ms, price, reason, fee, pnl, time_ms, p["id"]))
        self._refresh_candidate_status(db, p["event_id"])

    @staticmethod
    def _refresh_candidate_status(db: sqlite3.Connection, event_id: str) -> None:
        rows = db.execute("SELECT stage FROM positions WHERE event_id=?", (event_id,)).fetchall()
        if rows and all(row["stage"] == "closed" for row in rows):
            db.execute("UPDATE candidates SET status='closed',reason=NULL,updated_ms=? WHERE event_id=?",
                       (int(__import__("time").time() * 1000), event_id))

    def _finalize_case_target(self, db: sqlite3.Connection, p: dict[str, Any]) -> None:
        """Reprice the still-working target after each observed funding settlement."""
        if p["branch"] != "BTC_case" or p["stage"] != "open" or p["first_exit_ms"] is not None:
            return
        paid_points = -float(p["funding_pnl"]) / float(p["initial_qty"]) if p["initial_qty"] else 0.0
        new_target = self._target_price(float(p["entry"]), paid_points)
        db.execute("UPDATE positions SET target_price=? WHERE id=?", (new_target, p["id"]))

    def _normalize_candidate_funding(self, candidate: dict[str, Any], fundings: list[dict[str, Any]]) -> dict[str, Any]:
        copy = dict(candidate)
        entry_ms = _as_int(copy.get("entry_ms", copy.get("decision_ms")))
        branch = _candidate_branch(copy)
        members = copy.get("members") or copy.get("alt_members") or []
        symbols = [BTC_SYMBOL] if branch in {"BTC_case", "BTC_ordinary"} else [
            str(m.get("symbol") or "") for m in members if isinstance(m, dict)
        ]
        copy["_entry_funding"] = [f for f in fundings if f.get("time_ms") == entry_ms and f.get("symbol") in symbols]
        return copy

    def snapshot(self) -> dict[str, Any]:
        with self.db() as db:
            acct = dict(db.execute("SELECT * FROM account WHERE id=1").fetchone())
            recent_positions = [dict(r) for r in db.execute("SELECT * FROM positions ORDER BY entry_ms DESC,id DESC LIMIT 500")]
            open_positions = [dict(r) for r in db.execute("SELECT * FROM positions WHERE stage='open' ORDER BY entry_ms DESC,id DESC")]
            recent_ids = {p["id"] for p in recent_positions}
            positions = recent_positions + [p for p in open_positions if p["id"] not in recent_ids]
            signals = [dict(r) for r in db.execute("SELECT * FROM candidates ORDER BY decision_ms DESC LIMIT 100")]
            marks = {r["symbol"]: dict(r) for r in db.execute("SELECT * FROM latest_marks")}
            funding_missing_n = int(db.execute("SELECT COUNT(*) FROM positions WHERE funding_complete=0").fetchone()[0])
            total_positions = int(db.execute("SELECT COUNT(*) FROM positions").fetchone()[0])
            price_incomplete_n = int(db.execute("SELECT COUNT(*) FROM positions WHERE price_coverage_complete=0").fetchone()[0])
        for p in positions:
            p["funding_complete"] = bool(p["funding_complete"])
            p["price_coverage_complete"] = bool(p["price_coverage_complete"])
            mark = marks.get(p["symbol"])
            p["mark"] = mark["close"] if mark else None
            p["unrealized_price_pnl"] = ((float(mark["close"]) - float(p["entry"])) * float(p["remaining_qty"])) if mark and p["stage"] == "open" else 0.0
            p["pnl_complete"] = p["funding_complete"] and p["price_coverage_complete"]
            p["complete_net_pnl"] = float(p["realized_pnl"] + p["funding_pnl"] + p["unrealized_price_pnl"]) if p["pnl_complete"] else None
        funding_complete = funding_missing_n == 0
        price_complete = price_incomplete_n == 0
        btc_equity, alt_equity = float(acct["btc_equity"]), float(acct["alt_equity"])
        return {
            "rule": RULE,
            "now_ms": int(self.clock()),
            "start_equity": float(acct["start_equity"]),
            "started_ms": int(acct["started_ms"]),
            "equity": btc_equity + alt_equity,
            "equity_is_known_component_total": True,
            "btc_equity": btc_equity,
            "alt_equity": alt_equity,
            "initial_sleeves": {"btc": self.btc_start, "alt": self.alt_start},
            "open_positions": len(open_positions),
            "open_btc_positions": sum(p["branch"] != "ALT" for p in open_positions),
            "open_alt_positions": sum(p["branch"] == "ALT" for p in open_positions),
            "last_entry_ms": _as_int(acct.get("last_entry_ms")),
            "funding_complete": funding_complete,
            "funding_missing_positions": funding_missing_n,
            "price_coverage_complete": price_complete,
            "pnl_complete": funding_complete and price_complete,
            "position_count": total_positions,
            "cursor_close_ms": _as_int(acct.get("cursor_close_ms")),
            "btc_reserved_until_ms": int(acct["btc_reserved_until_ms"]),
            "alt_reserved_until_ms": int(acct["alt_reserved_until_ms"]),
            "signals": signals,
            "positions": positions,
        }
__all__ = ["BreadthEma20Paper", "RetryableBarError", "RULE"]
