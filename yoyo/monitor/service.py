"""Independent Mac scan loop: public OHLCV -> Pine-equivalent events -> outbox.

The owner explicitly requested local all-market 15m/1H/4H monitoring and Telegram
notifications on 2026-09-08. This is an indicator monitor, not an ACTIVE/model
promotion, broker position tracker, execution path or backtest. Existing VPS
cadence, cache and freshness settings remain untouched.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import hashlib
from pathlib import Path
import subprocess
import threading
import time

from yoyo.monitor import (FRESH_MS, TIMEFRAMES, VERSION, SIGNAL_PROTOCOL, SIGNAL_KIND,
                          TV_PROFILE_ID, HIGHER_TIMEFRAME, MONITORED_TIMEFRAMES)
from yoyo.monitor.policy import is_tv_start
from yoyo.monitor.okx import OKX
from yoyo.monitor.store import now_ms
from yoyo.monitor.telegram import TelegramWorker
from yoyo.monitor.bark import BarkWorker

LOG = logging.getLogger("fable.monitor")
PROTOCOL = SIGNAL_PROTOCOL


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
        self.timeframe_since = {tf: store.timeframe_activation(tf) for tf in MONITORED_TIMEFRAMES}
        self.threads = []

    def start(self):
        # Called only after server lifespan owns the singleton process lock.
        self.store.recover_outbox()
        for name, target in (("scan", self.run), ("telegram", self.deliver), ("bark", self.deliver_bark)):
            thread = threading.Thread(target=target, name="impulse-" + name, daemon=True)
            self.threads.append(thread)
            thread.start()

    def close(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=2)

    def deliver(self):
        while not self.stop_event.is_set():
            if not self.notification_ready.is_set():
                self.stop_event.wait(3)
                continue
            try:
                worked = self.telegram.deliver_once(self.client.clock())
            except Exception as exc:
                LOG.error("outbox worker failure: %s", type(exc).__name__)
                worked = False
            self.stop_event.wait(1.1 if worked else 3)

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.scan()
            except Exception as exc:
                LOG.error("scan failed: %s", type(exc).__name__)
                scan = self.store.get_meta("scan", {})
                scan.update(status="error", error=type(exc).__name__, finished_at_ms=now_ms(), next_scan_ms=now_ms() + self.interval * 1000)
                self.store.set_meta("scan", scan)
            self.stop_event.wait(self.interval)

    def deliver_bark(self):
        while not self.stop_event.is_set():
            if not self.notification_ready.is_set():
                self.stop_event.wait(3)
                continue
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
            self.notification_since = self.store.activate_notification_policy(self.client.clock())
            if self.bark.creds:
                self.bark_since = self.store.activate_bark_policy(self.client.clock())
            self.timeframe_since = {tf: self.store.activate_timeframe_policy(tf, self.client.clock())
                                    for tf in MONITORED_TIMEFRAMES}
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
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="okx-public") as executor:
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
                continue
            result = analyze(lower, loaded[higher], timeframe)
            state = dict(result["state"], symbol=symbol, timeframe=timeframe, active=True,
                         last_scan_ms=now, stale=stale, gap_count=gaps.get(timeframe, 0),
                         available_bars=len(lower), higher_bars=len(loaded[higher]),
                         higher_timeframe=higher, tick_size=instrument.get("tickSz"),
                         settlement=instrument.get("settleCcy"), protocol=PROTOCOL)
            if stale:
                state["error"] = "awaiting_latest_confirmed_bar"
                errors.append((timeframe, state["error"]))
            self.store.upsert_market(state)
            history_since = now - 7 * 86400000
            kept = []
            for raw in result["events"]:
                if raw["bar_close_ms"] < history_since:
                    continue
                event = dict(raw, symbol=symbol, timeframe=timeframe, protocol=PROTOCOL, detected_at_ms=now)
                event["is_fresh"] = 0 <= now - event["bar_close_ms"] <= FRESH_MS
                # Only the actual visible Pine focus-release marker is a signal.
                # Recomputed history predating this protocol's activation stays
                # historical even if a new identity would otherwise be fresh.
                timeframe_since = self.timeframe_since.get(timeframe)
                base_eligible = (event["is_fresh"] and is_tv_start(event)
                                 and timeframe_since is not None
                                 and event["bar_close_ms"] > timeframe_since)
                eligible = base_eligible and event["bar_close_ms"] > self.notification_since
                bark_eligible = (base_eligible and self.bark_since is not None
                                 and event["bar_close_ms"] > self.bark_since)
                # Do not permanently consume a new signal as history only
                # because the latest market candle is temporarily unavailable.
                # On recovery it can be inserted once, if still fresh. Existing
                # historical identities are never promoted or requeued.
                if not stale or not (eligible or bark_eligible):
                    self.store.upsert_event(event, notify=eligible and not stale,
                                            bark_notify=bark_eligible and not stale)
                kept.append(event)
            with self.lock:
                self.charts[(symbol, timeframe)] = dict(symbol=symbol, timeframe=timeframe,
                                                       candles=result["chart"][-240:], events=kept, state=state,
                                                       higher_last_ms=higher_last)
        return errors

    def chart(self, symbol, timeframe):
        with self.lock:
            chart = self.charts.get((symbol, timeframe))
            if chart is None:
                return None
            state = dict(chart["state"])
            expected = self.client.clock() // TIMEFRAMES[timeframe] * TIMEFRAMES[timeframe]
            if state.get("bar_close_ms", 0) < expected:
                state.update(stale=True, error=state.get("error") or "awaiting_latest_confirmed_bar")
            return dict(chart, state=state)

    def status(self):
        rows = self.markets()
        counts = Counter("stale" if r.get("stale") and r.get("phase") != "loading" else r.get("phase", "loading") for r in rows)
        return dict(service="Fable Impulse Monitor", version=VERSION, protocol=PROTOCOL,
                    now_ms=self.client.clock(), started_at_ms=self.started,
                    scan=self.store.get_meta("scan", {"status": "starting", "completed": 0, "total": 0, "errors": 0}),
                    universe=self.store.get_meta("universe", {"count": 0, "scope": "OKX 全部在交易永续合约"}),
                    counts=dict(counts, signals_24h=self.store.count_since(self.client.clock() - 86400000)),
                    telegram=self.telegram.status(), bark=self.bark.status(), runtime={"host": "This Mac", "notification_only": True,
                    "fresh_minutes": FRESH_MS // 60000, "interval_seconds": self.interval, "timeframes": list(MONITORED_TIMEFRAMES),
                    "clock_offset_ms": self.client.offset_ms, "public_requests": self.client.requests,
                    "candle_storage": "memory_only", "history_days": 7,
                    "signal_mode": "TradingView 主图启动（蓄势释放标记）", "signal_kind": SIGNAL_KIND,
                    "tv_profile": {"id": TV_PROFILE_ID, "show_focus": True, "show_marks": False,
                                   "focus_min_bars": 12, "focus_atr_band": .10, "verified_on": "2026-09-08",
                                   "sync_mode": "observed_settings_snapshot"},
                    "notification_since_ms": self.notification_since,
                    "bark_notification_since_ms": self.bark_since,
                    "timeframe_notification_since_ms": dict(self.timeframe_since),
                    "higher_mode": "已确认高周期背景标注，不过滤启动",
                    "source_commit": self.source_commit, "startup_source_sha256": self.source_hashes,
                    "warmup_bars": 340, "launch_agent": "com.fable.impulse-monitor"})

    def markets(self):
        rows = [r for r in self.store.list_markets() if r.get("active", True)]
        now = self.client.clock()
        for row in rows:
            duration = TIMEFRAMES[row["timeframe"]]
            if row.get("bar_close_ms") and row["bar_close_ms"] < now // duration * duration:
                row.update(stale=True, error=row.get("error") or "awaiting_latest_confirmed_bar")
        return rows
