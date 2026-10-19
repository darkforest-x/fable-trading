"""Live paper runner for the VIDYA trend-line retest on every OKX USDT perpetual (15m).

Owner request 2026-10-12: run the strategy as a real-time simulation across all
coins, in its own menu and module. Universe: regular OKX crypto USDT perpetuals
(instCategory 1, ruleType normal); tokenised stocks, commodities and pre-market
contracts are excluded. This process never sends orders or notifications
and holds no credentials; it reads public OKX candles through the monitor's existing
rate-limited client and writes only its own SQLite book in the monitor runtime
directory (it does not touch the VPS-owned OHLCV cache, forward_log or monitor.sqlite3).

Timing: a 15m bar is processed after its confirmed candle is published. Decisions
use bars <= the processed bar; fills happen at the next bar's open (the monitor's
paper convention) and costs are the 20bp round trip used by the other paper books.
History: OKX serves the latest ~1440 15m bars; at that length the indicator's trend,
line and delta at the newest bar matched a full-history computation at all 444
sampled cut points on ETH/BTC/SOL (2026-10-12 check), so no older history is fetched.

Sources: bars that closed before a symbol was first seen are replayed once as
``warmup`` (display only, never mixed into live statistics). ``live`` starts at the
first new bar after activation and continues across restarts from the stored state.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sqlite3
import time
import traceback

DATABASE = "vidya_retest.sqlite3"
TIMEFRAME = "15m"
BAR_MS = 900_000
HISTORY = 1440
TRIM_ABOVE = 3000           # keep memory bounded; parity check above shows 1440 suffices
WARMUP_FROM = 500           # first replayed index inside the initial history
RATE = 4.0                  # req/s for this process; the scanner keeps its own 8/s budget
UNIVERSE_REFRESH_MS = 3_600_000
SETTLE_MS = 20_000          # wait after a bar boundary before asking for the confirmed bar
NEAR_ATR = 1.0              # "approaching retest" display band, descriptive only
FRESH_MS = 30 * 60 * 1000   # same freshness limit as the monitor; older signals never open paper trades
LOG = logging.getLogger("fable.vidya_retest")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS states (
  strategy TEXT NOT NULL, symbol TEXT NOT NULL, source TEXT NOT NULL,
  last_bar_ms INTEGER, state TEXT NOT NULL, updated_ms INTEGER NOT NULL,
  PRIMARY KEY (strategy, symbol, source));
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, strategy TEXT NOT NULL, symbol TEXT NOT NULL,
  source TEXT NOT NULL, kind TEXT NOT NULL, side TEXT, bar_ms INTEGER NOT NULL,
  payload TEXT NOT NULL, created_ms INTEGER NOT NULL,
  UNIQUE (strategy, symbol, source, kind, side, bar_ms));
CREATE INDEX IF NOT EXISTS events_recent ON events (strategy, source, bar_ms DESC);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, strategy TEXT NOT NULL, symbol TEXT NOT NULL,
  source TEXT NOT NULL, side TEXT NOT NULL, touch_ms INTEGER, signal_ms INTEGER NOT NULL,
  entry_ms INTEGER NOT NULL, exit_ms INTEGER NOT NULL, entry REAL NOT NULL, exit REAL NOT NULL,
  stop REAL NOT NULL, target REAL, level REAL, delta REAL, risk_pct REAL NOT NULL,
  reason TEXT NOT NULL, r REAL NOT NULL, r_gross REAL NOT NULL, bars INTEGER NOT NULL,
  created_ms INTEGER NOT NULL,
  UNIQUE (strategy, symbol, source, entry_ms));
CREATE INDEX IF NOT EXISTS trades_recent ON trades (strategy, source, exit_ms DESC);
CREATE TABLE IF NOT EXISTS market (
  symbol TEXT PRIMARY KEY, bar_ms INTEGER NOT NULL, close REAL NOT NULL, trend TEXT NOT NULL,
  line REAL, delta REAL, atr REAL, dist_atr REAL, near INTEGER NOT NULL, bars INTEGER NOT NULL,
  updated_ms INTEGER NOT NULL);
"""

IDX_FIELDS = ("signal_index", "touch_index", "entry_index")


def tradable(instrument: dict) -> bool:
    """Owner asked for all coins: OKX crypto USDT perpetuals only.

    OKX tags SWAP instruments with instCategory 1 (crypto), 3 (tokenised stocks) or
    4 (commodities); pre-market contracts carry ruleType pre_market. Only regular
    crypto USDT-margined contracts are monitored.
    """
    return (str(instrument.get("instId", "")).endswith("-USDT-SWAP")
            and str(instrument.get("instCategory", "1")) == "1"
            and instrument.get("ruleType", "normal") == "normal")


def connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(path), timeout=10)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=10000")
    return db


class Book:
    """The worker's own SQLite ledger. Only this process writes it."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with connect(self.path) as db:
            db.executescript(SCHEMA)

    def get_meta(self, key, default=None):
        with connect(self.path) as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def load_state(self, strategy, symbol, source):
        with connect(self.path) as db:
            row = db.execute("SELECT last_bar_ms, state FROM states WHERE strategy=? AND symbol=? AND source=?",
                             (strategy, symbol, source)).fetchone()
        return (row[0], json.loads(row[1])) if row else (None, None)

    def prune(self, universe):
        """Drop display rows and warmup replays of instruments outside the universe.

        Live paper records are kept: they are forward history even after a delisting.
        """
        keep = set(universe)
        with connect(self.path) as db:
            for table in ("market",):
                gone = [r[0] for r in db.execute(f"SELECT DISTINCT symbol FROM {table}") if r[0] not in keep]
                db.executemany(f"DELETE FROM {table} WHERE symbol=?", [(g,) for g in gone])
            for table in ("trades", "events", "states"):
                gone = [r[0] for r in db.execute(f"SELECT DISTINCT symbol FROM {table} WHERE source='warmup'") if r[0] not in keep]
                db.executemany(f"DELETE FROM {table} WHERE source='warmup' AND symbol=?", [(g,) for g in gone])

    def commit_symbol(self, symbol, states, events, trades, market, now_ms):
        """Persist one symbol's step atomically so a crash never splits state from its trades."""
        with connect(self.path) as db:
            for (strategy, source), (last_bar_ms, state) in states.items():
                db.execute("INSERT OR REPLACE INTO states VALUES (?,?,?,?,?,?)",
                           (strategy, symbol, source, last_bar_ms, json.dumps(state), now_ms))
            for ev in events:
                db.execute("INSERT OR IGNORE INTO events (strategy,symbol,source,kind,side,bar_ms,payload,created_ms) "
                           "VALUES (?,?,?,?,?,?,?,?)",
                           (ev["strategy"], symbol, ev["source"], ev["kind"], ev.get("side"), ev["bar_ms"],
                            json.dumps(ev, ensure_ascii=False), now_ms))
            for t in trades:
                db.execute("INSERT OR IGNORE INTO trades (strategy,symbol,source,side,touch_ms,signal_ms,entry_ms,exit_ms,"
                           "entry,exit,stop,target,level,delta,risk_pct,reason,r,r_gross,bars,created_ms) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (t["strategy"], symbol, t["source"], t["side"], t["touch_ms"], t["signal_ms"], t["entry_ms"],
                            t["exit_ms"], t["entry"], t["exit"], t["stop"], t["target"], t["level"], t["delta"],
                            t["risk_pct"], t["reason"], t["r"], t["r_gross"], t["bars"], now_ms))
            if market is not None:
                db.execute("INSERT OR REPLACE INTO market VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (symbol, market["bar_ms"], market["close"], market["trend"], market["line"], market["delta"],
                            market["atr"], market["dist_atr"], int(market["near"]), market["bars"], now_ms))


def _finite(x):
    return x is not None and x == x and abs(x) != float("inf")


def state_to_times(st, times):
    """Replace bar indices with bar open times so state survives array rolling."""
    raw = st.to_dict()
    raw["pending"] = {k: (int(times[v]) if v is not None else None) for k, v in raw["pending"].items()}
    for key in ("order", "position"):
        if raw.get(key):
            raw[key] = {k: (int(times[v]) if k in IDX_FIELDS and v is not None and v >= 0 else v)
                        for k, v in raw[key].items()}
    return raw


def state_from_times(raw, index_of):
    """Inverse of state_to_times; times no longer in memory map to -1 (pending is dropped)."""
    from yoyo.monitor.vidya_retest import State

    if not raw:
        return State()
    pend = raw.get("pending") or {}
    raw = dict(raw, pending={k: index_of.get(v) for k, v in pend.items() if v is not None})
    for key in ("order", "position"):
        if raw.get(key):
            raw[key] = {k: (index_of.get(v, -1) if k in IDX_FIELDS and v is not None else v)
                        for k, v in raw[key].items()}
    return State.from_dict(raw)


def _event_record(ev, times, strategy, source):
    out = {"strategy": strategy, "source": source}
    for k, v in ev.items():
        if k == "index":
            out["bar_ms"] = int(times[v])
        elif k in IDX_FIELDS:
            out[k.replace("_index", "_ms")] = int(times[v]) if v is not None and v >= 0 else None
        elif isinstance(v, float):
            out[k] = v if _finite(v) else None
        else:
            out[k] = v
    return out


def _trade_record(ev, times, strategy, source):
    ms = lambda key: int(times[ev[key]]) if ev.get(key) is not None and ev[key] >= 0 else None
    return dict(strategy=strategy, source=source, side=ev["side"], touch_ms=ms("touch_index"),
                signal_ms=ms("signal_index"), entry_ms=ms("entry_index"), exit_ms=int(times[ev["index"]]),
                entry=ev["entry"], exit=ev["price"], stop=ev["stop"], target=ev["target"], level=ev["level"],
                delta=ev["delta"], risk_pct=ev["risk"] / ev["entry"] * 100, reason=ev["reason"],
                r=ev["r"], r_gross=ev["r_gross"], bars=int(ev["index"] - ev["entry_index"]))


def step_symbol(book, symbol, candles, now_ms, activated_ms):
    """Run every strategy over the bars this symbol has not processed yet."""
    import numpy as np
    from yoyo.monitor.vidya_retest import STRATEGIES, Strategy, indicator

    if len(candles) < WARMUP_FROM + 2:
        return None
    times = np.array([r["t"] for r in candles], dtype=np.int64)
    bars = {k: np.array([r[k] for r in candles], dtype=float) for k in ("o", "h", "l", "c")}
    vol = np.array([r["v"] for r in candles], dtype=float)
    index_of = {int(t): i for i, t in enumerate(times)}
    by_length = {}
    states, events, trades = {}, [], []
    for sid, cfg in STRATEGIES.items():
        p = cfg["params"]
        if p.length not in by_length:
            by_length[p.length] = indicator(bars["o"], bars["h"], bars["l"], bars["c"], vol,
                                            length=p.length, momentum=p.momentum, distance=p.distance)
        ind = by_length[p.length]
        strat = Strategy(p)
        for source in ("warmup", "live"):
            last_ms, raw = book.load_state(sid, symbol, source)
            if source == "warmup":
                if last_ms is not None:
                    continue                      # warmup replays once per symbol
                start = WARMUP_FROM
                stop_at = int(np.searchsorted(times, activated_ms - BAR_MS, side="right"))  # bars closed by activation
                st = state_from_times(None, index_of)
            else:
                if last_ms is None:
                    # first live sight: begin after the newest bar already closed
                    states[(sid, "live")] = (int(times[-1]), state_to_times(state_from_times(None, index_of), times))
                    continue
                start = int(np.searchsorted(times, last_ms, side="right"))
                stop_at = len(times)
                st = state_from_times(raw, index_of)
                if last_ms < times[0]:
                    # downtime longer than the fetched history: state cannot be continued honestly
                    events.append({"strategy": sid, "source": source, "kind": "gap", "side": None,
                                   "bar_ms": int(times[-1]), "last_bar_ms": int(last_ms)})
                    st = state_from_times(None, index_of)
                    start = stop_at
            for i in range(start, stop_at):
                fresh = source == "warmup" or now_ms - (int(times[i]) + BAR_MS) <= FRESH_MS
                for ev in strat.on_bar(i, bars, ind, st, allow_new=fresh):
                    if ev["kind"] in ("signal", "skip", "entry", "exit"):
                        events.append(_event_record(ev, times, sid, source))
                    if ev["kind"] == "exit":
                        trades.append(_trade_record(ev, times, sid, source))
            if source == "warmup":
                states[(sid, source)] = (int(times[max(stop_at - 1, 0)]), {"replayed_bars": max(0, stop_at - start)})
            else:
                states[(sid, source)] = (int(times[-1]), state_to_times(st, times))
    ind = by_length[min(by_length)]
    i = len(times) - 1
    up = bool(ind["up"][i])
    line, atr, delta = float(ind["line"][i]), float(ind["atr14"][i]), float(ind["delta"][i])
    dist = None
    if _finite(line) and _finite(atr) and atr > 0:
        dist = (bars["l"][i] - line) / atr if up else (line - bars["h"][i]) / atr
    strong = delta >= 50 if up else delta <= -50
    market = dict(bar_ms=int(times[i]), close=float(bars["c"][i]), trend="up" if up else "down",
                  line=line if _finite(line) else None, delta=delta, atr=atr if _finite(atr) else None,
                  dist_atr=dist, near=bool(strong and dist is not None and 0 <= dist <= NEAR_ATR), bars=len(times))
    book.commit_symbol(symbol, states, events, trades, market, now_ms)
    return len(events)


def run_forever(runtime: str):
    """Process entry point: fetch, step, persist; one pass per closed 15m bar."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from yoyo.monitor.okx import OKX

    book = Book(Path(runtime) / DATABASE)
    client = OKX(rate=RATE, runtime=runtime)
    activated = book.get_meta("activation")
    candles, universe, universe_at = {}, [], 0
    status = {"status": "starting", "pid": os.getpid(), "started_ms": int(time.time() * 1000), "errors": 0,
              "error_samples": [], "completed": 0, "total": 0, "cycles": 0}
    book.set_meta("status", status)
    while True:
        try:
            client.synchronize()
            now = client.clock()
            if activated is None:
                activated = {"activated_ms": now, "timeframe": TIMEFRAME, "strategies": "tl-2r,tl-trend",
                             "fill": "next_open", "round_trip_cost": 0.002}
                book.set_meta("activation", activated)
            if not universe or now - universe_at > UNIVERSE_REFRESH_MS:
                universe = [r["instId"] for r in client.instruments() if tradable(r)]
                universe_at = now
                book.prune(universe)
            expected = now // BAR_MS * BAR_MS - BAR_MS   # open time of the newest closed bar
            status.update(status="running" if status["cycles"] else "loading", total=len(universe), completed=0,
                          cycle_bar_ms=expected, cycle_started_ms=now, errors=0, error_samples=[])
            book.set_meta("status", status)
            stale = []
            for n, symbol in enumerate(universe, 1):
                try:
                    rows, _ = client.candles(symbol, TIMEFRAME, candles.get(symbol), limit=HISTORY)
                    if len(rows) > TRIM_ABOVE:
                        rows = rows[-HISTORY:]
                    candles[symbol] = rows
                    if not rows or rows[-1]["t"] < expected:
                        stale.append(symbol)
                    step_symbol(book, symbol, rows, client.clock(), activated["activated_ms"])
                except Exception as exc:  # one symbol never stops the pass
                    candles.pop(symbol, None)  # e.g. exchange revised a confirmed candle: refetch from scratch
                    status["errors"] += 1
                    if len(status["error_samples"]) < 8:
                        status["error_samples"].append(f"{symbol}: {type(exc).__name__}: {str(exc)[:120]}")
                    LOG.warning("symbol %s failed: %s", symbol, traceback.format_exc(limit=2))
                status["completed"] = n
                if n % 25 == 0:
                    book.set_meta("status", status)
            # one retry for bars OKX had not confirmed yet
            if stale:
                time.sleep(30)
                for symbol in stale:
                    try:
                        rows, _ = client.candles(symbol, TIMEFRAME, candles.get(symbol), limit=HISTORY)
                        candles[symbol] = rows
                        step_symbol(book, symbol, rows, client.clock(), activated["activated_ms"])
                    except Exception:
                        status["errors"] += 1
            status.update(status="running", cycles=status["cycles"] + 1, cycle_finished_ms=client.clock(),
                          stale_after_retry=len([s for s in stale if not candles.get(s) or candles[s][-1]["t"] < expected]),
                          requests=client.requests)
            nxt = (client.clock() // BAR_MS + 1) * BAR_MS + SETTLE_MS
            status["next_cycle_ms"] = nxt
            book.set_meta("status", status)
            time.sleep(max(5.0, (nxt - client.clock()) / 1000))
        except Exception as exc:
            status.update(status="error", last_error=f"{type(exc).__name__}: {str(exc)[:200]}")
            book.set_meta("status", status)
            LOG.error("cycle failed: %s", traceback.format_exc(limit=3))
            time.sleep(60)
