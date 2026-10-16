"""Independent read-only market observer and append-only leader snapshots.

The existing scanner remains the only checkpoint writer. This service reads
monitor.sqlite3 with mode=ro, fetches only public instrument metadata hourly,
and writes derived observations into its own SQLite book. It never fetches or
persists new OHLCV, shares the forward pulse, or calls trading/notification code.
HTTP reads a short cached result; the background loop owns all computation.
"""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
import gzip
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time

from fastapi import HTTPException, Query

from yoyo.monitor.altcoin_leaders import (BENCHMARKS, HOUR, VERSION, build_snapshot,
                                         closed_rows, crypto_instrument, detect_episode, digest)
from yoyo.monitor.okx import OKX

LOG = logging.getLogger("fable.monitor.altcoin_leaders")
DATABASE = "altcoin-leaders-v1.sqlite3"


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class LeaderBook:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS snapshots (
                  id INTEGER PRIMARY KEY, benchmark TEXT NOT NULL, asof_ms INTEGER NOT NULL,
                  observed_ms INTEGER NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS snapshot_benchmark ON snapshots(benchmark,id DESC);
                CREATE TABLE IF NOT EXISTS episodes (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=1)

    def metadata(self):
        with closing(self.connect()) as db:
            row = db.execute("SELECT payload FROM metadata WHERE key='instruments'").fetchone()
        return json.loads(row[0]) if row else None

    def save_metadata(self, value):
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('instruments',?)", (encode(value),))

    def episode(self, identity):
        with closing(self.connect()) as db:
            row = db.execute("SELECT payload FROM episodes WHERE id=?", (identity,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, value):
        # Arrival timestamps/provenance are retained on the first identical
        # observation; merely polling unchanged inputs must not manufacture data.
        identity = {k: v for k, v in value.items() if k not in ("observed_ms", "source_received_ms", "metadata_observed_ms")}
        fingerprint = digest(identity)
        with closing(self.connect()) as db, db:
            latest = db.execute("SELECT id,payload,fingerprint FROM snapshots WHERE benchmark=? ORDER BY id DESC LIMIT 1",
                                (value["benchmark"],)).fetchone()
            if latest and latest[2] == fingerprint:
                return dict(json.loads(latest[1]), snapshot_id=latest[0])
            if value.get("episode"):
                ep = value["episode"]
                db.execute("INSERT OR IGNORE INTO episodes VALUES (?,?)", (ep["id"], encode(ep)))
            cursor = db.execute("INSERT INTO snapshots(benchmark,asof_ms,observed_ms,fingerprint,payload) VALUES (?,?,?,?,?)",
                                (value["benchmark"], value["asof_ms"], value["observed_ms"], fingerprint, encode(value)))
            identity = cursor.lastrowid
        return dict(value, snapshot_id=identity)

    def latest(self, benchmark):
        with closing(self.connect()) as db:
            row = db.execute("SELECT id,payload FROM snapshots WHERE benchmark=? ORDER BY id DESC LIMIT 1", (benchmark,)).fetchone()
        return dict(json.loads(row[1]), snapshot_id=row[0]) if row else None

    def history(self, benchmark, limit=24):
        with closing(self.connect()) as db:
            rows = db.execute("SELECT id,payload FROM snapshots WHERE benchmark=? ORDER BY id DESC LIMIT ?",
                              (benchmark, limit)).fetchall()
        return [dict(snapshot_id=i, asof_ms=v["asof_ms"], observed_ms=v["observed_ms"],
                     episode_id=(v.get("episode") or {}).get("id"), phase=(v.get("episode") or {}).get("phase", "waiting"),
                     coverage=v["coverage"], leaders=[r["symbol"] for r in v["items"] if r.get("tier") == 1])
                for i, raw in rows for v in [json.loads(raw)]]

    def get(self, identity):
        with closing(self.connect()) as db:
            row = db.execute("SELECT payload FROM snapshots WHERE id=?", (identity,)).fetchone()
        return dict(json.loads(row[0]), snapshot_id=identity) if row else None


def read_checkpoints(path, symbols, now):
    """One SQLite read transaction; never initialize or repair the scanner DB."""
    result, received = {}, {}
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        rows = db.execute("SELECT symbol,payload,updated_ms FROM candle_checkpoints "
                          "WHERE timeframe='1H' AND symbol IN (SELECT value FROM json_each(?))", (encode(sorted(symbols)),)).fetchall()
    for symbol, payload, updated_ms in rows:
        if type(updated_ms) is not int or updated_ms > now:
            continue
        try:
            # Checkpoints normally contain a few thousand bars. A malformed
            # compression payload must not allocate unbounded memory.
            import io
            with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
                data = stream.read(32 * 1024 * 1024 + 1)
            if len(data) > 32 * 1024 * 1024:
                continue
            result[symbol] = json.loads(data)
            received[symbol] = updated_ms
        except (OSError, EOFError, ValueError, TypeError):
            continue
    return result, received


class LeaderService:
    def __init__(self, runtime, client=None, interval=60):
        self.runtime = Path(runtime)
        self.book = LeaderBook(self.runtime / DATABASE)
        self.client = client or OKX(rate=1)
        self.interval = interval
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.metadata = self.book.metadata()
        self.values = {b: self.book.latest(b) for b in BENCHMARKS}
        self.health = {"state": "starting", "last_checked_ms": None, "error": None}

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.run, name="altcoin-leaders", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)

    def run(self):
        while not self.stop.is_set():
            try:
                self.cycle()
            except Exception as exc:
                LOG.warning("leader observer unavailable: %s", type(exc).__name__)
                with self.lock:
                    self.health = dict(state="error", error=str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__,
                                       last_checked_ms=self.client.clock())
            self.stop.wait(self.interval)

    def cycle(self):
        now = self.client.clock()
        if not self.metadata or not 0 <= now - self.metadata["observed_ms"] < HOUR:
            self.client.synchronize()
            instruments = self.client.instruments()
            selected = [r for r in instruments if crypto_instrument(r)]
            if not all(any(r["instId"] == b for r in selected) for b in BENCHMARKS):
                raise ValueError("benchmark_classification_unavailable")
            self.metadata = dict(observed_ms=self.client.clock(), instruments=selected,
                                 source="https://www.okx.com/api/v5/public/instruments?instType=SWAP")
            self.book.save_metadata(self.metadata)
        now = self.client.clock()
        symbols = {r["instId"] for r in self.metadata["instruments"]}
        raw, received = read_checkpoints(self.runtime / "monitor.sqlite3", symbols, now)
        values, errors = {}, {}
        for benchmark in BENCHMARKS:
            try:
                b = closed_rows(raw.get(benchmark), now)
                if not b:
                    raise ValueError("benchmark_candles_unavailable")
                asof = b[-1]["t"] + HOUR
                ep = detect_episode(b, benchmark)
                frozen = self.book.episode(ep["id"]) if ep else None
                value = build_snapshot(raw, symbols, benchmark, asof, now, frozen)
                value.update(source_received_ms={s: received[s] for s in received},
                             metadata_observed_ms=self.metadata["observed_ms"], source_kind="existing_okx_confirmed_checkpoints")
                values[benchmark] = self.book.save(value)
            except ValueError as exc:
                errors[benchmark] = str(exc)
        with self.lock:
            self.values.update(values)
            self.health = dict(state="partial" if errors and values else "error" if errors else "ok",
                               error=None, benchmark_errors=errors, last_checked_ms=self.client.clock())

    def snapshot(self, benchmark):
        with self.lock:
            value = deepcopy(self.values.get(benchmark))
            health = dict(self.health)
        now = self.client.clock()
        unavailable = (health["state"] in ("starting", "error")
                       or benchmark in health.get("benchmark_errors", {})
                       or health["last_checked_ms"] is None
                       or not 0 <= now - health["last_checked_ms"] <= 3 * self.interval * 1000)
        stale = not value or value["asof_ms"] < now // HOUR * HOUR or value["asof_ms"] > now
        return dict(value or {"items": [], "episode": None, "coverage": {"total": 0, "comparable": 0, "candidates": 0},
                              "benchmark": benchmark, "rule_version": VERSION},
                    health=health, now_ms=now, stale=stale, ranking_active=not (stale or unavailable),
                    orders_enabled=False, execution_ready=False, trading_validated=False)


def install(app, service):
    def benchmark_symbol(benchmark):
        if benchmark not in ("ETH", "BTC"):
            raise HTTPException(400, "请选择 ETH 或 BTC 基准。")
        return benchmark + "-USDT-SWAP"

    @app.get("/api/altcoin-leaders")
    def current(benchmark: str = "ETH"):
        return service.snapshot(benchmark_symbol(benchmark))

    @app.get("/api/altcoin-leaders/history")
    def history(benchmark: str = "ETH", limit: int = Query(24, ge=1, le=100)):
        return {"items": service.book.history(benchmark_symbol(benchmark), limit)}

    @app.get("/api/altcoin-leaders/snapshots/{identity}")
    def detail(identity: int):
        result = service.book.get(identity)
        if result is None:
            raise HTTPException(404, "找不到该观察快照。")
        return dict(result, historical_snapshot=True, ranking_active=False, orders_enabled=False)
