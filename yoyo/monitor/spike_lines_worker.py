"""Isolated worker for the 突破 / 突破+spike menus (`spike_lines`).

Reads the V9 scanner's closed-bar checkpoints from ``monitor.sqlite3`` read-only (the
scanner stays the only writer of those candles) and fetches only daily ``1Dutc`` bars
itself, which the scanner does not keep, from OKX public market data at a lower rate
than the scanner. Results go to a separate SQLite book so a failure here cannot touch
V9 events, Bark or the model gate. No notification, no order endpoint.

A cell (symbol, timeframe) is recomputed only when its own checkpoint or its higher
timeframe's bars changed. Events are insert-only; an event first seen long after its
bar closed keeps that late ``detected_at_ms`` and is shown as a late record, never as
a fresh one. Only a joint's ``performance`` (its simulated position) is rewritten as
later bars close; a position left open when its signal falls out of the analysis
window becomes ``unknown`` rather than a frozen floating R. A projection-policy
change freezes the existing joint performance as an audit snapshot before a new
policy rewrites it; the snapshot is never recomputed.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from yoyo.monitor.store import now_ms

DATABASE = "spike-lines-v1.sqlite3"
PRIMARY_DATABASE = "monitor.sqlite3"
POLL_SECONDS = 60
HISTORY_BARS = 200          # how far back a cold start records events, per timeframe
DAY_MS = 86_400_000
SCANNER_TIMEFRAMES = ("15m", "30m", "1H", "4H")
LEGACY_PERFORMANCE_BASIS = "v11_2_box_joint_next_open_serial_net_of_round_trip_cost"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class LinesBook:
    """The worker's own SQLite file; the API reads it read-only."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                    bar_open_ms INTEGER NOT NULL, bar_close_ms INTEGER NOT NULL,
                    detected_at_ms INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_kind_close ON events(kind, bar_close_ms DESC);
                CREATE TABLE IF NOT EXISTS performance_versions (
                    event_id TEXT NOT NULL, version TEXT NOT NULL, payload TEXT NOT NULL, saved_ms INTEGER NOT NULL,
                    PRIMARY KEY(event_id, version));
                CREATE INDEX IF NOT EXISTS performance_versions_version ON performance_versions(version);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS daily (symbol TEXT PRIMARY KEY, payload BLOB NOT NULL, updated_ms INTEGER NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=20)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get_meta(self, key: str, default=None):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, _json(value)))

    def initialize_performance_policy(self, version: str, *, changed_at_ms: int) -> dict:
        """Set the active projection, atomically snapshotting all joint cards on change."""
        if not isinstance(version, str) or not version:
            raise ValueError("performance basis must be a non-empty string")
        with self.connect() as db:
            row = db.execute("SELECT payload FROM meta WHERE key='performance_policy'").fetchone()
            existing = json.loads(row[0]) if row else None
            current = existing.get("version") if isinstance(existing, dict) else LEGACY_PERFORMANCE_BASIS
            if current == version and isinstance(existing, dict):
                return existing
            if existing is None and version == LEGACY_PERFORMANCE_BASIS:
                policy = {"version": version, "changed_at_ms": changed_at_ms,
                          "baseline_version": None, "baseline_snapshot_ms": None}
            else:
                for event_id, raw in db.execute("SELECT id,payload FROM events WHERE kind='joint'"):
                    performance = dict((json.loads(raw).get("performance") or {}))
                    performance.setdefault("basis", current)
                    db.execute("INSERT OR IGNORE INTO performance_versions VALUES (?,?,?,?)",
                               (event_id, current, _json(performance), changed_at_ms))
                policy = {"version": version, "changed_at_ms": changed_at_ms,
                          "baseline_version": current, "baseline_snapshot_ms": changed_at_ms}
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", ("performance_policy", _json(policy)))
            return policy

    def insert(self, events: list[dict]) -> int:
        if not events:
            return 0
        with self.connect() as db:
            before = db.total_changes
            db.executemany("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?)",
                           [(e["id"], e["kind"], e["symbol"], e["timeframe"], e["bar_open_ms"], e["bar_close_ms"],
                             e["detected_at_ms"], _json(e)) for e in events])
            return db.total_changes - before

    def refresh_performance(self, symbol: str, timeframe: str, computed: dict[str, dict]) -> int:
        """Rewrite joint positions from the latest replay; strand no stale open position."""
        changed = 0
        with self.connect() as db:
            policy_row = db.execute("SELECT payload FROM meta WHERE key='performance_policy'").fetchone()
            policy = json.loads(policy_row[0]) if policy_row else None
            rows = db.execute("SELECT id,payload FROM events WHERE kind='joint' AND symbol=? AND timeframe=?",
                              (symbol, timeframe)).fetchall()
            for event_id, payload in rows:
                event = json.loads(payload)
                new = computed.get(event_id)
                if new is None:
                    old = event.get("performance") or {}
                    if old.get("status") != "active":
                        continue
                    new = {**old, "status": "unknown", "reason": "left_analysis_window", "current_r": None}
                old_basis = (event.get("performance") or {}).get("basis", LEGACY_PERFORMANCE_BASIS)
                new_basis = new.get("basis", LEGACY_PERFORMANCE_BASIS)
                if old_basis != new_basis and (not isinstance(policy, dict) or policy.get("version") != new_basis):
                    raise RuntimeError("refusing cross-version projection refresh without active policy")
                if new == event.get("performance"):
                    continue
                event["performance"] = new
                db.execute("UPDATE events SET payload=? WHERE id=?", (_json(event), event_id))
                changed += 1
        return changed

    def load_daily(self) -> dict[str, list[dict]]:
        with self.connect() as db:
            rows = db.execute("SELECT symbol,payload FROM daily").fetchall()
        return {r[0]: json.loads(gzip.decompress(r[1])) for r in rows}

    def save_daily(self, symbol: str, candles: list[dict]) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO daily VALUES (?,?,?)",
                       (symbol, gzip.compress(_json(candles).encode(), mtime=0), now_ms()))


class Primary:
    """Read-only view of the V9 scanner's checkpoints."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=20)

    def stamps(self) -> dict[tuple[str, str], int]:
        db = self._db()
        try:
            rows = db.execute("SELECT symbol,timeframe,updated_ms FROM candle_checkpoints").fetchall()
        finally:
            db.close()
        return {(r[0], r[1]): int(r[2]) for r in rows}

    def candles(self, symbol: str, timeframe: str) -> list[dict] | None:
        db = self._db()
        try:
            row = db.execute("SELECT payload FROM candle_checkpoints WHERE symbol=? AND timeframe=?",
                             (symbol, timeframe)).fetchone()
        finally:
            db.close()
        if row is None:
            return None
        try:
            return json.loads(gzip.decompress(row[0]).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None


def parse_daily(rows: list, server_ms: int) -> list[dict]:
    """OKX 1Dutc rows -> confirmed closed daily candles (t = UTC 00:00 open)."""
    out = {}
    for raw in rows:
        if not isinstance(raw, list) or len(raw) < 9 or str(raw[8]) != "1":
            continue
        stamp = int(raw[0])
        o, h, l, c, v = (float(x) for x in raw[1:6])
        if (stamp % DAY_MS or stamp + DAY_MS > server_ms or not all(math.isfinite(x) for x in (o, h, l, c, v))
                or min(o, h, l, c) <= 0 or h < max(o, l, c) or l > min(o, h, c)):
            continue
        out[stamp] = {"t": stamp, "o": o, "h": h, "l": l, "c": c, "v": v}
    return [out[k] for k in sorted(out)]


class LinesWorker:
    def __init__(self, runtime: Path):
        from yoyo.monitor.okx import OKX
        self.runtime = Path(runtime)
        self.book = LinesBook(self.runtime / DATABASE)
        self.primary = Primary(self.runtime / PRIMARY_DATABASE)
        # The scanner already uses 8 req/s; OKX allows 20/s for candles per IP.
        self.client = OKX(rate=3.0)
        self.instruments: dict[str, dict] = {}
        self.universe_at = 0
        self.daily = self.book.load_daily()
        self.seen: dict[tuple[str, str], tuple] = {}
        self.performance_policy: dict | None = None
        activation = self.book.get_meta("activation")
        if not isinstance(activation, dict) or type(activation.get("activated_ms")) is not int:
            activation = {"activated_ms": now_ms(), "protocol": None}
            self.book.set_meta("activation", activation)
        self.activated_ms = activation["activated_ms"]

    def refresh_universe(self) -> None:
        from yoyo.monitor.v9_worker import instrument_base
        if self.instruments and now_ms() - self.universe_at < 3_600_000:
            return
        rows = self.client.instruments()
        self.instruments = {r["instId"]: {"tick": float(r["tickSz"]), "base": instrument_base(r)} for r in rows}
        self.universe_at = now_ms()

    def daily_candles(self, symbol: str) -> list[dict]:
        """Closed 1Dutc bars, fetched once per new daily close (two pages on a cold start)."""
        have = self.daily.get(symbol) or []
        server = self.client.clock()
        last_closed_open = server // DAY_MS * DAY_MS - DAY_MS
        if have and have[-1]["t"] >= last_closed_open:
            return have
        rows = self.client.get("/api/v5/market/candles", {"instId": symbol, "bar": "1Dutc", "limit": "300"})
        fresh = parse_daily(rows, server)
        if not have and len(rows) == 300:
            oldest = min(int(r[0]) for r in rows)
            more = self.client.get("/api/v5/market/history-candles",
                                   {"instId": symbol, "bar": "1Dutc", "limit": "100", "after": str(oldest)})
            fresh = parse_daily(more, server) + fresh
        merged = {r["t"]: r for r in have}
        merged.update({r["t"]: r for r in fresh})
        candles = [merged[k] for k in sorted(merged)]
        # Keep only the latest contiguous run; a missing day restarts the series.
        start = 0
        for k in range(1, len(candles)):
            if candles[k]["t"] - candles[k - 1]["t"] != DAY_MS:
                start = k
        candles = candles[start:][-1500:]
        self.daily[symbol] = candles
        self.book.save_daily(symbol, candles)
        return candles

    def pass_once(self) -> dict:
        from yoyo.monitor import spike_lines as sl
        started = now_ms()
        self.performance_policy = self.book.initialize_performance_policy(sl.BASIS, changed_at_ms=started)
        self.client.synchronize()
        self.refresh_universe()
        stamps = self.primary.stamps()
        symbols = sorted({s for s, tf in stamps if s in self.instruments})
        scan = {"status": "scanning", "started_ms": started, "finished_ms": None, "symbols": len(symbols),
                "cells": 0, "changed": 0, "inserted": 0, "errors": 0, "error_samples": [], "worker_pid": os.getpid(),
                "activated_ms": self.activated_ms}
        self.book.set_meta("scan", scan)
        for symbol in symbols:
            meta = self.instruments[symbol]
            frames: dict[str, object] = {}

            def frame(tf: str):
                if tf not in frames:
                    if tf == "1Dutc":
                        frames[tf] = sl.frame_of(self.daily_candles(symbol))
                    elif tf == "2H":
                        frames[tf] = sl.complete_buckets(frame("1H"), 60, 120)
                    else:
                        frames[tf] = sl.frame_of(self.primary.candles(symbol, tf) or [])
                return frames[tf]

            for tf in ("15m", "30m", "1H", "4H", "1Dutc"):
                scan["cells"] += 1
                try:
                    higher = sl.HIGHER.get(tf)
                    key = (sl.BASIS,
                           stamps.get((symbol, tf)) if tf in SCANNER_TIMEFRAMES else None,
                           stamps.get((symbol, {"2H": "1H"}.get(higher, higher))) if higher in ("1H", "2H", "4H") else None)
                    if tf in ("4H", "1Dutc"):
                        daily = self.daily_candles(symbol)
                        key += (daily[-1]["t"] if daily else None,)
                    if self.seen.get((symbol, tf)) == key:
                        continue
                    scan["changed"] += 1
                    result = sl.analyze(frame(tf), tf, tick=meta["tick"], asset=meta["base"],
                                        higher=frame(higher) if higher else None,
                                        want_breaks=tf in sl.BREAK_TIMEFRAMES, want_joints=tf in sl.JOINT_TIMEFRAMES)
                    step = sl.MINUTES[tf] * 60_000
                    horizon = self.client.clock() - HISTORY_BARS * step
                    detected = now_ms()
                    rows = []
                    for event in result["breaks"] + result["joints"]:
                        if event["bar_close_ms"] < horizon:
                            continue
                        event.update(symbol=symbol, venue="okx", base_asset=meta["base"], detected_at_ms=detected,
                                     id=sl.event_id(event["kind"], symbol, tf, event["bar_open_ms"]))
                        rows.append(event)
                    scan["inserted"] += self.book.insert(rows)
                    if tf in sl.JOINT_TIMEFRAMES:
                        computed = {sl.event_id("joint", symbol, tf, e["bar_open_ms"]): e["performance"]
                                    for e in result["joints"]}
                        scan["positions_updated"] = scan.get("positions_updated", 0) + \
                            self.book.refresh_performance(symbol, tf, computed)
                    self.seen[(symbol, tf)] = key
                except Exception as exc:  # one bad cell must not stop the pass
                    scan["errors"] += 1
                    if len(scan["error_samples"]) < 8:
                        scan["error_samples"].append({"symbol": symbol, "timeframe": tf,
                                                      "error": f"{type(exc).__name__}: {exc}"[:160]})
            self.book.set_meta("scan", scan)
        scan.update(status="degraded" if scan["errors"] else "idle", finished_ms=now_ms(),
                    duration_seconds=round((now_ms() - started) / 1000, 1))
        self.book.set_meta("scan", scan)
        return scan


def lines_forever(runtime: str, interval_seconds: int = POLL_SECONDS) -> None:
    try:
        os.nice(10)
    except OSError:
        pass
    worker = LinesWorker(Path(runtime))
    while True:
        try:
            worker.pass_once()
        except Exception as exc:  # network or clock failure: record and retry next pass
            worker.book.set_meta("scan", {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:200],
                                          "finished_ms": now_ms(), "activated_ms": worker.activated_ms})
        time.sleep(interval_seconds)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", default=str(Path.home() / "Library/Application Support/Fable/ImpulseMonitor"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.once:
        print(json.dumps(LinesWorker(Path(args.runtime)).pass_once(), ensure_ascii=False))
    else:
        lines_forever(args.runtime)
