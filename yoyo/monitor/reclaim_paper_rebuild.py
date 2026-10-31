"""Rebuild the Monday 1.618 paper ledger on the backtest score basis (owner 2026-10-31).

Owner chose to recompute the accounts since 2026-10-25 rather than clear them. Every touch the paper engine recorded
(its signals table: week, symbol, side, candle and observation time) is replayed through ReclaimPaper itself with
score_basis="touch_close", minute by minute, on OKX history candles served as of a simulated clock:

  per minute M, at M+65s: live touches observed before M+60s enter as pending; score_due scores touches whose 5m bar
  has closed, books them at the touch minute and replays their gap; decide(M) then the M candle of every other
  watched symbol, the order of ReclaimPaper._catch_up.

Accounts start at their original started_ms with 1000 USDT (a disabled account opens nothing before it). The old
ledger's single manual close is not replayed. Candles come from /api/v5/market/history-candles at a few requests per
second into a local cache. Only complete bars (confirm=1, open time + bar <= simulated now) are served, so the replay
reads no candle the live engine could not have read. The output is a new SQLite file and a resumable cursor; putting it
in place of the live ledger is a separate step with the monitor stopped.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
import time
from pathlib import Path

import requests

from yoyo.monitor.monday_fib import FibBook, week_window
from yoyo.monitor.monday_reclaim_paper import (ALLIN3_TRACK, SPIKE_TRACK, V3_TRACK, V3ADD_TRACK, V3ADD20_TRACK,
                                               V3LOCK_TRACK, ReclaimPaper)

ORIGIN = "https://www.okx.com"
STEP = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1H": 3_600_000}
MIN = 60_000
PAGE = 300
GAP_S = 0.25  # history-candles shares a 20 requests / 2 s IP budget with the live monitor
# Account -> engine attribute that must stay None before the account's original start.
GATES = {V3_TRACK: "v3", V3ADD_TRACK: "add", SPIKE_TRACK: "spike", V3LOCK_TRACK: "v3lock", ALLIN3_TRACK: "allin",
         V3ADD20_TRACK: "add20"}


class CandleCache:
    """OKX history candles in SQLite with fetched-range coverage, so empty minutes are not refetched."""

    def __init__(self, path, session=None):
        self.path, self.session, self.last = Path(path), session or requests.Session(), 0.
        self.requests = 0
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS candles (inst TEXT, bar TEXT, ts INTEGER, row TEXT, PRIMARY KEY(inst, bar, ts));
                CREATE TABLE IF NOT EXISTS coverage (inst TEXT, bar TEXT, lo INTEGER, hi INTEGER);
                CREATE INDEX IF NOT EXISTS coverage_key ON coverage(inst, bar, lo);
            """)

    def db(self):
        db = sqlite3.connect(str(self.path), timeout=30)
        db.isolation_level = None
        return db

    def _get(self, inst, bar, after):
        for attempt in range(8):
            wait = self.last+GAP_S-time.time()
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.requests += 1
            try:
                r = self.session.get(ORIGIN+"/api/v5/market/history-candles", timeout=15,
                                     params={"instId": inst, "bar": bar, "after": str(after), "limit": str(PAGE)})
                body = r.json()
                if r.status_code == 200 and body.get("code") == "0":
                    return body["data"]
                if r.status_code == 429 or body.get("code") == "50011":
                    time.sleep(2*(attempt+1))
                    continue
                raise RuntimeError(f"okx {r.status_code} {body.get('code')} {body.get('msg')}")
            except (requests.RequestException, ValueError):
                time.sleep(2*(attempt+1))
        raise RuntimeError(f"okx history-candles failed: {inst} {bar} {after}")

    def ensure(self, inst, bar, lo, hi):
        """Fetch every bar with open time in [lo, hi) that is complete now (clamped to the real clock)."""
        step = STEP[bar]
        hi = min(hi, int(time.time()*1000)//step*step)
        if hi <= lo:
            return
        with self.db() as db:
            spans = db.execute("SELECT lo, hi FROM coverage WHERE inst=? AND bar=? AND hi>? AND lo<? ORDER BY lo",
                               (inst, bar, lo, hi)).fetchall()
        gaps, cur = [], lo
        for a, b in spans:
            if a > cur:
                gaps.append((cur, a))
            cur = max(cur, b)
        if cur < hi:
            gaps.append((cur, hi))
        for a, b in gaps:
            after, rows = b, []
            while after > a:
                data = self._get(inst, bar, after)
                if not data:
                    break
                rows += [r for r in data if r[8] == "1" and a <= int(r[0]) < b]
                oldest = min(int(r[0]) for r in data)
                if oldest >= after:
                    break
                after = oldest
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                db.executemany("INSERT OR REPLACE INTO candles VALUES (?,?,?,?)",
                               [(inst, bar, int(r[0]), json.dumps(r)) for r in rows])
                db.execute("INSERT INTO coverage VALUES (?,?,?,?)", (inst, bar, a, b))
                db.execute("COMMIT")

    def rows(self, inst, bar, lo, hi):
        self.ensure(inst, bar, lo, hi)
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute(
                "SELECT row FROM candles WHERE inst=? AND bar=? AND ts>=? AND ts<? ORDER BY ts", (inst, bar, lo, hi))]


class HistoryClient:
    """The OKX REST shape ReclaimPaper reads, answered from history as of ``now``: complete bars only."""

    def __init__(self, cache, now):
        self.cache, self.now = cache, now

    def clock(self):
        return self.now

    def get(self, path, params):
        bar, step = params["bar"], STEP[params["bar"]]
        hi = min(int(params.get("after", 1 << 62)), self.now//step*step)
        rows = self.cache.rows(params["instId"], bar, hi-int(params.get("limit", PAGE))*step, hi)
        return rows[::-1]  # newest first, as OKX


class ReplayFib:
    """Fib service stand-in: touch context from a copy of the live FibBook, no quotes or stream."""

    def __init__(self, runtime, client):
        self.client, self.book, self.lock, self.quotes, self.connected = client, FibBook(runtime), threading.Lock(), {}, True


def levels_of(sig):
    w = week_window(sig["candle_ms"])
    if w["week"] != sig["week"]:
        raise ValueError(f"week mismatch {sig['symbol']} {sig['week']} {w['week']}")
    high, low = sig["high"], sig["low"]
    return {**w, "high": high, "low": low, "upper": high+.618*(high-low), "lower": low-.618*(high-low)}


def copy_db(src, dst):
    with sqlite3.connect(str(src)) as a, sqlite3.connect(str(dst)) as b:
        a.backup(b)


def rebuild(source, fib_db, out, until, cache_path, log=print):
    """Replay (or resume) into ``out``/monday-reclaim-paper.sqlite3 through minute ``until`` (exclusive)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    state_path = out/"cursor.json"
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        starts = {r["track"]: r["started_ms"] for r in db.execute("SELECT track, started_ms FROM tracks")}
        signals = [dict(r) for r in db.execute("SELECT week,symbol,side,high,low,level,candle_ms,observed_ms FROM signals "
                                               "ORDER BY observed_ms, id")]
    state = json.loads(state_path.read_text()) if state_path.exists() else None
    cache = CandleCache(cache_path)
    first = min(starts.values())
    client = HistoryClient(cache, state["cursor"] if state else first)
    copy_db(fib_db, out/"monday-fib.sqlite3")  # touch context only; refreshed so resumed touches find their peers
    engine = ReclaimPaper(out, ReplayFib(out, client))
    if not state:
        with engine.db() as db:
            for track, ms in starts.items():
                db.execute("UPDATE tracks SET started_ms=? WHERE track=?", (ms, track))
    configs = {name: getattr(engine, name) for name in GATES.values()}
    with engine.db() as db:
        seen = {(r[0], r[1], r[2]) for r in db.execute("SELECT week, symbol, side FROM signals")}
    todo = [s for s in signals if (s["week"], s["symbol"], s["side"]) not in seen]
    cursor = state["cursor"] if state else first//MIN*MIN
    log(f"replay {len(todo)} touches from {cursor} to {until}; {len(seen)} already in the ledger")
    k, t0 = 0, time.time()
    for minute in range(cursor, until, MIN):
        at = minute+65_000
        for track, name in GATES.items():
            setattr(engine, name, configs[name] if at >= starts.get(track, 1 << 62) else None)
        while k < len(todo) and todo[k]["observed_ms"] < minute+MIN:
            s = todo[k]
            client.now = s["observed_ms"]
            engine.handle_touch(levels_of(s), s["symbol"], "lower" if s["side"] == 1 else "upper", s["level"],
                                s["observed_ms"], s["candle_ms"])
            k += 1
        client.now = at
        with engine.db() as db:
            before = {r[0] for r in db.execute("SELECT symbol FROM signals WHERE status='pending_score'")}
        engine.score_due(at)
        with engine.db() as db:
            replayed = before-{r[0] for r in db.execute("SELECT symbol FROM signals WHERE status='pending_score'")}
        engine.decide(minute)
        for symbol in sorted(engine.watch-replayed):
            bar = cache_minute(cache, symbol, minute, until)
            if bar:
                engine.handle_quote(symbol, {"candle_ms": minute, "high": float(bar[2]), "low": float(bar[3]),
                                             "price": float(bar[4])})
        if (minute//MIN) % 720 == 0:
            log(f"{time.strftime('%m-%d %H:%M', time.gmtime(minute/1000+8*3600))} touches {k}/{len(todo)} "
                f"watch {len(engine.watch)} requests {cache.requests} {time.time()-t0:.0f}s")
    for name, cfg in configs.items():
        setattr(engine, name, cfg)
    state_path.write_text(json.dumps({"cursor": until, "source": str(source), "touches_replayed": k+len(seen),
                                      "unreplayed": len(todo)-k, "requests": cache.requests}))
    return engine


_MINUTES = {}


def cache_minute(cache, symbol, minute, until):
    """One 1m candle, read in 300-minute chunks per symbol."""
    chunk = _MINUTES.get(symbol)
    if not chunk or not chunk[0] <= minute < chunk[1]:
        lo, hi = minute, min(minute+PAGE*MIN, max(until, minute+MIN))
        chunk = (lo, hi, {int(r[0]): r for r in cache.rows(symbol, "1m", lo, hi)})
        _MINUTES[symbol] = chunk
    return chunk[2].get(minute)


def summary(path):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        tracks = [dict(r) for r in db.execute("SELECT track, equity, stakes, started_ms FROM tracks ORDER BY track")]
        for t in tracks:
            members = (t["track"], t["track"]+1) if t["track"] in (V3ADD_TRACK, V3ADD20_TRACK) else (t["track"],)
            marks = ",".join("?"*len(members))
            row = db.execute(f"SELECT COUNT(*) n, SUM(pnl>0) wins, SUM(pnl) pnl, SUM(stage<>'closed') open FROM positions "
                             f"WHERE track IN ({marks}) AND taken=1", members).fetchone()
            t.update(funded=row["n"], wins=row["wins"], realized=row["pnl"], open=row["open"])
        status = {r[0]: r[1] for r in db.execute("SELECT status, COUNT(*) FROM signals GROUP BY status")}
    return dict(tracks=tracks, signals=status)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True, help="paper ledger whose signals are replayed (read only)")
    ap.add_argument("--fib", type=Path, required=True, help="monday-fib.sqlite3 for touch context (copied)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--until", default="now", help="epoch ms (exclusive minute) or 'now' (last complete minute)")
    args = ap.parse_args()
    until = (int(time.time()*1000)//MIN-1)*MIN if args.until == "now" else int(args.until)//MIN*MIN
    rebuild(args.source, args.fib, args.out, until, args.cache, log=lambda m: print(m, flush=True))
    s = summary(args.out/"monday-reclaim-paper.sqlite3")
    (args.out/"summary.json").write_text(json.dumps(s, indent=1))
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    main()
