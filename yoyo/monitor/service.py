"""Independent Mac scan loop: public OHLCV -> Pine-equivalent events -> outbox.

The owner requested all-market 5m/15m/30m/1H/4H/daily monitoring. 5m, 15m and 30m
are display-only; 1H/4H/daily send starts and extra confirmations via Bark;
Telegram is disabled. This is an indicator monitor, not an ACTIVE/model
promotion, broker position tracker, execution path or backtest. Existing VPS
cadence, cache and freshness settings remain untouched.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import multiprocessing
import hashlib
from pathlib import Path
import subprocess
import threading
import time

from yoyo.monitor import (FRESH_MS, TIMEFRAMES, VERSION, SIGNAL_PROTOCOL, SIGNAL_KIND,
                          TV_PROFILE_ID, HIGHER_TIMEFRAME, MONITORED_TIMEFRAMES, MODEL_KIND, MODEL_PROTOCOL, MODEL_MAX_WAIT,
                          DIRECT_POLICY, DIRECT_TIMEFRAMES, BARK_TIMEFRAMES)
from yoyo.monitor.policy import is_tv_start
from yoyo.monitor.notification_policy import delivery_error, activation
from yoyo.monitor.model_gate import ModelGate
from yoyo.monitor.okx import OKX
from yoyo.monitor.store import now_ms
from yoyo.monitor.telegram import TelegramWorker
from yoyo.monitor.bark import BarkWorker

LOG = logging.getLogger("fable.monitor")
PROTOCOL = MODEL_PROTOCOL


class Monitor:
    def __init__(self, store, interval=120, client=None):
        self.store = store
        self.client = client or OKX()
        self.interval = interval
        self.started = now_ms()
        self.notification_since = store.get_meta("notification_policy:" + PROTOCOL, {}).get("activated_ms", self.started)
        self.notification_ready = threading.Event()
        module_dir = Path(__file__).resolve().parent
        self.source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in module_dir.glob("*.py")}
        try:
            self.source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=module_dir.parents[1], text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            self.source_commit = None
        self.stop_event = threading.Event()
        self.candles = {}
        self.charts = {}
        self.lock = threading.RLock()
        self.instruments = []
        self.universe_at = 0
        self.telegram = TelegramWorker(store)
        self.bark = BarkWorker(store)
        self.bark_since = None
        self.timeframe_since = {tf: store.timeframe_activation(tf, protocol=PROTOCOL) for tf in MONITORED_TIMEFRAMES}
        self.model_gate = ModelGate(store, lambda: self.client.clock(), self.stop_event)
        self.threads = []
        self.scan_process = None
        self.model_process = None

    def start(self):
        # Called only after server lifespan owns the singleton process lock.
        self.store.recover_outbox()
        self.store.retire_telegram_pending()
        self.store.retire_disabled_timeframes()
        self.store.retire_muted_bark_timeframes()
        for name, target in (("scan", self.run), ("model", self.run_model), ("bark", self.deliver_bark)):
            thread = threading.Thread(target=target, name="impulse-" + name, daemon=True)
            self.threads.append(thread)
            thread.start()

    def close(self):
        self.stop_event.set()
        if self.scan_process is not None and self.scan_process.is_alive():
            self.scan_process.terminate()
            self.scan_process.join(timeout=2)
        if self.model_process is not None and self.model_process.is_alive():
            self.model_process.terminate()
            self.model_process.join(timeout=2)
        for thread in self.threads:
            thread.join(timeout=2)

    def run(self):
        """Launch the persistent isolated worker; never block FastAPI's GIL."""
        from yoyo.monitor.v1_worker import scan_forever
        while not self.stop_event.is_set():
            if self.scan_process is None or not self.scan_process.is_alive():
                self.scan_process = multiprocessing.Process(target=scan_forever, args=(str(self.store.path), self.interval), daemon=True)
                self.scan_process.start()
            self.stop_event.wait(self.interval)

    def run_model(self):
        """Keep Torch/Ultralytics outside the API process."""
        from yoyo.monitor.v1_model_worker import model_forever
        while not self.stop_event.is_set():
            if self.model_process is None or not self.model_process.is_alive():
                self.model_process = multiprocessing.Process(target=model_forever, args=(str(self.store.path),), daemon=True)
                self.model_process.start()
            self.stop_event.wait(self.interval)

    def deliver_bark(self):
        while not self.stop_event.is_set():
            if not self.notification_ready.is_set():
                if self.store.get_meta("notification_policy:v1_bark_arm") is None:
                    self.stop_event.wait(3)
                    continue
                self.notification_ready.set()
            try:
                worked = self.bark.deliver_once(self.client.clock())
            except Exception as exc:
                LOG.error("bark worker failure: %s", type(exc).__name__)
                worked = False
            self.stop_event.wait(1.1 if worked else 3)

    def scan(self):
        start = now_ms()
        self.client.synchronize()
        if not self.notification_ready.is_set():
            # Use the same calibrated clock as candle closes and freshness.
            # A slow Mac clock must not turn a pre-upgrade close into a new bar.
            # Retain the model-candidate history baseline without re-enabling TG.
            self.notification_since = self.store.get_meta("notification_policy:" + PROTOCOL, {}).get("activated_ms", self.client.clock())
            if self.bark.creds:
                self.bark_since = self.store.activate_bark_policy(self.client.clock(), protocol=PROTOCOL, retire_obsolete=False)
            self.timeframe_since = {tf: self.store.activate_timeframe_policy(tf, self.client.clock(), protocol=PROTOCOL)
                                    for tf in MONITORED_TIMEFRAMES}
            # This is an additive delivery policy, not a new signal definition.
            # Persist fresh cutovers; the pre-YOLO raw-arrow cutovers cannot grant it.
            if self.bark.creds:
                self.store.activate_bark_policy(self.client.clock(), protocol=DIRECT_POLICY, retire_obsolete=False)
            for tf in DIRECT_TIMEFRAMES:
                self.store.activate_timeframe_policy(tf, self.client.clock(), protocol=DIRECT_POLICY)
            self.notification_ready.set()
        if not self.instruments or start - self.universe_at >= 3600000:
            self.instruments = self.client.instruments()
            self.universe_at = start
            self.store.set_meta("universe", {"count": len(self.instruments), "scope": "OKX 全部在交易永续合约（USDT 与币本位）", "updated_at_ms": start,
                                              "settlements": dict(Counter(x["settleCcy"] for x in self.instruments))})
            live = {x["instId"] for x in self.instruments}
            for row in self.store.list_markets():
                if row["symbol"] not in live:
                    row.update(active=False, error="instrument_not_live")
                    self.store.upsert_market(row)
        scan = dict(status="scanning", started_at_ms=start, finished_at_ms=None,
                    completed=0, total=len(self.instruments) * len(MONITORED_TIMEFRAMES), errors=0, next_scan_ms=None,
                    error_samples=[])
        self.store.set_meta("scan", scan)
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="okx-public") as executor:
            jobs = {executor.submit(self.scan_symbol, x): x["instId"] for x in self.instruments}
            for future in as_completed(jobs):
                symbol = jobs[future]
                try:
                    errors = future.result()
                except Exception as exc:
                    errors = [(tf, type(exc).__name__) for tf in MONITORED_TIMEFRAMES]
                    for tf, error in errors:
                        self.store.upsert_market(dict(symbol=symbol, timeframe=tf, phase="loading", error=error,
                                                      active=True, last_scan_ms=now_ms()))
                scan["completed"] += len(MONITORED_TIMEFRAMES)
                scan["errors"] += len(errors)
                if len(scan["error_samples"]) < 8:
                    scan["error_samples"].extend([dict(symbol=symbol, timeframe=tf, error=e) for tf, e in errors])
                self.store.set_meta("scan", scan)
        end = now_ms()
        scan.update(status="degraded" if scan["errors"] else "idle", finished_at_ms=end,
                    duration_seconds=round((end - start) / 1000, 2), next_scan_ms=end + self.interval * 1000)
        self.store.set_meta("scan", scan)
        LOG.info("scan complete: %s/%s pairs, %s errors, %.1fs", scan["completed"], scan["total"], scan["errors"], (end - start) / 1000)

    def scan_symbol(self, instrument):
        from yoyo.monitor.signals import analyze

        symbol = instrument["instId"]
        errors, loaded, gaps = [], {}, {}
        for timeframe in TIMEFRAMES:
            try:
                previous = self.candles.get((symbol, timeframe), [])
                loaded[timeframe], gaps[timeframe] = self.client.candles(symbol, timeframe, previous)
                self.candles[(symbol, timeframe)] = loaded[timeframe]
            except Exception as exc:
                loaded[timeframe] = []
                if timeframe in MONITORED_TIMEFRAMES:
                    errors.append((timeframe, str(exc) if type(exc).__name__ == "MarketError" else type(exc).__name__))
        for timeframe, higher in HIGHER_TIMEFRAME.items():
            now = self.client.clock()
            lower = loaded[timeframe]
            if not lower:
                self.store.upsert_market(dict(symbol=symbol, timeframe=timeframe, phase="loading", error="market_data_unavailable",
                                              active=True, last_scan_ms=now))
                with self.lock:
                    old = self.charts.get((symbol, timeframe))
                    if old:
                        old["state"] = dict(old["state"], phase="loading", stale=True,
                                            error="market_data_unavailable", last_scan_ms=now)
                continue
            last_closed = lower[-1]["t"] + TIMEFRAMES[timeframe]
            expected_closed = now // TIMEFRAMES[timeframe] * TIMEFRAMES[timeframe]
            stale = last_closed < expected_closed
            # No new candle: a cached full calculation is the same observation.
            with self.lock:
                cached = self.charts.get((symbol, timeframe))
            higher_last = loaded[higher][-1]["t"] if loaded[higher] else None
            if (cached and cached["candles"] and cached["candles"][-1]["t"] == lower[-1]["t"]
                    and cached.get("higher_last_ms") == higher_last and not cached["state"].get("error")):
                state = dict(cached["state"], last_scan_ms=now, stale=stale)
                if stale:
                    state["error"] = "awaiting_latest_confirmed_bar"
                    errors.append((timeframe, state["error"]))
                self.store.upsert_market(state)
                with self.lock:
                    self.charts[(symbol, timeframe)]["state"] = state
                if not stale:
                    self.model_gate.submit(symbol, timeframe, cached["candles"])
                continue
            result = analyze(lower, loaded[higher], timeframe, tick=float(instrument["tickSz"]))
            state = dict(result["state"], symbol=symbol, timeframe=timeframe, active=True,
                         last_scan_ms=now, stale=stale, gap_count=gaps.get(timeframe, 0),
                         available_bars=len(lower), higher_bars=len(loaded[higher]),
                         higher_timeframe=higher, tick_size=instrument.get("tickSz"),
                         settlement=instrument.get("settleCcy"), protocol=SIGNAL_PROTOCOL)
            if stale:
                state["error"] = "awaiting_latest_confirmed_bar"
                errors.append((timeframe, state["error"]))
            self.store.upsert_market(state)
            history_since = now - 7 * 86400000
            kept = []
            for raw in result["events"]:
                if raw["bar_close_ms"] < history_since:
                    continue
                event = dict(raw, symbol=symbol, timeframe=timeframe, protocol=SIGNAL_PROTOCOL, detected_at_ms=now)
                event["is_fresh"] = 0 <= now - event["bar_close_ms"] <= FRESH_MS
                # Queue the original first; candidate registration also journals it.
                # Reinserted history must never acquire a new delivery receipt.
                self.record_arrow(event, result["chart"], now, stale=stale)
                timeframe_since = self.timeframe_since.get(timeframe)
                first_channel = min(self.notification_since, self.bark_since) if self.bark_since is not None else self.notification_since
                if (is_tv_start(event) and timeframe_since is not None
                        and event["bar_close_ms"] > max(first_channel, timeframe_since)
                        and now <= event["bar_close_ms"] + MODEL_MAX_WAIT * TIMEFRAMES[timeframe] + FRESH_MS):
                    self.model_gate.register(event)
                kept.append(event)
            with self.lock:
                self.charts[(symbol, timeframe)] = dict(symbol=symbol, timeframe=timeframe,
                                                       candles=result["chart"][-240:], events=kept, state=state,
                                                       higher_last_ms=higher_last)
            if not stale:
                self.model_gate.submit(symbol, timeframe, result["chart"])
        return errors

    def record_arrow(self, event, chart, now, *, stale=False):
        """Journal one closed arrow and its independent Bark-only direct leg."""
        bark_notify = (not stale and bool(self.bark.creds)
                       and delivery_error(self.store, event, now, "bark") is None)
        return self.store.upsert_event(event, notify=False, bark_notify=bark_notify)

    def chart(self, symbol, timeframe):
        with self.lock:
            chart = self.charts.get((symbol, timeframe))
            if chart is None:
                stored = next((row for row in self.store.list_markets() if row.get("symbol") == symbol and row.get("timeframe") == timeframe), None)
                if stored and stored.get("chart") is not None:
                    return {"symbol":symbol, "timeframe":timeframe, "candles":stored["chart"], "events":stored.get("events", []), "state":stored}
                return None
            state = dict(chart["state"])
            expected = self.client.clock() // TIMEFRAMES[timeframe] * TIMEFRAMES[timeframe]
            if state.get("bar_close_ms", 0) < expected:
                state.update(stale=True, error=state.get("error") or "awaiting_latest_confirmed_bar")
            return dict(chart, state=state)

    def status(self):
        counts = self.store.market_phase_counts(MONITORED_TIMEFRAMES)
        arm_receipt = self.store.get_meta("notification_policy:v1_bark_arm")
        return dict(service="spike Impulse Monitor", version=VERSION, protocol=PROTOCOL,
                    now_ms=self.client.clock(), started_at_ms=self.started,
                    scan=self.store.get_meta("scan", {"status": "starting", "completed": 0, "total": 0, "errors": 0}),
                    universe=self.store.get_meta("universe", {"count": 0, "scope": "OKX 全部在交易永续合约"}),
                    counts=dict(counts, signals_24h=self.store.count_since(self.client.clock() - 86400000, MODEL_KIND, PROTOCOL),
                                indicator_starts_24h=self.store.direct_event_count(self.client.clock() - 86400000)),
                    telegram=self.telegram.status(), bark=self.bark.status(), runtime={"host": "This Mac", "notification_only": True,
                    "fresh_minutes": FRESH_MS // 60000, "interval_seconds": self.interval, "timeframes": list(MONITORED_TIMEFRAMES),
                    "clock_offset_ms": self.client.offset_ms, "public_requests": self.client.requests,
                    "candle_storage": "memory_only", "history_days": 7,
                    "signal_mode": "SPIKE V1 长多 30m/1H/4H；原始收盘启动与 YOLO 补充确认分离", "signal_kind": MODEL_KIND,
                    "notification_mode": "two_stage" if arm_receipt else "two_stage_disarmed", "direct_timeframes": list(DIRECT_TIMEFRAMES),
                    "notification_channels": ["bark"],
                    "bark_timeframes": list(BARK_TIMEFRAMES),
                    "display_only_timeframes": [tf for tf in MONITORED_TIMEFRAMES if tf not in BARK_TIMEFRAMES],
                    "direct_notification_policy": DIRECT_POLICY,
                    "direct_notification_since_ms": {c: activation(self.store, c, DIRECT_POLICY) for c in ("telegram", "bark")},
                    "direct_timeframe_since_ms": {tf: self.store.timeframe_activation(tf, protocol=DIRECT_POLICY) for tf in DIRECT_TIMEFRAMES},
                    "model_gate": self.store.get_meta("v1:model_gate", self.model_gate.status()),
                    "notification_armed": bool(arm_receipt),
                    "tv_profile": {"id": TV_PROFILE_ID, "show_focus": True, "show_marks": False,
                                   "focus_min_bars": 12, "focus_atr_band": .10, "verified_on": "2026-09-08",
                                   "sync_mode": "observed_settings_snapshot"},
                    "notification_since_ms": self.notification_since,
                    "bark_notification_since_ms": self.bark_since,
                    "timeframe_notification_since_ms": dict(self.timeframe_since),
                    "higher_mode": "已确认高周期背景标注，不过滤启动",
                    "source_commit": self.source_commit, "startup_source_sha256": self.source_hashes,
                    "warmup_bars": 340, "launch_agent": "com.fable.impulse-monitor"})

    def health(self):
        """Return liveness without decoding charts, events, or receipt history."""
        now = self.client.clock()
        scan = self.store.get_meta("scan", {"status": "starting", "completed": 0, "total": 0, "errors": 0})
        model = self.store.get_meta("v1:model_gate", self.model_gate.status())
        last = scan.get("finished_at_ms")
        market_ready = bool(last and now - last < 20 * 60000
                            and scan.get("total", 0) > scan.get("errors", 0)
                            and scan.get("status") not in ("error", "starting"))
        model_ready = model.get("status") == "ready"
        return {"service_alive": True, "now_ms": now, "scan": scan,
                "market_ready": market_ready, "model_ready": model_ready,
                "ok": market_ready and model_ready and scan.get("errors", 0) == 0}

    def markets(self):
        rows = [r for r in self.store.list_markets()
                if r.get("active", True) and r.get("timeframe") in MONITORED_TIMEFRAMES]
        now = self.client.clock()
        for row in rows:
            duration = TIMEFRAMES[row["timeframe"]]
            if row.get("bar_close_ms") and row["bar_close_ms"] < now // duration * duration:
                row.update(stale=True, error=row.get("error") or "awaiting_latest_confirmed_bar")
        return rows
