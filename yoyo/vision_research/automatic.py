"""Durable, once-per-signal reviews isolated from SPIKE scanning/execution.

The owner authorized automatic API reviews on 2026-09-23. One local discovery
thread reads existing candidates every 30 seconds; one worker serializes reviews
with manual inference. The 12-bar research window is not a production freshness
gate. Claimed requests survive restart as interrupted and are never retried.
"""
from __future__ import annotations

import json
import threading
import time
import uuid

from .source import SourceError, finite
from .store import utc_now

POST_SIGNAL_BARS = 12
POLL_SECONDS = 30
PAUSE_CODES = {"authentication_failed", "payment_required", "quota", "quota_exceeded", "rate_limited",
               "permission_denied", "model_not_found", "unsupported_model_operation"}


class AutomaticReviews:
    def __init__(self, store, source, process, inference_lock, configured,
                 *, enabled_default=False, clock=None):
        self.store, self.source, self.process = store, source, process
        self.inference_lock, self.configured = inference_lock, configured
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.stop_event, self.wake_event = threading.Event(), threading.Event()
        self.threads = []
        self.error = None
        self.last_scan_at = None
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS automatic_settings "
                       "(id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL, error TEXT)")
            db.execute("INSERT OR IGNORE INTO automatic_settings(id,enabled) VALUES(1,?)",
                       (int(enabled_default),))
            db.execute("CREATE TABLE IF NOT EXISTS automatic_reviews "
                       "(signal_id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL, "
                       "bar_close_ms INTEGER NOT NULL, signal TEXT NOT NULL, record TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS automatic_pending ON automatic_reviews(status,bar_close_ms)")
            pending = db.execute("SELECT record FROM automatic_reviews WHERE status='running'").fetchall()
        for (raw,) in pending:
            job = json.loads(raw)
            run = store.get(job.get("run_id"))
            if run and run.get("status") in {"completed", "failed"}:
                self.finish(job, run)
            else:
                self.finish(job, status="interrupted", error="服务重启，上次调用结果未知；未自动重试。")

    def enabled(self):
        with self.store.connect() as db:
            return bool(db.execute("SELECT enabled FROM automatic_settings WHERE id=1").fetchone()[0])

    def set_enabled(self, enabled, error=None):
        with self.store.connect() as db:
            db.execute("UPDATE automatic_settings SET enabled=?,error=? WHERE id=1", (int(enabled), error))
        self.error = None
        self.wake_event.set()

    def snapshot(self):
        with self.store.connect() as db:
            enabled, saved_error = db.execute("SELECT enabled,error FROM automatic_settings WHERE id=1").fetchone()
            counts = dict.fromkeys(("queued", "running", "completed", "failed", "skipped", "interrupted"), 0)
            counts.update(dict(db.execute("SELECT status,COUNT(*) FROM automatic_reviews GROUP BY status")))
            rows = db.execute("SELECT record FROM automatic_reviews ORDER BY created_at DESC,bar_close_ms DESC LIMIT 200").fetchall()
        return {"enabled": bool(enabled), "post_signal_bars": POST_SIGNAL_BARS,
                "poll_interval_seconds": POLL_SECONDS, "counts": counts,
                "last_scan_at": self.last_scan_at, "error": saved_error or self.error,
                "items": [json.loads(raw) for (raw,) in rows]}

    def discover_once(self):
        if not self.enabled() or self.stop_event.is_set():
            return
        if not self.configured():
            self.error = "尚未配置智谱 API Key，自动识别等待配置。"
            return
        payload = self.source.list_signals()
        self.last_scan_at = utc_now()
        if payload.get("warning"):
            self.error = payload["warning"]
            return
        self.error = None
        now = self.clock()
        previous = {}
        for run in self.store.list():
            signal_id = run.get("provenance", {}).get("id")
            if (signal_id and run.get("status") == "completed"
                    and run.get("analysis_scope") == "current_right_edge"
                    and run.get("provenance", {}).get("time_boundary") == "live_observation"):
                previous.setdefault(signal_id, run)
        for signal in payload.get("items", []):
            if (not isinstance(signal, dict) or not isinstance(signal.get("id"), str)
                    or not signal["id"] or not finite(signal.get("bar_close_ms"))
                    or not finite(signal.get("timeframe_min")) or signal["timeframe_min"] <= 0
                    or signal["bar_close_ms"] > now):
                continue
            expired = now >= signal["bar_close_ms"] + POST_SIGNAL_BARS * signal["timeframe_min"] * 60_000
            job = {"signal_id": signal["id"], "status": "skipped" if expired else "queued",
                   "created_at": utc_now(), "run_id": None, "decision": None,
                   "error": "已超出信号后 12 根 K 线的复查范围。" if expired else None,
                   "observed_at_ms": None, "completed_at": None}
            run = previous.get(signal["id"])
            if run:
                job.update(self.run_summary(run), reused_existing=True)
            with self.store.connect() as db:
                # The database is the deduplication boundary, not a browser tab.
                db.execute("INSERT OR IGNORE INTO automatic_reviews VALUES(?,?,?,?,?,?)",
                           (signal["id"], job["status"], job["created_at"], signal["bar_close_ms"],
                            json.dumps(signal, ensure_ascii=False), json.dumps(job, ensure_ascii=False)))
        self.wake_event.set()

    @staticmethod
    def run_summary(run):
        return {"run_id": run["id"], "status": run["status"],
                "decision": run.get("decision") if run["status"] == "completed" else None,
                "error": run.get("error"), "completed_at": run.get("completed_at"),
                "observed_at_ms": run.get("provenance", {}).get("observed_at_ms")}

    def finish(self, job, run=None, *, status=None, error=None):
        result = dict(job)
        if run:
            result.update(self.run_summary(run))
        else:
            result.update(status=status, error=error, decision=None, completed_at=utc_now())
            if not self.store.get(result.get("run_id")):
                result["run_id"] = None
        with self.store.connect() as db:
            db.execute("UPDATE automatic_reviews SET status=?,record=? WHERE signal_id=?",
                       (result["status"], json.dumps(result, ensure_ascii=False), job["signal_id"]))
        code = (run or {}).get("error_details") or {}
        if code.get("code") in PAUSE_CODES or code.get("http_status") in {401, 402, 403, 429}:
            self.set_enabled(False, "自动识别已暂停：" + (result.get("error") or "请检查模型额度或配置。"))
        return result

    def process_once(self):
        if self.stop_event.is_set() or not self.enabled() or not self.configured():
            return False
        if not self.inference_lock.acquire(blocking=False):
            return False
        job = None
        try:
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                # Check pause inside the same transaction as claiming a job.
                if not db.execute("SELECT enabled FROM automatic_settings WHERE id=1").fetchone()[0]:
                    return False
                row = db.execute("SELECT signal,record FROM automatic_reviews WHERE status='queued' "
                                 "ORDER BY bar_close_ms DESC,created_at LIMIT 1").fetchone()
                if row is None:
                    return False
                signal, job = map(json.loads, row)
                job.update(status="running", run_id=uuid.uuid4().hex, started_at=utc_now())
                db.execute("UPDATE automatic_reviews SET status='running',record=? WHERE signal_id=?",
                           (json.dumps(job, ensure_ascii=False), job["signal_id"]))
            expiry = signal["bar_close_ms"] + POST_SIGNAL_BARS * signal["timeframe_min"] * 60_000
            if self.clock() >= expiry:
                self.finish(job, status="skipped", error="排队期间已超出 12 根 K 线复查范围，未调用模型。")
            else:
                run = self.process(signal, job["run_id"])
                self.finish(job, run)
            return True
        except SourceError as exc:
            if job:
                self.finish(job, status="failed", error=str(exc))
            return True
        except Exception:
            if job:
                self.finish(job, status="failed", error="自动识别异常；结果未知，未自动重试。")
            self.error = "自动识别发生异常，可查看失败卡片。"
            return False
        finally:
            self.inference_lock.release()

    def start(self):
        if self.threads:
            return
        self.stop_event.clear()

        def discover():
            while not self.stop_event.is_set():
                try:
                    self.discover_once()
                except Exception:
                    self.error = "自动识别暂时无法读取候选，请检查本地服务。"
                self.stop_event.wait(POLL_SECONDS)

        def work():
            while not self.stop_event.is_set():
                if not self.process_once():
                    self.wake_event.wait(1)
                    self.wake_event.clear()

        self.threads = [threading.Thread(target=discover, name="vision-discovery", daemon=True),
                        threading.Thread(target=work, name="vision-review", daemon=True)]
        for thread in self.threads:
            thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()
        for thread in self.threads:
            thread.join(timeout=1)
