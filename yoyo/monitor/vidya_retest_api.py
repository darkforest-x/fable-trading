"""Read-only API and process supervisor for the VIDYA trend-line retest paper book.

The HTTP side only reads ``vidya_retest.sqlite3``; the isolated worker process
(``vidya_retest_worker.run_forever``) is its only writer. Importing this module must
not import pandas/numpy (tests/monitor/test_api_startup_imports.py), so strategy
metadata is mirrored here and pinned to ``vidya_retest.STRATEGIES`` by a test.
No route places orders, sends notifications or changes strategy parameters.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import multiprocessing
from pathlib import Path
import sqlite3
import threading
import time
from typing import Optional

from fastapi import HTTPException, Query

from yoyo.monitor.vidya_retest_worker import DATABASE, run_forever

LOG = logging.getLogger("fable.vidya_retest.api")
ROUND_TRIP_COST = 0.002
RISK_FRACTION = 0.01
STRATEGY_META = {
    "tl-2r": {"label": "趋势线回踩 · 2R", "length": 10, "delta_threshold": 50.0, "k_atr": 1.0, "rr": 2.0,
              "exit_mode": "fixed", "tol_atr": 0.0},
    "tl-trend": {"label": "趋势线回踩 · 趋势离场", "length": 10, "delta_threshold": 50.0, "k_atr": 1.0, "rr": 2.0,
                 "exit_mode": "trend", "tol_atr": 0.2},
}
SOURCES = ("live", "warmup")
EXPERIMENT_DIR = Path(__file__).resolve().parents[2] / "experiments/active/exp-vidya-trendline-retest-20261012-v1"
_backtest_cache = {}


HISTORY_DIR = Path(__file__).resolve().parents[2] / "experiments/active/exp-vidya-report-nine-configs-20261011-v1"
CANDLE_DIR = Path(__file__).resolve().parents[2] / "data/vidya_retest"
_history_cache = {}
_candle_cache = {}


def load_history(directory: Optional[Path] = None) -> dict:
    """The report's nine configurations (history_configs.json + history_trades.json)."""
    directory = Path(directory or HISTORY_DIR)
    files = [directory / "history_configs.json", directory / "history_trades.json"]
    stamp = tuple(f.stat().st_mtime_ns for f in files)
    if _history_cache.get("key") != (str(directory), stamp):
        configs = json.loads(files[0].read_text())
        _history_cache.update(key=(str(directory), stamp), value={
            "experiment_id": configs["meta"]["experiment_id"], "meta": configs["meta"], "configs": configs["configs"],
            "trades": json.loads(files[1].read_text())})
    return _history_cache["value"]


def candle_window(tf: str, length: int, start_ms: int, end_ms: int, max_bars: int, directory: Optional[Path] = None) -> dict:
    """Candles in [start_ms, end_ms) with the trend line for ``length``, merged into <= max_bars buckets.

    numpy is imported here, on the first chart request, so the API listener still starts
    without it (tests/monitor/test_api_startup_imports.py). Buckets are aligned to absolute
    bar indices so panning does not reshuffle them.
    """
    import numpy as np

    path = Path(directory or CANDLE_DIR) / f"candles_ETH_{tf}.npz"
    stamp = path.stat().st_mtime_ns
    cached = _candle_cache.get(str(path))
    if cached is None or cached[0] != stamp:
        with np.load(path) as z:
            cached = (stamp, {k: z[k] for k in z.files})
        _candle_cache[str(path)] = cached
    a = cached[1]
    t = a["t"]
    bar = int(t[1] - t[0])
    i0 = int(np.searchsorted(t, start_ms, side="left"))
    i1 = int(np.searchsorted(t, end_ms, side="left"))
    count = max(0, i1 - i0)
    k = max(1, -(-count // max_bars))
    s0 = i0 - (i0 % k)
    line, up, delta = a[f"line{length}"], a[f"up{length}"], a[f"delta{length}"]
    rows = []
    for j in range(s0, i1, k):
        e = min(j + k, len(t))
        if e <= j:
            break
        last = e - 1
        ln = float(line[last])
        rows.append([int(t[j]), float(a["o"][j]), float(a["h"][j:e].max()), float(a["l"][j:e].min()), float(a["c"][last]),
                     ln if ln == ln else None, bool(up[last]), round(float(delta[last]), 1)])
    return {"tf": tf, "length": length, "bar_ms": bar, "bucket": k, "first_ms": int(t[0]), "last_ms": int(t[-1]) + bar,
            "columns": ["t", "o", "h", "l", "c", "line", "up", "delta"], "rows": rows}


def load_backtest(directory: Optional[Path] = None) -> dict:
    """Read the registered experiment's results.json and trades.json (re-read only when they change)."""
    directory = Path(directory or EXPERIMENT_DIR)
    files = [directory / "results.json", directory / "trades.json"]
    stamp = tuple(f.stat().st_mtime_ns for f in files)       # FileNotFoundError when the builder has not run
    if _backtest_cache.get("key") != (str(directory), stamp):
        results = json.loads(files[0].read_text())
        trades = json.loads(files[1].read_text())
        _backtest_cache.update(key=(str(directory), stamp), value={
            "experiment_id": results.get("experiment_id"), "basis": results.get("basis"),
            "strategies": results.get("strategies"), "data": results.get("data"), "main": results.get("main"),
            "reproduction": results.get("reproduction"), "length_sensitivity": results.get("length_sensitivity"),
            "trades": trades, "registry_status": "inconclusive", "production_eligible": False})
    return _backtest_cache["value"]


class VidyaRetestService:
    """Launch and supervise the worker process; never block the API event loop."""

    def __init__(self, runtime: Path, check_seconds: float = 30.0):
        self.runtime = Path(runtime)
        self.check_seconds = check_seconds
        self.process = None
        self.stop_event = threading.Event()
        self.thread = None
        self.restarts = 0

    def start(self):
        self.thread = threading.Thread(target=self._supervise, name="vidya-retest-supervisor", daemon=True)
        self.thread.start()

    def _supervise(self):
        while not self.stop_event.is_set():
            if self.process is None or not self.process.is_alive():
                if self.process is not None:
                    self.restarts += 1
                    LOG.warning("vidya retest worker exited (code %s); restarting", self.process.exitcode)
                self.process = multiprocessing.Process(target=run_forever, args=(str(self.runtime),),
                                                       name="vidya-retest-worker", daemon=True)
                self.process.start()
            self.stop_event.wait(self.check_seconds)

    def close(self):
        self.stop_event.set()
        if self.process is not None and self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=3)
        if self.thread is not None:
            self.thread.join(timeout=2)

    def health(self):
        alive = self.process is not None and self.process.is_alive()
        return {"worker_alive": alive, "worker_pid": self.process.pid if alive else None, "restarts": self.restarts}


@contextmanager
def _connect(path: Path):
    """Short-lived reader connection, always closed (the worker owns all writes)."""
    if not path.exists():
        raise FileNotFoundError(path)
    db = sqlite3.connect(str(path), timeout=5)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def _meta(db, key, default=None):
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def _stats(rows):
    rs = [r["r"] for r in rows]
    if not rs:
        return {"trades": 0, "wins": 0, "win_rate": None, "avg_r": None, "total_r": 0.0, "pf": None,
                "return_pct": 0.0, "max_dd_pct": 0.0}
    eq, peak, dd = 1.0, 1.0, 0.0
    for r in rs:
        eq *= 1 + RISK_FRACTION * r
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    gains = sum(r for r in rs if r > 0)
    losses = -sum(r for r in rs if r < 0)
    wins = sum(1 for r in rs if r > 0)
    return {"trades": len(rs), "wins": wins, "win_rate": round(wins / len(rs) * 100, 1),
            "avg_r": round(sum(rs) / len(rs), 3), "total_r": round(sum(rs), 2),
            "pf": round(gains / losses, 2) if losses > 0 else None,
            "return_pct": round((eq - 1) * 100, 2), "max_dd_pct": round(dd * 100, 2)}


def _equity(rows, cap=1500):
    out, eq, cum = [], 1.0, 0.0
    for r in rows:
        eq *= 1 + RISK_FRACTION * r["r"]
        cum += r["r"]
        out.append({"t": r["exit_ms"], "cum_r": round(cum, 3), "equity": round(eq * 100, 3)})
    return out[-cap:]


def _positions(db, strategy, source):
    marks = {r["symbol"]: r for r in db.execute("SELECT symbol, close, bar_ms, trend, line FROM market")}
    out = []
    for row in db.execute("SELECT symbol, state, last_bar_ms FROM states WHERE strategy=? AND source=?", (strategy, source)):
        state = json.loads(row["state"])
        for status, key in (("open", "position"), ("pending", "order")):
            p = state.get(key) if isinstance(state, dict) else None
            if not p:
                continue
            mark = marks.get(row["symbol"])
            item = {"symbol": row["symbol"], "status": status, "side": p.get("side"), "stop": p.get("stop"),
                    "level": p.get("level"), "delta": p.get("delta"), "signal_ms": p.get("signal_index"),
                    "touch_ms": p.get("touch_index"), "entry_ms": p.get("entry_index"), "entry": p.get("entry"),
                    "target": p.get("target"), "risk": p.get("risk"), "mark": mark["close"] if mark else None,
                    "mark_bar_ms": mark["bar_ms"] if mark else None, "trend_line": mark["line"] if mark else None,
                    "unrealized_r": None}
            if status == "open" and mark and p.get("risk"):
                e, m = p["entry"], mark["close"]
                gross = (m - e) if p["side"] == "long" else (e - m)
                item["unrealized_r"] = round((gross - ROUND_TRIP_COST * (e + m) / 2) / p["risk"], 3)
            out.append(item)
    out.sort(key=lambda x: (x["status"] != "open", -(x["entry_ms"] or x["signal_ms"] or 0)))
    return out


def install(app, runtime: Path, service: Optional[VidyaRetestService] = None):
    path = Path(runtime) / DATABASE

    def check(strategy, source):
        if strategy not in STRATEGY_META:
            raise HTTPException(400, "未知策略。")
        if source not in SOURCES:
            raise HTTPException(400, "来源只支持 live 或 warmup。")

    def unavailable():
        return HTTPException(503, "VIDYA 回踩模拟盘正在初始化，首轮行情加载约需 10 分钟。")

    @app.get("/api/vidya-retest/overview")
    def vidya_overview(strategy: str = "tl-2r", source: str = "live"):
        check(strategy, source)
        try:
            with _connect(path) as db:
                trades = db.execute("SELECT r, exit_ms FROM trades WHERE strategy=? AND source=? ORDER BY exit_ms, id",
                                    (strategy, source)).fetchall()
                by_symbol = [dict(r) for r in db.execute(
                    "SELECT symbol, COUNT(*) AS trades, SUM(r > 0) AS wins, ROUND(SUM(r), 3) AS sum_r, "
                    "ROUND(AVG(r), 3) AS avg_r FROM trades WHERE strategy=? AND source=? "
                    "GROUP BY symbol ORDER BY trades DESC, sum_r DESC LIMIT 200", (strategy, source))]
                signals = [dict(json.loads(r["payload"]), symbol=r["symbol"]) for r in db.execute(
                    "SELECT symbol, payload FROM events WHERE strategy=? AND source=? AND kind IN ('signal','skip','gap') "
                    "ORDER BY bar_ms DESC, id DESC LIMIT 150", (strategy, source))]
                counts = dict(db.execute("SELECT COUNT(*) AS universe, SUM(trend='up') AS up, SUM(trend='down') AS down, "
                                         "SUM(near) AS near, MAX(bar_ms) AS last_bar_ms FROM market").fetchone())
                result = {"status": _meta(db, "status", {}), "activation": _meta(db, "activation"),
                          "strategy": strategy, "source": source,
                          "strategies": [{"id": k, **v} for k, v in STRATEGY_META.items()],
                          "stats": _stats(trades), "equity": _equity(trades), "positions": _positions(db, strategy, source),
                          "signals": signals, "by_symbol": by_symbol, "market": counts,
                          "round_trip_cost": ROUND_TRIP_COST, "risk_fraction": RISK_FRACTION,
                          "orders_enabled": False, "notifications_enabled": False, "now_ms": int(time.time() * 1000)}
        except (FileNotFoundError, sqlite3.OperationalError):
            raise unavailable() from None
        result["service"] = service.health() if service else None
        return result

    @app.get("/api/vidya-retest/trades")
    def vidya_trades(strategy: str = "tl-2r", source: str = "live", symbol: str = Query("", max_length=40),
                     side: Optional[str] = None, outcome: str = "all", offset: int = Query(0, ge=0),
                     limit: int = Query(100, ge=1, le=1000)):
        check(strategy, source)
        if side not in (None, "", "long", "short") or outcome not in ("all", "win", "loss"):
            raise HTTPException(400, "不支持的筛选条件。")
        where, args = ["strategy=?", "source=?"], [strategy, source]
        if symbol:
            where.append("symbol LIKE ?")
            args.append("%" + symbol.upper().replace("%", "") + "%")
        if side:
            where.append("side=?")
            args.append(side)
        if outcome != "all":
            where.append("r > 0" if outcome == "win" else "r <= 0")
        sql = " AND ".join(where)
        try:
            with _connect(path) as db:
                total = db.execute(f"SELECT COUNT(*) FROM trades WHERE {sql}", args).fetchone()[0]
                rows = [dict(r) for r in db.execute(
                    f"SELECT * FROM trades WHERE {sql} ORDER BY exit_ms DESC, id DESC LIMIT ? OFFSET ?",
                    args + [limit, offset])]
        except (FileNotFoundError, sqlite3.OperationalError):
            raise unavailable() from None
        return {"items": rows, "total": total, "offset": offset, "limit": limit}

    @app.get("/api/vidya-retest/backtest")
    def vidya_backtest():
        try:
            return load_backtest()
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, "历史回测结果不存在：先运行实验目录里的 build.py。") from None

    @app.get("/api/vidya-retest/history")
    def vidya_history():
        try:
            data = dict(load_history())
        except (FileNotFoundError, ValueError, KeyError):
            raise HTTPException(404, "报告方案的回测结果不存在：先运行 exp-vidya-report-nine-configs 的 build.py。") from None
        try:
            bt = load_backtest()
            data["trendline"] = {"experiment_id": bt["experiment_id"], "main": bt["main"],
                                 "length_sensitivity": bt["length_sensitivity"], "basis": bt["basis"],
                                 "strategies": bt["strategies"]}
        except (FileNotFoundError, ValueError):
            data["trendline"] = None
        return data

    @app.get("/api/vidya-retest/history/candles")
    def vidya_history_candles(tf: str = "15m", length: int = 10, start_ms: int = Query(..., ge=0),
                              end_ms: int = Query(..., ge=0), max_bars: int = Query(600, ge=50, le=3000)):
        if tf not in ("15m", "1h") or length not in (10, 14) or end_ms <= start_ms:
            raise HTTPException(400, "不支持的周期、Length 或时间范围。")
        try:
            return candle_window(tf, length, start_ms, end_ms, max_bars)
        except FileNotFoundError:
            raise HTTPException(404, "K 线缓存不存在：先运行 exp-vidya-report-nine-configs 的 build.py。") from None

    @app.get("/api/vidya-retest/market")
    def vidya_market(scope: str = "near", sort: str = "dist", search: str = Query("", max_length=40),
                     limit: int = Query(400, ge=1, le=1000)):
        if scope not in ("near", "strong", "all") or sort not in ("dist", "delta", "symbol"):
            raise HTTPException(400, "不支持的筛选条件。")
        where, args = [], []
        if scope == "near":
            where.append("near=1")
        elif scope == "strong":
            where.append("((trend='up' AND delta >= 50) OR (trend='down' AND delta <= -50))")
        if search:
            where.append("symbol LIKE ?")
            args.append("%" + search.upper().replace("%", "") + "%")
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        order = {"dist": "dist_atr IS NULL, dist_atr ASC", "delta": "ABS(delta) DESC", "symbol": "symbol"}[sort]
        try:
            with _connect(path) as db:
                rows = [dict(r) for r in db.execute(f"SELECT * FROM market {clause} ORDER BY {order} LIMIT ?", args + [limit])]
        except (FileNotFoundError, sqlite3.OperationalError):
            raise unavailable() from None
        return {"items": rows, "total": len(rows), "scope": scope, "near_atr": 1.0, "semantics": "descriptive_only"}
