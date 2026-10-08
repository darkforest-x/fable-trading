"""Live observer for market-wide sync shocks (owner 2026-10-08: "给这个单独做个菜单吧").

Records what exp-market-sync-shock-20261008-v2 would flag on fresh Binance USDT-M bars and
how ETH, BTC and the alt basket moved afterwards. It never places orders and never sends
notifications; the workbench 全市场异动 menu reads its snapshot.

Definitions are v2's, re-implemented without numpy/pandas so the API import path stays light
(tests/monitor/test_api_startup_imports.py). tests/monitor/test_market_sync.py checks them
against yoyo.evaluation.market_sync_shock(_v2) on the same bars.

* bars: closed Binance klines, 30m and 1h, indexed on BTC's bars (a missing BTC bar is
  skipped, as the research resample keeps complete buckets only).
* z = (close/open - 1) / RMS of the previous 96 returns; volume ratio = volume / median of
  the previous 96 volumes; both need 96 finite previous bars.
* breadth: share of the month's top-50 alts whose bar moved the same way, from >= 10 valid
  alts. The month's alts rank on the previous calendar month's median daily quote volume,
  leaders and stablecoins excluded.
* hit: BTC and ETH on the same side, both |z| >= z_min and volume ratio >= v_min, breadth >=
  b_min. ``sync`` fires on a hit; ``cluster`` also needs a same-side hit in the two bars
  before. After an event nothing fires for 24h (per timeframe and config, either side).
* outcome: entry at the next bar's open, exit at the close h hours later, signed by the
  shock side, minus the 0.2% round trip. The alt basket is the equal-weight mean of per-alt
  returns over the event month's universe.

Two known differences from the frozen study: the universe ranks on the kline quote volume
(the study summed close x volume of 5m bars) and only lists contracts trading today, so a
delisted alt cannot enter. Events before RESEARCH_END overlap the study and are a
reconciliation check, not new evidence; events from RESEARCH_END on are out of sample.

Environment tags (owner 2026-10-08: "接进菜单"): each event also carries BTC's 30-day and 7-day
return and 30-day hourly volatility, read at the last 1H close at or before the event bar opens
(exp-market-sync-shock-20261008-v4), and two veto labels from v4's frozen top-tercile edges:
an up shock after a strong 30-day BTC rally, and a down shock in high volatility. They label
events for forward checking; nothing is filtered out.

Binance USDT-M market data: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api
"""
from __future__ import annotations

import calendar
import gzip
import json
import logging
import math
import sqlite3
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

LOG = logging.getLogger("fable.monitor.market_sync")
ROOT = Path(__file__).resolve().parents[2]
HISTORY = ROOT / "experiments/active/exp-market-sync-shock-20261008-v2/results_v2/menu_history.json.gz"
REGISTRY = ROOT / "experiments/registry.yaml"
PROGRAM_PREFIX = "exp-market-sync-shock-"
BINANCE = "https://fapi.binance.com"
ALLOWED_PATHS = {"/fapi/v1/klines", "/fapi/v1/exchangeInfo", "/fapi/v1/time"}
DATABASE = "market-sync-v1.sqlite3"
LEADERS = ("BTCUSDT", "ETHUSDT")
STABLE = {"USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "USDPUSDT", "DAIUSDT", "EURUSDT", "AEURUSDT",
          "USDEUSDT", "XUSDUSDT", "BFUSDUSDT", "USD1USDT", "RLUSDUSDT", "PAXGUSDT", "XAUTUSDT"}
UNIVERSE_SIZE = 50
MIN_ALTS = 10
LOOKBACK = 96
INTERVALS = {30: "30m", 60: "1h"}
GRID = tuple({"z_min": z, "v_min": v, "b_min": b} for z in (2.0, 3.0) for v in (2.0, 3.0) for b in (0.6, 0.75))
KINDS = ("sync", "cluster")
COOLDOWN_HOURS = 24
HOLD_HOURS = (1, 4, 12, 24)
INSTRUMENTS = ("eth", "btc", "alts")
COST = 0.002
PRIMARY = {"minutes": 60, "kind": "sync", "config": "z3.0_v3.0_b0.75", "side": 1, "hold": "12h", "instrument": "eth"}
OBSERVE_START_MS = 1785542400000   # 2026-08-01T00:00Z: events from here (covers 8.19 for reconciliation)
BACKFILL_START_MS = 1785110400000  # 2026-07-27T00:00Z: 5 days of warmup before OBSERVE_START
HOUR_MS = 3_600_000
ENV_START_MS = BACKFILL_START_MS - 31 * 24 * HOUR_MS  # BTC 1h history for the 30-day environment
# v4 top-tercile edges frozen on pre-2025 events (exp-market-sync-shock-20261008-v4 edges.csv).
VETO_EDGES = {"rally": ("trend30", 1, {30: 0.12699048292180384, 60: 0.13114983449061102}),
              "high_vol": ("vol30", -1, {30: 0.005424279086604813, 60: 0.00546888910589197})}
RESEARCH_END_MS = 1790121600000    # 2026-09-23T00:00Z: v2 config "end"; later events are out of sample
LATEST_BARS = 24
PAGE = 1000                        # klines limit 1000 -> request weight 5
WORKERS = 6                        # requests overlap network latency; the rate limit stays global


class MarketSyncError(RuntimeError):
    pass


def config_key(cfg: dict) -> str:
    """Same label as the study: z3.0_v3.0_b0.75."""
    return f"z{float(cfg['z_min'])}_v{float(cfg['v_min'])}_b{float(cfg['b_min'])}"


def month_of(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m")


def month_bounds(month: str) -> tuple[int, int]:
    year, mon = map(int, month.split("-"))
    start = calendar.timegm((year, mon, 1, 0, 0, 0))
    end = calendar.timegm((year + (mon == 12), mon % 12 + 1, 1, 0, 0, 0))
    return start * 1000, end * 1000


def previous_month(month: str) -> str:
    year, mon = map(int, month.split("-"))
    return f"{year - (mon == 1)}-{(mon - 2) % 12 + 1:02d}"


def finite(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def sign(value: float) -> int:
    return (value > 0) - (value < 0) if finite(value) else 0


# --------------------------------------------------------------------------- features

def leader_features(rows: list[tuple[float, float, float] | None], lookback: int = LOOKBACK) -> list[dict]:
    """rows[t] = (open, close, volume) on the BTC index, or None when missing.

    Uses bars < t for the scale (study: ``causal_features``, shift(1) then rolling).
    """
    rets = [(r[1] / r[0] - 1.0) if r and r[0] > 0 and r[1] > 0 else math.nan for r in rows]
    vols = [r[2] if r and finite(r[2]) and r[2] >= 0 else math.nan for r in rows]
    out = []
    for t, ret in enumerate(rets):
        z = vr = rms = math.nan
        if t >= lookback:
            prev = rets[t - lookback:t]
            pvol = vols[t - lookback:t]
            if all(finite(x) for x in prev):
                rms = math.sqrt(sum(x * x for x in prev) / lookback)
                z = ret / rms if rms > 0 and finite(ret) else math.nan
            if all(finite(x) for x in pvol):
                med = statistics.median(pvol)
                vr = vols[t] / med if med > 0 and finite(vols[t]) else math.nan
        out.append({"ret": ret, "z": z, "vr": vr, "rms": rms})
    return out


def breadth(index: list[int], alt_rows: dict[str, list], universe: dict[str, list[str]]) -> tuple[list, list, list]:
    """Up share, down share and valid count per bar over that bar's month universe."""
    up, down, count = [], [], []
    for t, ms in enumerate(index):
        members = [alt_rows[s] for s in universe.get(month_of(ms), []) if s in alt_rows]
        n = u = d = 0
        for rows in members:
            row = rows[t]
            if not row or not (row[0] > 0 and row[1] > 0):
                continue
            ret = row[1] / row[0] - 1.0
            n += 1
            u += ret > 0
            d += ret < 0
        ok = n >= MIN_ALTS
        up.append(u / n if ok else math.nan)
        down.append(d / n if ok else math.nan)
        count.append(n)
    return up, down, count


def sync_hits(btc: list[dict], eth: list[dict], up: list[float], down: list[float], *, z_min: float,
              v_min: float, b_min: float) -> list[int]:
    """Per bar +1/-1 where the study's sync condition holds, else 0 (``sync_hits``)."""
    hits = []
    for b, e, bu, bd in zip(btc, eth, up, down):
        side = sign(b["ret"])
        wide = bu if side > 0 else bd
        ok = (side != 0 and side == sign(e["ret"])
              and finite(b["z"]) and finite(e["z"]) and abs(b["z"]) >= z_min and abs(e["z"]) >= z_min
              and finite(b["vr"]) and finite(e["vr"]) and b["vr"] >= v_min and e["vr"] >= v_min
              and finite(wide) and wide >= b_min)
        hits.append(side if ok else 0)
    return hits


def events_from_hits(hits: list[int], kind: str, cooldown: int) -> list[tuple[int, int]]:
    """(bar, side) per event with the study's cooldown (``events_from_hits``)."""
    if kind not in KINDS:
        raise ValueError(kind)
    rows, block_until = [], -1
    for i, h in enumerate(hits):
        if h == 0:
            continue
        if kind == "cluster" and not ((i >= 1 and hits[i - 1] == h) or (i >= 2 and hits[i - 2] == h)):
            continue
        if i <= block_until:
            continue
        rows.append((i, h))
        block_until = i + cooldown
    return rows


def leg(rows: list, a: int, b: int) -> float:
    """Gross long return from open[a] to close[b]; NaN when either end is missing."""
    if a >= len(rows) or b >= len(rows) or not rows[a] or not rows[b]:
        return math.nan
    o, c = rows[a][0], rows[b][1]
    return c / o - 1.0 if o > 0 and c > 0 else math.nan


def outcome(series: dict, members: list[str], i: int, side: int, bars: int) -> dict:
    """Net (bp) per instrument for a hold of ``bars``; marks to the last bar while running."""
    n = len(series["BTCUSDT"])
    a, b = i + 1, i + bars
    if a >= n:
        return {"done": False, "started": False, **{k: None for k in INSTRUMENTS}}
    done = b < n
    end = b if done else n - 1
    out: dict = {"done": done, "started": True}
    for name, symbol in (("eth", "ETHUSDT"), ("btc", "BTCUSDT")):
        g = leg(series[symbol], a, end)
        out[name] = round((side * g - COST) * 1e4, 1) if finite(g) else None
    legs = [leg(series[s], a, end) for s in members if s in series]
    legs = [g for g in legs if finite(g)]
    out["alts"] = round((side * sum(legs) / len(legs) - COST) * 1e4, 1) if legs else None
    return out


def environment(closes: dict[int, float], asof_ms: int) -> dict:
    """BTC trend and volatility at the 1H close ``asof_ms`` (closes keyed by close time).

    Same definitions as yoyo.evaluation.market_sync_shock_v4.btc_hourly: wall-clock hours,
    a missing hour stays missing, volatility needs 700 of the 720 hourly log returns.
    """
    def ret(hours):
        a, b = closes.get(asof_ms), closes.get(asof_ms - hours * HOUR_MS)
        return a / b - 1.0 if a and b else math.nan
    logs = []
    for k in range(720):
        a, b = closes.get(asof_ms - k * HOUR_MS), closes.get(asof_ms - (k + 1) * HOUR_MS)
        if a and b:
            logs.append(math.log(a / b))
    return {"asof_ms": asof_ms, "trend30": ret(720), "trend7": ret(168),
            "vol30": statistics.stdev(logs) if len(logs) >= 700 else math.nan}


def vetoes(minutes: int, side: int, env: dict) -> list[str]:
    """v4 veto labels: ``rally`` (up shock after a top-tercile 30-day rally), ``high_vol``."""
    tags = []
    for tag, (feature, for_side, edges) in VETO_EDGES.items():
        value = env.get(feature)
        if side == for_side and finite(value) and value >= edges[minutes]:
            tags.append(tag)
    return tags


def analyse(index: list[int], series: dict[str, list], universe: dict[str, list[str]], minutes: int,
            *, observe_start_ms: int = OBSERVE_START_MS, btc_closes: dict[int, float] | None = None) -> dict:
    """Features, events and outcomes for one timeframe; all inputs on the BTC index."""
    per_hour = 60 // minutes
    btc = leader_features(series["BTCUSDT"])
    eth = leader_features(series["ETHUSDT"])
    alt_rows = {s: rows for s, rows in series.items() if s not in LEADERS}
    up, down, count = breadth(index, alt_rows, universe)
    events = []
    for cfg in GRID:
        hits = sync_hits(btc, eth, up, down, **cfg)
        for kind in KINDS:
            for i, side in events_from_hits(hits, kind, COOLDOWN_HOURS * per_hour):
                if index[i] < observe_start_ms:
                    continue
                members = universe.get(month_of(index[i]), [])
                env = environment(btc_closes or {}, index[i] // HOUR_MS * HOUR_MS)
                events.append({
                    "minutes": minutes, "kind": kind, "config": config_key(cfg), "open_ms": index[i],
                    "close_ms": index[i] + minutes * 60_000, "side": side,
                    "btc_ret": btc[i]["ret"], "eth_ret": eth[i]["ret"], "btc_z": btc[i]["z"], "eth_z": eth[i]["z"],
                    "btc_vr": btc[i]["vr"], "eth_vr": eth[i]["vr"], "breadth": up[i] if side > 0 else down[i],
                    "n_alts": count[i], "out_of_sample": index[i] >= RESEARCH_END_MS,
                    "outcomes": {f"{h}h": outcome(series, members, i, side, h * per_hour) for h in HOLD_HOURS},
                    "env": env, "vetoes": vetoes(minutes, side, env),
                })
    latest = []
    for t in range(max(0, len(index) - LATEST_BARS), len(index)):
        latest.append({"open_ms": index[t], "close_ms": index[t] + minutes * 60_000,
                       "btc_ret": btc[t]["ret"], "btc_z": btc[t]["z"], "btc_vr": btc[t]["vr"],
                       "eth_ret": eth[t]["ret"], "eth_z": eth[t]["z"], "eth_vr": eth[t]["vr"],
                       "breadth_up": up[t], "breadth_down": down[t], "n_alts": count[t]})
    return {"events": events, "latest": latest, "bars": len(index)}


def clean(value):
    """JSON without NaN/inf: non-finite floats become null."""
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


# --------------------------------------------------------------------------- exchange

class Binance:
    """Public, throttled GETs to a fixed allowlist (klines/exchangeInfo/time)."""

    def __init__(self, rate: float = 4.0):
        self.rate = rate
        self._lock = threading.Lock()
        self._local = threading.local()
        self._next = 0.0
        self.requests = 0

    def _session(self):
        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()
            self._local.session.headers["User-Agent"] = "Fable-MarketSync/1.0"
        return self._local.session

    def get(self, path: str, params: dict | None = None):
        if path not in ALLOWED_PATHS:
            raise MarketSyncError("path_not_allowed")
        for attempt in range(4):
            with self._lock:
                wait = self._next - time.monotonic()
                self._next = max(self._next, time.monotonic()) + 1 / self.rate
                self.requests += 1
            if wait > 0:
                time.sleep(wait)
            try:
                response = self._session().get(BINANCE + path, params=params, timeout=(5, 15))
                if response.status_code in (418, 429):
                    time.sleep(int(response.headers.get("Retry-After", 10 * (attempt + 1))))
                    continue
                if response.status_code != 200:
                    raise MarketSyncError(f"binance_http_{response.status_code}")
                return response.json()
            except (requests.RequestException, ValueError):
                if attempt == 3:
                    raise MarketSyncError("binance_network_or_json_error") from None
                time.sleep(attempt + 1)
        raise MarketSyncError("binance_rate_limited")

    def server_ms(self) -> int:
        return int(self.get("/fapi/v1/time")["serverTime"])

    def coins(self) -> list[str]:
        info = self.get("/fapi/v1/exchangeInfo")
        return sorted(s["symbol"] for s in info["symbols"]
                      if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT"
                      and s.get("status") == "TRADING" and s.get("underlyingType", "COIN") == "COIN")

    def klines(self, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = PAGE) -> list[list]:
        return self.get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "startTime": start_ms,
                                            "endTime": end_ms, "limit": limit})


# --------------------------------------------------------------------------- storage

class Book:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS bars(minutes INTEGER NOT NULL, symbol TEXT NOT NULL, open_ms INTEGER NOT NULL,
                  open REAL, high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY(minutes, symbol, open_ms));
                CREATE TABLE IF NOT EXISTS universe(month TEXT PRIMARY KEY, symbols TEXT NOT NULL,
                  ranked_from TEXT NOT NULL, candidates INTEGER NOT NULL, computed_ms INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def first_open(self, minutes: int, symbol: str) -> int | None:
        with self.connect() as db:
            row = db.execute("SELECT MIN(open_ms) FROM bars WHERE minutes=? AND symbol=?", (minutes, symbol)).fetchone()
        return row[0]

    def last_open(self, minutes: int, symbol: str) -> int | None:
        with self.connect() as db:
            row = db.execute("SELECT MAX(open_ms) FROM bars WHERE minutes=? AND symbol=?", (minutes, symbol)).fetchone()
        return row[0]

    def put_bars(self, minutes: int, symbol: str, rows: list[tuple]) -> None:
        with self.connect() as db:
            db.executemany("INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?,?)",
                           [(minutes, symbol, *r) for r in rows])

    def bars(self, minutes: int, since_ms: int) -> dict[str, dict[int, tuple]]:
        out: dict[str, dict[int, tuple]] = {}
        with self.connect() as db:
            for symbol, ms, o, c, v in db.execute(
                    "SELECT symbol, open_ms, open, close, volume FROM bars WHERE minutes=? AND open_ms>=?",
                    (minutes, since_ms)):
                out.setdefault(symbol, {})[ms] = (o, c, v)
        return out

    def universes(self) -> dict[str, dict]:
        with self.connect() as db:
            rows = db.execute("SELECT month, symbols, ranked_from, candidates, computed_ms FROM universe").fetchall()
        return {m: {"symbols": json.loads(s), "ranked_from": r, "candidates": c, "computed_ms": t}
                for m, s, r, c, t in rows}

    def put_universe(self, month: str, symbols: list[str], ranked_from: str, candidates: int) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO universe VALUES(?,?,?,?,?)",
                       (month, json.dumps(symbols), ranked_from, candidates, int(time.time() * 1000)))

    def get_meta(self, key: str):
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put_meta(self, key: str, value) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value, allow_nan=False)))


# --------------------------------------------------------------------------- observer

def rank_month(daily: dict[str, list[list]], month: str, size: int = UNIVERSE_SIZE) -> list[str]:
    """Top alts for ``month`` by median daily quote volume over the previous month."""
    start, end = month_bounds(previous_month(month))
    medians = {}
    for symbol, rows in daily.items():
        if symbol in LEADERS or symbol in STABLE:
            continue
        values = [float(k[7]) for k in rows if start <= int(k[0]) < end]
        if values:
            medians[symbol] = statistics.median(values)
    return [s for s, _ in sorted(medians.items(), key=lambda kv: (-kv[1], kv[0]))[:size]]


class Observer:
    """Background thread: refresh universes and bars after each 30m close, then analyse."""

    def __init__(self, runtime: Path, client: Binance | None = None):
        self.book = Book(Path(runtime) / DATABASE)
        self.client = client or Binance()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._snapshot = self.book.get_meta("snapshot")
        self.state = {"state": "starting", "error": None, "updated_ms": None, "next_ms": None}
        self.skipped: dict[str, str] = {}

    # -- lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="market-sync", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.cycle()
                delay = self._until_next_close()
            except Exception as error:  # keep observing; surface the error on the page
                LOG.warning("market sync cycle failed: %s", error)
                self.state.update(state="error", error=str(error)[:200])
                delay = 120
            self.state["next_ms"] = int((time.time() + delay) * 1000)
            self._stop.wait(delay)

    @staticmethod
    def _until_next_close(now: float | None = None) -> float:
        now = time.time() if now is None else now
        return 1800 - now % 1800 + 20

    # -- one refresh
    def cycle(self) -> dict:
        self.state.update(state="updating")
        self.skipped = {}
        server = self.client.server_ms()
        universes = self.ensure_universes(server)
        symbols = set(LEADERS)
        for month, entry in universes.items():
            if month_bounds(month)[1] > BACKFILL_START_MS:
                symbols.update(entry["symbols"])
        jobs = [(s, m, i) for m, i in INTERVALS.items() for s in sorted(symbols)]
        with ThreadPoolExecutor(WORKERS) as pool:
            list(pool.map(lambda job: self.fetch_bars(*job, server), jobs))
        first = self.book.first_open(60, "BTCUSDT")
        if first is not None and first > ENV_START_MS:  # one-off: 30 days of BTC 1h before the backfill
            self.fetch_bars("BTCUSDT", 60, "1h", first, start=ENV_START_MS)
        snapshot = self.build({m: e["symbols"] for m, e in universes.items()}, universes)
        snapshot["requests"] = self.client.requests
        snapshot["skipped"] = dict(sorted(self.skipped.items())[:20])
        with self._lock:
            self._snapshot = snapshot
        self.book.put_meta("snapshot", snapshot)
        self.state.update(state="ok", error=None, updated_ms=int(time.time() * 1000))
        return snapshot

    def ensure_universes(self, server_ms: int) -> dict[str, dict]:
        have = self.book.universes()
        months, ms = [], month_bounds(month_of(BACKFILL_START_MS))[0]
        while ms <= server_ms:
            months.append(month_of(ms))
            ms = month_bounds(month_of(ms))[1]
        missing = [m for m in months if m not in have]
        if missing:
            coins = self.client.coins()
            start = month_bounds(previous_month(missing[0]))[0]
            end = month_bounds(previous_month(missing[-1]))[1] - 1
            def fetch(symbol):
                try:
                    return symbol, self.client.klines(symbol, "1d", start, end, limit=500)
                except MarketSyncError as error:  # one bad contract must not block the ranking
                    self.skipped[symbol] = str(error)
                    return symbol, None
            wanted = [s for s in coins if s not in LEADERS and s not in STABLE]
            with ThreadPoolExecutor(WORKERS) as pool:
                daily = {s: rows for s, rows in pool.map(fetch, wanted) if rows is not None}
            for month in missing:
                ranked = rank_month(daily, month)
                if len(ranked) >= MIN_ALTS:
                    self.book.put_universe(month, ranked, previous_month(month), len(daily))
            have = self.book.universes()
        return have

    def fetch_bars(self, symbol: str, minutes: int, interval: str, server_ms: int,
                   start: int | None = None) -> int:
        step = minutes * 60_000
        if start is None:
            last = self.book.last_open(minutes, symbol)
            start = BACKFILL_START_MS if last is None else last + step
        added = 0
        while start + step <= server_ms:
            try:
                rows = self.client.klines(symbol, interval, start, server_ms, limit=PAGE)
            except MarketSyncError as error:  # keep the other contracts current
                self.skipped[symbol] = str(error)
                break
            closed = [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
                      for k in rows if int(k[6]) < server_ms]
            if closed:
                self.book.put_bars(minutes, symbol, closed)
                added += len(closed)
            if len(rows) < PAGE or not closed:
                break
            start = closed[-1][0] + step
        return added

    def build(self, universe: dict[str, list[str]], universes: dict[str, dict]):
        btc_1h = self.book.bars(60, ENV_START_MS).get("BTCUSDT", {})
        btc_closes = {ms + HOUR_MS: row[1] for ms, row in btc_1h.items() if row and row[1] and row[1] > 0}
        frames = {}
        for minutes in INTERVALS:
            raw = self.book.bars(minutes, BACKFILL_START_MS)
            index = sorted(raw.get("BTCUSDT", {}))
            series = {s: [rows.get(ms) for ms in index] for s, rows in raw.items()}
            if not index or "ETHUSDT" not in series:
                continue
            frames[str(minutes)] = analyse(index, series, universe, minutes, btc_closes=btc_closes)
        env_now = environment(btc_closes, max(btc_closes)) if btc_closes else {}
        events = [e for f in frames.values() for e in f["events"]]
        return clean({
            "generated_ms": int(time.time() * 1000),
            "observe_start_ms": OBSERVE_START_MS,
            "research_end_ms": RESEARCH_END_MS,
            "primary": PRIMARY,
            "grid": [config_key(c) for c in GRID],
            "kinds": list(KINDS),
            "holds": [f"{h}h" for h in HOLD_HOURS],
            "cost_bp": COST * 1e4,
            "universe": {m: {"size": len(e["symbols"]), "ranked_from": e["ranked_from"], "symbols": e["symbols"]}
                         for m, e in sorted(universes.items())},
            "latest": {m: f["latest"] for m, f in frames.items()},
            "environment_now": env_now,
            "veto_edges": {tag: {"feature": f, "side": sd, "edges": {str(k): v for k, v in e.items()}}
                           for tag, (f, sd, e) in VETO_EDGES.items()},
            "bars": {m: f["bars"] for m, f in frames.items()},
            "events": sorted(events, key=lambda e: (e["open_ms"], e["minutes"])),
        })

    def snapshot(self) -> dict:
        with self._lock:
            snap = self._snapshot
        return {"status": dict(self.state), "snapshot": snap}


# --------------------------------------------------------------------------- research side

_files: dict[str, tuple[float, object]] = {}


def _cached(path: Path, load):
    stamp = path.stat().st_mtime
    hit = _files.get(str(path))
    if hit is None or hit[0] != stamp:
        hit = (stamp, load(path))
        _files[str(path)] = hit
    return hit[1]


def history(group: str | None = None, path: Path = HISTORY) -> dict:
    """Research events exported by yoyo/evaluation/market_sync_menu_export.py.

    ``group`` is "minutes|kind|config"; without it only the metadata and group keys return.
    """
    if not path.exists():
        raise FileNotFoundError("market_sync_history_missing")
    payload = _cached(path, lambda p: json.loads(gzip.decompress(p.read_bytes())))
    meta = {k: v for k, v in payload.items() if k != "groups"}
    meta["group_keys"] = sorted(payload["groups"])
    if group is None:
        return meta
    if group not in payload["groups"]:
        raise KeyError(group)
    return {**meta, "group": group, "rows": payload["groups"][group]}


def iterations(path: Path = REGISTRY) -> list[dict]:
    """Registry rows of the sync-shock programme, oldest first."""
    def load(p: Path):
        import yaml  # deferred: only the menu needs the registry
        loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
        return yaml.load(p.read_text(), Loader=loader)

    rows = _cached(path, load).get("experiments", [])
    keep = ("experiment_id", "status", "source_commit", "question", "single_variable", "result", "notes")
    return [{k: row.get(k) for k in keep} for row in rows
            if str(row.get("experiment_id", "")).startswith(PROGRAM_PREFIX)]
