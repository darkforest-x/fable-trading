"""Durable forward simulation runs, intent receipts and immutable observations.

Run/strategy separation follows Freqtrade run bundles and Nautilus lifecycle
contracts. This store contains no account credentials, exchange orders or
production eligibility switches. Stop censors an open simulation, never invents
an exit. All entry timestamps are strictly after first observation.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid


FILL_POLICY = "observed-then-next-future-bar-open-v1"
STEP_MS = {"15m": 900000, "30m": 1800000, "1H": 3600000, "4H": 14400000}


def clock_ms():
    return time.time_ns() // 1_000_000


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


class PaperStore:
    def __init__(self, runtime):
        self.runtime = Path(runtime)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.path = self.runtime / "paper.sqlite3"
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, strategy_id TEXT NOT NULL,
              strategy_name TEXT NOT NULL, strategy_version TEXT NOT NULL,
              status TEXT NOT NULL, created_ms INTEGER NOT NULL, admit_after_ms INTEGER NOT NULL,
              heartbeat_ms INTEGER, error TEXT, spec TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS decisions (
              run_id TEXT NOT NULL, event_id TEXT NOT NULL, symbol TEXT NOT NULL,
              timeframe TEXT NOT NULL, side TEXT NOT NULL, signal_close_ms INTEGER NOT NULL,
              observed_ms INTEGER NOT NULL, scheduled_entry_ms INTEGER NOT NULL,
              status TEXT NOT NULL, reason TEXT, initial_stop REAL,
              payload TEXT NOT NULL, trade TEXT NOT NULL DEFAULT '{}',
              PRIMARY KEY(run_id,event_id));
            CREATE INDEX IF NOT EXISTS decision_run_status ON decisions(run_id,status);
            CREATE TABLE IF NOT EXISTS stream_bars (
              run_id TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
              t INTEGER NOT NULL, payload TEXT NOT NULL, observed_ms INTEGER NOT NULL,
              PRIMARY KEY(run_id,symbol,timeframe,t));
            CREATE TABLE IF NOT EXISTS transitions (
              sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
              at_ms INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _run(row):
        if row is None:
            raise KeyError("模拟运行不存在")
        value = dict(row)
        value["spec"] = json.loads(value["spec"])
        value.pop("request_id", None)
        return value

    @staticmethod
    def _event(db, run_id, at, kind, value):
        db.execute("INSERT INTO transitions(run_id,at_ms,kind,payload) VALUES(?,?,?,?)",
                   (run_id, at, kind, encode(value)))

    def create(self, strategy, spec, request_id, at=None):
        at = clock_ms() if at is None else at
        run_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM runs WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                old = self._run(existing)
                if old["strategy_id"] != strategy["id"] or old["spec"] != spec:
                    raise ValueError("请求标识已用于不同的运行配置")
                run_id = old["id"]
            else:
                count = db.execute("SELECT COUNT(*) FROM runs WHERE status IN ('running','paused')").fetchone()[0]
                if count >= 8:
                    raise ValueError("最多同时跟踪 8 个模拟运行，请先结束旧运行")
                db.execute("INSERT INTO runs(id,request_id,strategy_id,strategy_name,strategy_version,status,created_ms,admit_after_ms,spec) VALUES(?,?,?,?,?,'running',?,?,?)",
                           (run_id, request_id, strategy["id"], strategy["name"], strategy["version"], at, at, encode(spec)))
                self._event(db, run_id, at, "created", {"spec": spec})
        return self.run(run_id)

    def run(self, run_id):
        with self.connect() as db:
            result = self._run(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
            rows = db.execute("SELECT status,trade FROM decisions WHERE run_id=?", (run_id,)).fetchall()
        trades = [json.loads(r["trade"]) for r in rows if r["status"] == "closed"]
        net = [float(t["net_r"]) for t in trades if t.get("net_r") is not None]
        result["metrics"] = {"accepted": sum(r["status"] in {"pending", "open", "closed", "censored", "stopped"} for r in rows),
                             "closed": len(trades), "open": sum(r["status"] == "open" for r in rows),
                             "pending": sum(r["status"] == "pending" for r in rows),
                             "skipped": sum(r["status"] in {"skipped", "rejected"} for r in rows),
                             "censored": sum(r["status"] in {"censored", "stopped"} for r in rows),
                             "net_r": sum(net) if net else None,
                             "win_rate": sum(x > 0 for x in net) / len(net) if net else None}
        return result

    def runs(self, active_only=False):
        with self.connect() as db:
            clause = " WHERE status IN ('running','paused')" if active_only else ""
            ids = db.execute("SELECT id FROM runs" + clause + " ORDER BY created_ms DESC LIMIT 200").fetchall()
        return [self.run(r[0]) for r in ids]

    def action(self, run_id, action, at=None):
        at = clock_ms() if at is None else at
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._run(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
            target = {"pause": "paused", "resume": "running", "stop": "stopped"}[action]
            if row["status"] == "stopped":
                if target != "stopped":
                    raise ValueError("已结束运行不能续跑，请新建运行以保留时间边界")
                return self.run(run_id)
            if row["status"] == "error" and action != "stop":
                raise ValueError("运行证据或版本发生异常，请保留旧记录并新建运行")
            if target != row["status"]:
                # Cancel unfilled intents when pausing; keep tracking open trades.
                if action in {"pause", "stop"}:
                    db.execute("UPDATE decisions SET status='skipped',reason=? WHERE run_id=? AND status='pending' AND scheduled_entry_ms>=?",
                               ("paused_before_fill" if action == "pause" else "stopped_before_fill", run_id, at))
                if action == "stop":
                    db.execute("UPDATE decisions SET status='stopped',reason='run_stopped_with_open_position' WHERE run_id=? AND status IN ('open','pending')", (run_id,))
                db.execute("UPDATE runs SET status=?,admit_after_ms=?,revision=revision+1 WHERE id=?",
                           (target, at if action == "resume" else row["admit_after_ms"], run_id))
                self._event(db, run_id, at, action, {"from": row["status"], "to": target})
        return self.run(run_id)

    def heartbeat(self, run_id, error=None, fatal=False, at=None):
        at = clock_ms() if at is None else at
        with self.connect() as db:
            db.execute("UPDATE runs SET heartbeat_ms=?,error=? WHERE id=? AND status IN ('running','paused')", (at, error, run_id))
            if fatal:
                db.execute("UPDATE runs SET status='error',revision=revision+1 WHERE id=? AND status IN ('running','paused')", (run_id,))
                self._event(db, run_id, at, "error", {"message": error})

    def decide(self, run_id, event, observed_ms, fresh_ms):
        """Admit an observed signal once, without backdating its order intent."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._run(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
            if run["status"] != "running":
                return False
            if db.execute("SELECT 1 FROM decisions WHERE run_id=? AND event_id=?", (run_id, event["id"])).fetchone():
                return False
            close, detected = event["bar_close_ms"], event["detected_at_ms"]
            reason = None
            if close <= run["admit_after_ms"] or detected <= run["admit_after_ms"]:
                reason = "before_activation"
            elif not close <= detected <= observed_ms:
                reason = "invalid_signal_clock"
            elif observed_ms - close > fresh_ms:
                reason = "signal_stale_at_observation"
            elif db.execute("SELECT 1 FROM decisions WHERE run_id=? AND symbol=? AND timeframe=? AND status IN ('open','pending')",
                            (run_id, event["symbol"], event["timeframe"])).fetchone():
                reason = "already_in_position"
            step = STEP_MS[event["timeframe"]]
            scheduled = (observed_ms // step + 1) * step
            status = "skipped" if reason else "pending"
            db.execute("INSERT INTO decisions(run_id,event_id,symbol,timeframe,side,signal_close_ms,observed_ms,scheduled_entry_ms,status,reason,initial_stop,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (run_id, event["id"], event["symbol"], event["timeframe"], event["side"], close, observed_ms,
                        scheduled, status, reason, event.get("initial_stop"), encode(event)))
            self._event(db, run_id, observed_ms, "intent" if not reason else "skipped", {"event_id": event["id"], "scheduled_entry_ms": scheduled, "reason": reason})
        return not reason

    def seen_ids(self, run_id):
        with self.connect() as db:
            return {r[0] for r in db.execute("SELECT event_id FROM decisions WHERE run_id=?", (run_id,))}

    def decisions(self, run_id, limit=50, offset=0, active_only=False):
        self.run(run_id)
        with self.connect() as db:
            clause = " AND status IN ('pending','open')" if active_only else ""
            args = (run_id,)
            total = db.execute("SELECT COUNT(*) FROM decisions WHERE run_id=?" + clause, args).fetchone()[0]
            rows = db.execute("SELECT * FROM decisions WHERE run_id=?" + clause + " ORDER BY observed_ms DESC,event_id LIMIT ? OFFSET ?", args + (limit, offset)).fetchall()
        return {"decisions": [dict(r, payload=json.loads(r["payload"]), trade=json.loads(r["trade"])) for r in rows], "total": total, "offset": offset, "limit": limit}

    def update_trade(self, run_id, event_id, trade, at=None):
        at = clock_ms() if at is None else at
        if trade["status"] not in {"pending", "open", "closed", "censored", "rejected"}:
            raise ValueError("invalid simulator state")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if not state or state[0] not in {"running", "paused"}:
                return
            row = db.execute("SELECT status,trade FROM decisions WHERE run_id=? AND event_id=?", (run_id, event_id)).fetchone()
            if not row or row["status"] not in {"pending", "open"}:
                return
            if row["trade"] != encode(trade):
                db.execute("UPDATE decisions SET status=?,reason=?,trade=? WHERE run_id=? AND event_id=?",
                           (trade["status"], trade.get("reason"), encode(trade), run_id, event_id))
                self._event(db, run_id, at, "trade", {"event_id": event_id, "trade": trade})

    def merge_bars(self, run_id, symbol, timeframe, candles, observed_ms):
        """Persist consumed bars once; a revised candle invalidates this run."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for bar in candles:
                value = {k: bar[k] for k in ("t", "o", "h", "l", "c", "v")}
                old = db.execute("SELECT payload FROM stream_bars WHERE run_id=? AND symbol=? AND timeframe=? AND t=?",
                                 (run_id, symbol, timeframe, bar["t"])).fetchone()
                if old and old[0] != encode(value):
                    raise ValueError("已使用的闭合 K 线被修订，停止本次运行以保留原证据")
                db.execute("INSERT OR IGNORE INTO stream_bars VALUES(?,?,?,?,?,?)",
                           (run_id, symbol, timeframe, bar["t"], encode(value), observed_ms))
            rows = db.execute("SELECT payload FROM stream_bars WHERE run_id=? AND symbol=? AND timeframe=? ORDER BY t", (run_id, symbol, timeframe)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def export(self, run_id):
        result = self.run(run_id)
        result.update(self.decisions(run_id, limit=1_000_000))
        with self.connect() as db:
            rows = db.execute("SELECT at_ms,kind,payload FROM transitions WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()
            streams = db.execute("SELECT symbol,timeframe,COUNT(*) n,MIN(t) start,MAX(t) end FROM stream_bars WHERE run_id=? GROUP BY symbol,timeframe", (run_id,)).fetchall()
            h = hashlib.sha256()
            for r in db.execute("SELECT symbol,timeframe,t,payload FROM stream_bars WHERE run_id=? ORDER BY symbol,timeframe,t", (run_id,)):
                h.update(encode(list(r)).encode())
        result["transitions"] = [dict(r, payload=json.loads(r["payload"])) for r in rows]
        result["input_streams"] = [dict(r) for r in streams]
        result["input_sha256"] = h.hexdigest()
        result["production_eligible"] = False
        return result
